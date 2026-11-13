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

# --- Groq provider ---
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GROQ_URL: str = "https://api.groq.com/openai/v1/chat/completions"
MODEL: str = os.getenv("MODEL", "openai/gpt-oss-20b")  # check the Groq console for current model names

# --- Gemini provider ---
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
# Stable, free-tier "flash-lite" model; avoid gemini-2.5-flash (deprecated, rerouted from 2026-12-01)
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

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
}

# --- Client API keys (comma-separated). Every /chat call needs one in the X-API-Key header. ---
GATEWAY_API_KEYS: list[str] = [k.strip() for k in os.getenv("GATEWAY_API_KEYS", "").split(",") if k.strip()]

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

# --- Database ---
DB_PATH: str = os.getenv("DB_PATH", "requests.db")
