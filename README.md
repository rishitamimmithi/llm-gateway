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
- [ ] Step 4: exact cache, then semantic cache with false-hit testing
- [ ] Step 5: React dashboard
- [ ] Step 6: load testing, Docker, deployment
