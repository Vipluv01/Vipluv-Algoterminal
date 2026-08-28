"""Get-or-create for a user's RiskSettings row -- shared by routers/risk.py,
app/risk/circuit_breaker.py, and strategy_runner.py's Kelly sizing, all
three of which need "this user's current risk settings, creating the
default row on first access" and none of which should each implement that
lookup separately (a second implementation is how "the row doesn't exist
yet" ends up handled two different ways in two different places).
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.risk import RiskSettings


def get_or_create_risk_settings(db: Session, user_id: int) -> RiskSettings:
    settings = db.query(RiskSettings).filter(RiskSettings.user_id == user_id).first()
    if settings is None:
        settings = RiskSettings(user_id=user_id)  # every column has a default, see models/risk.py
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def raise_if_trading_halted(db: Session, user_id: int) -> None:
    """Guard for every order-submission endpoint (POST /orders, POST
    /orders/algo) -- shared here rather than duplicated per-router so the
    409 body text can't drift between them."""
    settings = get_or_create_risk_settings(db, user_id)
    if settings.trading_halted:
        raise HTTPException(
            status_code=409,
            detail="Trading halted: daily drawdown limit reached. POST /risk/reset-halt to resume.",
        )
