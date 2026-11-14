"""
Request log: records every request in SQLite and answers stats queries.
Wrapped in a class so tests can create a throwaway database instead of using requests.db.
"""
import math
import sqlite3
import threading
import time

# One small request costs about $0.000001, so fewer decimals would round real costs to 0
COST_DECIMALS = 10


def percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of an already sorted list (None if empty).
    p95 = the value that 95% of requests were at or below."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return round(sorted_values[rank - 1], 1)


class _Rows(list):
    """Query results already read into memory, with the two cursor methods the code uses."""

    def fetchone(self):
        return self[0] if self else None

    def fetchall(self) -> list:
        return list(self)


class LockedConnection:
    """One SQLite connection shared by the event loop (logging) and FastAPI's thread pool (stats
    endpoints). check_same_thread=False only turns off Python's check: two threads using the
    connection at the same moment still fails ("bad parameter or other API misuse"), which the
    dashboard's parallel stats calls triggered. So every call takes a lock and reads all of its rows
    before letting go. Trade-off: a log write can wait for a running stats query (a few ms)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def execute(self, sql: str, params: tuple = ()) -> _Rows:
        with self._lock:
            return _Rows(self._conn.execute(sql, params).fetchall())

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class RequestLog:
    def __init__(self, db_path: str) -> None:
        # check_same_thread=False: FastAPI uses this connection from different threads (see LockedConnection)
        self.db = LockedConnection(sqlite3.connect(db_path, check_same_thread=False))
        # Every request commits a row from the event loop. In the default journal mode each commit waits
        # for the disk (~10 ms on my laptop), which capped the gateway near 64 req/s under load.
        # WAL appends to a log file instead (~0.1 ms). (It would also let a second, read-only connection
        # read while this one writes; with one shared connection that doesn't happen yet.)
        # synchronous=NORMAL: a power cut can lose the last few rows, an app crash can't. Fine for a request log.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, provider TEXT, model TEXT,
                prompt_tokens INTEGER, completion_tokens INTEGER,
                latency_ms REAL, status TEXT, cost_usd REAL)"""
        )
        # Databases made before cost tracking have no cost_usd column: add it.
        # Old rows stay NULL (unknown), so they show up in "unpriced_requests".
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(requests)")}
        if "cost_usd" not in columns:
            self.db.execute("ALTER TABLE requests ADD COLUMN cost_usd REAL")
        # Cache hits store what the provider call WOULD have cost in saved_usd (NULL for other rows).
        if "saved_usd" not in columns:
            self.db.execute("ALTER TABLE requests ADD COLUMN saved_usd REAL")
        # A short start of the prompt for the "recent requests" table (NULL when previews are off)
        if "prompt_preview" not in columns:
            self.db.execute("ALTER TABLE requests ADD COLUMN prompt_preview TEXT")
        # The dashboard asks for "rows in the last N minutes" every few seconds: without an index on ts
        # that is a full table scan each time
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts)")
        self.db.commit()

    def log(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        status: str,
        cost_usd: float | None = None,
        prompt_preview: str | None = None,
    ) -> None:
        """cost_usd=None means "unknown price", which is kept apart from a real 0."""
        self.db.execute(
            "INSERT INTO requests (ts, provider, model, prompt_tokens, completion_tokens, latency_ms, status, "
            "cost_usd, prompt_preview) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), provider, model, prompt_tokens, completion_tokens, latency_ms, status, cost_usd, prompt_preview),
        )
        self.db.commit()

    def log_cache_hit(
        self, kind: str, provider: str, model: str, latency_ms: float, saved_usd: float | None,
        prompt_preview: str | None = None,
    ) -> None:
        """A request answered from the cache: no tokens, no cost, status cache_exact / cache_semantic."""
        self._log_saved(f"cache_{kind}", provider, model, latency_ms, saved_usd, prompt_preview)

    def log_deduplicated(
        self, provider: str, model: str, latency_ms: float, saved_usd: float | None, prompt_preview: str | None = None
    ) -> None:
        """A request that shared another identical in-flight request's provider call (status dedup)."""
        self._log_saved("dedup", provider, model, latency_ms, saved_usd, prompt_preview)

    def _log_saved(
        self, status: str, provider: str, model: str, latency_ms: float, saved_usd: float | None,
        prompt_preview: str | None,
    ) -> None:
        self.db.execute(
            "INSERT INTO requests (ts, provider, model, prompt_tokens, completion_tokens, latency_ms, status, "
            "cost_usd, saved_usd, prompt_preview) VALUES (?, ?, ?, 0, 0, ?, ?, 0, ?, ?)",
            (time.time(), provider, model, latency_ms, status, saved_usd, prompt_preview),
        )
        self.db.commit()

    def recent(self, limit: int) -> list[dict]:
        """The newest `limit` rows, newest first, for the dashboard's "recent requests" table."""
        rows = self.db.execute(
            "SELECT id, ts, status, provider, model, prompt_preview, latency_ms, prompt_tokens, completion_tokens, "
            "cost_usd, saved_usd FROM requests ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        names = ("id", "ts", "status", "provider", "model", "prompt_preview", "latency_ms", "prompt_tokens",
                 "completion_tokens", "cost_usd", "saved_usd")
        return [dict(zip(names, row)) for row in rows]

    def cache_stats(self) -> dict:
        """Misses = requests a provider answered (status ok); hits = requests the cache answered.
        Deduplicated requests (shared an in-flight call) are counted on their own, not as hits,
        but the money they saved is included in saved_usd."""
        misses, exact, semantic, dedup, saved, unpriced = self.db.execute(
            "SELECT SUM(status = 'ok'), SUM(status = 'cache_exact'), SUM(status = 'cache_semantic'), "
            "SUM(status = 'dedup'), SUM(saved_usd), "
            "SUM(status IN ('cache_exact', 'cache_semantic', 'dedup') AND saved_usd IS NULL) FROM requests"
        ).fetchone()
        misses, exact, semantic, dedup = misses or 0, exact or 0, semantic or 0, dedup or 0
        total = misses + exact + semantic
        return {
            "exact_hits": exact,
            "semantic_hits": semantic,
            "misses": misses,
            "deduplicated": dedup,
            "hit_rate": round((exact + semantic) / total, 4) if total else 0,
            "saved_usd": round(saved or 0, COST_DECIMALS),
            "unpriced_hits": unpriced or 0,  # hits / shared calls whose model had no price
        }

    def stats(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*), AVG(latency_ms), SUM(prompt_tokens + completion_tokens), "
            "SUM(cost_usd), SUM(cost_usd IS NULL) "
            "FROM requests WHERE status = 'ok'"
        ).fetchone()
        by_provider = {
            provider: {
                "successful_requests": count,
                "total_tokens": tokens or 0,
                "total_cost_usd": round(cost or 0, COST_DECIMALS),
                "unpriced_requests": unpriced or 0,
            }
            for provider, count, tokens, cost, unpriced in self.db.execute(
                "SELECT provider, COUNT(*), SUM(prompt_tokens + completion_tokens), SUM(cost_usd), "
                "SUM(cost_usd IS NULL) "
                "FROM requests WHERE status = 'ok' GROUP BY provider ORDER BY provider"
            )
        }
        return {
            "successful_requests": row[0],
            "avg_latency_ms": round(row[1] or 0, 1),
            "total_tokens": row[2] or 0,
            "total_cost_usd": round(row[3] or 0, COST_DECIMALS),
            "unpriced_requests": row[4] or 0,  # model missing in MODEL_PRICES, or logged before cost tracking
            "by_provider": by_provider,
            "cache": self.cache_stats(),
        }

    def timeseries(self, minutes: int, bucket_seconds: int, now: float | None = None) -> dict:
        """Requests in the last `minutes`, grouped into time buckets for the dashboard.
        Latency percentiles are worked out in Python because SQLite has no percentile function."""
        now = time.time() if now is None else now
        start = now - minutes * 60
        first = int(start // bucket_seconds) * bucket_seconds  # line buckets up on round times
        count = int((now - first) // bucket_seconds) + 1
        buckets = [
            {"ts": first + i * bucket_seconds, "ok": 0, "errors": 0, "cache_hits": 0, "deduplicated": 0,
             "cost_usd": 0.0, "saved_usd": 0.0, "latencies": []}
            for i in range(count)
        ]
        all_latencies: list[float] = []  # provider answers only: they are what the client waits for
        rows = self.db.execute(
            # from `first`, not `start`, so the oldest bucket is complete instead of showing a fake dip
            "SELECT ts, status, latency_ms, cost_usd, saved_usd FROM requests WHERE ts >= ? AND ts <= ?",
            (first, now),
        )
        for ts, status, latency, cost, saved in rows:
            bucket = buckets[min(int((ts - first) // bucket_seconds), count - 1)]
            if status == "ok":
                bucket["ok"] += 1
                if latency is not None:
                    bucket["latencies"].append(latency)
                    all_latencies.append(latency)
            elif status == "error":
                bucket["errors"] += 1
            elif status and status.startswith("cache_"):
                bucket["cache_hits"] += 1
            elif status == "dedup":
                bucket["deduplicated"] += 1
            if status == "ok":  # same rule as stats(): only provider answers count as spent
                bucket["cost_usd"] += cost or 0
            bucket["saved_usd"] += saved or 0
        for bucket in buckets:
            latencies = sorted(bucket.pop("latencies"))
            bucket["p50_ms"] = percentile(latencies, 50)
            bucket["p95_ms"] = percentile(latencies, 95)
            bucket["cost_usd"] = round(bucket["cost_usd"], COST_DECIMALS)
            bucket["saved_usd"] = round(bucket["saved_usd"], COST_DECIMALS)
        all_latencies.sort()
        return {
            "minutes": minutes,
            "bucket_seconds": bucket_seconds,
            "latency_ms": {p: percentile(all_latencies, n) for p, n in (("p50", 50), ("p95", 95), ("p99", 99))},
            "buckets": buckets,
        }

    def close(self) -> None:
        self.db.close()
