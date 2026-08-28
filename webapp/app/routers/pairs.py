"""Pair Overview / Pair Analytics: the pairs_cointegration strategy's own
dedicated pages (not just another row in the generic Strategies list) --
this is Vipluv's own validated strategy (ported from icici_mean_reversion,
see pairs_cointegration.py's docstring), the one piece of this platform
with real backtested evidence behind it, so it gets a real live-stats view
instead of being treated like the other four single-instrument strategies.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounting import compute_account
from app.auth import get_current_user
from app.db import get_db
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, OrderStatus
from app.models.user import User
from app.pairs_service import (
    PAIRS_STRATEGY,
    PAIRS_STRATEGY_KEY,
    PAIRS_SYMBOL_A,
    PAIRS_SYMBOL_B,
    current_pair_position,
    get_pair_telemetry,
    submit_paper_order,
)
from app.strategies.pairs_cointegration import compute_pair_stats

router = APIRouter(prefix="/pairs", tags=["pairs"])


def get_registry(request: Request) -> MarketRegistry:
    return request.app.state.registry


class LegOut(BaseModel):
    symbol: str
    qty: int
    avg_entry_px: float
    unrealized_pnl: float


class ConfigOut(BaseModel):
    entry_z: float
    exit_z: float
    stop_z: float
    coint_pvalue_max: float
    zscore_window: int
    min_history: int
    qty: int


class ActivityOut(BaseModel):
    id: int
    symbol: str
    side: str
    qty: int
    filled_qty: int
    avg_fill_px: float | None
    status: str
    entry_zscore: float | None
    created_at: object  # datetime, serialized by pydantic's default encoder

    model_config = {"from_attributes": True}


def _pairs_orders(db: Session, user_id: int) -> list[Order]:
    return (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.strategy_key == PAIRS_STRATEGY_KEY, Order.mode == Mode.paper)
        .order_by(Order.created_at.desc())
        .all()
    )


def _open_legs(orders: list[Order], current_prices: dict[str, float]) -> dict[str, LegOut]:
    # Filtered to this strategy's own fills only -- a manual trade on
    # ICICIBANK/HDFCBANK must not be mistaken for part of the pair position,
    # the same "derive it, don't store a second copy" discipline
    # app/accounting.py already documents.
    filled = [o for o in orders if o.status in (OrderStatus.filled, OrderStatus.partially_filled)]
    snapshot = compute_account(filled, current_prices, starting_cash=0.0)
    return {
        sym: LegOut(symbol=sym, qty=p.qty, avg_entry_px=p.avg_entry_px, unrealized_pnl=p.unrealized_pnl)
        for sym, p in snapshot.positions.items()
    }


def _telemetry_out() -> dict | None:
    """ADF/Johansen/Hurst/half-life -- read from the tick-cadence cache
    (app.pairs_service.refresh_pair_telemetry_once), NOT recomputed here.
    None until the first tick after startup has had enough history (same
    "no fabricated placeholder" discipline as every other missing-data
    case in this app) -- age_seconds tells the UI how stale the cached
    read is rather than implying it was just measured.

    Every raw statistic travels WITH its own interpretation context (the
    ADF t-stat next to its critical values, the Johansen trace stat next
    to its 90/95/99% criticals, Hurst next to the 0.5 random-walk
    reference, half-life in bars) -- a bare number is unreadable to
    anyone not already holding the relevant table in their head, and this
    screen exists to be read.
    """
    telemetry = get_pair_telemetry()
    if telemetry is None:
        return None
    age_seconds = (datetime.now(timezone.utc) - telemetry.computed_at).total_seconds()
    return {
        "computed_at": telemetry.computed_at,
        "age_seconds": age_seconds,
        "n_points": telemetry.n_points,
        "adf": {
            "stat": telemetry.adf.stat,
            "pvalue": telemetry.adf.pvalue,
            "usedlag": telemetry.adf.usedlag,
            "critical_values": telemetry.adf.critical_values,
            "is_stationary": telemetry.adf.is_stationary,
        },
        "johansen": {
            "trace_stats": telemetry.johansen.trace_stats,
            "critical_values_90": telemetry.johansen.critical_values_90,
            "critical_values_95": telemetry.johansen.critical_values_95,
            "critical_values_99": telemetry.johansen.critical_values_99,
            "r_0_passed": telemetry.johansen.r_0_passed,
            "r_1_passed": telemetry.johansen.r_1_passed,
        },
        "hurst": {"value": telemetry.hurst, "random_walk_reference": 0.5},
        "half_life_bars": telemetry.half_life_bars,
    }


def _current_entry_zscore(orders: list[Order], position: str) -> float | None:
    if position == "none":
        return None
    # orders is already sorted created_at desc -- the most recent
    # entry_zscore-tagged fill on the A leg is the entry that opened
    # whatever position is currently open, since the strategy's own state
    # machine (pairs_cointegration.py) never re-enters while already
    # holding a position.
    for o in orders:
        if o.symbol == PAIRS_SYMBOL_A and o.entry_zscore is not None:
            return o.entry_zscore
    return None


@router.get("/overview")
def get_overview(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prices_a = registry.prices(PAIRS_SYMBOL_A)
    prices_b = registry.prices(PAIRS_SYMBOL_B)
    stats = compute_pair_stats(
        prices_a, prices_b,
        zscore_window=PAIRS_STRATEGY.zscore_window, coint_pvalue_max=PAIRS_STRATEGY.coint_pvalue_max,
        min_history=PAIRS_STRATEGY.min_history, series_length=1,
    )

    orders = _pairs_orders(db, user.id)
    position = current_pair_position(db, user.id)
    current_prices = registry.current_prices()

    return {
        "symbol_a": PAIRS_SYMBOL_A,
        "symbol_b": PAIRS_SYMBOL_B,
        "position": position,
        "warming_up": stats is None,  # not enough price history yet -- a fresh market just started
        "zscore": stats.zscore if stats else None,
        "hedge_ratio": stats.hedge_ratio if stats else None,
        "cointegration_pvalue": stats.cointegration_pvalue if stats else None,
        "is_cointegrated": stats.is_cointegrated if stats else None,
        "correlation": stats.correlation if stats else None,
        "spread": stats.spread if stats else None,
        "telemetry": _telemetry_out(),
        "config": ConfigOut(
            entry_z=PAIRS_STRATEGY.entry_z, exit_z=PAIRS_STRATEGY.exit_z, stop_z=PAIRS_STRATEGY.stop_z,
            coint_pvalue_max=PAIRS_STRATEGY.coint_pvalue_max, zscore_window=PAIRS_STRATEGY.zscore_window,
            min_history=PAIRS_STRATEGY.min_history, qty=PAIRS_STRATEGY.qty,
        ),
        "legs": _open_legs(orders, current_prices),
        "entry_zscore": _current_entry_zscore(orders, position),
        "activity": [ActivityOut.model_validate(o) for o in orders[:20]],
    }


@router.get("/analytics")
def get_analytics(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prices_a = registry.prices(PAIRS_SYMBOL_A)
    prices_b = registry.prices(PAIRS_SYMBOL_B)
    stats = compute_pair_stats(
        prices_a, prices_b,
        zscore_window=PAIRS_STRATEGY.zscore_window, coint_pvalue_max=PAIRS_STRATEGY.coint_pvalue_max,
        min_history=PAIRS_STRATEGY.min_history, series_length=300,
    )

    orders = _pairs_orders(db, user.id)
    position = current_pair_position(db, user.id)
    current_prices = registry.current_prices()

    return {
        "symbol_a": PAIRS_SYMBOL_A,
        "symbol_b": PAIRS_SYMBOL_B,
        "warming_up": stats is None,
        "zscore_series": stats.zscore_series if stats else [],
        "hedge_ratio_series": stats.hedge_ratio_series if stats else [],
        "spread_series": stats.spread_series if stats else [],
        "telemetry": _telemetry_out(),
        "entry_z": PAIRS_STRATEGY.entry_z,
        "exit_z": PAIRS_STRATEGY.exit_z,
        "stop_z": PAIRS_STRATEGY.stop_z,
        "correlation": stats.correlation if stats else None,
        "cointegration_pvalue": stats.cointegration_pvalue if stats else None,
        "is_cointegrated": stats.is_cointegrated if stats else None,
        "hedge_ratio": stats.hedge_ratio if stats else None,
        "position": position,
        "legs": _open_legs(orders, current_prices),
        "entry_zscore": _current_entry_zscore(orders, position),
    }


@router.post("/close")
def force_close(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually unwinds the currently-open pair position at market, both
    legs, sized to whatever is actually held (not recomputed from the
    current hedge ratio -- closing must match what's really open, not a
    fresh beta that may have drifted since entry)."""
    orders = _pairs_orders(db, user.id)
    position = current_pair_position(db, user.id)
    if position == "none":
        raise HTTPException(status_code=400, detail="no open pair position to close")

    current_prices = registry.current_prices()
    legs = _open_legs(orders, current_prices)

    for leg in legs.values():
        if leg.qty == 0:
            continue
        side = "sell" if leg.qty > 0 else "buy"
        submit_paper_order(
            db, registry, user_id=user.id, strategy_key=PAIRS_STRATEGY_KEY, symbol=leg.symbol,
            side=side, qty=abs(leg.qty), order_type="market", px=None,
        )
    db.commit()
    return {"ok": True}
