"""Live tick-loop dispatch for the 4 multi-leg options strategies --
the options analogue of app/pairs_service.py: reconstructs a strategy's
current open-position state from real filled Order rows (never a second,
separately-tracked position record -- same "derive it, don't store a
second copy" discipline app/accounting.py's own docstring establishes),
then turns whatever OptionsSignal the strategy returns into real option
(and, for delta_neutral, equity) paper orders.

Split out of strategy_runner.py for the same reason app/pairs_service.py
was: strategy_runner.py is the tick-loop dispatcher for EVERY strategy
shape, and options position-reconstruction is real, non-trivial logic that
only this one strategy shape needs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from sqlalchemy.orm import Session

from app.markets import MarketRegistry
from app.models.trading import InstrumentType, Mode, Order, OrderStatus, Side, StrategyAllocation
from app.options.chain import RISK_FREE_RATE, list_expiries, live_atm_sigma, smile_iv, time_to_expiry_years
from app.options.execution import submit_option_paper_order
from app.pairs_service import submit_paper_order
from app.quant.black_scholes import bsm_greeks
from app.strategies.options_base import OptionLegSignal, OptionsSnapshot


def _resolve_expiry_iso(expiry_kind: str) -> str:
    expiries = list_expiries()
    for e in expiries:
        if e.kind == expiry_kind:
            return e.date
    return expiries[0].date  # only "weekly" is guaranteed distinct from "monthly" -- see list_expiries


def _compute_open_option_legs(
    db: Session, user_id: int, strategy_key: str,
) -> tuple[tuple[OptionLegSignal, ...], dict[tuple[float, str], str], datetime | None]:
    """Walks this strategy's own option fills chronologically, net qty per
    (strike, option_type) contract -- resetting the "when did this
    position open" clock every time the strategy returns to fully flat,
    so a strategy that has opened/closed/reopened isn't scored against its
    very first-ever entry time."""
    orders = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.strategy_key == strategy_key,
                Order.instrument_type == InstrumentType.option, Order.mode == Mode.paper,
                Order.status.in_([OrderStatus.filled, OrderStatus.partially_filled]))
        .order_by(Order.created_at)
        .all()
    )
    net: dict[tuple[float, str], int] = {}
    expiry_iso_by_key: dict[tuple[float, str], str] = {}
    entry_time: datetime | None = None

    for o in orders:
        key = (o.strike, o.option_type)
        was_flat = not any(v != 0 for v in net.values())
        signed = o.filled_qty if o.side == Side.buy else -o.filled_qty
        net[key] = net.get(key, 0) + signed
        expiry_iso_by_key[key] = o.expiry
        now_flat = not any(v != 0 for v in net.values())
        if was_flat and not now_flat:
            entry_time = o.created_at
        elif now_flat:
            entry_time = None

    legs = tuple(
        OptionLegSignal(
            option_type=option_type, side=("buy" if qty > 0 else "sell"), strike=strike,
            qty=abs(qty), reason="", expiry_kind="weekly", expiry_bars=0,
        )
        for (strike, option_type), qty in net.items() if qty != 0
    )
    return legs, expiry_iso_by_key, entry_time


def _compute_equity_hedge_state(
    db: Session, user_id: int, strategy_key: str, symbol: str,
) -> tuple[int, datetime | None]:
    orders = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.strategy_key == strategy_key, Order.symbol == symbol,
                Order.instrument_type == InstrumentType.equity, Order.mode == Mode.paper,
                Order.status.in_([OrderStatus.filled, OrderStatus.partially_filled]))
        .order_by(Order.created_at)
        .all()
    )
    qty = sum((o.filled_qty if o.side == Side.buy else -o.filled_qty) for o in orders)
    last_time = orders[-1].created_at if orders else None
    return qty, last_time


def _current_option_delta(
    legs: tuple[OptionLegSignal, ...], expiry_iso_by_key: dict[tuple[float, str], str],
    spot: float, lot_size: int, sigma0: float,
) -> float:
    total = 0.0
    for leg in legs:
        expiry_iso = expiry_iso_by_key.get((leg.strike, leg.option_type))
        if expiry_iso is None:
            continue
        T = time_to_expiry_years(expiry_iso)
        iv = smile_iv(leg.strike, spot, sigma0)
        g = bsm_greeks(spot, leg.strike, T, RISK_FREE_RATE, iv, leg.option_type)
        sign = 1 if leg.side == "buy" else -1
        total += sign * g.delta * leg.qty * lot_size
    return total


def run_options_strategy_once(db: Session, registry: MarketRegistry, alloc: StrategyAllocation, strategy) -> None:
    underlying = strategy.underlying
    prices = registry.current_prices()
    if underlying not in prices:
        return
    spot = prices[underlying]
    spot_history = registry.prices(underlying) if underlying in registry.markets else np.array([spot])

    open_legs, expiry_iso_by_key, entry_time = _compute_open_option_legs(db, alloc.user_id, alloc.strategy_key)
    position = "open" if open_legs else "none"

    lot_size = getattr(strategy, "lot_size", 1)
    hedge_qty, last_hedge_time = _compute_equity_hedge_state(db, alloc.user_id, alloc.strategy_key, underlying)

    # A value written as tz-AWARE comes back tz-NAIVE after a round trip
    # through SQLite -- entry_time/last_hedge_time are both Order.created_at
    # values read straight back from the DB, so they're naive here even
    # though models/trading.py writes them as UTC-aware. Comparing them
    # against a naive "now" (numerically still the same UTC moment) is the
    # same fix app/risk/circuit_breaker.py's _today_utc already documents
    # and applies, just needing FRACTIONAL days here rather than a whole
    # calendar-date comparison.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    days_held = (now - entry_time).total_seconds() / 86400.0 if entry_time else 0.0
    hold_days = getattr(strategy, "hold_days", float("inf"))
    rebalance_days = getattr(strategy, "rebalance_days", float("inf"))
    should_exit = position == "open" and days_held >= hold_days
    days_since_hedge = (now - last_hedge_time).total_seconds() / 86400.0 if last_hedge_time else float("inf")
    should_rebalance = position == "open" and not should_exit and days_since_hedge >= rebalance_days

    sigma0 = live_atm_sigma(underlying, registry)
    snapshot = OptionsSnapshot(
        underlying=underlying, spot=spot, spot_history=spot_history, position=position,
        open_legs=open_legs, should_exit=should_exit, should_rebalance=should_rebalance,
        current_hedge_qty=hedge_qty,
        current_option_delta=_current_option_delta(open_legs, expiry_iso_by_key, spot, lot_size, sigma0),
    )
    result = strategy.evaluate_options(snapshot)
    if result is None:
        return

    is_entry = position == "none"
    for leg in result.option_legs:
        if is_entry:
            expiry_iso = _resolve_expiry_iso(leg.expiry_kind)
        else:
            expiry_iso = expiry_iso_by_key.get((leg.strike, leg.option_type)) or _resolve_expiry_iso(leg.expiry_kind)
        submit_option_paper_order(
            db, registry, user_id=alloc.user_id, strategy_key=alloc.strategy_key, underlying=underlying,
            option_type=leg.option_type, strike=leg.strike, expiry_iso=expiry_iso,
            side=leg.side, qty=leg.qty, lot_size=lot_size,
        )

    for symbol, sig in result.equity_legs:
        submit_paper_order(
            db, registry, user_id=alloc.user_id, strategy_key=alloc.strategy_key, symbol=symbol,
            side=sig.side, qty=sig.qty, order_type=sig.order_type, px=sig.px,
        )
