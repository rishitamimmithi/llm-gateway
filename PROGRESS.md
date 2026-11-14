# PROGRESS.md

Updated at the end of every work session.

## Current phase
Phase 6 — React dashboard (built; shows live data from the stats API). Phase 4 still needs money saved on real traffic in the README.

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
- Phase 5 Step 1: request dedup (`app/core/dedup.py`): identical in-flight prompts share one provider call; errors shared; shared requests logged as `dedup` with `saved_usd`; `/stats` `cache.deduplicated`; `DEDUP_ENABLED`
- Phase 5 Step 2: consistent hash ring (`app/core/consistent_hash.py`, MD5, 100 virtual nodes) + sharded exact cache over several Redis nodes (`app/cache/sharded.py`, `CACHE_NODES`)
- Phase 5 proof: 50 identical requests → 1 provider call (49 shared, same p95); adding a 4th node moves 24.1% of keys vs 74.9% with `hash % N`
- Phase 5 review fixes: wait for shared calls at shutdown, caching errors after a paid answer fail open, exact cache re-checked before calling providers, cache node names without passwords, repeated `CACHE_NODES` dropped
- Phase 6 Step 1: `GET /stats/timeseries?minutes=&bucket=` — per-bucket provider answers, cache hits, dedup, failed attempts, p50/p95 latency, spent and saved; overall p50/p95/p99 (422 for bad ranges or > 2000 buckets)
- Phase 6 Step 2: React dashboard (`dashboard/`, Vite + recharts): 6 stat tiles, requests / latency / cost charts with tooltips, provider circuit table (colour + icon + word), data table, 15 min–24 h ranges, light + dark mode, 5 s refresh; checked against a running gateway with 400 seeded requests
- Phase 6 review fixes: one poll at a time (next scheduled after the last finishes), range change aborts the old request, index on `requests.ts`, oldest bucket no longer cut short, NULL status / latency rows don't crash; latency points visible when traffic is sparse, `bucket` longer than the window rejected (422), "spent" counts only provider answers (same as `/stats`)
- Phase 6 verified: 409 tests pass, dashboard builds, a row added while the gateway runs shows up in the dashboard API and charts

## In progress
- Nothing

## Open bugs / questions
- Gemini free tier is slow and unpredictable (1–22 s for tiny answers, one 30 s timeout)
- Fallback through slow Gemini took 7–24 s per request in the demo (timeouts are retried up to 3 × 10 s)
- `/stats`, `/stats/timeseries` and `/health` need no API key yet (fine locally; lock down before deploying)
- One SQLite connection is shared by the event loop (logging) and threadpool handlers (stats); may need a lock or a separate read connection under Locust load
- Dashboard "Requests" tile can't show client requests that failed on every provider (only per-attempt `error` rows exist); log one `failed` row per 503 before Phase 7
- Dashboard: if one of the three stats calls fails, the whole refresh is skipped and the banner says the gateway is unreachable
- Rate-limited (429) requests are not logged to the database yet
- Ollama provider from the Phase 2 list is not built yet (not needed for the done criteria)
- Failed attempts are logged with 0 tokens / $0, though a provider may bill a request that failed late
- Guard still lets through antonyms ("healthy"/"unhealthy", "most"/"least"), swapped numbers ("12 divided by 4" vs "4 divided by 12") and similar names (Slovakia/Slovenia)
- Guard treats "last" as a number word, so "undo my last commit" vs "revert the most recent commit" is blocked (found on the hold-out set; not fixed yet so the hold-out numbers stay honest)
- `/stats` hit rate counts every old `ok` row as a miss, so use a fresh `requests.db` when measuring
- Caches are shared across client keys (fine while all clients are mine)
- Followers of a failed shared call get 503 but only the leader's attempts are logged, so `/stats` undercounts failed client requests
- Dedup is per gateway instance: two instances can each make one call for the same prompt
- Sharded cache has no live add/remove of nodes from config (only `add_node` / `remove_node` in code)
- `matplotlib` not installed, so `eval/tradeoff.png` isn't drawn yet

