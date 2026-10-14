"""Odds math: American <-> decimal, implied probability, parlays, payouts, no-vig."""
from math import prod


def to_decimal(american: float) -> float:
    a = float(american)
    return 1 + a / 100 if a > 0 else 1 + 100 / abs(a)


def to_american(decimal: float) -> int:
    d = float(decimal)
    if d <= 1:
        raise ValueError("decimal odds must be > 1")
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def implied(american: float) -> float:
    return 1 / to_decimal(american)


def no_vig(a: float, b: float) -> tuple[float, float]:
    """Fair probabilities for a two-way market after removing the book's margin."""
    pa, pb = implied(a), implied(b)
    return pa / (pa + pb), pb / (pa + pb)


def parlay_american(legs_american: list[float]) -> int:
    return to_american(prod(to_decimal(x) for x in legs_american))


def to_win(stake: float, american: float) -> float:
    return round(stake * (to_decimal(american) - 1), 2)


def edge(model_prob: float, american: float) -> float:
    """Expected profit per $1 staked."""
    return model_prob * (to_decimal(american) - 1) - (1 - model_prob)


def kelly(model_prob: float, american: float, fraction: float = 0.25) -> float:
    b = to_decimal(american) - 1
    f = (b * model_prob - (1 - model_prob)) / b
    return max(0.0, f * fraction)
