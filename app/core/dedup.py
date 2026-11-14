"""
Request deduplication: identical requests that arrive while one is already in flight share
that one call instead of each calling the provider.

The first caller for a key (the "leader") starts the work as its own asyncio Task. Anyone who
asks for the same key before it finishes (a "follower") just awaits that same Task.
- The result (or the error) is shared by everyone waiting.
- Callers await through asyncio.shield, so one client giving up doesn't cancel the work
  for the others.
- The key is forgotten as soon as the work finishes, so later requests go through the cache
  instead of reusing an old result forever.

This is per process: two gateway instances can still each make one call for the same prompt.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

logger = logging.getLogger("gateway.dedup")

T = TypeVar("T")


class InFlight(Generic[T]):
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[T]] = {}
        self.leaders = 0  # calls that really ran the work
        self.followers = 0  # calls that shared someone else's work

    def __len__(self) -> int:
        return len(self._tasks)

    async def run(self, key: str, work: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """Run work() once per key at a time. Returns (result, shared); shared=True for followers."""
        task = self._tasks.get(key)
        shared = task is not None
        if task is None:
            task = asyncio.create_task(work())
            self._tasks[key] = task
            task.add_done_callback(lambda done: self._forget(key, done))
            self.leaders += 1
        else:
            self.followers += 1
        return await asyncio.shield(task), shared

    async def aclose(self) -> None:
        """On shutdown: let calls still in flight finish before their HTTP clients are closed."""
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def _forget(self, key: str, task: asyncio.Task[T]) -> None:
        if self._tasks.get(key) is task:  # only remove our own entry
            del self._tasks[key]
        # Mark the error as seen: if every waiter gave up, asyncio would otherwise warn
        # "Task exception was never retrieved".
        if not task.cancelled() and task.exception() is not None:
            logger.debug("Shared request %s failed: %r", key, task.exception())
