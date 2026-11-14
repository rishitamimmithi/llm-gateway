"""
Demo data for an empty request log, so a fresh deploy's dashboard has something to show.

The free Render instance has no disk: every restart starts with an empty database and blank charts.
With DEMO_SEED=true the gateway replays ~150 prompts from loadtest/prompt_mix.json at startup, spread
over the last hour. Every answer comes from the fake provider (no network, no cost) and repeats go
through an exact-cache check the way /chat does them, so the hit rate, money saved and recent requests
look like real traffic. The rows say provider "fake" and have demo = 1, and the dashboard shows a
"Demo data" badge whenever such rows exist. Their latencies and costs are simulated (the fake model is
priced like a small real one), so they are never the measured results in the README.
"""
import asyncio
import json
import logging
import random
import time
from collections.abc import Callable
from pathlib import Path

from app.cache.exact import cache_key
from app.core.cost import estimate_cost
from app.db.logger import RequestLog
from app.providers.base import Provider

logger = logging.getLogger("gateway.demo")

# Demo prompts are my own, not users', so they get a preview even on a public deploy
PREVIEW_CHARS = 60


def pick_prompts(mix: dict, n: int, rng: random.Random) -> list[str]:
    """Popular questions asked often (Zipf-like weights, random wording), look-alikes rarely, and a
    few one-offs, in a shuffled order. Same idea as loadtest/savings_replay.py."""
    groups = list(mix["popular"])
    weights = [1 / (rank + 1) for rank in range(len(groups))]
    groups += [[p] for p in mix["lookalikes"]]
    weights += [weights[-1]] * len(mix["lookalikes"])
    one_offs = mix["one_offs"][: n // 5]
    picks = []
    for _ in range(n - len(one_offs)):
        wordings = rng.choices(groups, weights)[0]
        picks.append(wordings[0] if len(wordings) == 1 or rng.random() < 0.5 else rng.choice(wordings[1:]))
    for prompt in one_offs:
        picks.insert(rng.randrange(len(picks) + 1), prompt)
    return picks


def preview(prompt: str) -> str:
    text = " ".join(prompt.split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS].rstrip() + "…"


async def seed(
    log: RequestLog,
    prompts: list[str],
    provider: Provider,
    *,
    prices: dict,
    provider_ms: Callable[[], float],
    hit_ms: Callable[[], float],
    now: float | None = None,
    window_seconds: float = 3600,
    rng: random.Random | None = None,
) -> int:
    """Write one log row per prompt, oldest first, spread over the last `window_seconds`.
    Does nothing if the log already has rows (a real database must never get demo rows mixed in).
    Returns how many rows were written."""
    if not prompts or not log.is_empty():
        return 0
    rng = rng or random.Random()
    now = time.time() if now is None else now
    step = window_seconds / len(prompts)
    answered: dict[str, float | None] = {}  # cache key -> what the provider call cost
    for i, prompt in enumerate(prompts):
        ts = now - window_seconds + i * step + rng.uniform(0, step * 0.8)
        key = cache_key(prompt)
        if key in answered:
            log.log_cache_hit(
                "exact", provider.name, provider.model, hit_ms(), answered[key], preview(prompt), ts=ts, demo=True
            )
        else:
            result = await provider.chat(prompt)
            cost = estimate_cost(result.model, result.prompt_tokens, result.completion_tokens, prices)
            log.log(
                result.provider, result.model, result.prompt_tokens, result.completion_tokens,
                provider_ms(), "ok", cost, preview(prompt), ts=ts, demo=True,
            )
            answered[key] = cost
        if i % 10 == 9:
            await asyncio.sleep(0)  # let real requests in while seeding
    return len(prompts)


async def seed_from_file(
    log: RequestLog, path: str, provider: Provider, *, n: int, prices: dict, provider_delay_ms: float
) -> int:
    """Startup entry point: load the prompt mix and seed. Never raises (demo data is a nice-to-have)."""
    try:
        mix = json.loads(Path(path).read_text(encoding="utf-8"))
        rng = random.Random()
        written = await seed(
            log,
            pick_prompts(mix, n, rng),
            provider,
            prices=prices,
            provider_ms=lambda: provider_delay_ms * rng.uniform(0.9, 1.8),  # some calls are slower
            hit_ms=lambda: rng.uniform(0.4, 2.5),
            rng=rng,
        )
        if written:
            logger.info("Demo data: wrote %d rows", written)
        else:
            logger.info("Demo data: the request log isn't empty, so nothing was added")
        return written
    except Exception:
        logger.exception("Demo data seeding failed")
        return 0
