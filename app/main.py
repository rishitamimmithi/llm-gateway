"""
LLM Gateway - FastAPI routes.
Forwards a prompt to an LLM provider (Groq for now) and logs tokens, latency and status.
Phase 2 adds more providers, retries, a circuit breaker and fallback.
"""
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app import config
from app.core.retry import retry_async
from app.db.logger import RequestLog
from app.providers.base import Provider, ProviderError
from app.providers.groq import GroqProvider

# One shared request log for the whole app (tests swap this for a temporary one)
request_log = RequestLog(config.DB_PATH)

# The provider that answers /chat (tests swap this for a fake one; Step 6 replaces it with a router)
provider: Provider = GroqProvider(
    config.GROQ_API_KEY, config.MODEL, url=config.GROQ_URL, timeout=config.GROQ_TIMEOUT_SECONDS
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # the server runs while we are "paused" here
    await provider.aclose()  # on shutdown: close the provider's shared network connections


app = FastAPI(title="LLM Gateway", lifespan=lifespan)


class ChatRequest(BaseModel):
    prompt: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    current = provider  # read the shared provider once, so every attempt and the log use the same one
    start = time.perf_counter()
    try:
        # Temporary failures (timeouts, 5xx, 429) are retried with growing waits; others fail at once.
        # The lambda is a "recipe" for the call, so each attempt makes a fresh call.
        result = await retry_async(
            lambda: current.chat(req.prompt),
            max_attempts=config.MAX_ATTEMPTS,
            base_delay=config.RETRY_BASE_DELAY_SECONDS,
            max_delay=config.RETRY_MAX_DELAY_SECONDS,
        )
    except ProviderError as e:
        latency = (time.perf_counter() - start) * 1000
        request_log.log(current.name, current.model, 0, 0, latency, "error")
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    latency = (time.perf_counter() - start) * 1000
    request_log.log(
        result.provider, result.model, result.prompt_tokens, result.completion_tokens, latency, "ok"
    )

    return {
        "answer": result.text,
        "latency_ms": round(latency, 1),
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
    }


@app.get("/stats")
def stats() -> dict:
    return request_log.stats()
