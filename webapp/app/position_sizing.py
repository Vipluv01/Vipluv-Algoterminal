"""Kelly Criterion position sizing -- the "Fractional Kelly 25%" execution
model shown in Vipluv's own prior Algo Terminal's screen recording.

Uses the classic win-rate/win-loss-ratio form (Kelly 1956), not the
continuous mu/sigma^2 form: strategy performance here is naturally tracked
as win rate + average win/loss (see Order rows -- filled_qty, avg_fill_px),
so this form needs no extra return-distribution assumptions on top of data
already being collected anyway.
"""

from __future__ import annotations

from dataclasses import dataclass


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """f* = p - q/b, where p = win_rate, q = 1-p, b = avg_win / avg_loss
    (avg_loss as a positive magnitude -- the size of a typical losing
    trade, not a signed number).

    Classic textbook check: a fair coin flip (p=0.5) paying 2:1 (b=2) has
    f*=0.25 -- bet a quarter of the bankroll. Verified directly in
    test_position_sizing.py against exactly that case, not just against
    this docstring's claim.
    """
    if not (0.0 <= win_rate <= 1.0):
        raise ValueError(f"win_rate must be in [0, 1], got {win_rate}")
    if avg_win <= 0 or avg_loss <= 0:
        raise ValueError("avg_win and avg_loss must both be positive magnitudes")

    b = avg_win / avg_loss
    p = win_rate
    q = 1.0 - p
    f = p - q / b
    # A negative or zero edge means "this isn't a bet worth sizing up for",
    # not "bet a negative fraction" (which would mean betting the other
    # way, which isn't what this strategy's signal says to do). Clip to 0
    # rather than letting a bad recent stretch flip a strategy's sign.
    return max(0.0, f)


@dataclass(frozen=True)
class SizingResult:
    kelly_fraction: float          # full Kelly, uncapped
    applied_fraction: float        # after the fractional multiplier
    position_value: float          # currency
    qty: int


def size_position(
    *,
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    account_value: float,
    price: float,
    kelly_multiplier: float = 0.25,
    max_position_fraction: float = 0.5,
) -> SizingResult:
    """kelly_multiplier=0.25 ("fractional Kelly") matches the 25% shown in
    the prior terminal's Execution Model panel -- full Kelly is provably
    growth-optimal but notoriously high-variance in practice (a single bad
    estimate of win_rate/avg_win/avg_loss gets amplified), so scaling it
    down is standard practice, not a hedge against this implementation
    being wrong.

    max_position_fraction is a hard ceiling independent of whatever Kelly
    computes -- a estimation error (thin trade history, one lucky streak)
    could otherwise size an oversized position with total confidence math
    that LOOKS rigorous. This is the same instinct as bourse's own
    Config.PositionLimit: a risk check that doesn't trust a single
    calculation to bound itself.
    """
    f_star = kelly_fraction(win_rate, avg_win, avg_loss)
    applied = min(f_star * kelly_multiplier, max_position_fraction)
    position_value = account_value * applied
    qty = int(position_value // price) if price > 0 else 0
    return SizingResult(kelly_fraction=f_star, applied_fraction=applied, position_value=position_value, qty=qty)
