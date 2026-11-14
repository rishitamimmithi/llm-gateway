import pytest

from app.core.lru_cache import LRUCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make(capacity: int = 3, ttl: float = 10, clock=None) -> LRUCache:
    return LRUCache(capacity, ttl, clock=clock or FakeClock())


def test_empty_cache_misses():
    cache = make()
    assert cache.get("a") is None
    assert len(cache) == 0
    assert cache.misses == 1 and cache.hits == 0


def test_set_then_get():
    cache = make()
    cache.set("a", 1)
    assert cache.get("a") == 1
    assert cache.hits == 1


def test_evicts_least_recently_used_when_full():
    cache = make(capacity=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert "a" not in cache
    assert cache.get("b") == 2 and cache.get("c") == 3
    assert cache.evictions == 1


def test_get_refreshes_recency():
    cache = make(capacity=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # now b is least recently used
    cache.set("c", 3)
    assert "b" not in cache
    assert cache.get("a") == 1


def test_update_existing_key_does_not_evict():
    cache = make(capacity=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 10)
    assert len(cache) == 2
    assert cache.get("a") == 10 and cache.get("b") == 2
    assert cache.keys_lru_order() == ["b", "a"]


def test_capacity_one():
    cache = make(capacity=1)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.keys_lru_order() == ["b"]
    assert cache.get("a") is None


def test_entry_expires_after_ttl(clock):
    cache = make(ttl=10, clock=clock)
    cache.set("a", 1)
    clock.now += 9.99
    assert cache.get("a") == 1
    clock.now += 0.01  # exactly at the expiry time counts as expired
    assert cache.get("a") is None
    assert len(cache) == 0  # removed lazily on read


def test_get_does_not_extend_ttl(clock):
    cache = make(ttl=10, clock=clock)
    cache.set("a", 1)
    clock.now += 6
    cache.get("a")
    clock.now += 6
    assert cache.get("a") is None


def test_set_again_resets_ttl(clock):
    cache = make(ttl=10, clock=clock)
    cache.set("a", 1)
    clock.now += 8
    cache.set("a", 2)
    clock.now += 8
    assert cache.get("a") == 2


def test_per_entry_ttl(clock):
    cache = make(ttl=10, clock=clock)
    cache.set("short", 1, ttl_seconds=1)
    cache.set("long", 2)
    clock.now += 2
    assert cache.get("short") is None
    assert cache.get("long") == 2


def test_expired_lru_entry_is_not_counted_as_eviction(clock):
    cache = make(capacity=2, ttl=10, clock=clock)
    cache.set("a", 1, ttl_seconds=1)
    cache.set("b", 2)
    clock.now += 5
    cache.set("c", 3)
    assert cache.evictions == 0
    assert cache.keys_lru_order() == ["c", "b"]


def test_contains_does_not_touch_order_or_counters():
    cache = make(capacity=2)
    cache.set("a", 1)
    cache.set("b", 2)
    assert "a" in cache
    assert cache.keys_lru_order() == ["b", "a"]
    assert cache.hits == 0 and cache.misses == 0


def test_delete_and_clear():
    cache = make()
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.delete("a") is True
    assert cache.delete("a") is False
    assert cache.keys_lru_order() == ["b"]
    cache.clear()
    assert len(cache) == 0 and cache.keys_lru_order() == []
    cache.set("c", 3)  # still usable after clear
    assert cache.get("c") == 3


def test_none_like_values_are_stored():
    cache = make()
    cache.set("zero", 0)
    cache.set("empty", "")
    assert cache.get("zero") == 0
    assert cache.get("empty") == ""


@pytest.mark.parametrize("capacity, ttl", [(0, 10), (-1, 10), (3, 0), (3, -5)])
def test_rejects_bad_settings(capacity, ttl):
    with pytest.raises(ValueError):
        LRUCache(capacity, ttl)


def test_rejects_bad_per_entry_ttl():
    with pytest.raises(ValueError):
        make().set("a", 1, ttl_seconds=0)


def test_many_operations_keep_list_and_map_in_sync():
    cache = make(capacity=50)
    for i in range(1000):
        cache.set(i % 70, i)
        cache.get((i * 7) % 70)
    order = cache.keys_lru_order()
    assert len(order) == len(cache) == 50
    assert len(set(order)) == 50


def test_set_on_expired_key_revives_it(clock):
    cache = make(ttl=10, clock=clock)
    cache.set("a", 1)
    clock.now += 20
    cache.set("a", 2)
    assert cache.get("a") == 2
    assert len(cache) == 1
