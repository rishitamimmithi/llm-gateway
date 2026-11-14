# LLM Gateway

A service between apps and LLM providers that will handle semantic caching, rate limiting,
provider fallback and cost tracking.

## Step 1: basic proxy (current)
- `POST /chat` forwards a prompt to Groq and returns the answer
- Every request is logged (tokens, latency, status) in `requests.db`
- `GET /stats` shows request count, average latency and total tokens

## Run it
1. `python -m venv .venv` then activate it
   - Windows: `.venv\Scripts\activate`
   - Mac/Linux: `source .venv/bin/activate`
2. `pip install -r requirements.txt` (for development and tests: `pip install -r requirements-dev.txt`, then run `pytest`)
3. Copy `.env.example` to `.env`, add your Groq (and Gemini) key and at least one client key in `GATEWAY_API_KEYS`
4. `uvicorn app.main:app --reload`
5. Call `/chat` with your client key in the `X-API-Key` header, e.g. `curl -X POST localhost:8000/chat -H "X-API-Key: <your key>" -H "Content-Type: application/json" -d "{\"prompt\": \"hi\"}"` (no key: 401, over the limit: 429 with `Retry-After`)

## Roadmap
- [x] Step 1: basic proxy with logging
- [x] Step 2: second provider, fallback, circuit breaker
- [x] Step 3: per-key rate limiting (token bucket or sliding window, optional Redis)
- [x] Step 4: exact cache, semantic cache, false-hit test set, money saved on a realistic prompt mix
- [x] Step 5: request deduplication + consistent hashing for cache nodes
- [x] Step 6: React dashboard
- [ ] Step 7: load testing, Docker, deployment

## Caching
- **Exact cache**: same prompt (extra spaces ignored) gets the stored answer. Own LRU + TTL cache in memory, or Redis when `REDIS_URL` is set. Responses say `"cache": "exact"` and show `saved_usd`.
- **Semantic cache** (optional, `SEMANTIC_CACHE_ENABLED=true`, needs `pip install -r requirements-semantic.txt`): reuses an answer when a new prompt's embedding (`all-MiniLM-L6-v2`) is close enough to a stored one.
- **Guard** (`app/cache/guard.py`, on by default): before a semantic hit, both prompts must have the same numbers, the same negation, and the same word after "to / into / from / than"; otherwise it's a miss.
- Cut-off answers (hit the token limit) are never cached. `/stats` shows hits, misses, hit rate and money saved.

### False-hit measurement
123 hand-written question pairs (`eval/pairs.jsonl`): 50 with the same meaning, 73 that look alike but ask something different
(swapped direction, changed number, other entity, negation, ...). Run `python -m eval.false_hit_eval`; full table in `eval/results.md`.
"Before" = similarity threshold only, "after" = threshold + guard.

| threshold | hit rate before | false-hit rate before | hit rate after | false-hit rate after |
|---|---|---|---|---|
| 0.85 | 90% | 51% | 90% | 16% |
| 0.90 | 78% | 33% | 78% | 7% |
| 0.94 | 60% | 22% | 60% | 3% |
| 0.98 | 30% | 12% | 30% | 0% |

The guard rules were written while looking at those 123 pairs, so I also wrote 30 new pairs afterwards
(`eval/holdout_pairs.jsonl`, rules not changed after) to check it isn't just fitted to them (`eval/holdout.md`):

| threshold | hit rate before | false-hit rate before | hit rate after | false-hit rate after |
|---|---|---|---|---|
| 0.90 | 53% | 60% | 53% | 13% |
| 0.94 | 40% | 40% | 40% | 7% |

What still gets through: antonyms ("healthy" vs "unhealthy", "most" vs "least"), swapped numbers
("12 divided by 4" vs "4 divided by 12"), and different names with a similar meaning (Slovakia vs Slovenia).

Embedding similarity alone can't tell "convert 5 miles to km" from "convert 5 km to miles" (similarity 0.99), so the
look-alike set is a stress test: real traffic has far fewer near-misses, but these are exactly the wrong answers a cache must not give.

