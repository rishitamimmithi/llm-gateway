import { useEffect, useState } from "react";

// Chart colours for light and dark mode. SVG chart marks get plain colour values,
// so the palette lives here; the page itself uses the CSS variables in styles.css.
const LIGHT = {
  surface: "#fcfcfb",
  grid: "#e1e0d9",
  axis: "#c3c2b7",
  muted: "#898781",
  ink: "#0b0b0b",
  series: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
};

const DARK = {
  surface: "#1a1a19",
  grid: "#2c2c2a",
  axis: "#383835",
  muted: "#898781",
  ink: "#f0efec",
  series: ["#3987e5", "#d95926", "#199e70", "#c98500"],
};

const query = "(prefers-color-scheme: dark)";

export function useChartTheme() {
  const [dark, setDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = (event) => setDark(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return dark ? DARK : LIGHT;
}
