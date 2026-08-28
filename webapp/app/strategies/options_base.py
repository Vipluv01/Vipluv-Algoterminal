"""Shared shape for the 4 multi-leg options strategies -- the options-
specific analogue of app/strategies/base.py's single-instrument Signal/
MarketSnapshot and pairs_cointegration.py's PairSnapshot/PairSignal.

A leg is priced and filled through app/options/execution.py's BSM model,
never bourse's Go engine (see that module's own docstring) -- so a leg
carries what BSM pricing needs (strike, option_type) rather than an
order_type/px the way base.Signal does; there is no book for a limit price
to rest against.

Position duration is tracked in TWO deliberately separate units, not one
overloaded field: `hold_bars` (backtest-only, path bars -- see
app/backtest/adapters.OptionsBacktestAdapter) and `hold_days` (live-only,
real wall-clock days since entry -- see strategy_runner._run_options).
Backtest bars and live seconds are already different time domains
elsewhere in this codebase (app/execution/slicer.py's bar-based scheduling
never runs during a backtest, and vice versa); reusing one field across
both here would silently conflate them the same way a "5" that means 5
bars in one context and 5 real days in another always eventually will.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from app.quant.black_scholes import OptionType
from app.strategies.base import Signal

OptionSide = Literal["buy", "sell"]
OptionsPosition = Literal["none", "open"]


@dataclass(frozen=True)
class OptionLegSignal:
    option_type: OptionType
    side: OptionSide
    strike: float
    qty: int
    reason: str
    # Which of the two real, currently-live expiries (app/options/chain.
    # list_expiries) this leg trades in LIVE dispatch -- meaningless in a
    # backtest, which has no real calendar and uses expiry_bars instead.
    expiry_kind: Literal["weekly", "monthly"] = "weekly"
    # Bars until this leg's SYNTHETIC (backtest-only) expiry, from the bar
    # it's entered on -- meaningless in live trading.
    expiry_bars: int = 0


@dataclass(frozen=True)
class OptionsSnapshot:
    underlying: str
    spot: float
    spot_history: np.ndarray
    position: OptionsPosition = "none"
    # Exactly what's currently held, when position == "open" -- the
    # options-multi-leg analogue of PairSnapshot.position_qty_a: closing
    # must unwind exactly what was opened (at the SAME strikes), not
    # freshly-recomputed ATM strikes that may have drifted since entry.
    open_legs: tuple[OptionLegSignal, ...] = ()
    # Both CALLER-computed (strategy_runner._run_options for live,
    # OptionsBacktestAdapter for backtest), not derived inside
    # evaluate_options -- only the caller knows whether ITS OWN notion of
    # "how long has this been open" is in bars or real days (see this
    # module's docstring), so each strategy's own hold_bars/hold_days (or
    # rebalance_bars/rebalance_days, for delta_neutral) is read directly
    # off the strategy instance by whichever caller applies in that unit,
    # and the boolean RESULT of that comparison is what crosses into
    # evaluate_options -- never the raw duration itself.
    should_exit: bool = False
    should_rebalance: bool = False
    # Shares of `underlying` currently held as a delta hedge -- only
    # delta_neutral.py reads this (every other strategy ignores it); kept
    # generic here rather than a delta_neutral-specific snapshot subclass
    # since it's the same "caller tracks position, strategy doesn't hide
    # it" data every other field on this snapshot already follows.
    current_hedge_qty: int = 0
    # Net, POSITION-SIGNED delta of the currently open option leg(s) --
    # e.g. a short call's contribution is NEGATIVE, already scaled by
    # qty*lot_size. Computed by the caller (strategy_runner._run_options
    # for live, using a real calendar expiry; OptionsBacktestAdapter for
    # backtest, using a bar-decayed one) rather than inside the strategy,
    # because computing it correctly needs to know which of those two time
    # domains applies -- something a strategy implementation deliberately
    # never knows (see app/strategies/base.py's own docstring on why a
    # strategy stays this narrow). 0.0 when flat.
    current_option_delta: float = 0.0


@dataclass(frozen=True)
class OptionsSignal:
    option_legs: list[OptionLegSignal]
    # (symbol, Signal) pairs for any equity hedge leg -- only delta_neutral
    # uses this; every other strategy returns an empty list. symbol is
    # always the strategy's own underlying (never an index -- see
    # delta_neutral.py's own docstring on why).
    equity_legs: list[tuple[str, Signal]] = field(default_factory=list)
    new_position: OptionsPosition = "open"


def close_open_legs(open_legs: tuple[OptionLegSignal, ...], reason: str) -> list[OptionLegSignal]:
    """Flips every currently-open leg's side -- the ONE shared "unwind
    exactly what's open" implementation every strategy's exit path calls,
    rather than four separate copies of the same flip logic."""
    return [
        OptionLegSignal(
            option_type=leg.option_type, side=("sell" if leg.side == "buy" else "buy"),
            strike=leg.strike, qty=leg.qty, reason=reason,
            expiry_kind=leg.expiry_kind, expiry_bars=leg.expiry_bars,
        )
        for leg in open_legs
    ]
