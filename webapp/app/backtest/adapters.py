"""Adapts the LIVE strategy objects (the ones app/strategy_runner.py's
tick loop actually drives) onto one uniform interface the backtest engine
can step through bar by bar.

Deliberately adapters, not a rewrite: strategy_runner.py depends on the
live `Strategy` protocol (app/strategies/base.py, one symbol) and the
pairs-specific `evaluate_pair` shape (app/strategies/pairs_cointegration.py,
two symbols) exactly as they are, already tested against real fills in the
live tick loop. Forcing every strategy to also implement some new unified
"BaseStrategy" interface would mean either maintaining two parallel
implementations per strategy (real drift risk) or rewriting the live path
to match a backtest-shaped abstraction it doesn't need. An adapter per
STRATEGY SHAPE (single-instrument, pairs, basket) is the smaller, safer
change: one canonical strategy implementation, wrapped for backtesting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np

from app.backtest.paths import MultiAssetHistory
from app.markets import DERIVED_INDICES
from app.options.chain import RISK_FREE_RATE, realized_vol_annualized, smile_iv
from app.quant.black_scholes import bsm_greeks, bsm_price
from app.strategies.base import MarketSnapshot
from app.strategies.options_base import OptionLegSignal, OptionsSnapshot, close_open_legs


@dataclass(frozen=True)
class BacktestOrder:
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    reason: str = ""


class BacktestStrategy(Protocol):
    """The uniform interface run_backtest drives -- distinct from (and
    deliberately narrower than) the live `Strategy` protocol: no order
    type, no limit price, no `MarketSnapshot`/`PairSnapshot` shape leaking
    through. A backtest bar always fills at that bar's close (see
    engine.py's fee model), so a limit price has nothing to mean here.
    """

    key: str

    def evaluate(self, history: MultiAssetHistory, step: int) -> list[BacktestOrder]:
        """step is the bar currently closing -- implementations must only
        look at history.close_series(symbol, upto=step + 1), never further
        ahead, matching how strategy_runner.py only ever sees prices up to
        the current tick."""
        ...

    def reset(self) -> None:
        """Clears any position state carried between bars WITHIN one
        backtest run, so the same adapter instance can be reused across
        MULTIPLE independent Monte Carlo paths (run_monte_carlo does
        exactly this) without one path's ending position silently leaking
        into the next path's starting state -- a real bug this interface
        exists to prevent: PairsAdapter/BasketAdapter track which side of
        a spread/basket they're holding across evaluate() calls, and
        without an explicit reset, path 2 would start already "holding" a
        position that only ever existed in path 1's synthetic history.
        """
        ...


class SingleInstrumentAdapter:
    """Wraps any live `Strategy` (alpha_rsi_ema, momentum_macd,
    mean_reversion_bb, vwap_reversion, bb_squeeze) fixed to one symbol."""

    def __init__(self, strategy, symbol: str):
        self.key = strategy.key
        self._strategy = strategy
        self._symbol = symbol

    def evaluate(self, history: MultiAssetHistory, step: int) -> list[BacktestOrder]:
        prices = history.close_series(self._symbol, upto=step + 1)
        volumes = history.paths[self._symbol].volume[: step + 1]
        signal = self._strategy.evaluate(MarketSnapshot(symbol=self._symbol, prices=prices, volumes=volumes))
        if signal is None:
            return []
        return [BacktestOrder(symbol=self._symbol, side=signal.side, qty=signal.qty, reason=signal.reason)]

    def reset(self) -> None:
        pass  # stateless: the wrapped Strategy recomputes everything from
              # the full prices array on every call, nothing to clear


class PairsAdapter:
    """Wraps any live pairs strategy exposing evaluate_pair(PairSnapshot)
    -> a PairSignal-shaped result (signal_a, signal_b, new_position) --
    both pairs_cointegration and pairs_kelly share this exact shape (see
    app/strategies/pairs_kelly.py).

    Tracks its OWN position state across bars (long_spread / short_spread
    / none), the same "caller tracks position, strategy doesn't hide it"
    discipline pairs_cointegration.py's own docstring establishes for the
    live path -- in strategy_runner.py that state comes from querying
    filled Order rows; here, since there is no live DB, it is exactly
    what THIS adapter itself has been submitting, tracked directly.
    """

    def __init__(self, strategy, symbol_a: str, symbol_b: str):
        self.key = strategy.key
        self._strategy = strategy
        self._symbol_a = symbol_a
        self._symbol_b = symbol_b
        self._position = "none"
        self._position_qty_a = 0

    def reset(self) -> None:
        self._position = "none"
        self._position_qty_a = 0

    def evaluate(self, history: MultiAssetHistory, step: int) -> list[BacktestOrder]:
        from app.strategies.pairs_cointegration import PairSnapshot

        a = history.close_series(self._symbol_a, upto=step + 1)
        b = history.close_series(self._symbol_b, upto=step + 1)
        result = self._strategy.evaluate_pair(PairSnapshot(
            symbol_a=self._symbol_a, symbol_b=self._symbol_b,
            prices_a=a, prices_b=b, position=self._position,
            position_qty_a=self._position_qty_a,
        ))
        if result is None:
            return []

        # Track what leg A's own signal actually does to the held quantity
        # -- an entry (position was "none") ESTABLISHES it, a close (new
        # position is "none") CLEARS it. This is what lets a variably-sized
        # strategy like pairs_kelly close exactly what it opened next time,
        # via PairSnapshot.position_qty_a above.
        if self._position == "none" and result.new_position != "none" and result.signal_a is not None:
            self._position_qty_a = result.signal_a.qty
        elif result.new_position == "none":
            self._position_qty_a = 0
        self._position = result.new_position

        orders = []
        if result.signal_a is not None:
            orders.append(BacktestOrder(self._symbol_a, result.signal_a.side, result.signal_a.qty, result.signal_a.reason))
        if result.signal_b is not None:
            orders.append(BacktestOrder(self._symbol_b, result.signal_b.side, result.signal_b.qty, result.signal_b.reason))
        return orders


class BasketAdapter:
    """Wraps a basket strategy exposing evaluate_basket(BasketSnapshot) ->
    a BasketSignal-shaped result (leg_signals: dict[symbol, Signal],
    new_position) -- see app/strategies/multi_basket.py. Position tracking
    mirrors PairsAdapter's, generalized from 2 legs to N."""

    def __init__(self, strategy, symbols: tuple[str, ...]):
        self.key = strategy.key
        self._strategy = strategy
        self._symbols = symbols
        self._position = "none"

    def reset(self) -> None:
        self._position = "none"

    def evaluate(self, history: MultiAssetHistory, step: int) -> list[BacktestOrder]:
        from app.strategies.multi_basket import BasketSnapshot

        prices = {sym: history.close_series(sym, upto=step + 1) for sym in self._symbols}
        result = self._strategy.evaluate_basket(BasketSnapshot(
            symbols=self._symbols, prices=prices, position=self._position,
        ))
        if result is None:
            return []
        self._position = result.new_position

        return [
            BacktestOrder(sym, sig.side, sig.qty, sig.reason)
            for sym, sig in result.leg_signals.items()
        ]


