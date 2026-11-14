// Small fetch helpers for the gateway's stats endpoints.

async function getJson(path, signal) {
  const response = await fetch(path, { signal });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

export async function loadDashboard(minutes, bucketSeconds, signal) {
  const [summary, series, providers] = await Promise.all([
    getJson("/stats", signal),
    getJson(`/stats/timeseries?minutes=${minutes}&bucket=${bucketSeconds}`, signal),
    getJson("/health/providers", signal),
  ]);
  return { summary, series, providers };
}