### Money saved on a realistic prompt mix
300 requests replayed one at a time through the gateway (real Groq `openai/gpt-oss-20b` calls, exact + semantic cache
at 0.94 with the guard, fresh database). The mix (`loadtest/prompt_mix.json`): 30 popular questions asked with a
Zipf-like popularity (the top one ~19% of traffic) in 3 different wordings, 8 look-alikes of popular questions
("Convert 10 INR to USD" next to "Convert 10 USD to INR"), and 30 one-off questions asked once.
Run `python -m loadtest.savings_replay --url http://localhost:8000 --key <client key>`.

| requests | provider calls | exact hits | semantic hits | hit rate | wrong semantic hits | spent USD | saved USD | cost cut |
|---|---|---|---|---|---|---|---|---|
| 300 | 86 | 158 | 56 | 71.3% | 0 | 0.0135 | 0.0408 | 75.1% |

- 104 different prompts were sent but only 86 reached a provider: 18 paraphrases were answered by the semantic cache the first time they appeared.
- All 7 look-alikes that came up went to the provider the first time (no wrong answer). "Wrong semantic hits" only
  counts hits for a question that was never answered before, so it's a lower bound, not a full false-hit check (that's the eval above).
- The mix is synthetic: the hit rate depends on how repetitive the traffic is. A support bot with a few FAQs will look like this;
  a coding assistant with mostly unique prompts will see far fewer hits. Dollar amounts are tiny because the model is cheap; the % is what carries over.

## Request deduplication
If identical prompts (extra spaces ignored) arrive while one is already waiting on a provider, they all share
that one call (`app/core/dedup.py`). Shared responses say `"deduplicated": true`, cost $0 and show `saved_usd`;
`/stats` counts them under `cache.deduplicated`. If the call fails, every waiting request gets the same 503
instead of each retrying. Turn off with `DEDUP_ENABLED=false`.

Burst of 50 identical requests, fake provider taking 0.5 s (`python -m loadtest.dedup_burst`):

| run | requests | provider calls | shared | p50 ms | p95 ms |
|---|---|---|---|---|---|
| dedup on | 50 | 1 | 49 | 542 | 558 |
| dedup off | 50 | 50 | 0 | 527 | 549 |

Same latency, 49 fewer provider calls. Against a running gateway: `python -m loadtest.dedup_burst --url http://localhost:8000 --key <client key>`.

## Dashboard
React (Vite + recharts) app in `dashboard/`, refreshing every 5 s: requests over time (provider answer / cache hit /
shared / failed), provider latency p50 and p95, money spent vs saved, and each provider's circuit breaker state.
Time ranges: 15 min, 1 hour, 6 hours, 24 hours.

1. Start the gateway (`uvicorn app.main:app`, port 8000)
2. `cd dashboard`, `npm install`, `npm run dev`, open http://localhost:5173
   (gateway on another address: `GATEWAY_URL=http://host:port npm run dev`)

Data comes from `GET /stats`, `GET /stats/timeseries?minutes=60&bucket=60` and `GET /health/providers`.

## Consistent hashing (sharded exact cache)
With `CACHE_NODES=redis://host1:6379/0,redis://host2:6379/0` (2+ URLs) the exact cache is split across the Redis
nodes by a consistent hash ring (`app/core/consistent_hash.py`, 100 virtual nodes per node). Keys moved when the
node count changes, 100,000 keys (`python -m eval.ring_moves`):

| change | ideal | consistent hashing | hash % N |
|---|---|---|---|
| 3 -> 4 nodes | 25% | 24.1% | 74.9% |
| 4 -> 5 nodes | 20% | 19.9% | 80.1% |
| 4 -> 3 nodes | 25% | 24.1% | 74.9% |
| 10 -> 11 nodes | 9% | 8.9% | 90.8% |

Virtual nodes even out the load: with 4 nodes the busiest one holds 41% of keys with 1 virtual node each,
28.7% with 100 and 26.0% with 500 (ideal 25%).
