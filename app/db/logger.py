"""
Request log: records every request in SQLite and answers stats queries.
Wrapped in a class so tests can create a throwaway database instead of using requests.db.
"""
import sqlite3
import time


class RequestLog:
    def __init__(self, db_path: str) -> None:
        # check_same_thread=False: FastAPI may use this connection from different threads
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, provider TEXT, model TEXT,
                prompt_tokens INTEGER, completion_tokens INTEGER,
                latency_ms REAL, status TEXT)"""
        )
        self.db.commit()

    def log(
        self,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        status: str,
    ) -> None:
        self.db.execute(
            "INSERT INTO requests (ts, provider, model, prompt_tokens, completion_tokens, latency_ms, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), provider, model, prompt_tokens, completion_tokens, latency_ms, status),
        )
        self.db.commit()

    def stats(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*), AVG(latency_ms), SUM(prompt_tokens + completion_tokens) "
            "FROM requests WHERE status = 'ok'"
        ).fetchone()
        return {
            "successful_requests": row[0],
            "avg_latency_ms": round(row[1] or 0, 1),
            "total_tokens": row[2] or 0,
        }

    def close(self) -> None:
        self.db.close()
