"""
False-hit evaluation for the semantic cache.

For every pair (a, b): pretend `a` is in the cache and `b` arrives. At threshold t the cache
returns a's answer when cosine(a, b) >= t.
- same-meaning pairs:      a hit is GOOD  -> hit rate       = hits / same pairs
- different-meaning pairs: a hit is WRONG -> false-hit rate = hits / different pairs
- precision = good hits / all hits (how often a semantic hit is actually right)
Each threshold is scored twice: similarity alone ("before"), and similarity + the guard
in app/cache/guard.py ("after"), which vetoes pairs whose numbers, negation or direction differ.

Uses the same embedder and normalisation as the gateway (app/cache/semantic.py).
Run:  python -m eval.false_hit_eval            (writes eval/results.csv, eval/results.md, eval/results_tradeoff.png)
      python -m eval.false_hit_eval --pairs eval/holdout_pairs.jsonl --out holdout
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

from app import config
from app.cache.exact import normalise_prompt
from app.cache.guard import conflict
from app.cache.semantic import SentenceTransformerEmbedder, unit_vectors

HERE = Path(__file__).parent
THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98]


def load_pairs(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def similarities(pairs: list[dict], embedder) -> np.ndarray:
    a = unit_vectors(embedder.embed([normalise_prompt(p["a"]) for p in pairs]))
    b = unit_vectors(embedder.embed([normalise_prompt(p["b"]) for p in pairs]))
    return np.sum(a * b, axis=1)  # row-wise cosine similarity


def score(sims: np.ndarray, same: np.ndarray, blocked: np.ndarray, threshold: float) -> dict:
    row = {"threshold": threshold}
    for label, hit in (("", sims >= threshold), ("guard_", (sims >= threshold) & ~blocked)):
        good, bad = int(np.sum(hit & same)), int(np.sum(hit & ~same))
        row |= {
            f"{label}hit_rate": good / max(1, int(same.sum())),
            f"{label}false_hit_rate": bad / max(1, int((~same).sum())),
            f"{label}precision": good / (good + bad) if good + bad else 1.0,
            f"{label}good_hits": good,
            f"{label}false_hits": bad,
        }
    return row


def write_outputs(rows: list[dict], pairs: list[dict], sims: np.ndarray, model: str, out: str) -> None:
    with open(HERE / f"{out}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    same = np.array([p["same"] for p in pairs])
    lines = [
        f"# Semantic cache false-hit evaluation\n",
        f"Model: `{model}`, {int(same.sum())} same-meaning pairs, {int((~same).sum())} different-meaning pairs.\n",
        "Before = similarity only. After = similarity + guard (numbers, negation, direction).\n",
        "| threshold | hit rate before | false-hit rate before | precision before "
        "| hit rate after | false-hit rate after | precision after |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['threshold']:.2f} | {r['hit_rate']:.1%} | {r['false_hit_rate']:.1%} | {r['precision']:.1%} "
            f"| {r['guard_hit_rate']:.1%} | {r['guard_false_hit_rate']:.1%} | {r['guard_precision']:.1%} |"
        )
    reasons = [conflict(p["a"], p["b"]) for p in pairs]
    lines += ["", "## What the guard caught", "", "| category | same? | pairs | blocked | reasons |", "|---|---|---|---|---|"]
    for category, is_same in sorted({(p["category"], p["same"]) for p in pairs}, key=lambda c: (not c[1], c[0])):
        idx = [i for i, p in enumerate(pairs) if p["category"] == category]
        why = sorted({reasons[i] for i in idx if reasons[i]})
        lines.append(
            f"| {category} | {is_same} | {len(idx)} | {sum(1 for i in idx if reasons[i])} | {', '.join(why) or '-'} |"
        )

    lines += ["", "## Similarity by category", "", "| category | same? | pairs | min | mean | max |", "|---|---|---|---|---|---|"]
    categories = sorted({(p["category"], p["same"]) for p in pairs}, key=lambda c: (not c[1], c[0]))
    for category, is_same in categories:
        s = np.array([sims[i] for i, p in enumerate(pairs) if p["category"] == category])
        lines.append(f"| {category} | {is_same} | {len(s)} | {s.min():.3f} | {s.mean():.3f} | {s.max():.3f} |")

    lines += ["", "## Most similar different-meaning pairs (the dangerous ones)", "",
              "| similarity | guard | a | b |", "|---|---|---|---|"]
    different = sorted((i for i, p in enumerate(pairs) if not p["same"]), key=lambda i: -sims[i])
    for i in different[:15]:
        lines.append(f"| {sims[i]:.3f} | {reasons[i] or 'let through'} | {pairs[i]['a']} | {pairs[i]['b']} |")

    lines += ["", "## Least similar same-meaning pairs (missed savings)", "", "| similarity | a | b |", "|---|---|---|"]
    same_idx = sorted((i for i, p in enumerate(pairs) if p["same"]), key=lambda i: sims[i])
    for i in same_idx[:10]:
        lines.append(f"| {sims[i]:.3f} | {pairs[i]['a']} | {pairs[i]['b']} |")
    (HERE / f"{out}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"matplotlib not installed: skipped {out}_tradeoff.png")
        return
    t = [r["threshold"] for r in rows]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(t, [r["hit_rate"] * 100 for r in rows], marker="o", label="hit rate (same meaning)")
    ax.plot(t, [r["false_hit_rate"] * 100 for r in rows], marker="o", label="false-hit rate (different meaning)")
    ax.plot(t, [r["guard_hit_rate"] * 100 for r in rows], marker="s", linestyle="--", label="hit rate + guard")
    ax.plot(t, [r["guard_false_hit_rate"] * 100 for r in rows], marker="s", linestyle="--", label="false-hit rate + guard")
    ax.set_xlabel("similarity threshold")
    ax.set_ylabel("% of pairs")
    ax.set_title("Semantic cache: hits vs false hits")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(HERE / f"{out}_tradeoff.png", dpi=120)
    print(f"wrote eval/{out}_tradeoff.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=Path, default=HERE / "pairs.jsonl")
    parser.add_argument("--model", default=config.EMBEDDING_MODEL)
    parser.add_argument("--out", default="results", help="output name inside eval/")
    args = parser.parse_args()

    pairs = load_pairs(args.pairs)
    sims = similarities(pairs, SentenceTransformerEmbedder(args.model))
    same = np.array([p["same"] for p in pairs])
    blocked = np.array([conflict(p["a"], p["b"]) is not None for p in pairs])
    rows = [score(sims, same, blocked, t) for t in THRESHOLDS]
    write_outputs(rows, pairs, sims, args.model, args.out)
    for r in rows:
        print(
            f"t={r['threshold']:.2f}  before: hit {r['hit_rate']:6.1%} false-hit {r['false_hit_rate']:6.1%}   "
            f"after: hit {r['guard_hit_rate']:6.1%} false-hit {r['guard_false_hit_rate']:6.1%}"
        )


if __name__ == "__main__":
    main()
