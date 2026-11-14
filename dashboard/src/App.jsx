import { useEffect, useState } from "react";
import { CALL_NAMES, loadDashboard } from "./api.js";
import { CostChart, LatencyChart, RequestsChart, Sparkline } from "./Charts.jsx";
import { DASH, formatCount, formatMs, formatPercent, formatTime, formatUsd } from "./format.js";
import { compare, splitPeriods, summarize } from "./metrics.js";
import { toggleTheme, useThemeMode } from "./theme.js";

const REFRESH_MS = 5000;

// Time ranges: how far back to look and how wide each bucket is (about 30-100 buckets each)
const RANGES = [
  { label: "15 min", minutes: 15, bucket: 30, prev: "prev 15 min" },
  { label: "1 hour", minutes: 60, bucket: 60, prev: "prev hour" },
  { label: "6 hours", minutes: 360, bucket: 300, prev: "prev 6 hours" },
  { label: "24 hours", minutes: 1440, bucket: 900, prev: "prev 24 hours" },
];

// Circuit breaker states: colour + symbol + word, so the state never depends on colour alone
const CIRCUIT = {
  closed: { label: "Closed", icon: "●", className: "good" },
  half_open: { label: "Half-open", icon: "◐", className: "warn" },
  open: { label: "Open", icon: "✕", className: "bad" },
};

// What each KPI card shows. `tone` decides the colour of the change: only clear good/bad moves are coloured.
const KPIS = [
  {
    id: "requests",
    label: "Requests",
    kind: "pct",
    get: (s) => s.requests,
    show: (v) => formatCount(v),
    spark: (b) => b.ok + b.cache_hits + b.deduplicated,
    tone: () => "flat",
  },
  {
    id: "errors",
    label: "Error rate",
    kind: "pt",
    get: (s) => s.errorRate,
    show: formatPercent,
    spark: (b) => {
      const total = b.ok + b.cache_hits + b.deduplicated + b.errors;
      return total ? b.errors / total : null;
    },
    tone: (d) => (d.diff > 0.5 ? "bad" : d.diff < -0.5 ? "good" : "flat"),
  },
  {
    id: "hits",
    label: "Cache hit rate",
    kind: "pt",
    get: (s) => s.hitRate,
    show: formatPercent,
    spark: (b) => (b.ok + b.cache_hits ? b.cache_hits / (b.ok + b.cache_hits) : null),
    tone: (d) => (d.diff > 1 ? "good" : "flat"),
  },
  {
    id: "p50",
    label: "Latency p50",
    kind: "pct",
    get: (s) => s.p50,
    show: formatMs,
    spark: (b) => b.p50_ms,
    tone: latencyTone,
  },
  {
    id: "p95",
    label: "Latency p95",
    kind: "pct",
    get: (s) => s.p95,
    show: formatMs,
    spark: (b) => b.p95_ms,
    tone: latencyTone,
  },
  {
    id: "cost",
    label: "Spent",
    kind: "pct",
    get: (s) => s.cost,
    show: formatUsd,
    spark: (b) => b.cost_usd,
    tone: () => "flat",
  },
  {
    id: "saved",
    label: "Saved",
    kind: "pct",
    get: (s) => s.saved,
    show: formatUsd,
    spark: (b) => b.saved_usd,
    tone: () => "flat",
  },
];

function latencyTone(d) {
  if (d.diff > 0.5) return "bad";
  if (d.diff > 0.1) return "warn";
  if (d.diff < -0.1) return "good";
  return "flat";
}

function Kpi({ def, current, compareCurrent, previous, buckets, range, exact }) {
  const change = previous && compareCurrent ? compare(def.get(compareCurrent), def.get(previous), def.kind) : null;
  const tone = change ? def.tone(change) : "flat";
  // Headline latency comes straight from the server's percentiles; the change uses bucket averages
  const value = def.show(exact ?? def.get(current));
  return (
    <div className="kpi">
      <div className="kpi-label">{def.label}</div>
      <div className="kpi-value">{value}</div>
      {/* no previous period to compare with: show nothing rather than a lone dash */}
      {change && <div className={`kpi-change ${tone}`}>{`${change.text} vs ${range.prev}`}</div>}
      <Sparkline values={buckets.map(def.spark)} />
    </div>
  );
}

