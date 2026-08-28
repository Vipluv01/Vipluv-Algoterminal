"""Short Strangle: sells one OTM put and one OTM call on a high-IV
underlying, collecting premium on both sides and profiting if the
underlying stays between the two short strikes through the holding period.
The simplest 2-leg premium-selling structure in this set -- naked
(undefined-risk) on both sides in a REAL market; this project's own risk
controls (max_order_qty, per-order qty) already bound the notional here the
same way they bound every other strategy's position size, and there is no
additional defined-risk wing the way iron_condor.py adds.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.options.chain import strike_step
from app.strategies.options_base import OptionLegSignal, OptionsSignal, OptionsSnapshot, close_open_legs


@dataclass
class ShortStrangleStrategy:
    key: str = "short_strangle"
    name: str = "Short Strangle (premium selling)"
    # Among this project's 2 synthetic indices, BANKNIFTY is the higher-
    # notional, wider-strike-step one -- used here as the spec's "high-IV
    # underlying." There is no real per-underlying realized-vol difference
    # in this synthetic system to pick one by (app/options/chain.py's IV
    # smile constants are the same for every underlying), so this is a
    # documented, fixed choice, not a measured one.
    underlying: str = "BANKNIFTY"
    otm_steps: int = 4  # how many strike-steps OTM each short leg sits
    qty: int = 1
    expiry_kind: str = "weekly"
    # 5 REAL trading days at 1 bar == 1 simulated second (see
    # app.backtest.adapters.BACKTEST_BARS_PER_YEAR's own comment on why a
    # bar is a second, not a day): 5 * 6.5 * 3600 == 117,000. hold_bars was
    # 200 before that clock was corrected -- 200 seconds is 3.3 minutes,
    # nowhere near hold_days' own stated 5-day intent.
    hold_bars: int = 117_000   # backtest -- read directly by OptionsBacktestAdapter
    hold_days: float = 5.0     # live -- read directly by strategy_runner._run_options

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
        put_strike = snap.spot - self.otm_steps * step
        call_strike = snap.spot + self.otm_steps * step
        legs = [
            OptionLegSignal(
                option_type="PE", side="sell", strike=put_strike, qty=self.qty,
                reason=f"{self.key} entry: short OTM put @ {put_strike:g}",
                expiry_kind=self.expiry_kind, expiry_bars=self.hold_bars,
            ),
            OptionLegSignal(
                option_type="CE", side="sell", strike=call_strike, qty=self.qty,
                reason=f"{self.key} entry: short OTM call @ {call_strike:g}",
                expiry_kind=self.expiry_kind, expiry_bars=self.hold_bars,
            ),
        ]
        return OptionsSignal(option_legs=legs, new_position="open")
