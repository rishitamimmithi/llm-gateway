"""
LRU cache with a time-to-live (TTL), built from a hash map + a doubly linked list.

- The dict maps key -> node, so finding an entry is O(1).
- The linked list keeps nodes in use order: most recently used next to `head`,
  least recently used next to `tail`. Moving or removing a node is O(1) because
  each node knows its neighbours.
- When the cache is full, the node just before `tail` (least recently used) is evicted.
- Every entry also has an expiry time. Expired entries are removed lazily, when they are read
  (or when they reach the LRU end), so there is no background cleanup thread.

Not thread-safe on its own; in the gateway it is only used from the asyncio event loop,
and none of its methods await, so no two requests can be inside it at the same time.
"""
import time
from collections.abc import Callable
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class _Node(Generic[K, V]):
    __slots__ = ("key", "value", "expires_at", "prev", "next")

    def __init__(self, key: K, value: V, expires_at: float) -> None:
        self.key = key
        self.value = value
        self.expires_at = expires_at
        self.prev: "_Node[K, V] | None" = None
        self.next: "_Node[K, V] | None" = None


class LRUCache(Generic[K, V]):
    def __init__(
        self, capacity: int, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._map: dict[K, _Node[K, V]] = {}
        # Two dummy nodes so we never have to special-case an empty list or the ends.
        self._head: _Node = _Node(None, None, 0.0)  # type: ignore[arg-type]
        self._tail: _Node = _Node(None, None, 0.0)  # type: ignore[arg-type]
        self._head.next = self._tail
        self._tail.prev = self._head
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    # --- linked list helpers (all O(1)) ---
    def _unlink(self, node: _Node[K, V]) -> None:
        node.prev.next = node.next  # type: ignore[union-attr]
        node.next.prev = node.prev  # type: ignore[union-attr]
        node.prev = node.next = None

    def _push_front(self, node: _Node[K, V]) -> None:
        node.prev = self._head
        node.next = self._head.next
        self._head.next.prev = node  # type: ignore[union-attr]
        self._head.next = node

    def _remove(self, node: _Node[K, V]) -> None:
        self._unlink(node)
        del self._map[node.key]

    # --- public API ---
    def get(self, key: K) -> V | None:
        """Return the value, or None if missing or expired. A hit makes the entry most recently used."""
        node = self._map.get(key)
        if node is None:
            self.misses += 1
            return None
        if self._clock() >= node.expires_at:
            self._remove(node)
            self.misses += 1
            return None
        self._unlink(node)
        self._push_front(node)
        self.hits += 1
        return node.value

    def set(self, key: K, value: V, ttl_seconds: float | None = None) -> None:
        """Insert or replace. Replacing also resets the TTL and makes the entry most recently used."""
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        expires_at = self._clock() + ttl
        node = self._map.get(key)
        if node is not None:
            node.value = value
            node.expires_at = expires_at
            self._unlink(node)
            self._push_front(node)
            return
        if len(self._map) >= self.capacity:
            self._evict_one()
        node = _Node(key, value, expires_at)
        self._map[key] = node
        self._push_front(node)

    def delete(self, key: K) -> bool:
        node = self._map.get(key)
        if node is None:
            return False
        self._remove(node)
        return True

    def clear(self) -> None:
        self._map.clear()
        self._head.next = self._tail
        self._tail.prev = self._head

    def _evict_one(self) -> None:
        """Make room: drop an expired entry from the old end if there is one, else the least recently used."""
        lru = self._tail.prev
        assert lru is not self._head  # only called when the cache is full, so never empty
        if self._clock() < lru.expires_at:  # type: ignore[union-attr]
            self.evictions += 1  # a live entry is pushed out; expired ones don't count as evictions
        self._remove(lru)  # type: ignore[arg-type]

    def __len__(self) -> int:
        """Entries stored, including expired ones not yet cleaned up."""
        return len(self._map)

    def __contains__(self, key: object) -> bool:
        """True if present and not expired. Does not change use order or hit/miss counters."""
        node = self._map.get(key)  # type: ignore[call-overload]
        return node is not None and self._clock() < node.expires_at

    def keys_lru_order(self) -> list[K]:
        """Keys from most to least recently used (for tests and debugging)."""
        keys = []
        node = self._head.next
        while node is not self._tail:
            keys.append(node.key)  # type: ignore[union-attr]
            node = node.next  # type: ignore[union-attr]
        return keys