function Providers({ providers }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Providers</h2>
        <p className="subtitle">Tried in this order; an open circuit is skipped until its cooldown ends</p>
      </header>
      {providers ? (
        <div className="table-scroll">
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
        </div>
      ) : (
        <p className="empty">{DASH}</p>
      )}
    </section>
  );
}

// "Served by" for one row, plus how to colour it (errors and cancelled get a status colour)
function servedBy(row) {
  switch (row.status) {
    case "ok":
      return { text: row.provider, className: "" };
    case "cache_exact":
      return { text: "exact cache", note: row.provider, className: "" };
    case "cache_semantic":
      return { text: "semantic cache", note: row.provider, className: "" };
    case "dedup":
      return { text: "dedup", note: row.provider, className: "" };
    case "error":
      return { text: "error", note: row.provider, className: "bad" };
    case "cancelled":
      return { text: "cancelled", note: row.provider, className: "warn" };
    default:
      return { text: row.status, className: "" };
  }
}

function Recent({ rows }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Recent requests</h2>
        <p className="subtitle">Newest first, last {rows ? rows.length : 50}</p>
      </header>
      {rows ? (
        <div className="table-scroll tall">
          <table className="recent">
            <thead>
              <tr>
                <th>Time</th>
                <th className="preview">Prompt</th>
                <th>Served by</th>
                <th className="num">Latency</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const served = servedBy(row);
                return (
                  <tr key={row.id}>
                    <td>{formatTime(row.ts, true)}</td>
                    <td className="preview" title={row.prompt_preview ?? undefined}>
                      {row.prompt_preview ?? DASH}
                    </td>
                    <td className={served.className} title={row.model}>
                      {served.text}
                      {served.note && <span className="note"> · {served.note}</span>}
                    </td>
                    <td className="num">{formatMs(row.latency_ms)}</td>
                    <td className="num" title={row.cost_usd == null ? "unpriced model" : undefined}>
                      {formatUsd(row.cost_usd)}
                    </td>
                  </tr>
                );
              })}
              {!rows.length && (
                <tr>
                  <td colSpan={5} className="empty">
                    No requests yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="empty">{DASH}</p>
      )}
    </section>
  );
}

