"""
Semantic cache tests with a fake embedder: each prompt maps to a hand-picked vector,
so similarities are known exactly and no model is downloaded.
"""
import numpy as np
import pytest

from app.cache.answer import CachedAnswer
from app.cache.semantic import SemanticCache, unit_vectors
from tests.test_lru_cache import FakeClock


class FakeEmbedder:
    dimension = 3

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table
        self.calls = 0

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        return np.array([self.table[t] for t in texts], dtype=np.float32)


TABLE = {
    "capital of France?": [1, 0, 0],
    "What is the capital of France?": [0.95, 0.312, 0],  # cosine ~0.95 with the first
    "capital of Spain?": [0.6, 0.8, 0],  # cosine 0.6
    "unrelated": [0, 0, 1],
    "zero": [0, 0, 0],
}


def answer(text: str) -> CachedAnswer:
    return CachedAnswer(text, "groq", "m", 3, 4, 0.001)


def make(threshold=0.9, max_entries=10, ttl=60, clock=None) -> SemanticCache:
    return SemanticCache(
        FakeEmbedder(TABLE), threshold=threshold, max_entries=max_entries, ttl_seconds=ttl, clock=clock or FakeClock()
    )


async def store(cache: SemanticCache, prompt: str, text: str) -> None:
    cache.add(await cache.embed(prompt), answer(text))


@pytest.mark.asyncio
async def test_empty_cache_misses():
    cache = make()
    assert cache.lookup(await cache.embed("capital of France?")) is None
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_similar_prompt_hits_with_similarity():
    cache = make()
    await store(cache, "capital of France?", "Paris")
    hit = cache.lookup(await cache.embed("What is the capital of France?"))
    assert hit is not None and hit.answer.text == "Paris"
    assert hit.similarity == pytest.approx(0.95, abs=0.01)


@pytest.mark.asyncio
async def test_below_threshold_misses():
    cache = make(threshold=0.9)
    await store(cache, "capital of France?", "Paris")
    assert cache.lookup(await cache.embed("capital of Spain?")) is None


@pytest.mark.asyncio
async def test_threshold_is_inclusive():
    cache = make(threshold=0.6)
    await store(cache, "capital of France?", "Paris")
    assert cache.lookup(await cache.embed("capital of Spain?")) is not None


@pytest.mark.asyncio
async def test_best_match_wins():
    cache = make(threshold=0.5)
    await store(cache, "capital of Spain?", "Madrid")
    await store(cache, "capital of France?", "Paris")
    await store(cache, "unrelated", "x")
    hit = cache.lookup(await cache.embed("What is the capital of France?"))
    assert hit.answer.text == "Paris"


@pytest.mark.asyncio
async def test_expired_entries_are_skipped():
    clock = FakeClock()
    cache = make(ttl=60, clock=clock)
    await store(cache, "capital of France?", "Paris")
    clock.now += 60
    assert cache.lookup(await cache.embed("capital of France?")) is None
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_full_cache_overwrites_oldest():
    cache = make(max_entries=2, threshold=0.99)
    await store(cache, "capital of France?", "Paris")
    await store(cache, "capital of Spain?", "Madrid")
    await store(cache, "unrelated", "x")  # pushes out France
    assert cache.lookup(await cache.embed("capital of France?")) is None
    assert cache.lookup(await cache.embed("capital of Spain?")).answer.text == "Madrid"
    assert len(cache) == 2


@pytest.mark.asyncio
async def test_zero_vector_never_hits():
    cache = make(threshold=0.1)
    await store(cache, "capital of France?", "Paris")
    vector = await cache.embed("zero")
    assert not np.isnan(vector).any()
    assert cache.lookup(vector) is None


@pytest.mark.asyncio
async def test_prompt_whitespace_is_normalised_before_embedding():
    cache = make()
    vector = await cache.embed("  capital of   France? ")
    assert np.allclose(vector, [1, 0, 0])


def test_unit_vectors_have_length_one():
    rows = unit_vectors(np.array([[3, 4, 0], [0, 0, 0]]))
    assert np.allclose(np.linalg.norm(rows, axis=1), [1, 0])


@pytest.mark.parametrize("threshold, entries, ttl", [(0, 10, 60), (1.5, 10, 60), (0.9, 0, 60), (0.9, 10, 0)])
def test_rejects_bad_settings(threshold, entries, ttl):
    with pytest.raises(ValueError):
        make(threshold=threshold, max_entries=entries, ttl=ttl)


@pytest.mark.asyncio
async def test_expired_slot_is_reused_before_live_entries_are_overwritten():
    clock = FakeClock()
    cache = make(max_entries=2, threshold=0.99, ttl=60, clock=clock)
    await store(cache, "capital of France?", "Paris")
    clock.now += 30
    await store(cache, "capital of Spain?", "Madrid")
    clock.now += 31  # France expired, Spain still live
    await store(cache, "unrelated", "x")
    assert cache.lookup(await cache.embed("capital of Spain?")).answer.text == "Madrid"
    assert len(cache) == 2


class OneAtATimeEmbedder(FakeEmbedder):
    """Fails if two threads are inside embed() at the same time."""

    def __init__(self, table):
        super().__init__(table)
        import threading

        self.inside = threading.Lock()

    def embed(self, texts):
        import time

        if not self.inside.acquire(blocking=False):
            raise RuntimeError("Already borrowed")
        try:
            time.sleep(0.005)
            return super().embed(texts)
        finally:
            self.inside.release()


@pytest.mark.asyncio
async def test_concurrent_embeds_run_one_at_a_time():
    import asyncio

    embedder = OneAtATimeEmbedder(TABLE)
    cache = SemanticCache(embedder, threshold=0.9, max_entries=10, ttl_seconds=60)
    vectors = await asyncio.gather(*(cache.embed("capital of France?") for _ in range(20)))
    assert len(vectors) == 20 and embedder.calls == 20
