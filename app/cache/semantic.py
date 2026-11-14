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
"10 INR to USD"). That's the "false hit" rate measured in eval/. With the guard on, every
match above the threshold must also pass app/cache/guard.py (same numbers, negation, direction);
if the best match fails, the next best is tried.
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
from app.cache.guard import Features, features, features_conflict

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
        guard: bool = True,
        clock: Callable[[], float] = time.monotonic,
        max_batch: int = 32,
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
        self._features: list[Features | None] = [None] * max_entries  # guard checks, computed once per entry
        self.guard = guard
        self.guard_blocks = 0  # matches above the threshold that the guard turned down
        # One embedding call at a time: the tokenizer isn't safe to share between threads,
        # and parallel torch calls would just fight over the same CPU cores.
        # Instead, prompts that arrive while a call is running wait in _pending and are embedded
        # together in the next call (micro-batching): on my laptop 17.6 ms per prompt alone,
        # ~5 ms per prompt in a batch of 8+. Nobody waits for a batch to "fill up".
        self.max_batch = max_batch
        self._pending: list[tuple[str, asyncio.Future]] = []
        self._worker: asyncio.Task | None = None

    async def embed(self, prompt: str) -> np.ndarray:
        """Queue the prompt for the next batch and wait for its vector."""
        loop = asyncio.get_running_loop()
        if self._worker is not None and self._worker.get_loop() is not loop:
            # A worker from an event loop that has closed (e.g. between tests) will never run again
            self._worker, self._pending = None, []
        future = loop.create_future()
        self._pending.append((normalise_prompt(prompt), future))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._embed_pending())
        return await future

    async def _embed_pending(self) -> None:
        """Embed waiting prompts in batches (in a worker thread, so the event loop keeps serving) until none are left."""
        batch: list[tuple[str, asyncio.Future]] = []
        try:
            while self._pending:
                batch, self._pending = self._pending[: self.max_batch], self._pending[self.max_batch :]
                try:
                    vectors = unit_vectors(await asyncio.to_thread(self.embedder.embed, [text for text, _ in batch]))
                    results = list(zip(batch, vectors, strict=True))  # one vector per prompt, or it's an error
                except Exception as e:  # every request in the batch gets the error (they skip the semantic cache)
                    for _, future in batch:
                        if not future.done():
                            future.set_exception(e)
                    continue
                for (_, future), vector in results:
                    if not future.done():  # done = that request was cancelled (client went away)
                        future.set_result(vector)
        finally:
            # Worker cancelled (shutdown): nobody may be left waiting on a future that will never be set
            for _, future in batch + self._pending:
                if not future.done():
                    future.cancel()
            self._pending = []

    async def aclose(self) -> None:
        """At shutdown: cancel prompts still queued, then let the batch already in the model finish.
        (Cancelling mid-batch would leave its thread running, and the next call could overlap it.)"""
        for _, future in self._pending:
            future.cancel()
        self._pending = []
        if self._worker is not None and not self._worker.done():
            await self._worker

    def lookup(self, vector: np.ndarray, prompt: str | None = None) -> SemanticHit | None:
        """Best live match at or above the threshold that the guard accepts.
        prompt=None skips the guard (only tests that check the similarity maths do that)."""
        scores = self._vectors @ vector  # cosine similarity with every slot at once
        scores[self._expires_at <= self._clock()] = -np.inf  # skip empty and expired slots
        candidates = np.flatnonzero(scores >= self.threshold)
        query = features(prompt) if self.guard and prompt is not None and len(candidates) else None
        for slot in candidates[np.argsort(-scores[candidates])]:  # best first
            if query is not None and features_conflict(self._features[slot], query):  # type: ignore[arg-type]
                self.guard_blocks += 1
                continue
            return SemanticHit(self._answers[slot], float(scores[slot]))  # type: ignore[arg-type]
        return None

    def add(self, vector: np.ndarray, answer: CachedAnswer, prompt: str) -> None:
        # The slot that expires first: an empty or expired one if there is one, else the oldest
        # entry (every entry gets the same TTL, so "expires first" = "added first").
        slot = int(np.argmin(self._expires_at))
        self._vectors[slot] = vector
        self._expires_at[slot] = self._clock() + self.ttl_seconds
        self._answers[slot] = answer
        self._features[slot] = features(prompt)

    def __len__(self) -> int:
        """Live (stored and not expired) entries."""
        return int(np.count_nonzero(self._expires_at > self._clock()))