function DataTable({ buckets }) {
  const rows = buckets.filter((b) => b.ok || b.cache_hits || b.deduplicated || b.errors).reverse();
  return (
    <details className="panel">
      <summary>
        Data table ({rows.length} {rows.length === 1 ? "bucket" : "buckets"} with traffic)
      </summary>
      <div className="table-scroll tall">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th className="num">Provider</th>
              <th className="num">Cache</th>
              <th className="num">Dedup</th>
              <th className="num">Failed</th>
              <th className="num">p50</th>
              <th className="num">p95</th>
              <th className="num">Spent</th>
              <th className="num">Saved</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.ts}>
                <td>{formatTime(b.ts)}</td>
                <td className="num">{b.ok}</td>
                <td className="num">{b.cache_hits}</td>
                <td className="num">{b.deduplicated}</td>
                <td className="num">{b.errors}</td>
                <td className="num">{formatMs(b.p50_ms)}</td>
                <td className="num">{formatMs(b.p95_ms)}</td>
                <td className="num">{formatUsd(b.cost_usd)}</td>
                <td className="num">{formatUsd(b.saved_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

// Healthy = every circuit closed and every stats call answered; degraded = any half-open or open
// circuit, or some stats calls failing; unreachable = every call failed.
function gatewayStatus(data, errors) {
  const failed = Object.keys(errors).length;
  if (failed >= CALL_NAMES.length) return { key: "bad", label: "Unreachable" };
  if (errors.providers) return { key: "unknown", label: "Unknown" };
  if (!data.providers) return { key: "unknown", label: "Connecting" };
  if (!data.providers.length) return { key: "unknown", label: "No providers" };
  const allClosed = data.providers.every((p) => p.circuit === "closed");
  return allClosed && !failed ? { key: "good", label: "Healthy" } : { key: "warn", label: "Degraded" };
}

function ThemeToggle() {
  const mode = useThemeMode();
  const next = mode === "dark" ? "light" : "dark";
  return (
    <button type="button" className="btn" onClick={toggleTheme} aria-label={`Switch to ${next} theme`}>
      {mode === "dark" ? "Light" : "Dark"}
    </button>
  );
}

export default function App() {
  const [range, setRange] = useState(RANGES[1]);
  // Each piece keeps its last good value, so one failing endpoint doesn't blank the others.
  // The series remember which range they were fetched for, so a slow range switch never shows old numbers.
  const [data, setData] = useState({});
  const [errors, setErrors] = useState({});
  const [updatedAt, setUpdatedAt] = useState(null);

  useEffect(() => {
    // One poll at a time: the next one is scheduled only after the last finished, so slow answers
    // can't pile up. Changing the range aborts the old request, so its late answer can't overwrite
    // the new range's data.
    const controller = new AbortController();
    let timer;

    async function poll() {
      try {
        const result = await loadDashboard(range.minutes, range.bucket, controller.signal);
        if (controller.signal.aborted) return;
        setData((old) => ({
          ...old,
          ...result.values,
          ...(result.values.series ? { seriesRange: range.label } : {}),
          ...(result.values.wideSeries ? { wideRange: range.label } : {}),
        }));
        setErrors(result.errors);
        if (Object.keys(result.values).length) setUpdatedAt(Date.now() / 1000);
      } catch (err) {
        if (controller.signal.aborted) return;
        setErrors({ all: err.message });
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, REFRESH_MS);
    }

    poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [range]);

  const status = gatewayStatus(data, errors);
  const failedNames = Object.values(errors);
  const allFailed = status.key === "bad" || errors.all;

  const series = data.seriesRange === range.label ? data.series : null;
  const periods = data.wideRange === range.label ? splitPeriods(data.wideSeries, range.minutes) : null;
  const buckets = series?.buckets ?? [];
  const current = series ? summarize(buckets) : null;
  const previous = periods && periods.previous.length ? summarize(periods.previous) : null;
  // Previous period only counts when it had traffic, otherwise "vs prev" would be meaningless
  const prevUsable = previous && (previous.requests || previous.errors) ? previous : null;
  // Compare like with like: the current half of the wide series, not the separately fetched one
  const currentForCompare = periods ? summarize(periods.current) : null;
  const exact = { p50: series?.latency_ms?.p50, p95: series?.latency_ms?.p95 };

  return (
    <main>
      <header className="topbar">
        <div className="brand">
          <h1>LLM Gateway</h1>
          <span className={`pill ${status.key}`} role="status">
            <span className="dot" aria-hidden="true" />
            {status.label}
          </span>
        </div>
        <div className="controls">
          <span className="updated">
            {updatedAt ? `Updated ${formatTime(updatedAt, true)} · every 5 s` : "Loading…"}
          </span>
          <div className="ranges" role="group" aria-label="Time range">
            {RANGES.map((r) => (
              <button
                key={r.label}
                type="button"
                className={r === range ? "active" : ""}
                aria-pressed={r === range}
                onClick={() => setRange(r)}
              >
                {r.label}
              </button>
            ))}
          </div>
          <ThemeToggle />
        </div>
      </header>

      {failedNames.length > 0 && (
        <div className="banner" role="alert">
          {allFailed
            ? `Can't reach the gateway (${failedNames[0]}). Is it running?`
            : `Some data failed to load (${failedNames.join("; ")}). The rest is still updating.`}
        </div>
      )}

      <section className="kpis" aria-label="Key numbers">
        {KPIS.map((def) =>
          current ? (
            <Kpi
              key={def.id}
              def={def}
              current={current}
              compareCurrent={currentForCompare}
              previous={prevUsable}
              buckets={buckets}
              range={range}
              exact={def.id === "p50" ? exact.p50 : def.id === "p95" ? exact.p95 : undefined}
            />
          ) : (
            <div className="kpi" key={def.id}>
              <div className="kpi-label">{def.label}</div>
              <div className="kpi-value">{DASH}</div>
              <div className="spark" aria-hidden="true" />
            </div>
          )
        )}
      </section>

      {series ? (
        <div className="charts">
          <RequestsChart buckets={buckets} />
          <LatencyChart buckets={buckets} />
          <CostChart buckets={buckets} />
        </div>
      ) : (
        <p className="empty panel">{errors.series ? "Charts unavailable" : "Loading charts…"}</p>
      )}

      <div className="lower">
        <Recent rows={data.recent} />
        <Providers providers={data.providers} />
      </div>

      {series && <DataTable buckets={buckets} />}
    </main>
  );
}
