// Number formatting shared by cards, tooltips and tables.

export const DASH = "—";

export function formatUsd(value) {
  if (value == null) return DASH;
  if (value === 0) return "$0";
  if (Math.abs(value) < 0.01) return `$${Number(value.toPrecision(2))}`; // tiny per-request costs, no trailing zeros
  return `$${value.toFixed(2)}`;
}

export function formatMs(value) {
  if (value == null) return DASH;
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

export function formatPercent(fraction) {
  if (fraction == null) return DASH;
  return `${(fraction * 100).toFixed(1)}%`;
}

export function formatCount(value) {
  if (value == null) return DASH;
  return value.toLocaleString();
}

export function formatTime(epochSeconds, withSeconds = false) {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
  });
}
