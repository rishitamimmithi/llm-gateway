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
- [~] Step 4: exact cache (done), semantic cache + false-hit test set (done); money saved on real traffic still to measure
- [ ] Step 5: React dashboard
- [ ] Step 6: load testing, Docker, deployment

## Caching
- **Exact cache**: same prompt (extra spaces ignored) gets the stored answer. Own LRU + TTL cache in memory, or Redis when `REDIS_URL` is set. Responses say `"cache": "exact"` and show `saved_usd`.
- **Semantic cache** (optional, `SEMANTIC_CACHE_ENABLED=true`, needs `pip install -r requirements-semantic.txt`): reuses an answer when a new prompt's embedding (`all-MiniLM-L6-v2`) is close enough to a stored one.
- Cut-off answers (hit the token limit) are never cached. `/stats` shows hits, misses, hit rate and money saved.

### False-hit measurement
123 hand-written question pairs (`eval/pairs.jsonl`): 50 with the same meaning, 73 that look alike but ask something different
(swapped direction, changed number, other entity, negation, ...). Run `python -m eval.false_hit_eval`; full table in `eval/results.md`.

| threshold | hit rate (same meaning) | false-hit rate (look-alike) |
|---|---|---|
| 0.85 | 90% | 51% |
| 0.90 | 78% | 33% |
| 0.94 | 60% | 22% |
| 0.98 | 30% | 12% |

Embedding similarity alone can't tell "convert 5 miles to km" from "convert 5 km to miles" (similarity 0.99), so the
look-alike set is a stress test: real traffic has far fewer near-misses, but these are exactly the wrong answers a cache must not give.
