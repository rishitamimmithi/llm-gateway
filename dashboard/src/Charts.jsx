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

const HEIGHT = 240;

// Legend in series order (not alphabetical), with text in normal ink instead of the series colour
function legendProps(theme) {
  return {
    itemSorter: null,
    wrapperStyle: { fontSize: 12 },
    formatter: (value) => <span style={{ color: theme.ink }}>{value}</span>,
  };
}

function axisProps(theme) {
  return {
    stroke: theme.axis,
    tick: { fill: theme.muted, fontSize: 12 },
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
          <strong>{format(item.value)}</strong>
        </div>
      ))}
    </div>
  );
}

function Frame({ title, subtitle, children }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {subtitle && <p className="subtitle">{subtitle}</p>}
      <div className="chart">
        <ResponsiveContainer width="100%" height={HEIGHT}>
          {children}
        </ResponsiveContainer>
      </div>
    </section>
  );
}

const OUTCOMES = [
  { key: "ok", name: "Provider answer" },
  { key: "cache_hits", name: "Cache hit" },
  { key: "deduplicated", name: "Shared (dedup)" },
  { key: "errors", name: "Failed attempt" },
];

export function RequestsChart({ buckets }) {
  const theme = useChartTheme();
  return (
    <Frame title="Requests over time" subtitle="How each request was answered, per time bucket">
      <BarChart data={buckets} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke={theme.grid} />
        <XAxis dataKey="ts" tickFormatter={(ts) => formatTime(ts)} minTickGap={32} {...axisProps(theme)} />
        <YAxis allowDecimals={false} width={40} axisLine={false} {...axisProps(theme)} />
        <Tooltip
          cursor={{ fill: theme.grid, opacity: 0.5 }}
          content={<ChartTooltip format={(v) => v} />}
        />
        <Legend iconType="square" {...legendProps(theme)} />
        {OUTCOMES.map((outcome, i) => (
          <Bar
            key={outcome.key}
            dataKey={outcome.key}
            name={outcome.name}
            stackId="outcome"
            fill={theme.series[i]}
            stroke={theme.surface}
            strokeWidth={1}
            maxBarSize={24}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    </Frame>
  );
}

export function LatencyChart({ buckets }) {
  const theme = useChartTheme();
  // Buckets with no provider answers are null, so the line has gaps; small dots keep a lone point visible
  const lines = [
    { key: "p50_ms", name: "p50", dot: { r: 3, strokeWidth: 0, fill: theme.series[0] } },
    { key: "p95_ms", name: "p95", dot: { r: 3, strokeWidth: 0, fill: theme.series[1] } },
  ];
  return (
    <Frame title="Provider latency" subtitle="p50 and p95 of answered provider calls (cache hits left out)">
      <LineChart data={buckets} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke={theme.grid} />
        <XAxis dataKey="ts" tickFormatter={(ts) => formatTime(ts)} minTickGap={32} {...axisProps(theme)} />
        <YAxis width={56} axisLine={false} tickFormatter={formatMs} {...axisProps(theme)} />
        <Tooltip
          cursor={{ stroke: theme.axis }}
          content={<ChartTooltip format={formatMs} />}
        />
        <Legend iconType="plainline" {...legendProps(theme)} />
        {lines.map((line, i) => (
          <Line
            key={line.key}
            dataKey={line.key}
            name={line.name}
            stroke={theme.series[i]}
            strokeWidth={2}
            dot={line.dot ?? false}
            activeDot={{ r: 4, stroke: theme.surface, strokeWidth: 2 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </Frame>
  );
}

export function CostChart({ buckets }) {
  const theme = useChartTheme();
  const lines = [
    { key: "cost_usd", name: "Spent" },
    { key: "saved_usd", name: "Saved by cache + dedup" },
  ];
  return (
    <Frame title="Cost" subtitle="Money spent on providers vs money the cache and dedup saved">
      <LineChart data={buckets} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke={theme.grid} />
        <XAxis dataKey="ts" tickFormatter={(ts) => formatTime(ts)} minTickGap={32} {...axisProps(theme)} />
        <YAxis width={64} axisLine={false} tickFormatter={formatUsd} {...axisProps(theme)} />
        <Tooltip
          cursor={{ stroke: theme.axis }}
          content={<ChartTooltip format={formatUsd} />}
        />
        <Legend iconType="plainline" {...legendProps(theme)} />
        {lines.map((line, i) => (
          <Line
            key={line.key}
            dataKey={line.key}
            name={line.name}
            stroke={theme.series[i]}
            strokeWidth={2}
            dot={line.dot ?? false}
            activeDot={{ r: 4, stroke: theme.surface, strokeWidth: 2 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </Frame>
  );
}
