"""
False-hit evaluation for the semantic cache.

For every pair (a, b): pretend `a` is in the cache and `b` arrives. At threshold t the cache
returns a's answer when cosine(a, b) >= t.
- same-meaning pairs:      a hit is GOOD  -> hit rate       = hits / same pairs
- different-meaning pairs: a hit is WRONG -> false-hit rate = hits / different pairs
- precision = good hits / all hits (how often a semantic hit is actually right)

Uses the same embedder and normalisation as the gateway (app/cache/semantic.py).
Run:  python -m eval.false_hit_eval            (writes eval/results.csv, eval/results.md, eval/tradeoff.png)
"""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

from app import config
from app.cache.exact import normalise_prompt
from app.cache.semantic import SentenceTransformerEmbedder, unit_vectors

HERE = Path(__file__).parent
THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98]


def load_pairs(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def similarities(pairs: list[dict], embedder) -> np.ndarray:
    a = unit_vectors(embedder.embed([normalise_prompt(p["a"]) for p in pairs]))
    b = unit_vectors(embedder.embed([normalise_prompt(p["b"]) for p in pairs]))
    return np.sum(a * b, axis=1)  # row-wise cosine similarity


def score(sims: np.ndarray, same: np.ndarray, threshold: float) -> dict:
    hit = sims >= threshold
    good, bad = int(np.sum(hit & same)), int(np.sum(hit & ~same))
    return {
        "threshold": threshold,
        "hit_rate": good / max(1, int(same.sum())),
        "false_hit_rate": bad / max(1, int((~same).sum())),
        "precision": good / (good + bad) if good + bad else 1.0,
        "good_hits": good,
        "false_hits": bad,
    }


def write_outputs(rows: list[dict], pairs: list[dict], sims: np.ndarray, model: str) -> None:
    with open(HERE / "results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    same = np.array([p["same"] for p in pairs])
    lines = [
        f"# Semantic cache false-hit evaluation\n",
        f"Model: `{model}`, {int(same.sum())} same-meaning pairs, {int((~same).sum())} different-meaning pairs.\n",
        "| threshold | hit rate | false-hit rate | precision | good hits | false hits |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['threshold']:.2f} | {r['hit_rate']:.1%} | {r['false_hit_rate']:.1%} | {r['precision']:.1%} "
            f"| {r['good_hits']} | {r['false_hits']} |"
        )

    lines += ["", "## Similarity by category", "", "| category | same? | pairs | min | mean | max |", "|---|---|---|---|---|---|"]
    categories = sorted({(p["category"], p["same"]) for p in pairs}, key=lambda c: (not c[1], c[0]))
    for category, is_same in categories:
        s = np.array([sims[i] for i, p in enumerate(pairs) if p["category"] == category])
        lines.append(f"| {category} | {is_same} | {len(s)} | {s.min():.3f} | {s.mean():.3f} | {s.max():.3f} |")

    lines += ["", "## Most similar different-meaning pairs (the dangerous ones)", "", "| similarity | a | b |", "|---|---|---|"]
    different = sorted((i for i, p in enumerate(pairs) if not p["same"]), key=lambda i: -sims[i])
    for i in different[:15]:
        lines.append(f"| {sims[i]:.3f} | {pairs[i]['a']} | {pairs[i]['b']} |")

    lines += ["", "## Least similar same-meaning pairs (missed savings)", "", "| similarity | a | b |", "|---|---|---|"]
    same_idx = sorted((i for i, p in enumerate(pairs) if p["same"]), key=lambda i: sims[i])
    for i in same_idx[:10]:
        lines.append(f"| {sims[i]:.3f} | {pairs[i]['a']} | {pairs[i]['b']} |")
    (HERE / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: skipped tradeoff.png")
        return
    t = [r["threshold"] for r in rows]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(t, [r["hit_rate"] * 100 for r in rows], marker="o", label="hit rate (same meaning)")
    ax.plot(t, [r["false_hit_rate"] * 100 for r in rows], marker="o", label="false-hit rate (different meaning)")
    ax.set_xlabel("similarity threshold")
    ax.set_ylabel("% of pairs")
    ax.set_title("Semantic cache: hits vs false hits")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(HERE / "tradeoff.png", dpi=120)
    print("wrote eval/tradeoff.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=Path, default=HERE / "pairs.jsonl")
    parser.add_argument("--model", default=config.EMBEDDING_MODEL)
    args = parser.parse_args()

    pairs = load_pairs(args.pairs)
    sims = similarities(pairs, SentenceTransformerEmbedder(args.model))
    same = np.array([p["same"] for p in pairs])
    rows = [score(sims, same, t) for t in THRESHOLDS]
    write_outputs(rows, pairs, sims, args.model)
    for r in rows:
        print(
            f"t={r['threshold']:.2f}  hit {r['hit_rate']:6.1%}  false-hit {r['false_hit_rate']:6.1%}  "
            f"precision {r['precision']:6.1%}"
        )


if __name__ == "__main__":
    main()
