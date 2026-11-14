"""
All gateway settings live here, so nothing is hard-coded inside the logic.
Values come from the .env file (or real environment variables), with safe defaults.
"""
import math
import os

from dotenv import load_dotenv

load_dotenv()  # reads .env into environment variables


def read_number(name: str, default: str, *, cast: type = float, allow_zero: bool = False) -> float:
    """Read a number setting and refuse silly values at startup, not mid-request.

    e.g. a timeout of 0 would fail every call, and "nan" as a delay could make a request hang forever.
    """
    raw = os.getenv(name, default)
    try:
        value = cast(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None
    in_range = value >= 0 if allow_zero else value > 0
    if not (math.isfinite(value) and in_range):
        kind = "zero or positive" if allow_zero else "positive"
        raise ValueError(f"{name} must be a {kind} number, got {raw!r}")
    return value


def read_bool(name: str, default: str) -> bool:
    raw = os.getenv(name, default).strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}")

# --- Groq provider ---
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GROQ_URL: str = "https://api.groq.com/openai/v1/chat/completions"
MODEL: str = os.getenv("MODEL", "openai/gpt-oss-20b")  # check the Groq console for current model names

# --- Gemini provider ---
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
# Stable, free-tier "flash-lite" model; avoid gemini-2.5-flash (deprecated, rerouted from 2026-12-01)
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# --- Fake provider (load tests only: PROVIDER_ORDER=fake) ---
FAKE_MODEL: str = os.getenv("FAKE_MODEL", "fake-llm")
FAKE_DELAY_SECONDS: float = read_number("FAKE_DELAY_SECONDS", "0.3", allow_zero=True)  # pretend model time

# --- Routing: providers tried in this order (first = main, the rest = backups) ---
PROVIDER_ORDER: list[str] = [p.strip() for p in os.getenv("PROVIDER_ORDER", "groq,gemini").split(",") if p.strip()]

# --- Timeouts: total time allowed for ONE attempt, per provider ---
# Gemini's free tier was measured at 1-22 s for tiny answers, so 10 s stops a slow call holding a client.
GROQ_TIMEOUT_SECONDS: float = read_number("GROQ_TIMEOUT_SECONDS", "10")
GEMINI_TIMEOUT_SECONDS: float = read_number("GEMINI_TIMEOUT_SECONDS", "10")

# --- Retries (exponential backoff with jitter) ---
MAX_ATTEMPTS: int = read_number("MAX_ATTEMPTS", "3", cast=int)  # 1 first try + up to 2 retries
# waits 0.5, 1, 2, ... (0 is allowed: no waiting, handy in tests)
RETRY_BASE_DELAY_SECONDS: float = read_number("RETRY_BASE_DELAY_SECONDS", "0.5", allow_zero=True)
RETRY_MAX_DELAY_SECONDS: float = read_number("RETRY_MAX_DELAY_SECONDS", "4")  # never wait longer than this

# --- Circuit breaker (one per provider) ---
BREAKER_FAILURE_THRESHOLD: int = read_number("BREAKER_FAILURE_THRESHOLD", "3", cast=int)  # failed requests in a row
BREAKER_COOLDOWN_SECONDS: float = read_number("BREAKER_COOLDOWN_SECONDS", "30")  # skip time before one trial call

# --- Prices: USD per 1 million tokens (input, output), keyed by model name ---
# These are the providers' list prices (free-tier calls cost us nothing, but this shows
# what the traffic would cost on a paid plan). Checked 2026-11-14 against:
# https://console.groq.com/docs/models  and  https://aistudio.google.com/docs/pricing (standard, text)
# Models with no published price are left out on purpose, so they show up as "unpriced".
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "gemini-3.1-flash-lite": (0.25, 1.50),  # output price includes thinking tokens
    # Load-test fake priced like gpt-oss-20b, so load tests show realistic spent / saved numbers
    "fake-llm": (0.075, 0.30),
}

# --- Client API keys (comma-separated). Every /chat call needs one in the X-API-Key header. ---
GATEWAY_API_KEYS: list[str] = [k.strip() for k in os.getenv("GATEWAY_API_KEYS", "").split(",") if k.strip()]

# --- Public deploy (e.g. Render): safer defaults for a gateway anyone on the internet can reach ---
DEPLOY_PUBLIC: bool = read_bool("DEPLOY_PUBLIC", "false")
if DEPLOY_PUBLIC and not GATEWAY_API_KEYS:
    # Every /chat call would get 401 anyway, but a public deploy with no keys is surely a mistake: fail loudly.
    raise ValueError("DEPLOY_PUBLIC=true needs GATEWAY_API_KEYS (comma-separated client keys)")

# --- Rate limiting (per client API key) ---
RATE_LIMIT_ALGORITHM: str = os.getenv("RATE_LIMIT_ALGORITHM", "token_bucket").strip().lower()
if RATE_LIMIT_ALGORITHM not in ("token_bucket", "sliding_window"):
    raise ValueError(f"RATE_LIMIT_ALGORITHM must be token_bucket or sliding_window, got {RATE_LIMIT_ALGORITHM!r}")
