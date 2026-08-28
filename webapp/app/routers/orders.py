"""Order submission, cancellation, and listing -- paper mode only for now
(Phase 4 wires live broker execution behind manual confirmation;
submitting mode="live" here returns 501 until then, so the request shape
doesn't need to change later).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.brackets import cancel_brackets_closed_elsewhere
from app.db import get_db
from app.markets import DERIVED_INDICES, HUMAN_USER_OWNER_ID, MarketRegistry
from app.models.trading import Bracket, Mode, Order, OrderStatus, OrderType, Side
from app.models.user import User
from app.orders_limits import MAX_ORDER_QTY
from app.risk_settings_service import raise_if_trading_halted

router = APIRouter(prefix="/orders", tags=["orders"])


def get_registry(request: Request) -> MarketRegistry:
    return request.app.state.registry


class SubmitOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market", "stop_limit"] = "market"
    qty: int = Field(gt=0, le=MAX_ORDER_QTY)
    px: float | None = None
    # Trigger price for stop_limit orders. The engine has supported these
    # since internal/book/stops.go; this endpoint just never accepted them.
    stop_px: float | None = None
    mode: Literal["paper", "live"] = "paper"
    strategy_key: str | None = None
    # Optional bracket, attached only if this entry order actually fills
    # (any amount -- see submit_order below for the partial-fill case).
    # A long's stop_loss_px must be below px/current price and its
    # take_profit_px above it; validating that here would need the fill
    # price this endpoint doesn't have until AFTER submission, so it's
    # deferred to app.brackets.check_trigger simply never firing for an
    # inverted threshold rather than rejected up front.
    stop_loss_px: float | None = None
    take_profit_px: float | None = None


class OrderOut(BaseModel):
    id: int
    symbol: str
    side: str
    order_type: str
    qty: int
    px: float | None
    stop_px: float | None
    status: str
    filled_qty: int
    avg_fill_px: float | None
    parent_order_id: int | None
    sub_account_id: int | None
    created_at: datetime
    strategy_key: str | None

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
    raise_if_trading_halted(db, user.id)
    if body.symbol in DERIVED_INDICES:
        # NIFTY50/BANKNIFTY are derived baskets (app/markets.py), not real
        # simulated instruments with their own order book -- there is
        # nothing here for an equity order to match against. Trading THEM
        # is only meaningful through their OPTIONS (POST /options/orders),
        # priced off this same derived value, not as a direct equity fill.
        raise HTTPException(status_code=400, detail="Index underlyings cannot be traded directly as equities")
    if body.order_type == "limit" and body.px is None:
        raise HTTPException(status_code=400, detail="limit orders require px")
    if body.order_type == "stop_limit" and (body.px is None or body.stop_px is None):
        # Both, not either: stop_px is the trigger, px is the limit price the
        # order converts to once triggered (internal/book/stops.go). A stop
        # with no limit price has nowhere to rest once it fires.
        raise HTTPException(status_code=400, detail="stop_limit orders require both px and stop_px")

    try:
        market = registry[body.symbol]
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    px_ticks = 0
    stop_px_ticks = 0
    if body.order_type in ("limit", "stop_limit"):
        from simulate import to_ticks_static  # bourse_sim is on sys.path via app.markets' own import
        px_ticks = to_ticks_static(body.px, market.tick_size)
    if body.order_type == "stop_limit":
        from simulate import to_ticks_static
        stop_px_ticks = to_ticks_static(body.stop_px, market.tick_size)

    order_id = market.next_order_id()
    result = market.eng.submit(
        order_id=order_id, side=body.side, qty=body.qty, px=px_ticks,
        stop_px=stop_px_ticks, owner=HUMAN_USER_OWNER_ID, order_type=body.order_type, tif="gtc",
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
        qty=body.qty, px=body.px, stop_px=body.stop_px, status=status,
        filled_qty=result.filled_qty, avg_fill_px=avg_fill_px,
        engine_order_id=order_id,
    )
    db.add(order)
    db.flush()  # need order.id before a Bracket can reference it

    if result.filled_qty > 0:
        cancel_brackets_closed_elsewhere(db, user_id=user.id, symbol=body.symbol, order_side=body.side)

    # Attach a bracket only if there's a real filled quantity to protect --
    # an order that rested or was fully rejected has no position yet for a
    # stop-loss/take-profit to watch. Sized to the FILLED quantity, not the
    # requested one: a partial fill means only that much is actually at
    # risk, and closing more than that on trigger would over-close a
    # position that was never fully opened.
    if result.filled_qty > 0 and (body.stop_loss_px is not None or body.take_profit_px is not None):
        db.add(Bracket(
            user_id=user.id, mode=Mode.paper, symbol=body.symbol, entry_side=Side(body.side),
            qty=result.filled_qty, stop_loss_px=body.stop_loss_px, take_profit_px=body.take_profit_px,
            entry_order_id=order.id,
        ))

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


# Hard ceiling on page size, not just a default -- the whole point of this
# endpoint accepting limit/offset is that a caller (the Logs screen,
# specifically) can never again pull every order a user has ever placed in
# one response. 200 is a display-page size, not a tuning knob: nothing
# downstream needs more than one page at a time, and a caller that wants
# "everything" should page through it, not raise this past the ceiling.
DEFAULT_ORDERS_PAGE_SIZE = 200
MAX_ORDERS_PAGE_SIZE = 200


@router.get("", response_model=list[OrderOut])
def list_orders(
    response: Response,
    mode: Literal["paper", "live"] | None = None,
    status: OrderStatus | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(DEFAULT_ORDERS_PAGE_SIZE, gt=0, le=MAX_ORDERS_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Filterable, ALWAYS-paginated order history. The response body stays
    a bare list (not an {items, total} envelope) so every existing caller
    of this endpoint keeps working unchanged; the true count of rows
    matching the filters (before limit/offset) travels in the
    X-Total-Count header instead, the same convention GitHub's own REST
    API uses for exactly this reason -- a caller that needs to render
    "page 2 of 14" can read the header without the body shape changing
    for callers that don't.

    limit is capped at MAX_ORDERS_PAGE_SIZE regardless of what's requested
    -- this endpoint used to hand back a user's ENTIRE order history in one
    unbounded query (fine at 100 orders, a real problem at 100,000); that
    ends here, not just for the default case.
    """
    q = db.query(Order).filter(Order.user_id == user.id)
    if mode is not None:
        q = q.filter(Order.mode == Mode(mode))
    if status is not None:
        q = q.filter(Order.status == status)
    if symbol is not None:
        q = q.filter(Order.symbol == symbol)
    if strategy is not None:
        q = q.filter(Order.strategy_key == strategy)
    if date_from is not None:
        q = q.filter(Order.created_at >= date_from)
    if date_to is not None:
        q = q.filter(Order.created_at <= date_to)

    response.headers["X-Total-Count"] = str(q.count())
    return q.order_by(Order.created_at.desc()).offset(offset).limit(limit).all()


