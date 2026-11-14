"""
Request log: records every request in SQLite and answers stats queries.
Wrapped in a class so tests can create a throwaway database instead of using requests.db.
"""
import sqlite3
import time

# One small request costs about $0.000001, so fewer decimals would round real costs to 0
COST_DECIMALS = 10


class RequestLog:
    def __init__(self, db_path: str) -> None:
        # check_same_thread=False: FastAPI may use this connection from different threads
        self.db = sqlite3.connect(db_path, check_same_thread=False)
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
    ) -> None:
        """cost_usd=None means "unknown price", which is kept apart from a real 0."""
        self.db.execute(
            "INSERT INTO requests (ts, provider, model, prompt_tokens, completion_tokens, latency_ms, status, cost_usd) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), provider, model, prompt_tokens, completion_tokens, latency_ms, status, cost_usd),
        )
        self.db.commit()

    def log_cache_hit(
        self, kind: str, provider: str, model: str, latency_ms: float, saved_usd: float | None
    ) -> None:
        """A request answered from the cache: no tokens, no cost, status cache_exact / cache_semantic."""
        self._log_saved(f"cache_{kind}", provider, model, latency_ms, saved_usd)

    def log_deduplicated(self, provider: str, model: str, latency_ms: float, saved_usd: float | None) -> None:
        """A request that shared another identical in-flight request's provider call (status dedup)."""
        self._log_saved("dedup", provider, model, latency_ms, saved_usd)

    def _log_saved(self, status: str, provider: str, model: str, latency_ms: float, saved_usd: float | None) -> None:
        self.db.execute(
            "INSERT INTO requests (ts, provider, model, prompt_tokens, completion_tokens, latency_ms, status, "
            "cost_usd, saved_usd) VALUES (?, ?, ?, 0, 0, ?, ?, 0, ?)",
            (time.time(), provider, model, latency_ms, status, saved_usd),
        )
        self.db.commit()

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

    def close(self) -> None:
        self.db.close()
