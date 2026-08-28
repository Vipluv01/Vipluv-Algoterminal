"""Delta Neutral: sells one ATM call and dynamically hedges it with the
underlying EQUITY, rebalancing the hedge as delta drifts with spot and
time -- the only one of the 4 options strategies with a genuine equity
leg, and the only one that trades a real, directly-tradable equity
underlying rather than an index.

Why an equity underlying, not NIFTY50/BANKNIFTY: the spec calls for
"equity/futures hedges", but app/routers/orders.py explicitly rejects
direct equity orders on a derived index (task 2 of this phase -- there is
nothing to buy/sell there), and this project has no futures instrument
anywhere. ICICIBANK options, hedged with ICICIBANK shares -- an instrument
this app can genuinely trade both legs of -- is the only underlying choice
that doesn't run straight into a constraint this same phase just added.

Deliberately does NOT compute Greeks itself: pricing an option correctly
needs a time-to-expiry, and "how much time is left" means something
different in live trading (a real calendar date, app/options/chain.py) than
in a backtest (a bar count, OptionsBacktestAdapter) -- exactly the same
domain split app/strategies/options_base.py's own docstring calls out for
hold_bars/hold_days. Rather than have this strategy guess which domain
it's running in, the caller supplies the open leg's current, correctly-
computed delta via OptionsSnapshot.current_option_delta; this strategy only
ever compares deltas and quantities, never prices anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.options.chain import strike_step
from app.strategies.base import Signal
from app.strategies.options_base import OptionLegSignal, OptionsSignal, OptionsSnapshot, close_open_legs

# A freshly-sold ATM call's delta is always close to 0.5 -- used ONLY to
# size the very first hedge, before any position/mark exists to derive an
# exact delta from. Every rebalance afterward uses the caller-supplied,
# exactly-computed snap.current_option_delta instead of this shortcut.
ATM_DELTA_APPROX = 0.5


def _round_half_up(x: float) -> int:
    """Python's builtin round() rounds half-to-EVEN (round(0.5) == 0), a
    real bug caught here directly: with the default qty=1/lot_size=1, the
    entry hedge's raw target is EXACTLY 0.5 shares, and round(0.5) silently
    produced 0 -- no hedge order at all, defeating a "delta-neutral"
    strategy's entire purpose for its own default single-contract case.
    Ordinary round-half-up is what a real hedge-sizing decision needs."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


@dataclass
class DeltaNeutralStrategy:
    key: str = "delta_neutral"
    name: str = "Delta-Neutral Short Call (equity-hedged)"
    underlying: str = "ICICIBANK"
    qty: int = 1  # option contracts
    lot_size: int = 1
    # 20/2 real trading days at 1 bar == 1 simulated second (see
    # app.backtest.adapters.BACKTEST_BARS_PER_YEAR): 6.5*3600 == 23,400
    # bars/day.
    hold_bars: int = 468_000       # 20 days
    hold_days: float = 20.0
    rebalance_bars: int = 46_800   # 2 days
    rebalance_days: float = 2.0
    # A hedge adjustment smaller than this many shares isn't worth its own
    # order -- without a floor, delta's continuous drift would demand a
    # fresh 1-share rebalance almost every eligible tick, which is not how
    # a real desk manages hedge friction/transaction costs.
    min_rebalance_shares: int = 2

    def evaluate_options(self, snap: OptionsSnapshot) -> OptionsSignal | None:
        if snap.underlying != self.underlying:
            raise ValueError(f"{self.key} trades {self.underlying}, got snapshot for {snap.underlying}")
        if snap.position == "none":
            return self._enter(snap)
        if snap.should_exit:
            return self._exit(snap)
        if snap.should_rebalance:
            return self._rebalance(snap)
        return None

    def _enter(self, snap: OptionsSnapshot) -> OptionsSignal:
        step = strike_step(self.underlying, snap.spot)
        atm_strike = round(snap.spot / step) * step
        leg = OptionLegSignal(
            option_type="CE", side="sell", strike=atm_strike, qty=self.qty,
            reason=f"{self.key} entry: short ATM call @ {atm_strike:g}",
            expiry_kind="monthly", expiry_bars=self.hold_bars,
        )
        hedge_qty = _round_half_up(self.qty * self.lot_size * ATM_DELTA_APPROX)
        equity_legs = []
        if hedge_qty > 0:
            equity_legs.append((self.underlying, Signal(
                side="buy", qty=hedge_qty, order_type="market", px=None,
                reason=f"{self.key} entry: initial delta hedge, buy {hedge_qty} shares",
            )))
        return OptionsSignal(option_legs=[leg], equity_legs=equity_legs, new_position="open")

    def _rebalance(self, snap: OptionsSnapshot) -> OptionsSignal | None:
        # current_option_delta is already negative (a short call), so the
        # hedge that offsets it is a LONG position of the same magnitude.
        target = _round_half_up(-snap.current_option_delta)
        diff = target - snap.current_hedge_qty
        if abs(diff) < self.min_rebalance_shares:
            return None
        side = "buy" if diff > 0 else "sell"
        equity_legs = [(self.underlying, Signal(
            side=side, qty=abs(diff), order_type="market", px=None,
            reason=f"{self.key} rebalance: {side} {abs(diff)} shares (target hedge {target})",
        ))]
        return OptionsSignal(option_legs=[], equity_legs=equity_legs, new_position="open")

    def _exit(self, snap: OptionsSnapshot) -> OptionsSignal:
        legs = close_open_legs(snap.open_legs, reason=f"{self.key}: holding period elapsed, closing")
        equity_legs = []
        if snap.current_hedge_qty > 0:
            equity_legs.append((self.underlying, Signal(
                side="sell", qty=snap.current_hedge_qty, order_type="market", px=None,
                reason=f"{self.key}: unwinding delta hedge",
            )))
        return OptionsSignal(option_legs=legs, equity_legs=equity_legs, new_position="none")