# Token bucket: burst of up to CAPACITY requests, then REFILL_PER_SECOND on average (0.2/s = 12 per minute)
TOKEN_BUCKET_CAPACITY: int = read_number("TOKEN_BUCKET_CAPACITY", "10", cast=int)
TOKEN_BUCKET_REFILL_PER_SECOND: float = read_number("TOKEN_BUCKET_REFILL_PER_SECOND", "0.2")
# Sliding window: at most LIMIT requests in any WINDOW_SECONDS stretch
SLIDING_WINDOW_LIMIT: int = read_number("SLIDING_WINDOW_LIMIT", "12", cast=int)
SLIDING_WINDOW_SECONDS: float = read_number("SLIDING_WINDOW_SECONDS", "60")
# Empty = limits kept in this process's memory. Set (e.g. redis://localhost:6379/0) to share limits across instances.
REDIS_URL: str = os.getenv("REDIS_URL", "").strip()

# --- Requests ---
MAX_PROMPT_CHARS: int = read_number("MAX_PROMPT_CHARS", "32000", cast=int)  # longer prompts get HTTP 422
# How much of each prompt the request log keeps for the dashboard's "recent requests" table.
# Prompts up to this length are stored WHOLE, and /stats/recent needs no API key yet, so set 0
# (store nothing) anywhere other people's prompts pass through or the stats endpoints are reachable.
# Public deploys store nothing unless PROMPT_PREVIEW_CHARS is set on purpose.
def default_prompt_preview_chars(public: bool) -> str:
    return "0" if public else "60"


PROMPT_PREVIEW_CHARS: int = read_number(
    "PROMPT_PREVIEW_CHARS", default_prompt_preview_chars(DEPLOY_PUBLIC), cast=int, allow_zero=True
)

# Demo data: on startup, if the request log is empty, replay DEMO_SEED_REQUESTS prompts from the prompt mix
# through the fake provider (no network, no cost), spread over the last hour, so a fresh deploy's dashboard
# isn't blank. The dashboard shows a "Demo data" badge while this is on.
DEMO_SEED: bool = read_bool("DEMO_SEED", "false")
DEMO_SEED_REQUESTS: int = read_number("DEMO_SEED_REQUESTS", "150", cast=int)
DEMO_PROMPTS_FILE: str = os.getenv("DEMO_PROMPTS_FILE", "loadtest/prompt_mix.json")

# Built dashboard (npm run build) served at "/" when the folder exists; the Docker image puts it here
DASHBOARD_DIR: str = os.getenv("DASHBOARD_DIR", "dashboard/dist")

# --- Caching (Redis is used for the exact cache too when REDIS_URL is set) ---
EXACT_CACHE_ENABLED: bool = read_bool("EXACT_CACHE_ENABLED", "true")
CACHE_TTL_SECONDS: float = read_number("CACHE_TTL_SECONDS", "3600")  # how long an answer may be reused
CACHE_MAX_ENTRIES: int = read_number("CACHE_MAX_ENTRIES", "1000", cast=int)  # per cache, in memory
# Several cache nodes (comma-separated Redis URLs): the exact cache is split across them with a
# consistent hash ring. Empty = one cache (Redis at REDIS_URL, or in memory).
# Repeats are dropped (the same Redis listed twice is still one node).
CACHE_NODES: list[str] = list(dict.fromkeys(url.strip() for url in os.getenv("CACHE_NODES", "").split(",") if url.strip()))
CACHE_VIRTUAL_NODES: int = read_number("CACHE_VIRTUAL_NODES", "100", cast=int)  # ring positions per node
# Identical requests in flight at the same time share one provider call
DEDUP_ENABLED: bool = read_bool("DEDUP_ENABLED", "true")
# Semantic cache is off by default: it needs sentence-transformers (pip install -r requirements-semantic.txt)
SEMANTIC_CACHE_ENABLED: bool = read_bool("SEMANTIC_CACHE_ENABLED", "false")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
# Cosine similarity needed to reuse an answer. Higher = fewer wrong answers, fewer hits.
# 0.94 from eval/results.md: ~60% of paraphrases hit, ~22% of tricky look-alike questions wrongly hit.
SEMANTIC_CACHE_THRESHOLD: float = read_number("SEMANTIC_CACHE_THRESHOLD", "0.94")
# Extra word checks before a semantic hit (same numbers, negation, to/from direction). See app/cache/guard.py.
SEMANTIC_GUARD_ENABLED: bool = read_bool("SEMANTIC_GUARD_ENABLED", "true")
# Most prompts embedded in one model call (prompts that queue up while a call runs go in the next one)
SEMANTIC_MAX_BATCH: int = read_number("SEMANTIC_MAX_BATCH", "32", cast=int)
if SEMANTIC_CACHE_THRESHOLD > 1:
    raise ValueError(f"SEMANTIC_CACHE_THRESHOLD must be at most 1, got {SEMANTIC_CACHE_THRESHOLD}")

# --- Database ---
DB_PATH: str = os.getenv("DB_PATH", "requests.db")
