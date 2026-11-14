"""What the caches store: a finished answer plus what it cost, so a cache hit can report money saved."""
from dataclasses import asdict, dataclass

from app.providers.base import ProviderResponse


@dataclass(frozen=True)
class CachedAnswer:
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None  # what the original call cost (None = model has no price)

    @classmethod
    def from_response(cls, result: ProviderResponse, cost_usd: float | None) -> "CachedAnswer":
        return cls(
            result.text, result.provider, result.model, result.prompt_tokens, result.completion_tokens, cost_usd
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CachedAnswer":
        return cls(**{name: data[name] for name in cls.__dataclass_fields__})
