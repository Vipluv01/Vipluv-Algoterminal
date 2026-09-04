"""Portfolio IQ -- Brinson-Fachler attribution against a NAMED benchmark,
the realized P&L walk, and the sub-account breakdown, all in one place
for the #/portfolio screen.

app/quant/attribution.py's brinson_attribution was implemented and tested
but exposed by no router -- this is that router. See attribution.py's own
module docstring: "attribution is meaningless without naming the
benchmark," which is why benchmark_name/benchmark_symbols always travel in
the response rather than being a fact the frontend has to already know or
hardcode.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounting import compute_realized_pnl_curve, get_cached_account_snapshot
from app.auth import get_current_user
from app.db import get_db
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, SubAccount
from app.models.user import User
from app.options.execution import mark_option_positions
from app.quant.attribution import BENCHMARK_SYMBOLS, brinson_attribution
from app.routers.orders import get_registry

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

BENCHMARK_NAME = f"Equal-weight buy-and-hold: {', '.join(BENCHMARK_SYMBOLS)}"

# Brinson-Fachler's own hard requirement (see brinson_attribution's
# docstring): weights must sum to exactly 1, which means idle cash has to
# be its own explicit entry rather than omitted. This key can never
# collide with a real traded symbol (NAMED_INSTRUMENTS/option contract
# keys are always uppercase tickers or NSE-style contract strings, never
# this literal).
CASH_KEY = "CASH"


class AttributionOut(BaseModel):
    benchmark_name: str
    benchmark_symbols: list[str]
    portfolio_return: float
    benchmark_return: float
    allocation: float
    selection: float
    interaction: float
    excess: float
    # None (with reason) when there's nothing to attribute -- e.g. a
    # brand-new account with total_value <= 0. Never a fabricated 0.0
    # standing in for "not computable."
    computable: bool
    reason: str | None
    # Stated explicitly, not left implicit: portfolio returns are measured
    # since each open position's OWN average entry price (different
    # positions can have different entry times); benchmark returns are
    # measured since market open (each symbol's own seed reference price).
    # These are not the same time window -- this is a live positioning
    # snapshot against a buy-and-hold reference, not a same-period
    # backtest comparison. Said here so the number is never read as more
    # rigorous than it is.
    methodology_note: str = (
        "Portfolio returns are measured since each open position's own average entry "
        "price; benchmark returns are measured since market open (each symbol's own "
        "seed reference price). These are different time windows -- read this as a "
        "live positioning snapshot against a buy-and-hold reference, not a matched-"
        "period backtest comparison."
    )


def _portfolio_weights_and_returns(
    db: Session, user: User, registry: MarketRegistry, mode: Mode,
) -> tuple[dict[str, float], dict[str, float], float, str | None]:
    prices = {**registry.current_prices(), **mark_option_positions(db, user.id, registry)}
    snapshot = get_cached_account_snapshot(db, user.id, mode, prices, only_primary=True)

    if snapshot.total_value <= 0:
        return {}, {}, snapshot.total_value, "account total_value is not positive -- nothing to attribute"

    weights: dict[str, float] = {CASH_KEY: snapshot.cash / snapshot.total_value}
    returns: dict[str, float] = {CASH_KEY: 0.0}
    for pos in snapshot.positions.values():
        if pos.qty == 0:
            continue
        market_value = pos.qty * prices.get(pos.symbol, pos.avg_entry_px)
        cost_basis = abs(pos.qty) * pos.avg_entry_px
        weights[pos.symbol] = market_value / snapshot.total_value
        returns[pos.symbol] = (pos.unrealized_pnl / cost_basis) if cost_basis > 0 else 0.0

    return weights, returns, snapshot.total_value, None


def _benchmark_returns(registry: MarketRegistry) -> dict[str, float]:
    """B_i since market open for every benchmark constituent -- each
    symbol's own SymbolMarket.s0 is the single, well-defined reference
    point (see AttributionOut.methodology_note on why this and the
    portfolio side use different windows)."""
    prices = registry.current_prices()
    result = {}
    for sym in BENCHMARK_SYMBOLS:
        market = registry.markets.get(sym)
        if market is None:
            continue
        result[sym] = (prices[sym] - market.s0) / market.s0
    return result


@router.get("/attribution", response_model=AttributionOut)
def get_attribution(
    # "live" is deliberately NOT a valid value here -- there is no real
    # mark-to-market registry, option-position marking, or benchmark
    # price source for live positions yet (this whole computation reads
    # off the SIMULATED engine's registry/current_prices, which never
    # sees a real Angel One price at all). Real bug this closes
    # (confirmed live, 2026-09-03): this endpoint had NO mode parameter
    # at all before, always querying Mode.paper regardless of which mode
    # was actually selected -- Portfolio IQ silently showed paper's
    # accumulated attribution/P&L under live mode, mislabeled as if it
    # applied there. The frontend now only ever calls this for paper/
    # virtual and shows an honest "not available in live mode yet" state
    # itself for live -- the same pattern AccountPanel.js already
    # established for GET /account's own paper-only scope.
    mode: Literal["paper", "virtual"] = "paper",
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    weights, returns, total_value, reason = _portfolio_weights_and_returns(db, user, registry, Mode(mode))
    if reason is not None:
        return AttributionOut(
            benchmark_name=BENCHMARK_NAME, benchmark_symbols=list(BENCHMARK_SYMBOLS),
            portfolio_return=0.0, benchmark_return=0.0, allocation=0.0, selection=0.0,
            interaction=0.0, excess=0.0, computable=False, reason=reason,
        )

    benchmark_returns = _benchmark_returns(registry)
    result = brinson_attribution(weights, returns, benchmark_returns)

    return AttributionOut(
        benchmark_name=BENCHMARK_NAME, benchmark_symbols=list(BENCHMARK_SYMBOLS),
        portfolio_return=result.portfolio_return, benchmark_return=result.benchmark_return,
        allocation=result.allocation, selection=result.selection, interaction=result.interaction,
        excess=result.excess, computable=True, reason=None,
    )


class RealizedPnlPointOut(BaseModel):
    # Deliberately NOT called "equity" -- this is starting_cash +
    # cumulative REALIZED P&L only (fill-indexed), kept realized-only on
    # purpose for Brinson attribution (clean per-period realized returns,
    # not mark-to-market noise from an open position). GET
    # /account/equity-curve is the genuine mark-to-market curve; naming
    # this one differently is what stops the two charts from ever being
    # read as disagreeing with each other again -- see
    # accounting.RealizedPnlPoint's own docstring.
    order_id: int
    created_at: object
    realized_pnl: float

    model_config = {"from_attributes": True}


@router.get("/realized-pnl-curve", response_model=list[RealizedPnlPointOut])
def get_portfolio_realized_pnl_curve(
    mode: Literal["paper", "virtual"] = "paper",  # see get_attribution's own docstring on why "live" isn't valid here
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Same only_primary scoping as GET /account/equity-curve -- the
    # realized (fill-indexed) walk, not mark-to-market; see
    # accounting.compute_realized_pnl_curve's own docstring.
    orders = (
        db.query(Order)
        .filter(Order.user_id == user.id, Order.mode == Mode(mode), Order.sub_account_id.is_(None))
        .all()
    )
    return compute_realized_pnl_curve(orders)


class SubAccountBreakdownOut(BaseModel):
    id: int
    label: str
    sizing_multiplier: float
    is_active: bool
    cash: float
    total_value: float
    total_realized_pnl: float
    total_unrealized_pnl: float


@router.get("/sub-accounts", response_model=list[SubAccountBreakdownOut])
def get_sub_account_breakdown(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    subs = db.query(SubAccount).filter(SubAccount.user_id == user.id).order_by(SubAccount.created_at).all()
    if not subs:
        return []

    prices = {**registry.current_prices(), **mark_option_positions(db, user.id, registry)}

    out = []
    for sub in subs:
        snapshot = get_cached_account_snapshot(db, user.id, Mode.paper, prices, sub_account_id=sub.id)
        out.append(SubAccountBreakdownOut(
            id=sub.id, label=sub.label, sizing_multiplier=sub.sizing_multiplier, is_active=sub.is_active,
            cash=snapshot.cash, total_value=snapshot.total_value,
            total_realized_pnl=snapshot.total_realized_pnl, total_unrealized_pnl=snapshot.total_unrealized_pnl,
        ))
    return out
