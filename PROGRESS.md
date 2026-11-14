# PROGRESS.md

Updated at the end of every work session.

## Current phase
Phase 4 — Caching (built; 328 tests pass). Left for done: measure money saved on real traffic and put it in the README.

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
- Phase 4 Step 1: own LRU cache with TTL (`app/core/lru_cache.py`), 21 tests
- Phase 4 Step 2: exact cache (memory LRU or Redis, fail open, 5 s skip after a Redis error); providers mark answers `complete` only on a normal stop, others are never cached
- Phase 4 Step 3: `/chat` checks exact → semantic → providers; hits logged as `cache_exact` / `cache_semantic` with `saved_usd`; `/stats` has a `cache` section (hits, misses, hit rate, money saved)
- Phase 4 Step 4: semantic cache (`all-MiniLM-L6-v2`, numpy matrix, cosine threshold 0.94, off by default, one embedding at a time)
- Phase 4 Step 6: semantic guard (`app/cache/guard.py`): numbers, negation, to/from direction must match. False hits at 0.94: 22% → 3% (hold-out: 40% → 7%), hit rate unchanged
- Guard review fixes: linear-time checks, features stored per entry, prompt length cap (422), digits inside words (gpt4), apostrophe-less negation (isnt), "how to" not treated as a direction; eval numbers unchanged
- Phase 4 Step 5: false-hit test set (123 pairs: 50 same, 73 look-alike) + `python -m eval.false_hit_eval`; results in `eval/results.md` and README. At 0.90: 78% hits / 33% false hits; at 0.94: 60% / 22%. Direction swaps ("5 miles to km" vs "5 km to miles") score 0.99+, so a threshold alone can't stop them

## In progress
- Nothing

## Open bugs / questions
- Gemini free tier is slow and unpredictable (1–22 s for tiny answers, one 30 s timeout)
- Fallback through slow Gemini took 7–24 s per request in the demo (timeouts are retried up to 3 × 10 s)
- `/stats` and `/health` need no API key yet (fine locally; lock down before deploying)
- Rate-limited (429) requests are not logged to the database yet
- Ollama provider from the Phase 2 list is not built yet (not needed for the done criteria)
- Failed attempts are logged with 0 tokens / $0, though a provider may bill a request that failed late
- Guard still lets through antonyms ("healthy"/"unhealthy", "most"/"least"), swapped numbers ("12 divided by 4" vs "4 divided by 12") and similar names (Slovakia/Slovenia)
- Guard treats "last" as a number word, so "undo my last commit" vs "revert the most recent commit" is blocked (found on the hold-out set; not fixed yet so the hold-out numbers stay honest)
- `/stats` hit rate counts every old `ok` row as a miss, so use a fresh `requests.db` when measuring
- Caches are shared across client keys (fine while all clients are mine)
- `matplotlib` not installed, so `eval/tradeoff.png` isn't drawn yet

## Next steps
1. Measure money saved: replay a realistic prompt mix (with repeats) through the gateway with a fresh `requests.db`, record hit rate and `saved_usd` in the README (Phase 4 done criteria)
2. Phase 5: request deduplication (in-flight futures map), then consistent hashing
3. Optional: remove "last" from the guard's number words, then write a fresh hold-out set before re-measuring

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
- `app/core/lru_cache.py` — hash map + doubly linked list; dummy head/tail; O(1) get/set/evict; lazy TTL expiry
- `app/cache/exact.py` — cache key = hash of normalised prompt; Redis `SET PX`; fail open + skip Redis after an error
- `app/cache/semantic.py` — embeddings; cosine similarity = dot product of unit vectors; threshold; one matrix multiply per lookup
- `eval/false_hit_eval.py` — hit rate vs false-hit rate vs threshold; why embeddings miss word order and negation
- `app/providers/*` — finish reasons; only cache complete answers
- `app/cache/guard.py` — why embeddings ignore word order; cheap rule checks; next-best match; hold-out set to avoid fitting rules to the test

## To rebuild myself later
- `app/core/retry.py` — retry with exponential backoff and jitter
- `app/core/circuit_breaker.py` — circuit breaker state machine
- `app/core/token_bucket.py` — token bucket rate limiter
- `app/core/sliding_window.py` — sliding window log rate limiter
- `app/core/lru_cache.py` — LRU cache with TTL

## Improvement ideas
- Log one row per attempt (attempt number, error type) instead of one per request
- Return 504 for timeouts instead of 502
- Compute cost once per request (now priced in both the log callback and `/chat`)
- Small cross-encoder re-check for semantic hits, to catch antonyms the guard misses

## Interview practice questions
- Token bucket with capacity 10, refill 0.2/s: a client sends 15 requests at once. How many pass, and when can it send the next?
- Why can a fixed window allow 2× the limit, and how does the sliding window log stop that?
- Two gateway instances share a Redis limit. Why use a Lua script instead of GET then SET?
- Walk through what happens to 10 requests when Groq's key is wrong: which ones touch Groq, and why?
- Why does the breaker sit outside the retry loop instead of inside it?
- Why does an LRU cache need a doubly linked list and not just a dict? What does each one give you?
- Your semantic cache returned the USD→INR answer for "convert 10 INR to USD". Why does that happen, and how would you stop it?
- Why is a cut-off (MAX_TOKENS) answer returned to the client but not cached?
- Why did you test the guard on a hold-out set, and what would it mean if hold-out results were much worse?
- Why store an unknown price as NULL instead of 0?

## Session log
| Date | What we did |
|---|---|
| 2026-11-14 | Finished Phase 1; Phase 2 Steps 0–4 (app/ layout, Groq + Gemini providers, retries) |
| 2026-11-14 | Phase 2 Steps 5–9: circuit breaker, router with fallback, cost tracking, failover demo; Phase 2 done |
| 2026-11-14 | Phase 3: API keys, token bucket, sliding window, Redis (Lua) versions, 429 + Retry-After |
| 2026-11-14 | Phase 4: LRU + TTL, exact cache (memory / Redis), semantic cache, false-hit eval (123 pairs) |
| 2026-11-14 | Semantic guard (numbers, negation, direction); false hits 22% → 3% at 0.94; 30-pair hold-out set |
