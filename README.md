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
3. Copy `.env.example` to `.env` and add your Groq API key
4. `uvicorn main:app --reload`
5. Open http://127.0.0.1:8000/docs and try `/chat`

## Roadmap
- [x] Step 1: basic proxy with logging
- [ ] Step 2: second provider, fallback, circuit breaker
- [ ] Step 3: token-bucket rate limiting
- [ ] Step 4: exact cache, then semantic cache with false-hit testing
- [ ] Step 5: React dashboard
- [ ] Step 6: load testing, Docker, deployment
