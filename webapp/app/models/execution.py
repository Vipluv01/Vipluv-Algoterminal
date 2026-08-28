"""Parent/child order tracking for algorithmic execution (TWAP/VWAP
slicing) -- see app/execution/slicer.py. A ParentOrder is never itself
submitted to the matching engine; it's the record of an INTENT ("buy 500
ICICIBANK over 20 bars"), and each child Order row it produces is a real,
independently-filled market order linked back via Order.parent_order_id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SlicerAlgo(str, PyEnum):
    twap = "twap"
    vwap = "vwap"


class ParentOrderStatus(str, PyEnum):
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class ParentOrder(Base):
    __tablename__ = "parent_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))  # "buy" | "sell" -- plain string,
    # not the Order model's Side enum: a parent order isn't itself a
    # tradable order the engine ever sees, just this algo's own intent record.
    total_qty: Mapped[int] = mapped_column(Integer)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    algo: Mapped[SlicerAlgo] = mapped_column(Enum(SlicerAlgo))
    horizon_bars: Mapped[int] = mapped_column(Integer)
    # The registry-wide step count (see app/markets.py) this parent order
    # was created at -- NOT a bar index local to one symbol's own history,
    # since the slicer ticks in lockstep with the shared 1-second tick
    # loop, not per-symbol. current_bar - start_bar is how the slicer
    # knows how many slices should have fired by now.
    start_bar: Mapped[int] = mapped_column(Integer)
    status: Mapped[ParentOrderStatus] = mapped_column(Enum(ParentOrderStatus), default=ParentOrderStatus.active, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
