"""
Burst test for request deduplication: fire N identical requests at the same moment and count
how many reached a provider.

Two modes:
- default: runs the gateway in this process with a fake provider that takes --delay seconds,
  so no API keys or money are needed. Compares dedup on vs off.
- --url http://localhost:8000 --key <client key>: hits a running gateway (real provider calls!).
  Each run uses a fresh random prompt so the cache can't answer it.

Run: python -m loadtest.dedup_burst
"""
import argparse
import asyncio
import statistics
import time
import uuid

import httpx


async def fire(client: httpx.AsyncClient, prompt: str, n: int) -> tuple[list[dict], float]:
    start = time.perf_counter()
    responses = await asyncio.gather(*(client.post("/chat", json={"prompt": prompt}) for _ in range(n)))
    elapsed = time.perf_counter() - start
    failed = [r.status_code for r in responses if r.status_code != 200]
    if failed:
        raise SystemExit(f"{len(failed)} requests failed: {failed[:5]}")
    return [r.json() for r in responses], elapsed


def report(label: str, bodies: list[dict], elapsed: float, provider_calls: int | None = None) -> None:
    shared = sum(bool(b.get("deduplicated")) for b in bodies)
    calls = provider_calls if provider_calls is not None else len(bodies) - shared
    latencies = sorted(b["latency_ms"] for b in bodies)
    p95 = latencies[int(0.95 * (len(latencies) - 1))]
    saved = sum(b.get("saved_usd") or 0 for b in bodies)
    print(
        f"| {label} | {len(bodies)} | {calls} | {shared} | {statistics.median(latencies):.0f} | {p95:.0f} "
        f"| {elapsed:.2f} | {saved:.6f} |"
    )


async def in_process(n: int, delay: float) -> None:
    import app.main as main
    from app.core.api_keys import ApiKeys
    from app.core.circuit_breaker import CircuitBreaker
    from app.core.dedup import InFlight
    from app.core.router import Route, Router
    from app.core.token_bucket import TokenBucketLimiter
    from app.db.logger import RequestLog
    from app.providers.base import Provider, ProviderResponse

    class SlowFakeProvider(Provider):
        name, model = "fake", "fake-model"

        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, prompt: str) -> ProviderResponse:
            self.calls += 1
            await asyncio.sleep(delay)
            return ProviderResponse("an answer", self.name, self.model, prompt_tokens=20, completion_tokens=200)

    main.request_log = RequestLog(":memory:")
    main.api_keys = ApiKeys(["burst-key"])
    main.limiter = TokenBucketLimiter(capacity=n * 10, refill_per_second=1)
    main.exact_cache = None
    main.semantic_cache = None
    main.config.MODEL_PRICES = {"fake-model": (0.05, 0.08)}  # made-up small-model prices per 1M tokens

    for label, dedup in (("dedup on", True), ("dedup off", False)):
        provider = SlowFakeProvider()
        main.router = Router(
            [Route(provider, CircuitBreaker("fake", failure_threshold=5, cooldown_seconds=30))],
            max_attempts=1, base_delay=0, max_delay=0, on_result=main.record_result,
        )
        main.inflight = InFlight() if dedup else None
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw", headers={"X-API-Key": "burst-key"}) as c:
            bodies, elapsed = await fire(c, f"burst {uuid.uuid4()}", n)
        report(label, bodies, elapsed, provider.calls)


async def remote(url: str, key: str, n: int) -> None:
    async with httpx.AsyncClient(base_url=url, headers={"X-API-Key": key}, timeout=120) as client:
        bodies, elapsed = await fire(client, f"Reply with one word: hello. (run {uuid.uuid4()})", n)
    report("running gateway", bodies, elapsed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=50, help="identical requests in the burst")
    parser.add_argument("--delay", type=float, default=0.5, help="fake provider time in seconds (in-process mode)")
    parser.add_argument("--url", help="running gateway, e.g. http://localhost:8000")
    parser.add_argument("--key", help="client API key for --url")
    args = parser.parse_args()

    print("| run | requests | provider calls | shared | p50 ms | p95 ms | wall s | saved USD |")
    print("|---|---|---|---|---|---|---|---|")
    if args.url:
        if not args.key:
            raise SystemExit("--key is needed with --url")
        asyncio.run(remote(args.url, args.key, args.n))
    else:
        asyncio.run(in_process(args.n, args.delay))


if __name__ == "__main__":
    main()
