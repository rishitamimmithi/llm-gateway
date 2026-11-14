"""
Semantic cache: reuse an answer when a new prompt MEANS nearly the same as a stored one.

1. Turn the prompt into an embedding (a vector of numbers; similar meaning -> similar direction).
2. Compare it with every stored prompt vector using cosine similarity. The vectors are
   normalised to length 1, so cosine similarity is just a dot product: one matrix multiply.
3. If the best match is at or above the threshold (and not expired), return its answer.

Storage is a fixed-size matrix: a new entry goes into an empty or expired slot, or
overwrites the oldest entry when full. Expired entries are skipped at lookup time.
A linear scan is fine for a few thousand entries (1000 x 384 floats is ~1.5 MB and one
matrix multiply); a vector index (FAISS, HNSW) would be the next step for millions.

The risk: two prompts can look alike but ask different things ("10 USD to INR" vs
"10 INR to USD"). That's the "false hit" rate measured in eval/.
"""
import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.cache.answer import CachedAnswer
from app.cache.exact import normalise_prompt

logger = logging.getLogger("gateway.cache")


class Embedder(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return one row per text. Slow CPU work, so the cache runs it in a worker thread."""
        ...


class SentenceTransformerEmbedder:
    """Local, free embeddings. The model (~90 MB) is downloaded on first use, then loaded from disk."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, only when semantic cache is on

        self.model = SentenceTransformer(model_name, device="cpu")
        self.dimension = self.model.get_embedding_dimension()

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)


@dataclass(frozen=True)
class SemanticHit:
    answer: CachedAnswer
    similarity: float


def unit_vectors(vectors: np.ndarray) -> np.ndarray:
    """Scale each row to length 1 (rows of all zeros stay zero instead of dividing by 0)."""
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


class SemanticCache:
    def __init__(
        self,
        embedder: Embedder,
        *,
        threshold: float,
        max_entries: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("max_entries and ttl_seconds must be positive")
        self.embedder = embedder
        self.threshold = threshold
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._vectors = np.zeros((max_entries, embedder.dimension), dtype=np.float32)
        self._expires_at = np.full(max_entries, -np.inf)  # -inf = empty slot, never matches
        self._answers: list[CachedAnswer | None] = [None] * max_entries
        # One embedding at a time: the tokenizer isn't safe to share between threads,
        # and parallel torch calls would just fight over the same CPU cores.
        self._embed_lock = asyncio.Lock()

    async def embed(self, prompt: str) -> np.ndarray:
        """Embed in a worker thread so the event loop keeps serving other requests meanwhile."""
        async with self._embed_lock:
            vectors = await asyncio.to_thread(self.embedder.embed, [normalise_prompt(prompt)])
        return unit_vectors(vectors)[0]

    def lookup(self, vector: np.ndarray) -> SemanticHit | None:
        scores = self._vectors @ vector  # cosine similarity with every slot at once
        scores[self._expires_at <= self._clock()] = -np.inf  # skip empty and expired slots
        best = int(np.argmax(scores))
        similarity = float(scores[best])
        if similarity < self.threshold:
            return None
        return SemanticHit(self._answers[best], similarity)  # type: ignore[arg-type]

    def add(self, vector: np.ndarray, answer: CachedAnswer) -> None:
        # The slot that expires first: an empty or expired one if there is one, else the oldest
        # entry (every entry gets the same TTL, so "expires first" = "added first").
        slot = int(np.argmin(self._expires_at))
        self._vectors[slot] = vector
        self._expires_at[slot] = self._clock() + self.ttl_seconds
        self._answers[slot] = answer

    def __len__(self) -> int:
        """Live (stored and not expired) entries."""
        return int(np.count_nonzero(self._expires_at > self._clock()))