class BracketOut(BaseModel):
    id: int
    symbol: str
    entry_side: str
    qty: int
    stop_loss_px: float | None
    take_profit_px: float | None
    status: str

    model_config = {"from_attributes": True}


@router.get("/brackets", response_model=list[BracketOut])
def list_brackets(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.trading import BracketStatus
    return (
        db.query(Bracket)
        .filter(Bracket.user_id == user.id, Bracket.status == BracketStatus.active)
        .order_by(Bracket.created_at.desc())
        .all()
    )


@router.delete("/brackets/{bracket_id}")
def cancel_bracket(bracket_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.trading import BracketStatus
    bracket = db.get(Bracket, bracket_id)
    if bracket is None or bracket.user_id != user.id:
        raise HTTPException(status_code=404, detail="bracket not found")
    if bracket.status != BracketStatus.active:
        raise HTTPException(status_code=400, detail=f"cannot cancel a bracket in status {bracket.status}")
    bracket.status = BracketStatus.cancelled
    db.commit()
    return {"ok": True, "bracket_id": bracket_id}


# --- Algorithmic execution (TWAP/VWAP slicing) --------------------------
# See app/execution/slicer.py for the actual per-bar slicing logic; this
# is just the REST surface for creating a ParentOrder and reading back its
# progress.

class AlgoOrderRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    total_qty: int = Field(gt=0, le=MAX_ORDER_QTY)
    algo: Literal["twap", "vwap"]
    horizon_bars: int = Field(gt=0, le=10_000)


class ParentOrderOut(BaseModel):
    id: int
    symbol: str
    side: str
    total_qty: int
    filled_qty: int
    algo: str
    horizon_bars: int
    start_bar: int
    status: str

    model_config = {"from_attributes": True}


@router.post("/algo", response_model=ParentOrderOut)
def submit_algo_order(
    body: AlgoOrderRequest,
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.execution import ParentOrder, SlicerAlgo

    raise_if_trading_halted(db, user.id)
    if body.symbol in DERIVED_INDICES:
        raise HTTPException(status_code=400, detail="Index underlyings cannot be traded directly as equities")
    if body.symbol not in registry.markets:
        raise HTTPException(status_code=404, detail=f"unknown symbol {body.symbol!r}")

    parent = ParentOrder(
        user_id=user.id, symbol=body.symbol, side=body.side, total_qty=body.total_qty,
        algo=SlicerAlgo(body.algo), horizon_bars=body.horizon_bars, start_bar=registry.current_step,
    )
    db.add(parent)
    db.commit()
    db.refresh(parent)
    return parent


class AlgoOrderStatusOut(BaseModel):
    parent: ParentOrderOut
    children: list[OrderOut]
    total_child_filled_qty: int  # sum of children's ACTUAL engine fills,
    # distinct from parent.filled_qty (cumulative qty REQUESTED across
    # slices -- see slicer.py's own note on why those can differ)


@router.get("/algo/{parent_order_id}", response_model=AlgoOrderStatusOut)
def get_algo_order(parent_order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.execution import ParentOrder

    parent = db.get(ParentOrder, parent_order_id)
    if parent is None or parent.user_id != user.id:
        raise HTTPException(status_code=404, detail="parent order not found")

    children = (
        db.query(Order)
        .filter(Order.parent_order_id == parent_order_id)
        .order_by(Order.created_at)
        .all()
    )
    return AlgoOrderStatusOut(
        parent=parent, children=children,
        total_child_filled_qty=sum(c.filled_qty for c in children),
    )
