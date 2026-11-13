# PROGRESS.md

Updated at the end of every work session.

## Current phase
Phase 3 — Rate limiting (done; 259 tests pass). Next: Phase 4 caching.

## Done
- Phase 1 complete: proxy runs, requests logged, `/stats` matches the database, pushed with `.env` excluded
- Phase 2 Step 0: `.venv` set up, dependencies pinned, `requirements-dev.txt`
- Phase 2 Step 1: code moved into `app/` (config, db, routes) with API tests
- Phase 2 Step 2: provider interface + Groq provider with retryable / non-retryable error labels
- Phase 2 Step 3: Gemini provider (`gemini-3.1-flash-lite`) + shared request error handling
- Phase 2 Step 4: retries with exponential backoff + jitter, per-provider timeouts, config checked at startup
- Phase 2 Step 5: circuit breaker per provider
- Phase 2 Step 6: router with fallback (Groq → Gemini), 503 when all fail, breaker per provider
- Phase 2 Step 7: cost tracking — price table, `cost_usd` per request (column added to old databases), cost in `/chat` and `/stats` (total + per provider + unpriced count)
- Prices checked 2026-11-14 (Groq console models page, Gemini pricing page); llama-3.1-8b-instant removed (no published price)
- Phase 2 Step 9: failover demo with a wrong Groq key: 6/6 requests answered (HTTP 200) by Gemini, Groq breaker went OPEN, a HALF_OPEN trial failed and re-opened it, key never appeared in the server log
- Phase 2 verified: both "done" criteria met (fallback with zero failed requests; tests for every breaker state transition), 204 tests pass
- Phase 3: client API keys (`X-API-Key`, 401), token bucket + sliding window limiters (in memory and Redis/Lua), 429 with `Retry-After` and `X-RateLimit-Remaining`, burst behaviour and comparison in NOTES.md

## In progress
- Nothing

## Open bugs / questions
- Gemini free tier is slow and unpredictable (1–22 s for tiny answers, one 30 s timeout)
- Fallback through slow Gemini took 7–24 s per request in the demo (timeouts are retried up to 3 × 10 s)
- `/stats` and `/health` need no API key yet (fine locally; lock down before deploying)
- Rate-limited (429) requests are not logged to the database yet
- Ollama provider from the Phase 2 list is not built yet (not needed for the done criteria)
- Failed attempts are logged with 0 tokens / $0, though a provider may bill a request that failed late
- MAX_TOKENS (cut-off) answers count as success; revisit before caching in Phase 4

## Next steps
1. Add `GATEWAY_API_KEYS` to `.env`, try `/chat` with and without the header
2. Optional: run a real Redis (Docker / WSL) and set `REDIS_URL` to check two gateway instances share one limit
3. Start Phase 4: own LRU cache with TTL

## To learn later
- `app/config.py` — settings in one place; checking numbers at startup
- `app/db/logger.py` — SQLite request log; in-memory database for tests
- `app/providers/base.py` — provider interface; retryable vs non-retryable errors; shared `send_request`; API key redaction; total deadline with `asyncio.timeout`
- `app/providers/groq.py` — OpenAI-style chat API; shared HTTP client (connection reuse)
- `app/providers/gemini.py` — generateContent API; "200 but no answer" cases; thinking tokens billed as output
- `app/core/retry.py` — exponential backoff with jitter; respecting Retry-After; when to give up
- `tests/` — fake internet with `httpx.MockTransport`; fake sleep for instant retry tests
- `app/core/circuit_breaker.py` — CLOSED / OPEN / HALF_OPEN; one trial at a time; generation number so late results are ignored; which errors count

- `app/core/router.py` — try providers in order; breaker wraps retries; fallback
- `app/core/cost.py` — tokens × price per 1M; input vs output price; unknown ≠ free
- `app/db/logger.py` — adding a column to an existing SQLite table (simple migration); SUM / GROUP BY stats
- `app/core/api_keys.py` — API keys checked by hash; client id from the hash, never the raw key
- `app/core/token_bucket.py` — capacity + refill rate; lazy refill; burst then steady rate
- `app/core/sliding_window.py` — log of timestamps; max N in any T seconds; no edge double burst
- `app/core/redis_limiters.py` — Lua script = atomic check across instances; Redis TIME; key expiry; fail open
- `app/main.py` — FastAPI dependency for auth + limit; 429 + Retry-After

## To rebuild myself later
- `app/core/retry.py` — retry with exponential backoff and jitter
- `app/core/circuit_breaker.py` — circuit breaker state machine
- `app/core/token_bucket.py` — token bucket rate limiter
- `app/core/sliding_window.py` — sliding window log rate limiter

## Improvement ideas
- Log one row per attempt (attempt number, error type) instead of one per request
- Return 504 for timeouts instead of 502
- Compute cost once per request (now priced in both the log callback and `/chat`)

## Interview practice questions
- Token bucket with capacity 10, refill 0.2/s: a client sends 15 requests at once. How many pass, and when can it send the next?
- Why can a fixed window allow 2× the limit, and how does the sliding window log stop that?
- Two gateway instances share a Redis limit. Why use a Lua script instead of GET then SET?
- Walk through what happens to 10 requests when Groq's key is wrong: which ones touch Groq, and why?
- Why does the breaker sit outside the retry loop instead of inside it?
- Why store an unknown price as NULL instead of 0?

## Session log
| Date | What we did |
|---|---|
| 2026-11-14 | Finished Phase 1; Phase 2 Steps 0–4 (app/ layout, Groq + Gemini providers, retries) |
| 2026-11-14 | Phase 2 Steps 5–9: circuit breaker, router with fallback, cost tracking, failover demo; Phase 2 done |
| 2026-11-14 | Phase 3: API keys, token bucket, sliding window, Redis (Lua) versions, 429 + Retry-After |
