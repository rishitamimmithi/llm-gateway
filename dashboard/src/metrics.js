// Turns timeseries buckets into the numbers on the KPI cards, and compares two periods.

// Average of a per-bucket percentile, weighted by how many provider answers each bucket had.
// It is an approximation (real percentiles can't be merged), so it's only used to compare halves.
function weighted(buckets, key) {
  let sum = 0;
  let weight = 0;
  for (const b of buckets) {
    if (b[key] == null || !b.ok) continue;
    sum += b[key] * b.ok;
    weight += b.ok;
  }
  return weight ? sum / weight : null;
}

export function summarize(buckets) {
  const sum = (key) => buckets.reduce((total, b) => total + b[key], 0);
  const ok = sum("ok");
  const hits = sum("cache_hits");
  const requests = ok + hits + sum("deduplicated");
  const errors = sum("errors");
  return {
    requests,
    errors,
    errorRate: requests + errors ? errors / (requests + errors) : null,
    hitRate: ok + hits ? hits / (ok + hits) : null,
    p50: weighted(buckets, "p50_ms"),
    p95: weighted(buckets, "p95_ms"),
    cost: sum("cost_usd"),
    saved: sum("saved_usd"),
  };
}

// The 2x-window series is cut into two halves with the SAME number of full buckets.
// The newest bucket is still filling, so it's left out of both: comparing a part-filled
// bucket with a full one would make every total look like it dropped a few percent.
export function splitPeriods(wideSeries, minutes) {
  const full = (wideSeries?.buckets ?? []).slice(0, -1);
  const n = Math.round((minutes * 60) / wideSeries?.bucket_seconds);
  if (!n || full.length < 2 * n) return null;
  return {
    previous: full.slice(-2 * n, -n),
    current: full.slice(-n),
  };
}

// Compare one value with its previous-period value. kind "pct" = relative change,
// kind "pt" = percentage-point change (for rates). Returns null when there is nothing to compare.
export function compare(current, previous, kind) {
  if (current == null || previous == null) return null;
  if (kind === "pt") {
    // round first, so a tiny change shows "0.0 pt" instead of "+0.0 pt"
    const diff = Math.round((current - previous) * 1000) / 10;
    return { diff, text: `${diff > 0 ? "+" : diff < 0 ? "−" : ""}${Math.abs(diff).toFixed(1)} pt` };
  }
  if (previous === 0) return null;
  const rel = (current - previous) / previous;
  const pct = Math.round(Math.abs(rel) * 100);
  return { diff: rel, text: `${rel > 0 && pct ? "+" : rel < 0 && pct ? "−" : ""}${pct}%` };
}
