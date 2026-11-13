# NOTES.md — Design decisions

For each important decision, record what we chose, what else we considered, and why.

| Date | Decision | Alternatives considered | Why |
|---|---|---|---|
| | FastAPI + httpx (async) for the gateway | Flask, Django | Async I/O suits many concurrent LLM calls; built-in docs at /docs |
| | Build LRU cache, token bucket and circuit breaker myself | Use existing libraries | Interview preparation: must understand and explain them |
| 2026-11-14 | Cost = tokens × list price per 1M tokens, separate input/output prices, table in `config.MODEL_PRICES` | Use the provider's billed amount; one flat price per model | Providers don't return a cost; output costs more than input; free-tier calls still show what paid traffic would cost |
| 2026-11-14 | Unknown model price is stored as NULL and counted in `unpriced_requests` | Store 0 | 0 would make an unpriced model look free and hide a missing price |
