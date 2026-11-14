"""
Replay a realistic prompt mix through a running gateway and measure how much the caches save.

The mix (loadtest/prompt_mix.json):
- 30 popular questions, each with 3 wordings; picked with a Zipf-like weight (rank 1 is asked most often)
  and a random wording (half the time the first one, otherwise one of the paraphrases)
- 8 look-alikes of popular questions that ask something different ("10 INR to USD" vs "10 USD to INR"),
  picked rarely; a semantic hit on one of these before it was ever answered is a wrong answer
- 30 one-off questions, each asked once (the long tail a cache can't help with)

Requests go one at a time (no dedup effect) with a pause after each provider call so the free tier
isn't hammered. Use a fresh DB_PATH on the gateway so /stats only counts this run.

Run: python -m loadtest.savings_replay --url http://localhost:8000 --key <client key>
"""
import argparse
import json
import random
import time
from collections import Counter
from pathlib import Path

import httpx

MIX_FILE = Path(__file__).with_name("prompt_mix.json")


def build_mix(n: int, seed: int, zipf_s: float) -> list[tuple[str, str]]:
    """Return (group id, prompt) in the order they will be sent."""
    mix = json.loads(MIX_FILE.read_text(encoding="utf-8"))
    rng = random.Random(seed)
    one_offs = [(f"one-{i}", p) for i, p in enumerate(mix["one_offs"])]
    groups = [(f"pop-{i}", wordings) for i, wordings in enumerate(mix["popular"])]
    weights = [1 / (rank + 1) ** zipf_s for rank in range(len(groups))]
    # look-alikes share the weight of a rarely asked question
    groups += [(f"look-{i}", [p]) for i, p in enumerate(mix["lookalikes"])]
    weights += [weights[-1]] * len(mix["lookalikes"])

    picks: list[tuple[str, str]] = []
    for _ in range(n - len(one_offs)):
        group, wordings = rng.choices(groups, weights)[0]
        wording = wordings[0] if len(wordings) == 1 or rng.random() < 0.5 else rng.choice(wordings[1:])
        picks.append((group, wording))
    for item in one_offs:
        picks.insert(rng.randrange(len(picks) + 1), item)
    return picks


def send(client: httpx.Client, prompt: str) -> dict | None:
    """The gateway's answer, or None if the request failed (503, timeout, ...)."""
    while True:
        try:
            r = client.post("/chat", json={"prompt": prompt})
        except httpx.TransportError as e:
            print(f"     request failed: {type(e).__name__}", flush=True)
            return None
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "1")))
            continue
        if r.status_code != 200:
            print(f"     request failed: HTTP {r.status_code}", flush=True)
            return None
        return r.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--key", required=True, help="client API key")
    parser.add_argument("-n", type=int, default=300, help="total requests")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--zipf", type=float, default=1.0, help="popularity skew (higher = a few questions dominate)")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds to wait after each provider call")
    args = parser.parse_args()

    picks = build_mix(args.n, args.seed, args.zipf)
    kinds: Counter[str] = Counter()
    answered: set[str] = set()  # groups that got a real provider answer so far
    wrong_semantic: list[tuple[str, float]] = []
    spent = saved = 0.0

    with httpx.Client(base_url=args.url, headers={"X-API-Key": args.key}, timeout=120) as client:
        for i, (group, prompt) in enumerate(picks, 1):
            body = send(client, prompt)
            if body is None:
                kinds["failed"] += 1
                continue
            kind = body.get("cache") or "provider"
            kinds[kind] += 1
            spent += body.get("cost_usd") or 0
            saved += body.get("saved_usd") or 0
            # A semantic hit for a question never answered before must be someone else's answer.
            # (Hits on already-answered questions aren't checked, so this is a lower bound.)
            if kind == "semantic" and group not in answered:
                wrong_semantic.append((prompt, body.get("similarity", 0)))
            if kind == "provider":
                answered.add(group)
                time.sleep(args.pause)
            print(f"{i:4d} {kind:9s} {prompt[:60]}", flush=True)

        stats = client.get("/stats").json()

    hits = kinds["exact"] + kinds["semantic"]
    without_cache = spent + saved
    print("\n| requests | failed | provider calls | exact hits | semantic hits | hit rate | wrong semantic hits "
          "| spent USD | saved USD | cost cut |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    print(f"| {len(picks)} | {kinds['failed']} | {kinds['provider']} | {kinds['exact']} | {kinds['semantic']} | {hits / len(picks):.1%} "
          f"| {len(wrong_semantic)} | {spent:.6f} | {saved:.6f} | {saved / without_cache if without_cache else 0:.1%} |")
    for prompt, sim in wrong_semantic:
        print(f"  wrong semantic hit ({sim}): {prompt}")
    print("\n/stats:", json.dumps(stats.get("cache"), indent=2))


if __name__ == "__main__":
    main()
