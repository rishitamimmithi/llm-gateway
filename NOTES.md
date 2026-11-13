# NOTES.md — Design decisions

For each important decision, record what we chose, what else we considered, and why.

| Date | Decision | Alternatives considered | Why |
|---|---|---|---|
| | FastAPI + httpx (async) for the gateway | Flask, Django | Async I/O suits many concurrent LLM calls; built-in docs at /docs |
| | Build LRU cache, token bucket and circuit breaker myself | Use existing libraries | Interview preparation: must understand and explain them |
| 2026-11-14 | Cost = tokens × list price per 1M tokens, separate input/output prices, table in `config.MODEL_PRICES` | Use the provider's billed amount; one flat price per model | Providers don't return a cost; output costs more than input; free-tier calls still show what paid traffic would cost |
| 2026-11-14 | Unknown model price is stored as NULL and counted in `unpriced_requests` | Store 0 | 0 would make an unpriced model look free and hide a missing price |
| 2026-11-14 | Clients authenticate with `X-API-Key`; only SHA-256 hashes kept, a 16-char hash prefix is the client id | JWT / OAuth; store raw keys | Simple for server-to-server use; a raw key never appears in memory dumps, limits or logs. No keys configured = every `/chat` gets 401 (fail closed) |
| 2026-11-14 | Rate limit runs after auth, before the router | Limit by IP; limit before auth | Bad keys can't use up a real client's limit; limited requests never reach a provider |
| 2026-11-14 | Token bucket (default) vs sliding window log, picked by `RATE_LIMIT_ALGORITHM` | Fixed window counter; sliding window counter (weighted) | **Token bucket:** 2 numbers per client, allows a burst of `capacity` then a steady `refill` rate; good for bursty chat apps. **Sliding window log:** exact "max N in any T seconds", no double burst at a window edge (fixed window allows 2N), but stores one timestamp per request (memory ∝ limit). Fixed window rejected for the edge burst; weighted counter is a later option if limits get large |
| 2026-11-14 | Burst behaviour (defaults): token bucket lets 10 requests through at once, then 1 every 5 s; sliding window lets 12 at once, then nothing until the oldest is 60 s old | — | Both average 12/min; the bucket recovers smoothly, the window recovers in steps |
| 2026-11-14 | Redis state via one Lua script per check; Redis server `TIME` as the clock; keys expire when idle | `WATCH`/`MULTI` transactions; each gateway's own clock | A script runs atomically, so instances can't race (no lost updates); one shared clock avoids drift between instances; `PEXPIRE` stops idle clients filling Redis |
| 2026-11-14 | If Redis can't be reached the limiter fails open (allows, logs a warning), then skips Redis for 5 s; no client-side Redis retries; script errors still raise | Fail closed (reject all); keep retrying Redis on every request | A limiter outage shouldn't become a full gateway outage, and requests shouldn't each wait on a dead Redis. Only connection/timeout errors fail open, so a script bug can't silently switch limiting off. Revisit if abuse matters more than availability |
| 2026-11-14 | `Retry-After` = seconds rounded up, at least 1; `X-RateLimit-Remaining` on every answer | Float seconds; HTTP date | The header must be whole seconds; rounding down could send clients back too early |
