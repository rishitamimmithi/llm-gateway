"""
What every rate limiter returns, so /chat doesn't care which algorithm (or store) is behind it.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int  # requests the client can still make right now
    retry_after: float  # seconds until the next request would be allowed (0 when allowed)


class RateLimiter(Protocol):
    async def check(self, key: str) -> Decision:
        """Count one request for `key` and say whether it may go through."""
        ...

    async def aclose(self) -> None:
        ...
