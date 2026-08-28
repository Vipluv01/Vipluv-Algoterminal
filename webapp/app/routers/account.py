"""The derived account snapshot (cash, positions, P&L) -- see
app/accounting.py's own docstring for why this is computed from Order
history rather than stored and mutated directly."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounting import compute_account, compute_equity_curve
from app.auth import get_current_user
from app.db import get_db
from app.market_price_lookup import historical_price_lookup
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, SubAccount
from app.models.user import User
from app.options.execution import mark_option_positions
from app.routers.orders import get_registry

router = APIRouter(prefix="/account", tags=["account"])


class PositionOut(BaseModel):
    symbol: str
    qty: int
    avg_entry_px: float
    realized_pnl: float
    unrealized_pnl: float


class AccountOut(BaseModel):
    cash: float
    total_value: float
    total_realized_pnl: float
    total_unrealized_pnl: float
    positions: list[PositionOut]


@router.get("", response_model=AccountOut)
def get_account(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    # only_primary=True: once sub-accounts exist, their cloned orders
    # (Order.sub_account_id set -- see pairs_service.submit_paper_order)
    # would otherwise mix into the PRIMARY account's own cash/positions
    # here, inflating them with every sub-account's activity at once.
    # GET /account/sub/{id} is the view for a specific sub-account's own
    # numbers; this endpoint is the primary book alone.
    #
    # Option contract marks are merged in BEFORE compute_account runs --
    # without this, compute_account's current_prices.get(symbol, avg_entry_px)
    # fallback would silently report every open option position as flat
    # P&L, since an option contract key is never a key registry.
    # current_prices() already knows about on its own (see
    # app/options/execution.py's mark_option_positions docstring).
    prices = {**registry.current_prices(), **mark_option_positions(db, user.id, registry)}
    snapshot = compute_account(orders, prices, only_primary=True)
    return AccountOut(
        cash=snapshot.cash,
        total_value=snapshot.total_value,
        total_realized_pnl=snapshot.total_realized_pnl,
        total_unrealized_pnl=snapshot.total_unrealized_pnl,
        positions=[
            PositionOut(symbol=p.symbol, qty=p.qty, avg_entry_px=p.avg_entry_px,
                        realized_pnl=p.realized_pnl, unrealized_pnl=p.unrealized_pnl)
            for p in snapshot.positions.values()
        ],
    )


class EquityPointOut(BaseModel):
    order_id: int
    created_at: object
    equity: float

    model_config = {"from_attributes": True}


@router.get("/equity-curve", response_model=list[EquityPointOut])
def get_equity_curve(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Same only_primary scoping as GET /account above, applied by filtering
    # the order list directly -- compute_equity_curve is a thin wrapper
    # over _walk_fills with no sub_account_id parameter of its own, so
    # there's nothing to add there; the filter belongs at the call site.
    #
    # Genuinely mark-to-market (see accounting.EquityPoint's own
    # docstring on why): historical_price_lookup marks every open
    # position to price_history's own value at each fill's timestamp,
    # not just cumulative realized P&L -- this is what agrees with
    # GET /account's total_value at every point where no fill is pending.
    orders = (
        db.query(Order)
        .filter(Order.user_id == user.id, Order.mode == Mode.paper, Order.sub_account_id.is_(None))
        .all()
    )
    return compute_equity_curve(orders, price_lookup=historical_price_lookup(registry))


# --- Sub-accounts ------------------------------------------------------
#
# Routed under /account/sub (this router's existing /account prefix,
# singular), not /accounts/sub -- the app has exactly one account-resource
# prefix already (GET /account, GET /account/equity-curve both singular);
# introducing a second, differently-pluralized prefix for sub-accounts
# alone would be a genuinely confusing, inconsistent API surface for no
# real benefit.

class SubAccountOut(BaseModel):
    id: int
    label: str
    sizing_multiplier: float
    is_active: bool

    model_config = {"from_attributes": True}


class SubAccountCreate(BaseModel):
    label: str
    sizing_multiplier: float = 1.0


@router.get("/sub", response_model=list[SubAccountOut])
def list_sub_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(SubAccount)
        .filter(SubAccount.user_id == user.id)
        .order_by(SubAccount.created_at)
        .all()
    )


@router.post("/sub", response_model=SubAccountOut)
def create_sub_account(
    body: SubAccountCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    if body.sizing_multiplier <= 0:
        raise HTTPException(status_code=400, detail="sizing_multiplier must be positive")
    sub = SubAccount(user_id=user.id, label=body.label, sizing_multiplier=body.sizing_multiplier)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.get("/sub/{sub_account_id}", response_model=AccountOut)
def get_sub_account(
    sub_account_id: int,
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = db.get(SubAccount, sub_account_id)
    if sub is None or sub.user_id != user.id:
        raise HTTPException(status_code=404, detail="sub-account not found")

    orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    prices = {**registry.current_prices(), **mark_option_positions(db, user.id, registry)}
    snapshot = compute_account(orders, prices, sub_account_id=sub_account_id)
    return AccountOut(
        cash=snapshot.cash,
        total_value=snapshot.total_value,
        total_realized_pnl=snapshot.total_realized_pnl,
        total_unrealized_pnl=snapshot.total_unrealized_pnl,
        positions=[
            PositionOut(symbol=p.symbol, qty=p.qty, avg_entry_px=p.avg_entry_px,
                        realized_pnl=p.realized_pnl, unrealized_pnl=p.unrealized_pnl)
            for p in snapshot.positions.values()
        ],
    )
