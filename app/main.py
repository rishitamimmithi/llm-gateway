"""
LLM Gateway - FastAPI routes.
/chat sends the prompt through the router: providers in order, retries, circuit breakers, fallback.
"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app import config
from app.core.circuit_breaker import CircuitBreaker
from app.core.cost import estimate_cost
from app.core.router import AllProvidersFailed, Route, Router
from app.db.logger import COST_DECIMALS, RequestLog
from app.providers.base import Provider
from app.providers.gemini import GeminiProvider
from app.providers.groq import GroqProvider

logger = logging.getLogger("gateway")

# One shared request log for the whole app (tests swap this for a temporary one)
request_log = RequestLog(config.DB_PATH)


def record_result(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: float, status: str
) -> None:
    """Called by the router after each provider attempt: price it, then log it."""
    cost = estimate_cost(model, prompt_tokens, completion_tokens, config.MODEL_PRICES)
    request_log.log(provider, model, prompt_tokens, completion_tokens, latency_ms, status, cost)


def _make_provider(name: str) -> tuple[Provider, str | None]:
    """Build a provider by name; also return its API key so we can skip unconfigured ones."""
    if name == "groq":
        return GroqProvider(
            config.GROQ_API_KEY, config.MODEL, url=config.GROQ_URL, timeout=config.GROQ_TIMEOUT_SECONDS
        ), config.GROQ_API_KEY
    if name == "gemini":
        return GeminiProvider(
            config.GEMINI_API_KEY, config.GEMINI_MODEL, timeout=config.GEMINI_TIMEOUT_SECONDS
        ), config.GEMINI_API_KEY
    raise ValueError(f"Unknown provider in PROVIDER_ORDER: {name!r} (use groq, gemini)")


def build_router() -> Router:
    routes = []
    for name in config.PROVIDER_ORDER:
        provider, key = _make_provider(name)
        if not key:
            logger.warning("Skipping provider %s: no API key in .env", name)
            continue
        breaker = CircuitBreaker(
            name, failure_threshold=config.BREAKER_FAILURE_THRESHOLD, cooldown_seconds=config.BREAKER_COOLDOWN_SECONDS
        )
        routes.append(Route(provider, breaker))
    return Router(
        routes,
        max_attempts=config.MAX_ATTEMPTS,
        base_delay=config.RETRY_BASE_DELAY_SECONDS,
        max_delay=config.RETRY_MAX_DELAY_SECONDS,
        on_result=lambda *row: record_result(*row),  # looked up at call time, so tests can swap the log
    )


# The router that answers /chat (tests swap this for one with fake providers)
router: Router = build_router()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # the server runs while we are "paused" here
    await router.aclose()  # on shutdown: close the providers' shared network connections


app = FastAPI(title="LLM Gateway", lifespan=lifespan)


class ChatRequest(BaseModel):
    prompt: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/providers")
def provider_health() -> list[dict]:
    return router.health()


@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    start = time.perf_counter()
    try:
        result = await router.chat(req.prompt)
    except AllProvidersFailed as e:
        raise HTTPException(status_code=503, detail=str(e))

    latency = (time.perf_counter() - start) * 1000
    cost = estimate_cost(result.model, result.prompt_tokens, result.completion_tokens, config.MODEL_PRICES)
    return {
        "answer": result.text,
        "provider": result.provider,
        "model": result.model,
        "latency_ms": round(latency, 1),
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
        "cost_usd": None if cost is None else round(cost, COST_DECIMALS),  # None = model has no price in config
    }


@app.get("/stats")
def stats() -> dict:
    return request_log.stats()