# A backtest bar is one registry.step_all() call -- one simulated SECOND
# (see app/backtest/engine.py's own BARS_PER_YEAR comment: this matches
# sim/bourse_sim/fundamental.py's own dt exactly, and matches live's
# real MARKET_TICK_SECONDS=1.0 cadence). An EARLIER version of this module
# invented its own, unrelated "40 bars/day" convention purely for options
# T-decay -- a second clock, silently disagreeing with the one
# realized_vol_annualized below (and app.backtest.engine's own Sharpe/
# Calmar annualization) actually use for the SAME underlying bar sequence.
# That mismatch is now fixed by sharing exactly one clock for the whole
# backtest domain: BACKTEST_BARS_PER_YEAR IS app.backtest.engine.
# BARS_PER_YEAR, not a second number that happens to be close to it.
#
# Real consequence, stated plainly rather than hidden: a genuinely
# multi-day options strategy (short_strangle's default hold is ~5 REAL
# days) now needs hold_bars on the order of 5*6.5*3600 ~= 117,000, not the
# 200 an earlier, wrong-clock version used -- see each of the 4 options
# strategies' own hold_bars/expiry_bars defaults, updated to match. A
# quick verification-scale sweep (a few thousand bars) will correctly hit
# insufficient_horizon for all four: a few thousand SIMULATED SECONDS
# (minutes) genuinely cannot evaluate a multi-day options strategy, in any
# microstructure-honest simulation, regardless of how well volatility is
# calibrated. That's the guard working as designed, not a new gap.
#
# Not imported from app.backtest.engine (which already imports THIS module
# for BacktestStrategy -- importing back would be circular): defined
# independently with the identical value/formula instead. Keep both
# literally in sync if either ever changes.
BACKTEST_BARS_PER_YEAR = 252 * 6.5 * 3600  # == app.backtest.engine.BARS_PER_YEAR
MIN_T_YEARS = 1.0 / BACKTEST_BARS_PER_YEAR  # floor at ~1 bar's worth of time -- bsm_price/bsm_greeks raise on T<=0


