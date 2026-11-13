"""
Redis-backed token bucket and sliding window, so several gateway instances share one limit.

Each check is ONE Lua script. Redis runs a script start to finish with nothing in between,
so "read the count, decide, write it back" can't race with another gateway instance.

Time comes from the Redis server (TIME), not each gateway's clock, so instances whose clocks
drift a little still agree. Tests can pass their own clock instead.

If Redis is unreachable we let the request through (fail open) and log a warning:
a broken limiter store shouldn't take the whole gateway down.
"""
import logging
import time
import uuid
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.rate_limiter import Decision

logger = logging.getLogger("gateway.ratelimit")

# Shared start of both scripts: use the caller's time (ARGV[3]) if given, else the Redis server clock.
_NOW = """
local now = tonumber(ARGV[3])
if not now then
  local t = redis.call('TIME')
  now = tonumber(t[1]) + tonumber(t[2]) / 1000000
end
"""

# KEYS[1] = hash {tokens, ts}; ARGV = capacity, refill_per_second, now (or "")
TOKEN_BUCKET_LUA = _NOW + """
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil or ts == nil then
  tokens = capacity
  ts = now
end
tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
else
  retry = (1 - tokens) / rate
end
redis.call('HSET', KEYS[1], 'tokens', string.format('%.17g', tokens), 'ts', string.format('%.17g', now))
-- forget idle buckets once they would be full again anyway (capped at 1 day so the number stays sane)
redis.call('PEXPIRE', KEYS[1], math.ceil(math.min(capacity / rate, 86400) * 1000) + 1000)
-- Redis turns Lua numbers into integers, so the fraction goes back as a string
return {allowed, math.floor(tokens), string.format('%.17g', retry)}
"""

# KEYS[1] = sorted set of request times; ARGV = limit, window_seconds, now (or ""), unique member id
SLIDING_WINDOW_LUA = _NOW + """
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', string.format('%.17g', now - window))
local count = redis.call('ZCARD', KEYS[1])
if count < limit then
  redis.call('ZADD', KEYS[1], string.format('%.17g', now), ARGV[4])
  redis.call('PEXPIRE', KEYS[1], math.ceil(window * 1000) + 1000)
  return {1, limit - count - 1, '0'}
end
-- a slot opens when the window holds limit-1 requests, i.e. when entry (count - limit) leaves
-- (that is entry 0 normally; later if the limit was lowered while the set was fuller)
local idx = count - limit
local oldest = redis.call('ZRANGE', KEYS[1], idx, idx, 'WITHSCORES')
return {0, 0, string.format('%.17g', tonumber(oldest[2]) + window - now)}
"""


class _RedisLimiter:
    """Runs one Lua script per check; subclasses give the script and its arguments."""

    script_source: str
    key_prefix: str
    max_requests: int  # the full allowance, reported as "remaining" while failing open

    def __init__(
        self,
        redis: Redis,
        clock: Callable[[], float] | None = None,
        down_cooldown_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.redis = redis
        self.clock = clock
        self._script = redis.register_script(self.script_source)
        # After a connection failure, skip Redis for a few seconds so every request
        # doesn't sit waiting on a dead server's timeout.
        self.down_cooldown_seconds = down_cooldown_seconds
        self._monotonic = monotonic
        self._skip_redis_until = 0.0

    def _args(self, now: str) -> list:
        raise NotImplementedError

    def _fail_open(self) -> Decision:
        return Decision(True, self.max_requests, 0.0)

    async def check(self, key: str) -> Decision:
        if self._monotonic() < self._skip_redis_until:
            return self._fail_open()
        now = "" if self.clock is None else repr(self.clock())
        try:
            allowed, remaining, retry = await self._script(keys=[self.key_prefix + key], args=self._args(now))
        except (RedisConnectionError, RedisTimeoutError) as e:
            # Only "can't reach Redis" fails open. A script bug (ResponseError) still raises, so it gets noticed.
            logger.warning(
                "Rate limit store unavailable (%s), letting requests through for %.0f s",
                type(e).__name__, self.down_cooldown_seconds,
            )
            self._skip_redis_until = self._monotonic() + self.down_cooldown_seconds
            return self._fail_open()
        return Decision(bool(int(allowed)), int(remaining), max(0.0, float(retry)))

    async def aclose(self) -> None:
        await self.redis.aclose()


class RedisTokenBucketLimiter(_RedisLimiter):
    script_source = TOKEN_BUCKET_LUA
    key_prefix = "ratelimit:tb:"

    def __init__(
        self,
        redis: Redis,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] | None = None,
        **options,
    ) -> None:
        if capacity < 1 or refill_per_second <= 0:
            raise ValueError("capacity must be >= 1 and refill_per_second > 0")
        super().__init__(redis, clock, **options)
        self.capacity = self.max_requests = capacity
        self.refill_per_second = refill_per_second

    def _args(self, now: str) -> list:
        return [self.capacity, repr(self.refill_per_second), now]


class RedisSlidingWindowLimiter(_RedisLimiter):
    script_source = SLIDING_WINDOW_LUA
    key_prefix = "ratelimit:sw:"

    def __init__(
        self,
        redis: Redis,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
        **options,
    ) -> None:
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit must be >= 1 and window_seconds > 0")
        super().__init__(redis, clock, **options)
        self.limit = self.max_requests = limit
        self.window_seconds = window_seconds

    def _args(self, now: str) -> list:
        # a unique member per request, so two requests at the same instant both count
        return [self.limit, repr(self.window_seconds), now, uuid.uuid4().hex]
