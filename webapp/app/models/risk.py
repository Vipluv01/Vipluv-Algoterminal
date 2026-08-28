"""Per-user, persisted risk settings -- what routers/risk.py's GET/PUT
actually read and write, and what app/risk/circuit_breaker.py and
strategy_runner.py's Kelly sizing both consult.

Before this existed, GET /risk introspected size_position's own Python
keyword defaults purely for DISPLAY -- there was no way to change a value,
and no code path anywhere used a PER-USER setting for anything (every
strategy hardcoded DEFAULT_QTY=10 regardless of what /risk showed). This
table is what makes those numbers real inputs to execution rather than a
read-only mirror of constants.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    max_order_qty: Mapped[int] = mapped_column(Integer, default=500)
    kelly_multiplier: Mapped[float] = mapped_column(Float, default=0.25)
    max_position_fraction: Mapped[float] = mapped_column(Float, default=0.20)
    daily_max_drawdown_pct: Mapped[float] = mapped_column(Float, default=5.0)

    pairs_entry_z: Mapped[float] = mapped_column(Float, default=1.5)
    pairs_exit_z: Mapped[float] = mapped_column(Float, default=0.1)
    pairs_stop_z: Mapped[float] = mapped_column(Float, default=3.0)
    coint_pvalue_max: Mapped[float] = mapped_column(Float, default=0.05)

    # Set by app/risk/circuit_breaker.py when the daily drawdown limit is
    # breached; cleared only by an explicit POST /risk/reset-halt, never
    # automatically -- see circuit_breaker.py's own module docstring on
    # why auto-clearing would defeat the point of a circuit breaker.
    trading_halted: Mapped[bool] = mapped_column(Boolean, default=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )
