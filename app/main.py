"""
LLM Gateway - FastAPI routes.
/chat checks the client's API key and rate limit, then looks in the exact cache, then the
semantic cache, and only on a miss sends the prompt through the router (providers in order,
retries, circuit breakers, fallback). Complete answers are stored in both caches.
"""
import logging
import math
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from app import config
from app.cache.answer import CachedAnswer
from app.cache.exact import ExactCache, MemoryExactCache, RedisExactCache
from app.cache.semantic import SemanticCache, SemanticHit, SentenceTransformerEmbedder
from app.core.api_keys import ApiKeys
from app.core.circuit_breaker import CircuitBreaker
from app.core.rate_limiter import RateLimiter
from app.core.redis_limiters import RedisSlidingWindowLimiter, RedisTokenBucketLimiter
from app.core.sliding_window import SlidingWindowLimiter
from app.core.token_bucket import TokenBucketLimiter
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


def make_redis() -> Redis:
    # No client-side retries: if Redis is down we'd rather fail open fast than make every request wait.
    return Redis.from_url(
        config.REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5, retry=Retry(NoBackoff(), 0)
    )


def build_limiter() -> RateLimiter:
    """Pick the algorithm from config; keep its state in Redis if REDIS_URL is set, else in memory."""
    token_bucket = config.RATE_LIMIT_ALGORITHM == "token_bucket"
    if config.REDIS_URL:
        redis = make_redis()
        if token_bucket:
            return RedisTokenBucketLimiter(redis, config.TOKEN_BUCKET_CAPACITY, config.TOKEN_BUCKET_REFILL_PER_SECOND)
        return RedisSlidingWindowLimiter(redis, config.SLIDING_WINDOW_LIMIT, config.SLIDING_WINDOW_SECONDS)
    if token_bucket:
        return TokenBucketLimiter(config.TOKEN_BUCKET_CAPACITY, config.TOKEN_BUCKET_REFILL_PER_SECOND)
    return SlidingWindowLimiter(config.SLIDING_WINDOW_LIMIT, config.SLIDING_WINDOW_SECONDS)


def build_exact_cache() -> ExactCache | None:
    if not config.EXACT_CACHE_ENABLED:
        return None
    if config.REDIS_URL:
        return RedisExactCache(make_redis(), config.CACHE_TTL_SECONDS)
    return MemoryExactCache(config.CACHE_MAX_ENTRIES, config.CACHE_TTL_SECONDS)


def build_semantic_cache() -> SemanticCache | None:
    if not config.SEMANTIC_CACHE_ENABLED:
        return None
    return SemanticCache(
        SentenceTransformerEmbedder(config.EMBEDDING_MODEL),
        threshold=config.SEMANTIC_CACHE_THRESHOLD,
        max_entries=config.CACHE_MAX_ENTRIES,
        ttl_seconds=config.CACHE_TTL_SECONDS,
    )


# The router that answers /chat (tests swap this for one with fake providers)
router: Router = build_router()
# Valid client keys and the per-client rate limiter (tests swap these too)
api_keys = ApiKeys(config.GATEWAY_API_KEYS)
limiter: RateLimiter = build_limiter()
# Caches (None = turned off in config; tests swap these too)
exact_cache: ExactCache | None = build_exact_cache()
semantic_cache: SemanticCache | None = build_semantic_cache()
if not len(api_keys):
    logger.warning("No GATEWAY_API_KEYS set: every /chat request will be rejected with 401")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if semantic_cache is not None:
        await semantic_cache.embed("warm up")  # load the model now, not during the first request
    yield  # the server runs while we are "paused" here
    await router.aclose()  # on shutdown: close the providers' shared network connections
    await limiter.aclose()
    if exact_cache is not None:
        await exact_cache.aclose()


app = FastAPI(title="LLM Gateway", lifespan=lifespan)


class ChatRequest(BaseModel):
    prompt: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/providers")
def provider_health() -> list[dict]:
    return router.health()


async def rate_limited_client(response: Response, x_api_key: str | None = Header(default=None)) -> str:
    """Runs before /chat: check the API key (401), then the client's rate limit (429)."""
    client_id = api_keys.client_id(x_api_key)
    if client_id is None:
        raise HTTPException(status_code=401, detail="Missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"})

    decision = await limiter.check(client_id)
    if not decision.allowed:
        retry_after = max(1, math.ceil(decision.retry_after))  # header must be whole seconds; never tell a client 0
        logger.info("Rate limited client %s, retry after %ss", client_id, retry_after)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after), "X-RateLimit-Remaining": "0"},
        )
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    return client_id


def cache_hit_response(kind: str, answer: CachedAnswer, start: float, similarity: float | None = None) -> dict:
    latency = (time.perf_counter() - start) * 1000
    request_log.log_cache_hit(kind, answer.provider, answer.model, latency, answer.cost_usd)
    body = {
        "answer": answer.text,
        "provider": answer.provider,  # who originally answered
        "model": answer.model,
        "latency_ms": round(latency, 1),
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},  # nothing new was used
        "cost_usd": 0,
        "cache": kind,
        "saved_usd": None if answer.cost_usd is None else round(answer.cost_usd, COST_DECIMALS),
    }
    if similarity is not None:
        body["similarity"] = round(similarity, 4)
    return body


async def semantic_vector(prompt: str):
    """Embed the prompt for the semantic cache; any embedding failure just skips that cache."""
    if semantic_cache is None:
        return None
    try:
        return await semantic_cache.embed(prompt)
    except Exception:
        logger.exception("Embedding failed; skipping the semantic cache for this request")
        return None


@app.post("/chat")
async def chat(req: ChatRequest, client_id: str = Depends(rate_limited_client)) -> dict:
    start = time.perf_counter()

    if exact_cache is not None:
        cached = await exact_cache.get(req.prompt)
        if cached is not None:
            return cache_hit_response("exact", cached, start)

    vector = await semantic_vector(req.prompt)
    if vector is not None:
        hit: SemanticHit | None = semantic_cache.lookup(vector)
        if hit is not None:
            return cache_hit_response("semantic", hit.answer, start, hit.similarity)

    try:
        result = await router.chat(req.prompt)
    except AllProvidersFailed as e:
        raise HTTPException(status_code=503, detail=str(e))

    latency = (time.perf_counter() - start) * 1000
    cost = estimate_cost(result.model, result.prompt_tokens, result.completion_tokens, config.MODEL_PRICES)

    if result.complete:  # never reuse a cut-off or filtered answer
        answer = CachedAnswer.from_response(result, cost)
        if exact_cache is not None:
            await exact_cache.set(req.prompt, answer)
        if vector is not None:
            semantic_cache.add(vector, answer)

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
        "cache": None,
    }


@app.get("/stats")
def stats() -> dict:
    return request_log.stats()
