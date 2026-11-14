import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatMs, formatTime, formatUsd } from "./format.js";
import { useChartTheme } from "./theme.js";

const HEIGHT = 200;
const MARGIN = { top: 6, right: 8, bottom: 0, left: 0 };

// Tiny trend line for a KPI card. Points with no value are skipped, so gaps just close up.
export function Sparkline({ values }) {
  const theme = useChartTheme();
  const points = values.map((v, i) => [i, v]).filter(([, v]) => v != null);
  if (points.length < 2) return null; // one point isn't a trend
  const ys = points.map(([, v]) => v);
  const min = Math.min(...ys);
  const span = Math.max(...ys) - min || 1;
  const last = values.length - 1 || 1;
  const path = points
    .map(([i, v]) => `${((i / last) * 100).toFixed(2)},${(22 - ((v - min) / span) * 20).toFixed(2)}`)
    .join(" ");
  return (
    <svg className="spark" viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">
      <polyline
        points={path}
        fill="none"
        stroke={theme.neutral}
        strokeWidth="1.25"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

// Legend in series order (not alphabetical), with text in normal ink instead of the series colour
function legendProps(theme) {
  return {
    itemSorter: null,
    wrapperStyle: { fontSize: 12, paddingTop: 4 },
    formatter: (value) => <span style={{ color: theme.muted }}>{value}</span>,
  };
}

function axisProps(theme) {
  return {
    stroke: theme.axis,
    tick: { fill: theme.muted, fontSize: 11 },
    tickLine: false,
  };
}

function ChartTooltip({ active, payload, label, format }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <div className="tooltip-time">{formatTime(label, true)}</div>
      {payload.map((item) => (
        <div key={item.dataKey} className="tooltip-row">
          <span className="swatch" style={{ background: item.color }} />
          <span>{item.name}</span>
          <strong>{item.value == null ? "—" : format(item.value)}</strong>
        </div>
      ))}
    </div>
  );
}

function Frame({ title, subtitle, children }) {
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>{title}</h2>
        {subtitle && <p className="subtitle">{subtitle}</p>}
      </header>
      <div className="chart">
        <ResponsiveContainer width="100%" height={HEIGHT}>
          {children}
        </ResponsiveContainer>
      </div>
    </section>
  );
}

export function RequestsChart({ buckets }) {
  const theme = useChartTheme();
  const outcomes = [
    { key: "ok", name: "Provider", color: theme.accent },
    { key: "cache_hits", name: "Cache hit", color: theme.neutral },
    { key: "deduplicated", name: "Dedup", color: theme.neutralSoft },
    { key: "errors", name: "Failed", color: theme.critical },
  ];
  return (
    <Frame title="Requests" subtitle="How each request was answered, per bucket">
      <BarChart data={buckets} margin={MARGIN}>
        <CartesianGrid vertical={false} stroke={theme.grid} />
        <XAxis dataKey="ts" tickFormatter={(ts) => formatTime(ts)} minTickGap={32} {...axisProps(theme)} />
        <YAxis allowDecimals={false} width={36} axisLine={false} {...axisProps(theme)} />
        <Tooltip cursor={{ fill: theme.grid, opacity: 0.6 }} content={<ChartTooltip format={(v) => v} />} />
        <Legend iconType="square" iconSize={8} {...legendProps(theme)} />
        {outcomes.map((outcome) => (
          <Bar
            key={outcome.key}
            dataKey={outcome.key}
            name={outcome.name}
            stackId="outcome"
            fill={outcome.color}
            maxBarSize={20}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    </Frame>
  );
}

function isLonePoint(buckets, index, key) {
  const value = buckets[index]?.[key];
  return value != null && buckets[index - 1]?.[key] == null && buckets[index + 1]?.[key] == null;
}

function LineFrame({ title, subtitle, buckets, lines, format, width }) {
  const theme = useChartTheme();
  return (
    <Frame title={title} subtitle={subtitle}>
      <LineChart data={buckets} margin={MARGIN}>
        <CartesianGrid vertical={false} stroke={theme.grid} />
        <XAxis dataKey="ts" tickFormatter={(ts) => formatTime(ts)} minTickGap={32} {...axisProps(theme)} />
        <YAxis width={width} axisLine={false} tickFormatter={format} {...axisProps(theme)} />
        <Tooltip cursor={{ stroke: theme.axis }} content={<ChartTooltip format={format} />} />
        <Legend iconType="plainline" iconSize={12} {...legendProps(theme)} />
        {lines.map((line) => (
          <Line
            key={line.key}
            dataKey={line.key}
            name={line.name}
            stroke={line.color}
            strokeWidth={1.5}
            // buckets with no data are null, so a lone point needs a dot to stay visible;
            // everywhere else the line is enough (a dot per bucket gets noisy fast)
            dot={(p) =>
              isLonePoint(buckets, p.index, line.key) ? (
                <circle key={p.key} cx={p.cx} cy={p.cy} r={2} fill={line.color} />
              ) : (
                <g key={p.key} />
              )
            }
            activeDot={{ r: 3, stroke: theme.surface, strokeWidth: 1 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </Frame>
  );
}

export function LatencyChart({ buckets }) {
  const theme = useChartTheme();
  return (
    <LineFrame
      title="Provider latency"
      subtitle="p50 and p95 of answered provider calls"
      buckets={buckets}
      format={formatMs}
      width={52}
      lines={[
        { key: "p50_ms", name: "p50", color: theme.accent },
        { key: "p95_ms", name: "p95", color: theme.neutral },
      ]}
    />
  );
}

export function CostChart({ buckets }) {
  const theme = useChartTheme();
  return (
    <LineFrame
      title="Cost"
      subtitle="Spent on providers vs saved by cache and dedup"
      buckets={buckets}
      format={formatUsd}
      width={60}
      lines={[
        { key: "cost_usd", name: "Spent", color: theme.accent },
        { key: "saved_usd", name: "Saved", color: theme.neutral },
      ]}
    />
  );
}
