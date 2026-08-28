"""Calendar Spread: long the near-month option, short the far-month option,
same strike (ATM) -- profits from the near leg's faster theta decay
relative to the far leg's, and from vega exposure to a rise in implied
vol. The only one of the 4 options strategies whose two legs sit at
DIFFERENT expiries (every other strategy's legs all share one expiry).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.options.chain import strike_step
from app.strategies.options_base import OptionLegSignal, OptionsSignal, OptionsSnapshot, close_open_legs


@dataclass
class CalendarSpreadStrategy:
    key: str = "calendar_spread"
    name: str = "Calendar Spread (near long / far short, same strike)"
    underlying: str = "NIFTY50"
    option_type: str = "CE"
    qty: int = 1
    # 10/5/25 real trading days at 1 bar == 1 simulated second (see
    # app.backtest.adapters.BACKTEST_BARS_PER_YEAR): 6.5*3600 == 23,400
    # bars/day.
    hold_bars: int = 234_000  # 10 days
    hold_days: float = 10.0
    # Backtest-only synthetic expiries for the two legs -- the near leg
    # expires (and would need closing) well before the far one; hold_bars
    # above is deliberately shorter than far_expiry_bars, so this strategy
    # always exits on its own schedule rather than ever running into the
    # near leg's synthetic expiry unmanaged.
    near_expiry_bars: int = 117_000  # 5 days -- "weekly"
    far_expiry_bars: int = 585_000   # 25 days -- "monthly"

    def evaluate_options(self, snap: OptionsSnapshot) -> OptionsSignal | None:
        if snap.underlying != self.underlying:
            raise ValueError(f"{self.key} trades {self.underlying}, got snapshot for {snap.underlying}")
        if snap.position == "none":
            return self._enter(snap)
        if snap.should_exit:
            legs = close_open_legs(snap.open_legs, reason=f"{self.key}: holding period elapsed, closing")
            return OptionsSignal(option_legs=legs, new_position="none")
        return None

    def _enter(self, snap: OptionsSnapshot) -> OptionsSignal:
        step = strike_step(self.underlying, snap.spot)
        atm_strike = round(snap.spot / step) * step
        legs = [
            OptionLegSignal(
                option_type=self.option_type, side="buy", strike=atm_strike, qty=self.qty,
                reason=f"{self.key} entry: long near-month @ {atm_strike:g}",
                expiry_kind="weekly", expiry_bars=self.near_expiry_bars,
            ),
            OptionLegSignal(
                option_type=self.option_type, side="sell", strike=atm_strike, qty=self.qty,
                reason=f"{self.key} entry: short far-month @ {atm_strike:g}",
                expiry_kind="monthly", expiry_bars=self.far_expiry_bars,
            ),
        ]
        return OptionsSignal(option_legs=legs, new_position="open")
