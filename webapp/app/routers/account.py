"""The derived account snapshot (cash, positions, P&L) -- see
app/accounting.py's own docstring for why this is computed from Order
history rather than stored and mutated directly."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounting import compute_account
from app.auth import get_current_user
from app.db import get_db
from app.markets import MarketRegistry
from app.models.trading import Mode, Order
from app.models.user import User
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
    snapshot = compute_account(orders, registry.current_prices())
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
