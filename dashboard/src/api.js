// Small fetch helpers for the gateway's stats endpoints.

async function getJson(path, signal) {
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

// Every call is independent, so one failing endpoint doesn't hide the others.
// The previous period is the same series fetched for twice the window; the caller splits it in half.
// Every call loadDashboard makes (the status pill needs to know when ALL of them failed)
export const CALL_NAMES = ["summary", "series", "wideSeries", "recent", "providers"];

export async function loadDashboard(minutes, bucketSeconds, signal) {
  const calls = {
    summary: "/stats",
    series: `/stats/timeseries?minutes=${minutes}&bucket=${bucketSeconds}`,
    wideSeries: `/stats/timeseries?minutes=${minutes * 2}&bucket=${bucketSeconds}`,
    recent: "/stats/recent?limit=50",
    providers: "/health/providers",
  };
  const keys = CALL_NAMES;
  const settled = await Promise.allSettled(keys.map((key) => getJson(calls[key], signal)));
  const values = {};
  const errors = {};
  settled.forEach((result, i) => {
    if (result.status === "fulfilled") values[keys[i]] = result.value;
    else errors[keys[i]] = result.reason?.message ?? String(result.reason);
  });
  return { values, errors };
}
