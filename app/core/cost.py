"""
Cost tracking: turn token counts into US dollars using the price table in config.

Prices are list prices per 1 million tokens, with separate input (prompt) and output
(completion) prices, because providers charge more for output.
A model missing from the table gives None ("unknown") instead of 0, so an unpriced model
never silently looks free in the stats.
"""
import logging

logger = logging.getLogger("gateway.cost")

# model name -> (input USD per 1M tokens, output USD per 1M tokens)
PriceTable = dict[str, tuple[float, float]]

_warned: set[str] = set()  # warn once per unknown model, not on every request


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int, prices: PriceTable) -> float | None:
    price = prices.get(model)
    if price is None:
        if model not in _warned:
            _warned.add(model)
            logger.warning("No price for model %r; its cost is logged as unknown", model)
        return None
    input_price, output_price = price
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000
