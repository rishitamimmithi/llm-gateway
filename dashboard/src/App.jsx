import { useEffect, useState } from "react";
import { loadDashboard } from "./api.js";
import { CostChart, LatencyChart, RequestsChart } from "./Charts.jsx";
import { formatMs, formatPercent, formatTime, formatUsd } from "./format.js";

const REFRESH_MS = 5000;

// Time ranges: how far back to look and how wide each bucket is (about 30-100 buckets each)
const RANGES = [
  { label: "15 min", minutes: 15, bucket: 30 },
  { label: "1 hour", minutes: 60, bucket: 60 },
  { label: "6 hours", minutes: 360, bucket: 300 },
  { label: "24 hours", minutes: 1440, bucket: 900 },
];

// Circuit breaker states: colour + icon + word, so the state never depends on colour alone
const CIRCUIT = {
  closed: { label: "Closed (healthy)", icon: "●", className: "status-good" },
  half_open: { label: "Half-open (testing)", icon: "◐", className: "status-warning" },
  open: { label: "Open (skipped)", icon: "✕", className: "status-critical" },
};

function totals(buckets) {
  const sum = (key) => buckets.reduce((total, b) => total + b[key], 0);
  const ok = sum("ok");
  const hits = sum("cache_hits");
  return {
    requests: ok + hits + sum("deduplicated"),
    errors: sum("errors"),
    hitRate: ok + hits ? hits / (ok + hits) : 0,
    cost: sum("cost_usd"),
    saved: sum("saved_usd"),
  };
}

function Tile({ label, value, note }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {note && <div className="tile-note">{note}</div>}
    </div>
  );
}

function Providers({ providers }) {
  return (
    <section className="card">
      <h2>Providers</h2>
      <p className="subtitle">Tried in this order; an open circuit is skipped until its cooldown ends</p>
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Model</th>
            <th>Circuit</th>
          </tr>
        </thead>
        <tbody>
          {providers.map((p) => {
            const state = CIRCUIT[p.circuit] ?? { label: p.circuit, icon: "?", className: "" };
            return (
              <tr key={p.provider}>
                <td>{p.provider}</td>
                <td className="mono">{p.model}</td>
                <td>
                  <span className={`status ${state.className}`}>
                    <span aria-hidden="true">{state.icon}</span> {state.label}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function DataTable({ buckets }) {
  const rows = buckets.filter((b) => b.ok || b.cache_hits || b.deduplicated || b.errors).reverse();
  return (
    <details className="card">
      <summary>Data table ({rows.length} buckets with traffic)</summary>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Provider</th>
              <th>Cache</th>
              <th>Dedup</th>
              <th>Failed</th>
              <th>p50</th>
              <th>p95</th>
              <th>Spent</th>
              <th>Saved</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.ts}>
                <td>{formatTime(b.ts)}</td>
                <td>{b.ok}</td>
                <td>{b.cache_hits}</td>
                <td>{b.deduplicated}</td>
                <td>{b.errors}</td>
                <td>{formatMs(b.p50_ms)}</td>
                <td>{formatMs(b.p95_ms)}</td>
                <td>{formatUsd(b.cost_usd)}</td>
                <td>{formatUsd(b.saved_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export default function App() {
  const [range, setRange] = useState(RANGES[1]);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);

  useEffect(() => {
    // One poll at a time: the next one is scheduled only after the last finished, so slow answers
    // can't pile up. Changing the range aborts the old request, so its late answer can't overwrite
    // the new range's data.
    const controller = new AbortController();
    let timer;

    async function poll() {
      try {
        const next = await loadDashboard(range.minutes, range.bucket, controller.signal);
        setData(next);
        setError(null);
        setUpdatedAt(Date.now() / 1000);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err.message); // keep showing the last good data
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, REFRESH_MS);
    }

    poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [range]);

  const recent = data ? totals(data.series.buckets) : null;
  const latency = data?.series.latency_ms;

  return (
    <main>
      <header>
        <div>
          <h1>LLM Gateway</h1>
          <p className="subtitle">
            {updatedAt ? `Updated ${formatTime(updatedAt, true)} · refreshes every 5 s` : "Loading…"}
          </p>
        </div>
        <div className="ranges" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button
              key={r.label}
              className={r === range ? "active" : ""}
              aria-pressed={r === range}
              onClick={() => setRange(r)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </header>

      {error && (
        <div className="banner" role="alert">
          Can't reach the gateway ({error}). Is it running?
        </div>
      )}

      {data && (
        <>
          <div className="tiles">
            <Tile label="Requests" value={recent.requests} note={`${recent.errors} failed attempts`} />
            <Tile
              label="Cache hit rate"
              value={formatPercent(recent.hitRate)}
              note={`all time ${formatPercent(data.summary.cache.hit_rate)}`}
            />
            <Tile label="Latency p50" value={formatMs(latency.p50)} note="provider answers" />
            <Tile label="Latency p95" value={formatMs(latency.p95)} note={`p99 ${formatMs(latency.p99)}`} />
            <Tile label="Spent" value={formatUsd(recent.cost)} note={`all time ${formatUsd(data.summary.total_cost_usd)}`} />
            <Tile
              label="Saved"
              value={formatUsd(recent.saved)}
              note={`all time ${formatUsd(data.summary.cache.saved_usd)}`}
            />
          </div>
          <div className="grid">
            <RequestsChart buckets={data.series.buckets} />
            <LatencyChart buckets={data.series.buckets} />
            <CostChart buckets={data.series.buckets} />
            <Providers providers={data.providers} />
          </div>
          <DataTable buckets={data.series.buckets} />
        </>
      )}
    </main>
  );
}
