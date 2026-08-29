"""Order submission, cancellation, and listing.

Three modes now (Mode.paper, Mode.virtual, Mode.live): paper and virtual
share the identical simulated-engine submission path below (just a
different Order.mode tag and starting-capital ledger -- see
Mode.virtual's own docstring in models/trading.py), routed through
`registry`/the bourse Engine exactly as before. live is a genuinely
different path (_submit_live_order) that never touches the simulated
registry at all -- a live order's symbol is a real NSE ticker, not one of
the 7 simulated NAMED_INSTRUMENTS. Phase 4's manual-confirmation gate is
now real: submitting mode="live" only ever creates a
status=pending_confirmation row (nothing sent to Angel One yet); POST
/orders/{id}/confirm is the separate step that actually dispatches it
(app/broker/angelone.py), so a live order can never leave this process
toward a real broker without an explicit second call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.brackets import cancel_brackets_closed_elsewhere
from app.broker.adapter_cache import IncompleteBrokerCredentialError, NoBrokerCredentialError, get_adapter_for_user
from app.broker.angelone import AngelOneError
from app.broker.notify import notify_order_submitted
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
    mode: Literal["paper", "virtual", "live"] = "paper"
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
    mode: str
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
    # Both null until a live order is confirmed (POST /orders/{id}/confirm)
    # -- see this module's own docstring on the two-step live flow. Always
    # null for paper/virtual, which have no broker leg at all.
    broker_order_id: str | None
    confirmed_at: datetime | None

    model_config = {"from_attributes": True}


def _submit_live_order(body: SubmitOrderRequest, user: User, db: Session) -> Order:
    """Creates a status=pending_confirmation row and STOPS -- no broker
    call, no engine, no symbol/tick validation against the simulated
    registry (a live order's symbol is a real NSE ticker, not one of the 7
    NAMED_INSTRUMENTS this registry knows about). POST /orders/{id}/confirm
    is the only path that ever reaches Angel One."""
    if body.order_type not in ("market", "limit"):
        raise HTTPException(status_code=400, detail="live orders support order_type market or limit only")
    if body.order_type == "limit" and body.px is None:
        raise HTTPException(status_code=400, detail="limit orders require px")
    if body.stop_loss_px is not None or body.take_profit_px is not None:
        # A paper/virtual bracket is enforced by THIS process watching the
        # simulated engine every tick (app/brackets.py) -- there is no
        # equivalent here that could watch a real Angel One position and
        # place a real closing order, so accepting these silently would be
        # a promise this endpoint can't keep.
        raise HTTPException(status_code=400, detail="stop_loss_px/take_profit_px are not yet supported for live orders")

    order = Order(
        user_id=user.id, mode=Mode.live, strategy_key=body.strategy_key,
        symbol=body.symbol, side=Side(body.side), order_type=OrderType(body.order_type),
        qty=body.qty, px=body.px, stop_px=None, status=OrderStatus.pending_confirmation,
        filled_qty=0, avg_fill_px=None,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.post("", response_model=OrderOut)
def submit_order(
    body: SubmitOrderRequest,
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Applies uniformly to every mode now, checked BEFORE the mode branch
    # below -- a live order that only ever reaches pending_confirmation
    # here still needs to be blocked the same way a paper order already
    # is; letting live skip this because it doesn't touch the engine yet
    # would be exactly the kind of risk-gate gap Phase 7 was scoped to
    # close, not introduce.
    raise_if_trading_halted(db, user.id)

    if body.mode == "live":
        return _submit_live_order(body, user, db)

    mode_enum = Mode.virtual if body.mode == "virtual" else Mode.paper

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
        user_id=user.id, mode=mode_enum, strategy_key=body.strategy_key,
        symbol=body.symbol, side=Side(body.side), order_type=OrderType(body.order_type),
        qty=body.qty, px=body.px, stop_px=body.stop_px, status=status,
        filled_qty=result.filled_qty, avg_fill_px=avg_fill_px,
        engine_order_id=order_id,
    )
    db.add(order)
    db.flush()  # need order.id before a Bracket can reference it

    if result.filled_qty > 0:
        cancel_brackets_closed_elsewhere(db, user_id=user.id, symbol=body.symbol, order_side=body.side, mode=mode_enum)

    # Attach a bracket only if there's a real filled quantity to protect --
    # an order that rested or was fully rejected has no position yet for a
    # stop-loss/take-profit to watch. Sized to the FILLED quantity, not the
    # requested one: a partial fill means only that much is actually at
    # risk, and closing more than that on trigger would over-close a
    # position that was never fully opened.
    if result.filled_qty > 0 and (body.stop_loss_px is not None or body.take_profit_px is not None):
        db.add(Bracket(
            user_id=user.id, mode=mode_enum, symbol=body.symbol, entry_side=Side(body.side),
            qty=result.filled_qty, stop_loss_px=body.stop_loss_px, take_profit_px=body.take_profit_px,
            entry_order_id=order.id,
        ))

    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/confirm", response_model=OrderOut)
def confirm_live_order(
    order_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The second, explicit step of the live-order flow (see this
    module's own docstring) -- the ONLY place an order actually reaches
    Angel One. Re-checks raise_if_trading_halted: real time has passed
    since the order was created (a human looking at a confirmation
    dialog), during which the circuit breaker could have tripped -- the
    check at submit time does not cover that window."""
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="order not found")
    if order.mode != Mode.live:
        raise HTTPException(status_code=400, detail="only live orders require confirmation")
    if order.status != OrderStatus.pending_confirmation:
        raise HTTPException(status_code=400, detail=f"order is not pending confirmation (status={order.status})")

    raise_if_trading_halted(db, user.id)

    try:
        adapter = get_adapter_for_user(db, user.id)
    except (NoBrokerCredentialError, IncompleteBrokerCredentialError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        # resolve_equity_symbol, not search_symbol_token + matches[0] --
        # a real-money-safety bug found live: matches[0] is not guaranteed
        # to be the equity entry (e.g. SBIN's search returns 14 series,
        # matches[0] would have been SBIN-AF, not the actual stock). See
        # AngelOneAdapter.resolve_equity_symbol's own docstring.
        match = adapter.resolve_equity_symbol("NSE", order.symbol)
        broker_order_id = adapter.place_order(
            symbol=match.get("tradingsymbol", order.symbol), symboltoken=match["symboltoken"],
            exchange=match.get("exchange", "NSE"), side=order.side.value, qty=order.qty,
            order_type="MARKET" if order.order_type == OrderType.market else "LIMIT", price=order.px,
        )
    except AngelOneError as e:
        # Rejected, not silently left pending -- a human retrying a
        # confirm that's genuinely going to keep failing (e.g. a bad
        # symbol) needs a terminal status to see, not an order stuck
        # forever in pending_confirmation.
        order.status = OrderStatus.rejected
        db.commit()
        raise HTTPException(status_code=502, detail=f"Angel One rejected the order: {e}")

    order.broker_order_id = broker_order_id
    order.status = OrderStatus.submitted
    order.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(order)

    notify_order_submitted(symbol=order.symbol, side=order.side.value, qty=order.qty, broker_order_id=broker_order_id)
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

    if order.mode == Mode.live and order.status == OrderStatus.pending_confirmation:
        # Never reached the broker (confirm hasn't happened yet) -- a pure
        # DB status flip, no engine_order_id and nothing to cancel on
        # Angel One's side.
        order.status = OrderStatus.cancelled
        db.commit()
        return {"ok": True, "order_id": order_id}

    if order.status not in (OrderStatus.submitted, OrderStatus.partially_filled):
        raise HTTPException(status_code=400, detail=f"cannot cancel an order in status {order.status}")

    if order.mode == Mode.live:
        # Already confirmed and dispatched -- a real broker order,
        # cancelled through the same adapter confirm used to place it.
        try:
            adapter = get_adapter_for_user(db, user.id)
            adapter.cancel_order(order.broker_order_id)
        except (NoBrokerCredentialError, IncompleteBrokerCredentialError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except AngelOneError as e:
            raise HTTPException(status_code=502, detail=f"Angel One rejected the cancel: {e}")
        order.status = OrderStatus.cancelled
        db.commit()
        return {"ok": True, "order_id": order_id}

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
    mode: Literal["paper", "virtual", "live"] | None = None,
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
def list_brackets(
    mode: Literal["paper", "virtual", "live"] | None = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    from app.models.trading import BracketStatus
    q = db.query(Bracket).filter(Bracket.user_id == user.id, Bracket.status == BracketStatus.active)
    if mode is not None:
        q = q.filter(Bracket.mode == Mode(mode))
    return q.order_by(Bracket.created_at.desc()).all()


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
