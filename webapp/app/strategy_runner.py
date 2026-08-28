"""Evaluates enabled strategies against live market data each tick and
turns their signals into real (paper) orders.

market_maker is deliberately NOT one of the user-selectable strategies
here, even though it's one of the strategy inventory: it already runs
continuously inside every SymbolMarket (app/markets.py's own self.maker),
providing the liquidity backdrop the other strategies trade against. A
user "enabling" it as a personal strategy would mean something different
(running their own competing quoting bot) and needs its own continuous-
refresh execution model, not the periodic-signal-check loop the others
use -- tracked as a follow-up, not bolted on here just to hit a count.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy.orm import Session

from app.accounting import compute_account, compute_realizations
from app.dashboard_stats import compute_trade_stats
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, StrategyAllocation
from app.options.live_dispatch import run_options_strategy_once
from app.pairs_service import (
    PAIRS_STRATEGY,
    PAIRS_STRATEGY_KEY,
    PAIRS_SYMBOL_A,
    PAIRS_SYMBOL_B,
    compute_pair_position_state,
    submit_paper_order,
)
from app.position_sizing import size_position
from app.risk_settings_service import get_or_create_risk_settings
from app.strategies.alpha import AlphaRSIEMAStrategy
from app.strategies.base import MarketSnapshot
from app.strategies.bb_squeeze import BBSqueezeStrategy
from app.strategies.calendar_spread import CalendarSpreadStrategy
from app.strategies.delta_neutral import DeltaNeutralStrategy
from app.strategies.iron_condor import IronCondorStrategy
from app.strategies.mean_reversion_bb import MeanReversionBollingerStrategy
from app.strategies.momentum import MomentumMACDStrategy
from app.strategies.multi_basket import BASKET_SYMBOLS, BasketSnapshot, MultiBasketStrategy
from app.strategies.pairs_cointegration import PairSnapshot
from app.strategies.pairs_kelly import PairsKellyStrategy
from app.strategies.short_strangle import ShortStrangleStrategy
from app.strategies.vwap_reversion import VWAPReversionStrategy

# Below this many historical closed trades for a given strategy, a win-
# rate/avg-win/avg-loss estimate is closer to noise than signal -- same
# floor pairs_kelly.py's own historical scan uses (MIN_HISTORICAL_TRADES),
# just sourced from real DB order history here instead of a synthetic
# scan over price history, since this sizing wraps strategies that don't
# do any self-contained historical analysis of their own.
MIN_HISTORICAL_TRADES_FOR_KELLY = 10
# Fallback sizing when there isn't enough history yet: a fixed fraction of
# equity, not a fixed share count -- so a fallback-sized order still scales
# sensibly with account size, the same way the eventual Kelly-sized order
# would.
FALLBACK_SIZING_FRACTION = 0.05

SINGLE_INSTRUMENT_STRATEGIES = {
    "alpha_rsi_ema": AlphaRSIEMAStrategy(),
    "momentum_macd": MomentumMACDStrategy(),
    "mean_reversion_bb": MeanReversionBollingerStrategy(),
    "vwap_reversion": VWAPReversionStrategy(),
    "bb_squeeze": BBSqueezeStrategy(),
}

# Strategies that actually read MarketSnapshot.volumes -- everything else
# in SINGLE_INSTRUMENT_STRATEGIES ignores that field entirely, so trimming
# price history to match it would be a pointless (and for EMA-based
# strategies, actively harmful -- EMA's value depends on its full history,
# not just a recent window) change to make for strategies that never look
# at volume at all. See _run_single_instrument for why trimming is even
# necessary for the ones that do.
VOLUME_AWARE_STRATEGY_KEYS = {"vwap_reversion"}

# PAIRS_STRATEGY_KEY, PAIRS_SYMBOL_A/B, PAIRS_STRATEGY, submit_paper_order
# all live in app/pairs_service.py -- this module still needs them for its
# own tick-loop dispatch below, so they stay imported here too, just no
# longer DEFINED here. See pairs_service.py's module docstring for why the
# split happened.

# Two pairs-SHAPED strategies now trade the identical symbol pair:
# pairs_cointegration (fixed qty) and pairs_kelly (Kelly-sized qty). Each
# tracks its OWN position independently (compute_pair_position_state is
# keyed by strategy_key specifically so these can never be confused with
# each other), the same isolation a manual trade already gets from either.
PAIRS_STRATEGIES = {
    PAIRS_STRATEGY_KEY: (PAIRS_STRATEGY, PAIRS_SYMBOL_A, PAIRS_SYMBOL_B),
    "pairs_kelly": (PairsKellyStrategy(), PAIRS_SYMBOL_A, PAIRS_SYMBOL_B),
}

BASKET_STRATEGIES = {
    "multi_basket": (MultiBasketStrategy(), BASKET_SYMBOLS),
}

# Multi-leg synthetic options strategies -- see app/strategies/options_base.py
# for the shared shape and app/options/live_dispatch.py for how a signal
# from evaluate_options() turns into real option (and, for delta_neutral,
# equity) paper orders.
OPTIONS_STRATEGIES = {
    "iron_condor": IronCondorStrategy(),
    "calendar_spread": CalendarSpreadStrategy(),
    "short_strangle": ShortStrangleStrategy(),
    "delta_neutral": DeltaNeutralStrategy(),
}


def run_strategies_once(db: Session, registry: MarketRegistry) -> None:
    """Called once per market tick (see app/main.py's tick loop). Evaluates
    every user's enabled paper-mode StrategyAllocation rows and submits
    whatever orders the signals call for."""
    allocations = (
        db.query(StrategyAllocation)
        .filter(StrategyAllocation.enabled.is_(True), StrategyAllocation.mode == Mode.paper)
        .all()
    )

    # One trading_halted lookup per user per tick, not per allocation --
    # a user can have several enabled allocations, and the circuit breaker
    # (app/risk/circuit_breaker.py) halts ALL of a user's strategy-driven
    # trading at once, not one strategy at a time, so this is checked
    # once and reused across every allocation that user owns.
    halted_by_user: dict[int, bool] = {}

    for alloc in allocations:
        if alloc.user_id not in halted_by_user:
            halted_by_user[alloc.user_id] = get_or_create_risk_settings(db, alloc.user_id).trading_halted
        if halted_by_user[alloc.user_id]:
            continue

        if alloc.strategy_key in SINGLE_INSTRUMENT_STRATEGIES:
            _run_single_instrument(db, registry, alloc)
        elif alloc.strategy_key in PAIRS_STRATEGIES:
            _run_pairs(db, registry, alloc)
        elif alloc.strategy_key in BASKET_STRATEGIES:
            _run_basket(db, registry, alloc)
        elif alloc.strategy_key in OPTIONS_STRATEGIES:
            run_options_strategy_once(db, registry, alloc, OPTIONS_STRATEGIES[alloc.strategy_key])
        # An unrecognized strategy_key (typo, a since-removed strategy) is
        # silently skipped rather than raised here -- one bad allocation
        # row must not take down every other user's strategy evaluation
        # for the whole tick.

    db.commit()


def _size_single_instrument_qty(
    db: Session, registry: MarketRegistry, *, user_id: int, strategy_key: str, weight: float, price: float,
) -> int:
    """Fractional-Kelly position sizing from THIS strategy's own real
    trade history for THIS user, replacing the fixed DEFAULT_QTY=10 every
    single-instrument strategy otherwise hardcodes. Reuses app.position_
    sizing.size_position (Phase 1 found it fully implemented and tested
    but never called by any execution path) rather than re-deriving the
    Kelly formula here a second time.

    weight scales the CAPITAL BASE Kelly sizes against (account_value =
    equity * weight), not the final quantity -- so risk_settings.
    max_position_fraction, applied inside size_position, caps exposure
    relative to THIS strategy's own allocated slice of capital, not the
    whole account. The literal formula this phase specifies (qty =
    equity * f* * kelly_multiplier * weight / price) has no cap term at
    all; composing it this way is what makes max_position_fraction (a
    real, persisted risk setting from this same phase) actually mean
    something, rather than sitting next to Kelly sizing unused the same
    way Phase 1 found Kelly itself sitting unused.
    """
    risk_settings = get_or_create_risk_settings(db, user_id)
    all_orders = db.query(Order).filter(Order.user_id == user_id, Order.mode == Mode.paper).all()
    equity = compute_account(all_orders, registry.current_prices()).total_value

    if price <= 0 or equity <= 0:
        return 1

    def _clamped(qty: int) -> int:
        return max(1, min(qty, risk_settings.max_order_qty))

    strategy_realizations = [r for r in compute_realizations(all_orders) if r.strategy_key == strategy_key]
    stats = compute_trade_stats(strategy_realizations)

    has_enough_history = (
        stats.n_trades >= MIN_HISTORICAL_TRADES_FOR_KELLY
        and stats.avg_win is not None  # Kelly needs BOTH -- see position_sizing.kelly_fraction,
        and stats.avg_loss is not None  # which raises without them (all-wins or all-losses history)
    )

    if not has_enough_history:
        fallback_qty = round(FALLBACK_SIZING_FRACTION * equity * weight / price)
        return _clamped(fallback_qty)

    sizing = size_position(
        win_rate=stats.win_rate, avg_win=stats.avg_win, avg_loss=stats.avg_loss,
        account_value=equity * weight, price=price,
        kelly_multiplier=risk_settings.kelly_multiplier, max_position_fraction=risk_settings.max_position_fraction,
    )
    return _clamped(sizing.qty)


def _run_single_instrument(db: Session, registry: MarketRegistry, alloc: StrategyAllocation) -> None:
    if not alloc.symbol:
        return
    strategy = SINGLE_INSTRUMENT_STRATEGIES[alloc.strategy_key]
    prices = registry.prices(alloc.symbol)

    volumes = None
    if alloc.strategy_key in VOLUME_AWARE_STRATEGY_KEYS:
        # recent_volume (app/markets.py, Phase 1) is a BOUNDED ring buffer
        # (maxlen=500), unlike registry.prices()'s full unbounded history
        # -- MarketSnapshot.volumes must be the SAME length as prices, so
        # prices is trimmed to match volume's window rather than padding
        # volume backward with fabricated data for ticks before tracking
        # began. This only happens for strategies that actually need
        # volume; every other strategy still gets full, untrimmed history.
        recent_volume = registry[alloc.symbol].recent_volume
        if recent_volume:
            volumes = np.array(recent_volume)
            prices = prices[-len(volumes):]

    signal = strategy.evaluate(MarketSnapshot(symbol=alloc.symbol, prices=prices, volumes=volumes))
    if signal is None:
        return

    # The strategy's OWN signal.qty (every single-instrument strategy
    # hardcodes DEFAULT_QTY=10) is deliberately NOT what gets submitted --
    # sizing is this function's job, not the strategy's; see the module
    # docstring on why pairs/basket strategies are exempt from this
    # override (they already do their own internal sizing that a runner-
    # level replacement would break: pairs_kelly's self-contained Kelly
    # scan would be double-applied, and both pairs_cointegration and
    # multi_basket's leg-B/leg-N quantities are proportional to leg-A's
    # ACTUAL qty, which this override doesn't know how to keep consistent
    # across multiple legs).
    qty = _size_single_instrument_qty(
        db, registry, user_id=alloc.user_id, strategy_key=alloc.strategy_key,
        weight=alloc.weight, price=float(prices[-1]),
    )

    submit_paper_order(
        db, registry, user_id=alloc.user_id, strategy_key=alloc.strategy_key, symbol=alloc.symbol,
        side=signal.side, qty=qty, order_type=signal.order_type, px=signal.px,
    )


def _run_pairs(db: Session, registry: MarketRegistry, alloc: StrategyAllocation) -> None:
    strategy, symbol_a, symbol_b = PAIRS_STRATEGIES[alloc.strategy_key]
    prices_a = registry.prices(symbol_a)
    prices_b = registry.prices(symbol_b)
    state = compute_pair_position_state(db, alloc.user_id, strategy_key=alloc.strategy_key, symbol_a=symbol_a)

    result = strategy.evaluate_pair(PairSnapshot(
        symbol_a=symbol_a, symbol_b=symbol_b,
        prices_a=prices_a, prices_b=prices_b,
        position=state.position, position_qty_a=state.qty_a,
    ))
    if result is None:
        return

    # entry_zscore only means something for an ENTRY (new_position != "none")
    # -- a close's Order rows aren't opening anything, so there's no "entered
    # at" value to record for them.
    entry_zscore = result.zscore if result.new_position != "none" else None

    if result.signal_a is not None:
        submit_paper_order(
            db, registry, user_id=alloc.user_id, strategy_key=alloc.strategy_key, symbol=symbol_a,
            side=result.signal_a.side, qty=result.signal_a.qty,
            order_type=result.signal_a.order_type, px=result.signal_a.px,
            entry_zscore=entry_zscore,
        )
    if result.signal_b is not None:
        submit_paper_order(
            db, registry, user_id=alloc.user_id, strategy_key=alloc.strategy_key, symbol=symbol_b,
            side=result.signal_b.side, qty=result.signal_b.qty,
            order_type=result.signal_b.order_type, px=result.signal_b.px,
            entry_zscore=entry_zscore,
        )


def _compute_basket_position(db: Session, user_id: int, strategy_key: str, symbols: tuple[str, ...]) -> str:
    """Basket-shaped analogue of compute_pair_position_state, kept private
    to this module (unlike the pairs version, no router depends on this
    yet -- see multi_basket.py's own docstring on BasketSnapshot.
    position_qtys not currently being load-bearing the way pairs_kelly's
    position_qty_a is, since multi_basket sizes with a fixed qty at both
    entry and close)."""
    from app.models.trading import Order, OrderStatus, Side

    orders = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.strategy_key == strategy_key,
                Order.mode == Mode.paper, Order.status.in_([OrderStatus.filled, OrderStatus.partially_filled]))
        .order_by(Order.created_at)
        .all()
    )
    first_symbol = symbols[0]
    net_first = sum((o.filled_qty if o.side == Side.buy else -o.filled_qty) for o in orders if o.symbol == first_symbol)
    if net_first > 0:
        return "long_spread"
    if net_first < 0:
        return "short_spread"
    return "none"


def _run_basket(db: Session, registry: MarketRegistry, alloc: StrategyAllocation) -> None:
    strategy, symbols = BASKET_STRATEGIES[alloc.strategy_key]
    prices = {sym: registry.prices(sym) for sym in symbols}
    position = _compute_basket_position(db, alloc.user_id, alloc.strategy_key, symbols)

    result = strategy.evaluate_basket(BasketSnapshot(symbols=symbols, prices=prices, position=position))
    if result is None:
        return

    for sym, sig in result.leg_signals.items():
        submit_paper_order(
            db, registry, user_id=alloc.user_id, strategy_key=alloc.strategy_key, symbol=sym,
            side=sig.side, qty=sig.qty, order_type=sig.order_type, px=sig.px,
        )
