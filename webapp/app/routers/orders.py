"""Order submission, cancellation, and listing -- paper mode only for now
(Phase 4 wires live broker execution behind manual confirmation;
submitting mode="live" here returns 501 until then, so the request shape
doesn't need to change later).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side
from app.models.user import User
from app.orders_limits import MAX_ORDER_QTY

router = APIRouter(prefix="/orders", tags=["orders"])


def get_registry(request: Request) -> MarketRegistry:
    return request.app.state.registry


class SubmitOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"] = "market"
    qty: int = Field(gt=0, le=MAX_ORDER_QTY)
    px: float | None = None
    mode: Literal["paper", "live"] = "paper"
    strategy_key: str | None = None


class OrderOut(BaseModel):
    id: int
    symbol: str
    side: str
    order_type: str
    qty: int
    px: float | None
    status: str
    filled_qty: int
    avg_fill_px: float | None

    model_config = {"from_attributes": True}


@router.post("", response_model=OrderOut)
def submit_order(
    body: SubmitOrderRequest,
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.mode == "live":
        # Phase 4 (Angel One adapter, manual confirmation) isn't built yet
        # -- 501, not a silently-accepted paper fill, so a live-mode UI
        # built against this endpoint fails loudly instead of quietly
        # trading paper money under a "live" label.
        raise HTTPException(status_code=501, detail="live trading is not yet available")
    if body.order_type == "limit" and body.px is None:
        raise HTTPException(status_code=400, detail="limit orders require px")

    try:
        market = registry[body.symbol]
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    px_ticks = 0
    if body.order_type == "limit":
        from simulate import to_ticks_static  # bourse_sim is on sys.path via app.markets' own import
        px_ticks = to_ticks_static(body.px, market.tick_size)

    order_id = market.next_order_id()
    result = market.eng.submit(
        order_id=order_id, side=body.side, qty=body.qty, px=px_ticks,
        owner=1, order_type=body.order_type, tif="gtc",
    )

    avg_fill_px = None
    if result.filled_qty > 0:
        total = sum(f.px * f.qty for f in result.fills)
        avg_fill_px = (total / result.filled_qty) * market.tick_size

    if result.accepted:
        status = OrderStatus.filled if result.filled_qty == body.qty else (
            OrderStatus.partially_filled if result.filled_qty > 0 else OrderStatus.submitted
        )
    else:
        status = OrderStatus.rejected

    order = Order(
        user_id=user.id, mode=Mode.paper, strategy_key=body.strategy_key,
        symbol=body.symbol, side=Side(body.side), order_type=OrderType(body.order_type),
        qty=body.qty, px=body.px, status=status,
        filled_qty=result.filled_qty, avg_fill_px=avg_fill_px,
        engine_order_id=order_id,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.delete("/{order_id}")
def cancel_order(
    order_id: int,
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="order not found")
    if order.status not in (OrderStatus.submitted, OrderStatus.partially_filled):
        raise HTTPException(status_code=400, detail=f"cannot cancel an order in status {order.status}")
    if order.engine_order_id is None:
        raise HTTPException(status_code=500, detail="order has no engine_order_id -- cannot cancel")

    market = registry[order.symbol]
    reject = market.eng.cancel(order.engine_order_id)
    if reject != "none":
        raise HTTPException(status_code=409, detail=f"engine rejected cancel: {reject}")

    order.status = OrderStatus.cancelled
    db.commit()
    return {"ok": True, "order_id": order_id}


@router.get("", response_model=list[OrderOut])
def list_orders(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()).all()
