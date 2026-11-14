"""
Exact cache: the same prompt (ignoring extra spaces) gets the stored answer back.

Two stores behind one interface:
- MemoryExactCache: my own LRU + TTL cache, private to this gateway process.
- RedisExactCache: shared by every gateway instance; Redis handles expiry (SET ... EX).
  If Redis is down, the cache just misses (fail open) - the request still gets answered.

The key is a SHA-256 of the normalised prompt, so long prompts make short keys and
prompts are never stored as Redis key names.
"""
import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.cache.answer import CachedAnswer
from app.core.lru_cache import LRUCache

logger = logging.getLogger("gateway.cache")

KEY_PREFIX = "llmgw:exact:v1:"  # bump v1 if the stored format changes, so old entries are ignored


def normalise_prompt(prompt: str) -> str:
    """Trim and collapse whitespace. Case is kept: "US" and "us" can mean different things."""
    return " ".join(prompt.split())


def cache_key(prompt: str) -> str:
    return KEY_PREFIX + hashlib.sha256(normalise_prompt(prompt).encode("utf-8")).hexdigest()


class ExactCache(Protocol):
    async def get(self, prompt: str) -> CachedAnswer | None: ...
    async def set(self, prompt: str, answer: CachedAnswer) -> None: ...
    async def aclose(self) -> None: ...


class MemoryExactCache:
    def __init__(self, max_entries: int, ttl_seconds: float, **lru_kwargs) -> None:
        self.lru: LRUCache[str, CachedAnswer] = LRUCache(max_entries, ttl_seconds, **lru_kwargs)

    async def get(self, prompt: str) -> CachedAnswer | None:
        return self.lru.get(cache_key(prompt))

    async def set(self, prompt: str, answer: CachedAnswer) -> None:
        self.lru.set(cache_key(prompt), answer)

    async def aclose(self) -> None:
        self.lru.clear()


class RedisExactCache:
    def __init__(
        self,
        redis: Redis,
        ttl_seconds: float,
        *,
        down_cooldown_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.redis = redis
        self.ttl_ms = max(1, round(ttl_seconds * 1000))
        # After a Redis error, skip Redis for a few seconds so every request doesn't
        # wait on a hung server's timeout (twice: once to read, once to write).
        self.down_cooldown_seconds = down_cooldown_seconds
        self._monotonic = monotonic
        self._skip_redis_until = 0.0

    def _redis_down(self, action: str, error: Exception) -> None:
        logger.warning(
            "Exact cache %s failed (%s), skipping Redis for %.0f s",
            action, type(error).__name__, self.down_cooldown_seconds,
        )
        self._skip_redis_until = self._monotonic() + self.down_cooldown_seconds

    def _skipping(self) -> bool:
        return self._monotonic() < self._skip_redis_until

    async def get(self, prompt: str) -> CachedAnswer | None:
        if self._skipping():
            return None
        try:
            raw = await self.redis.get(cache_key(prompt))
        except RedisError as e:
            self._redis_down("read", e)
            return None
        if raw is None:
            return None
        try:
            return CachedAnswer.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            logger.warning("Ignoring a broken exact cache entry")
            return None

    async def set(self, prompt: str, answer: CachedAnswer) -> None:
        if self._skipping():
            return
        try:
            await self.redis.set(cache_key(prompt), json.dumps(answer.to_dict()), px=self.ttl_ms)
        except RedisError as e:
            self._redis_down("write", e)

    async def aclose(self) -> None:
        await self.redis.aclose()
