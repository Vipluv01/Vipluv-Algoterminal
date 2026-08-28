"""Virtual-mode account view -- Mode.virtual's own accounting, mirroring
GET /account and GET /account/equity-curve almost exactly.

Virtual mode shares the EXACT same simulated-engine order flow as paper
mode (see routers/orders.py's submit_order and Mode.virtual's own
docstring in models/trading.py) -- the only differences are the Order.mode
tag orders are stamped with and the starting-capital figure
(accounting.STARTING_VIRTUAL_CASH_DEFAULT, Rs 1 crore) this router's views
are computed against. This router is therefore a thin, mode-filtered
sibling of routers/account.py, not a second accounting implementation --
same compute_account/compute_equity_curve, same historical_price_lookup,
just Mode.virtual instead of Mode.paper.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounting import STARTING_VIRTUAL_CASH_DEFAULT, compute_account, compute_equity_curve
from app.auth import get_current_user
from app.db import get_db
from app.market_price_lookup import historical_price_lookup
from app.markets import MarketRegistry
from app.models.trading import Mode, Order
from app.models.user import User
from app.routers.orders import get_registry

router = APIRouter(prefix="/virtual", tags=["virtual"])


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
    starting_cash: float = STARTING_VIRTUAL_CASH_DEFAULT


@router.get("/account", response_model=AccountOut)
def get_virtual_account(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.virtual).all()
    # No option-mark merge here (unlike GET /account) -- option orders are
    # never tagged Mode.virtual (app/options/execution.py only ever
    # submits Mode.paper or Mode.live), so there is nothing for
    # mark_option_positions to find for this mode.
    snapshot = compute_account(orders, registry.current_prices(), starting_cash=STARTING_VIRTUAL_CASH_DEFAULT)
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
def get_virtual_equity_curve(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.virtual).all()
    return compute_equity_curve(
        orders, price_lookup=historical_price_lookup(registry), starting_cash=STARTING_VIRTUAL_CASH_DEFAULT,
    )
