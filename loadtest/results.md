# Load test results

Locust (`loadtest/locustfile.py`), headless, 200 users, 60 s per run, each user sending the prompt mix from
`loadtest/prompt_mix.json` with a 0–100 ms pause between requests. Gateway: one uvicorn worker, fake provider
(`PROVIDER_ORDER=fake`, 300 ms per call), in-memory exact cache, rate limits set very high, fresh SQLite database.
Gateway and Locust ran on the same laptop (12 cores, Windows 11), so they share the CPU.

| setup | requests | req/s | p50 ms | p95 ms | p99 ms | errors | cache hit rate |
|---|---|---|---|---|---|---|---|
| no cache, no dedup | 30,990 | 523 | 310 | 340 | 370 | 0% | – |
| exact cache + dedup | 46,345 | 779 | 170 | 390 | 690 | 0% | 99.7% |
| exact + semantic cache + dedup | 22,365 | 378 | 360 | 1,200 | 2,100 | 0% | 99.5% |

How to read this:
- **No cache**: every request waits 300 ms for the fake provider, so 200 users can make at most ~570 req/s.
  523 req/s with p95 340 ms means the gateway adds only ~10–40 ms on top of the provider.
- **Exact cache**: hits skip the provider, so throughput is now limited by CPU (one Python process doing
  JSON, auth, rate limiting and a SQLite insert per request, plus Locust on the same machine).
- **Semantic cache**: every exact-cache miss is embedded on the CPU (`all-MiniLM-L6-v2`), which competes with the
  event loop for the same cores. Slower than exact-only, but still no errors.
- The hit rates are far above real traffic because 200 users keep cycling through the same 5,000-prompt sequence.
  The realistic hit rate and cost cut come from the one-at-a-time replay in the README (71.3% hits, 75.1% cost cut).

## Bottlenecks found and fixed

| problem | before | after |
|---|---|---|
| SQLite commit waited for the disk on every request (~10 ms, blocking the event loop) → `PRAGMA journal_mode=WAL`, `synchronous=NORMAL` | 64 req/s, p50 670 ms (50 users, no cache) | 128 req/s, p50 300 ms |
| Embeddings ran one prompt at a time (17.6 ms each) → micro-batching: prompts that queue during one call are embedded together in the next (~5 ms per prompt) | 137 req/s, p95 5,900 ms (semantic run) | 378 req/s, p95 1,200 ms |

## Run it
```
# terminal 1 (PowerShell); delete the old database first so /stats only counts this run
Remove-Item loadtest.db* -ErrorAction SilentlyContinue
$env:PROVIDER_ORDER="fake"; $env:GATEWAY_API_KEYS="lt-key"; $env:DB_PATH="loadtest.db"
$env:TOKEN_BUCKET_CAPACITY="100000000"; $env:TOKEN_BUCKET_REFILL_PER_SECOND="100000000"
uvicorn app.main:app --port 8000

# terminal 2
$env:LOADTEST_API_KEY="lt-key"
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 200 -r 200 -t 60s --only-summary
```
No cache: also set `EXACT_CACHE_ENABLED=false` and `DEDUP_ENABLED=false`. Semantic: `SEMANTIC_CACHE_ENABLED=true`.
