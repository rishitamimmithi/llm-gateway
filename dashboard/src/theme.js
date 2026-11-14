import { useSyncExternalStore } from "react";

// Two jobs: the dark/light choice (saved in localStorage) and the chart colours.
// SVG chart marks get plain colour values, so the chart palette lives here;
// the rest of the page uses the CSS variables in styles.css.
const STORAGE_KEY = "gateway-dashboard-theme";

function readStored() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "light" || value === "dark") return value;
  } catch {
    // storage blocked or unavailable: fall through to the default
  }
  return "dark";
}

let mode = readStored();
const listeners = new Set();

function apply() {
  document.documentElement.dataset.theme = mode;
}
apply();

export function toggleTheme() {
  mode = mode === "dark" ? "light" : "dark";
  apply();
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // not saved, the choice still holds for this visit
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useThemeMode() {
  return useSyncExternalStore(subscribe, () => mode);
}

const PALETTES = {
  light: {
    surface: "#ffffff",
    grid: "#e6e8eb",
    axis: "#c9cdd2",
    muted: "#5f6872",
    ink: "#14181c",
    accent: "#0b7a9b",
    neutral: "#8a939c",
    neutralSoft: "#c2c8ce",
    critical: "#c9453e",
  },
  dark: {
    surface: "#14171b",
    grid: "#232830",
    axis: "#343b44",
    muted: "#8d96a0",
    ink: "#e6e8eb",
    accent: "#3aa6c8",
    neutral: "#7d8791",
    neutralSoft: "#4a525b",
    critical: "#e5675f",
  },
};

export function useChartTheme() {
  return PALETTES[useThemeMode()];
}
