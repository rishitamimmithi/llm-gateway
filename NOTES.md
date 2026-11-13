# NOTES.md — Design decisions

For each important decision, record what we chose, what else we considered, and why.

| Date | Decision | Alternatives considered | Why |
|---|---|---|---|
| | FastAPI + httpx (async) for the gateway | Flask, Django | Async I/O suits many concurrent LLM calls; built-in docs at /docs |
| | Build LRU cache, token bucket and circuit breaker myself | Use existing libraries | Interview preparation: must understand and explain them |
