"""Stop-loss / take-profit monitoring, run once per market tick.

The trigger-direction logic is deliberately split out as a pure function
(`check_trigger`) from the DB/engine-touching orchestration (`monitor_brackets`)
-- the same separation strategies/base.py's Strategy protocol uses, and for
the same reason: the part that's easy to get backwards (which direction a
short's stop-loss fires in) should be testable against a plain price number
in milliseconds, not only exercisable by spinning up a real market.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from app.markets import HUMAN_USER_OWNER_ID, MarketRegistry
from app.models.trading import Bracket, BracketStatus, Mode, Order, OrderStatus, OrderType, Side

TriggerKind = Literal["stop_loss", "take_profit"]


def check_trigger(*, entry_side: str, price: float, stop_loss_px: float | None, take_profit_px: float | None) -> TriggerKind | None:
    """A LONG position (entry_side="buy") loses money as price falls, so
    its stop-loss fires at-or-below the threshold and its take-profit
    fires at-or-above. A SHORT position is the mirror image. Getting this
    backwards would mean a "stop-loss" that actually locks in losses and
    lets winners run unprotected -- exactly the failure mode this function
    exists to keep out of the tick loop.

    Stop-loss is checked first and returned immediately if both thresholds
    somehow trigger on the same tick (a large single-tick move could jump
    past both) -- protecting against further loss takes priority over
    locking in a gain.
    """
    if entry_side == "buy":
        if stop_loss_px is not None and price <= stop_loss_px:
            return "stop_loss"
        if take_profit_px is not None and price >= take_profit_px:
            return "take_profit"
    else:
        if stop_loss_px is not None and price >= stop_loss_px:
            return "stop_loss"
        if take_profit_px is not None and price <= take_profit_px:
            return "take_profit"
    return None


def cancel_brackets_closed_elsewhere(db: Session, *, user_id: int, symbol: str, order_side: str) -> None:
    """Any active bracket on this symbol whose entry_side is the OPPOSITE
    of a fill that just happened is watching a position that's now been
    reduced (or closed, or flipped) by something other than the bracket
    itself -- a manual order, or a different strategy trading the same
    symbol. Left active, it would later fire against a position that's
    smaller than what it was sized for, or gone entirely, and could open
    an unwanted position in the other direction. Cancels rather than
    resizes: correctly re-deriving the bracket's remaining protected qty
    would need the same fill-by-fill walk app/accounting.py already does
    for positions, and a stale bracket is a worse failure mode than one
    that's gone -- a user or strategy that just traded manually is already
    paying attention to that symbol.

    Called from both routers/orders.py (manual orders) and
    strategy_runner.py (strategy-generated orders) -- the two places a
    fill can happen -- so this can't be skipped by using one path over
    the other.
    """
    from app.models.trading import BracketStatus

    opposite_side = Side.sell if order_side == "buy" else Side.buy
    active = (
        db.query(Bracket)
        .filter(Bracket.user_id == user_id, Bracket.symbol == symbol,
                Bracket.status == BracketStatus.active, Bracket.entry_side == opposite_side)
        .all()
    )
    for b in active:
        b.status = BracketStatus.cancelled


def monitor_brackets(db: Session, registry: MarketRegistry) -> None:
    active = db.query(Bracket).filter(Bracket.status == BracketStatus.active, Bracket.mode == Mode.paper).all()
    for b in active:
        price = registry[b.symbol].current_price
        kind = check_trigger(entry_side=b.entry_side.value, price=price,
                              stop_loss_px=b.stop_loss_px, take_profit_px=b.take_profit_px)
        if kind is None:
            continue
        _close_bracket(db, registry, b, kind)
    db.commit()


def _close_bracket(db: Session, registry: MarketRegistry, bracket: Bracket, kind: TriggerKind) -> None:
    closing_side = "sell" if bracket.entry_side == Side.buy else "buy"
    market = registry[bracket.symbol]
    order_id = market.next_order_id()
    result = market.eng.submit(order_id=order_id, side=closing_side, qty=bracket.qty, px=0,
                                owner=HUMAN_USER_OWNER_ID, order_type="market", tif="gtc")

    avg_fill_px = None
    if result.filled_qty > 0:
        total = sum(f.px * f.qty for f in result.fills)
        avg_fill_px = (total / result.filled_qty) * market.tick_size
    status = OrderStatus.filled if result.filled_qty == bracket.qty else (
        OrderStatus.partially_filled if result.filled_qty > 0 else OrderStatus.submitted
    )

    closing_order = Order(
        user_id=bracket.user_id, mode=Mode.paper, strategy_key=f"bracket_{kind}",
        symbol=bracket.symbol, side=Side(closing_side), order_type=OrderType.market,
        qty=bracket.qty, px=None, status=status, filled_qty=result.filled_qty,
        avg_fill_px=avg_fill_px, engine_order_id=order_id,
    )
    db.add(closing_order)
    db.flush()  # need closing_order.id before it can be referenced

    bracket.status = BracketStatus.triggered
    bracket.closing_order_id = closing_order.id