## Next steps
1. Measure money saved: replay a realistic prompt mix (with repeats) through the gateway with a fresh `requests.db`, record hit rate and `saved_usd` in the README (Phase 4 done criteria) — the dashboard will show it live
2. Phase 7: Locust load test (req/s, p50/p95/p99, with and without cache), then Dockerfile + docker-compose (gateway + Redis + dashboard build)
3. Add a dashboard screenshot to the README
4. Optional: remove "last" from the guard's number words, then write a fresh hold-out set before re-measuring

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
- `app/core/dedup.py` — in-flight map of asyncio Tasks; leader vs followers; `asyncio.shield`; done callback removes the key; shared errors stop a thundering herd
- `app/core/consistent_hash.py` — hash ring; virtual nodes; binary search with wrap-round; why `hash % N` moves most keys
- `app/cache/sharded.py` — routing cache keys to nodes; moved keys just miss once
- `loadtest/dedup_burst.py` / `eval/ring_moves.py` — proving dedup and key movement with numbers
- `app/db/logger.py` `timeseries` — time buckets; nearest-rank percentile (p50/p95/p99); why averages hide slow requests
- `dashboard/src/App.jsx` — React state + effect; polling with `setInterval` and cleanup; Vite dev proxy instead of CORS
- `dashboard/src/Charts.jsx` — recharts stacked bars and lines; one y-axis per chart; tooltips and legends

## To rebuild myself later
- `app/core/retry.py` — retry with exponential backoff and jitter
- `app/core/circuit_breaker.py` — circuit breaker state machine
- `app/core/token_bucket.py` — token bucket rate limiter
- `app/core/sliding_window.py` — sliding window log rate limiter
- `app/core/lru_cache.py` — LRU cache with TTL
- `app/core/dedup.py` — request deduplication (in-flight map)
- `app/core/consistent_hash.py` — consistent hash ring with virtual nodes

## Improvement ideas
- Log one row per attempt (attempt number, error type) instead of one per request
- Return 504 for timeouts instead of 502
- Compute cost once per request (now priced in both the log callback and `/chat`)
- Cross-instance dedup: Redis `SET NX` lock per prompt + pub/sub to hand the answer to other instances
- Small cross-encoder re-check for semantic hits, to catch antonyms the guard misses
- `/stats/timeseries`: count per bucket in SQL (`GROUP BY`) and pull only `ok` latencies into Python, for 1M+ row tables

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
- 50 identical prompts arrive at once and the provider is down. How many provider calls happen with and without dedup, and why does sharing the error matter?
- Why does each waiter await `asyncio.shield(task)` instead of the task itself?
- Going from 3 to 4 cache nodes, why does `hash % N` move ~75% of keys while a hash ring moves ~25%? What do virtual nodes fix?
- Why show p95 latency instead of the average? Why are cache hits left out of the latency chart?
- Your dashboard polls every 5 s with `setInterval`, and the gateway gets slow. What goes wrong, and how does "schedule the next poll after this one finishes" fix it?
- The stats query filters on `ts` every few seconds. What does an index on `ts` change, and what does it cost on every insert?

## Session log
| Date | What we did |
|---|---|
| 2026-11-14 | Finished Phase 1; Phase 2 Steps 0–4 (app/ layout, Groq + Gemini providers, retries) |
| 2026-11-14 | Phase 2 Steps 5–9: circuit breaker, router with fallback, cost tracking, failover demo; Phase 2 done |
| 2026-11-14 | Phase 3: API keys, token bucket, sliding window, Redis (Lua) versions, 429 + Retry-After |
| 2026-11-14 | Phase 4: LRU + TTL, exact cache (memory / Redis), semantic cache, false-hit eval (123 pairs) |
| 2026-11-14 | Phase 5: request dedup, consistent hash ring, sharded exact cache, burst + key-movement demos |
| 2026-11-14 | Semantic guard (numbers, negation, direction); false hits 22% → 3% at 0.94; 30-pair hold-out set |
| 2026-11-14 | Phase 6: `/stats/timeseries` endpoint + React dashboard (requests, latency p50/p95, cost, breaker states) |