def _bars_remaining_T(expiry_bars: int, entry_step: int, step: int) -> float:
    elapsed = step - entry_step
    remaining_bars = max(expiry_bars - elapsed, 0)
    return max(remaining_bars / BACKTEST_BARS_PER_YEAR, MIN_T_YEARS)


class OptionsBacktestAdapter:
    """Wraps one of the 4 multi-leg options strategies (app/strategies/
    options_base.py) for Monte Carlo backtesting -- iron_condor,
    calendar_spread, short_strangle, delta_neutral.

    Every leg is priced through the exact same BSM model live trading uses
    (app/options/chain.py's smile + app/quant/black_scholes.bsm_price),
    computed directly from the underlying's own price PATH rather than a
    pre-generated per-contract path: an option contract key is never one
    of the finitely-many pre-generated equity symbols (there are
    unboundedly many possible strikes), so there is nothing to look up in
    path.paths for one. See engine.py's _resolve_price and this class's
    own mark_price() for how a strategy opts into supplying its own prices
    instead of a path lookup.

    A synthetic contract SYMBOL here encodes everything mark_price needs
    to reprice it at ANY bar (underlying, entry step, expiry_bars, strike,
    option_type) -- deliberately so mark_price never depends on this
    adapter's OWN mutable position state. That matters because
    _mark_to_market_equity_curve runs as a SEPARATE second pass over every
    bar, AFTER _build_orders has already advanced this adapter's state to
    the end of the whole backtest; a mark_price that read self._open_legs
    would silently price every historical bar using the FINAL cycle's
    legs, not whatever was actually open at that bar.
    """

    def __init__(self, strategy):
        self.key = strategy.key
        self._strategy = strategy
        self._underlying = strategy.underlying
        # Proxied onto the adapter itself (not just self._strategy) so
        # app.backtest.monte_carlo.run_monte_carlo can check
        # getattr(adapter, "hold_bars", None) generically, without needing
        # to know this adapter wraps a strategy object at all -- the same
        # reason mark_price/force_close are adapter-level methods rather
        # than something the caller reaches through to self._strategy for.
        self.hold_bars = getattr(strategy, "hold_bars", None)
        self.reset()

    def reset(self) -> None:
        self._position: Literal["none", "open"] = "none"
        self._open_legs: tuple[OptionLegSignal, ...] = ()
        self._entry_step: int | None = None
        self._last_rebalance_step: int | None = None
        self._hedge_qty = 0

    def _spot_at(self, history: MultiAssetHistory, step: int) -> float:
        weights = DERIVED_INDICES.get(self._underlying)
        if weights is not None:
            return sum(w * float(history.paths[sym].close[step]) for sym, w in weights.items())
        return float(history.paths[self._underlying].close[step])

    def _price_history_at(self, history: MultiAssetHistory, step: int) -> np.ndarray:
        """Full price history for this adapter's own underlying, up to
        and including `step` -- the backtest-domain analogue of
        app.markets.MarketRegistry.price_history_for, needed so _atm_sigma
        below can measure REALIZED volatility from data the strategy could
        actually have observed by this bar (never a look-ahead)."""
        weights = DERIVED_INDICES.get(self._underlying)
        if weights is not None:
            return sum(w * history.close_series(sym, upto=step + 1) for sym, w in weights.items())
        return history.close_series(self._underlying, upto=step + 1)

    def _atm_sigma(self, history: MultiAssetHistory, step: int) -> float:
        """The ATM vol every BSM call in this adapter uses -- calibrated
        from this underlying's OWN realized volatility up to `step`,
        annualized via BACKTEST_BARS_PER_YEAR (the SAME clock
        _bars_remaining_T uses for time-to-expiry). This replaced a fixed
        IV_SIGMA0=0.18 constant that was a real, serious bug: measured
        directly, this simulation's own per-bar realized vol annualizes to
        well under 1% under this clock, a ~200x mismatch against a fixed
        18% pricing input that made every short-premium strategy
        (short_strangle, iron_condor) a near-guaranteed win and every
        long-premium leg (calendar_spread's near leg, delta_neutral's
        hedge cost) a near-guaranteed bleed -- exactly the kind of
        indefensible, too-good-to-be-true backtest number this whole
        Monte Carlo harness exists to prevent. See
        app.options.chain.realized_vol_annualized's own docstring.
        """
        prices = self._price_history_at(history, step)
        return realized_vol_annualized(prices, BACKTEST_BARS_PER_YEAR)

    def _leg_symbol(self, leg: OptionLegSignal, entry_step: int) -> str:
        return f"{self._underlying}#{entry_step}#{leg.expiry_bars}#{leg.strike:g}{leg.option_type}"

    def evaluate(self, history: MultiAssetHistory, step: int) -> list[BacktestOrder]:
        strategy = self._strategy
        spot = self._spot_at(history, step)

        should_exit = False
        should_rebalance = False
        current_option_delta = 0.0
        if self._position == "open":
            elapsed = step - self._entry_step
            hold_bars = getattr(strategy, "hold_bars", None)
            should_exit = hold_bars is not None and elapsed >= hold_bars
            rebalance_bars = getattr(strategy, "rebalance_bars", None)
            if not should_exit and rebalance_bars is not None:
                since_last = step - (self._last_rebalance_step if self._last_rebalance_step is not None else self._entry_step)
                should_rebalance = since_last >= rebalance_bars
            lot_size = getattr(strategy, "lot_size", 1)
            sigma0 = self._atm_sigma(history, step)
            for leg in self._open_legs:
                T = _bars_remaining_T(leg.expiry_bars, self._entry_step, step)
                iv = smile_iv(leg.strike, spot, sigma0)
                g = bsm_greeks(spot, leg.strike, T, RISK_FREE_RATE, iv, leg.option_type)
                sign = 1 if leg.side == "buy" else -1
                current_option_delta += sign * g.delta * leg.qty * lot_size

        underlying_in_path = self._underlying not in DERIVED_INDICES
        spot_history = (
            history.close_series(self._underlying, upto=step + 1) if underlying_in_path else np.array([spot])
        )

        snapshot = OptionsSnapshot(
            underlying=self._underlying, spot=spot, spot_history=spot_history,
            position=self._position, open_legs=self._open_legs,
            should_exit=should_exit, should_rebalance=should_rebalance,
            current_hedge_qty=self._hedge_qty, current_option_delta=current_option_delta,
        )
        result = strategy.evaluate_options(snapshot)
        if result is None:
            return []

        is_entry = self._position == "none"
        entry_step = step if is_entry else self._entry_step

        orders = [
            BacktestOrder(symbol=self._leg_symbol(leg, entry_step), side=leg.side, qty=leg.qty, reason=leg.reason)
            for leg in result.option_legs
        ]
        orders.extend(
            BacktestOrder(symbol=symbol, side=sig.side, qty=sig.qty, reason=sig.reason)
            for symbol, sig in result.equity_legs
        )

        if result.new_position == "open":
            if is_entry:
                self._open_legs = tuple(result.option_legs)
                self._entry_step = step
            self._position = "open"
            if result.equity_legs:
                for _, sig in result.equity_legs:
                    self._hedge_qty += sig.qty if sig.side == "buy" else -sig.qty
                self._last_rebalance_step = step
        else:
            self._position = "none"
            self._open_legs = ()
            self._entry_step = None
            self._last_rebalance_step = None
            self._hedge_qty = 0

        return orders

    def mark_price(self, symbol: str, history: MultiAssetHistory, step: int) -> float | None:
        """Stateless by construction (see this class's own docstring) --
        every input this needs is decoded straight out of `symbol`
        itself, never read from self._open_legs."""
        if "#" not in symbol:
            return None  # an equity hedge leg (delta_neutral) -- fall back to the normal path lookup
        # This adapter only ever produces symbols for ITS OWN underlying
        # (see _leg_symbol) -- the leading component is decoded only to
        # keep the format self-describing, not because a different value
        # is ever expected here.
        _underlying, entry_step_s, expiry_bars_s, rest = symbol.split("#", 3)
        option_type = rest[-2:]
        strike = float(rest[:-2])
        entry_step = int(entry_step_s)
        expiry_bars = int(expiry_bars_s)

        spot = self._spot_at(history, step)
        T = _bars_remaining_T(expiry_bars, entry_step, step)
        iv = smile_iv(strike, spot, self._atm_sigma(history, step))
        return bsm_price(spot, strike, T, RISK_FREE_RATE, iv, option_type)

    def force_close(self, history: MultiAssetHistory, step: int) -> list[BacktestOrder]:
        """Called ONCE by engine.py's _build_orders, after the main per-bar
        loop, if this adapter is still holding a position at the very last
        bar -- unwinds every open option leg AND the equity hedge (if any),
        so compute_realizations/trade_count reflect a complete, honest
        picture rather than a position that stays open forever with
        nothing ever realized (see app.backtest.monte_carlo.run_monte_carlo's
        own insufficient_horizon guard for the companion fix: a strategy
        whose hold_bars is at least as long as the whole path is skipped
        entirely rather than relying on this to paper over it).

        This does NOT change the equity curve's shape (already
        marked-to-market every bar via mark_price) and does NOT by itself
        fix a degenerate Sharpe/Calmar -- see engine.py's
        MAX_PLAUSIBLE_ANNUALIZED_RATIO guard for that. It only completes
        the REALIZED trade-level accounting.
        """
        if self._position != "open":
            return []
        entry_step = self._entry_step
        closing_legs = close_open_legs(self._open_legs, reason=f"{self.key}: force-closed at path end")
        orders = [
            BacktestOrder(symbol=self._leg_symbol(leg, entry_step), side=leg.side, qty=leg.qty, reason=leg.reason)
            for leg in closing_legs
        ]
        if self._hedge_qty != 0:
            side = "sell" if self._hedge_qty > 0 else "buy"
            orders.append(BacktestOrder(
                symbol=self._underlying, side=side, qty=abs(self._hedge_qty),
                reason=f"{self.key}: force-closing delta hedge at path end",
            ))
        self._position = "none"
        self._open_legs = ()
        self._entry_step = None
        self._last_rebalance_step = None
        self._hedge_qty = 0
        return orders
