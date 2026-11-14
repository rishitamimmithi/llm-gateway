"""The request log is used by the event loop (writes) and several threadpool threads (stats reads)
at the same time. Without the lock this failed with sqlite3.InterfaceError under the dashboard's
parallel stats calls."""
from concurrent.futures import ThreadPoolExecutor

from app.db.logger import RequestLog


def test_parallel_reads_and_writes_do_not_fail():
    log = RequestLog(":memory:")
    for _ in range(200):
        log.log("fake", "fake-model", 4, 6, 100.0, "ok", 0.0001, "hi")

    def work(i: int) -> None:
        for _ in range(40):
            if i % 4 == 0:
                log.log("fake", "fake-model", 4, 6, 100.0, "ok", 0.0001, "hi")
            elif i % 4 == 1:
                log.timeseries(120, 60)
            elif i % 4 == 2:
                log.stats()
            else:
                log.recent(50)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(16)))  # list() re-raises any error from a thread

    assert log.stats()["successful_requests"] == 200 + 4 * 40
    log.close()
