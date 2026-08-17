"""Token → USD estimation for run cost reporting (docs/07 §3).

Rates are list prices per million tokens and change over time — they are an
estimate for the UI and internal margin tracking, never a billing source of
truth. Cache reads bill at ~10% of the input rate; batch requests bill at 50%
of everything (applied by the caller, which knows the transport).
"""

# model id -> (input $/MTok, output $/MTok)
RATES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
}

DEFAULT_RATE = (3.00, 15.00)
CACHE_READ_MULTIPLIER = 0.1
MILLION = 1_000_000


def estimate_usd(detail: dict) -> float:
    """Full-price estimate for one recorded call (before any batch discount)."""
    input_rate, output_rate = RATES.get(detail.get("model", ""), DEFAULT_RATE)
    fresh_input = int(detail.get("input_tokens") or 0)
    cache_read = int(detail.get("cache_read_tokens") or 0)
    output = int(detail.get("output_tokens") or 0)
    return (
        fresh_input * input_rate
        + cache_read * input_rate * CACHE_READ_MULTIPLIER
        + output * output_rate
    ) / MILLION
