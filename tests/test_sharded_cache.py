"""Exact cache split across nodes with a consistent hash ring (app/cache/sharded.py)."""
import pytest

from app.cache.answer import CachedAnswer
from app.cache.exact import MemoryExactCache
from app.cache.sharded import ShardedExactCache

ANSWER = CachedAnswer("4", "fake", "fake-model", 1, 1, 0.0)
PROMPTS = [f"what is {i} + {i}?" for i in range(300)]


def make(names=("a", "b", "c")) -> ShardedExactCache:
    return ShardedExactCache({name: MemoryExactCache(1000, 60) for name in names})


@pytest.mark.asyncio
async def test_set_then_get_through_the_ring():
    cache = make()
    await cache.set("hello", ANSWER)
    assert await cache.get("  hello ") == ANSWER  # same normalised key, same node
    assert await cache.get("other") is None


@pytest.mark.asyncio
async def test_each_entry_lives_on_exactly_one_node():
    cache = make()
    for prompt in PROMPTS:
        await cache.set(prompt, ANSWER)
    sizes = {name: len(node.lru) for name, node in cache.nodes.items()}
    assert sum(sizes.values()) == len(PROMPTS)
    assert all(size > 0 for size in sizes.values())


@pytest.mark.asyncio
async def test_adding_a_node_keeps_most_entries_reachable():
    cache = make()
    for prompt in PROMPTS:
        await cache.set(prompt, ANSWER)
    cache.add_node("d", MemoryExactCache(1000, 60))
    still_hit = [p for p in PROMPTS if await cache.get(p) is not None]
    # Only keys that moved to the new (empty) node miss: ideally 1/4
    assert len(still_hit) / len(PROMPTS) > 0.6


@pytest.mark.asyncio
async def test_removing_a_node_loses_only_its_entries():
    cache = make(("a", "b", "c", "d"))
    for prompt in PROMPTS:
        await cache.set(prompt, ANSWER)
    on_b = {p for p in PROMPTS if cache.node_for(p) == "b"}
    await cache.remove_node("b")
    for prompt in PROMPTS:
        assert (await cache.get(prompt) is None) == (prompt in on_b)


def test_needs_at_least_one_node():
    with pytest.raises(ValueError):
        ShardedExactCache({})
