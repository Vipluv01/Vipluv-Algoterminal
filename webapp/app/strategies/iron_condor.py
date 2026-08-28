"""Iron Condor: sells an OTM put spread and an OTM call spread on NIFTY50
-- 4 legs total (sell near put, buy far put, sell near call, buy far call).
Unlike short_strangle.py's naked strangle, every short leg here has a
further-OTM long leg protecting it, so the structure's max loss is capped
(the width between each spread's two strikes, minus the net premium
collected) rather than theoretically unbounded.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.options.chain import strike_step
from app.strategies.options_base import OptionLegSignal, OptionsSignal, OptionsSnapshot, close_open_legs


@dataclass
class IronCondorStrategy:
    key: str = "iron_condor"
    name: str = "Iron Condor (defined-risk premium selling)"
    underlying: str = "NIFTY50"
    short_otm_steps: int = 3   # the two SOLD legs (nearer to spot, more premium)
    long_otm_steps: int = 6    # the two BOUGHT protective legs (further out)
    qty: int = 1
    expiry_kind: str = "weekly"
    # 7 real trading days at 1 bar == 1 simulated second: 7*6.5*3600 ==
    # 163,800 -- see app.backtest.adapters.BACKTEST_BARS_PER_YEAR.
    hold_bars: int = 163_800
    hold_days: float = 7.0

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
        short_put = snap.spot - self.short_otm_steps * step
        long_put = snap.spot - self.long_otm_steps * step
        short_call = snap.spot + self.short_otm_steps * step
        long_call = snap.spot + self.long_otm_steps * step

        def leg(option_type, side, strike, tag):
            return OptionLegSignal(
                option_type=option_type, side=side, strike=strike, qty=self.qty,
                reason=f"{self.key} entry: {tag} @ {strike:g}",
                expiry_kind=self.expiry_kind, expiry_bars=self.hold_bars,
            )

        legs = [
            leg("PE", "sell", short_put, "sell near put"),
            leg("PE", "buy", long_put, "buy far put (protection)"),
            leg("CE", "sell", short_call, "sell near call"),
            leg("CE", "buy", long_call, "buy far call (protection)"),
        ]
        return OptionsSignal(option_legs=legs, new_position="open")
