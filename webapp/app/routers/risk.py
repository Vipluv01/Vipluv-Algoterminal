"""Reads and writes the risk controls that actually govern execution --
GET creates the user's default row on first access (see
risk_settings_service.get_or_create_risk_settings), PUT applies a partial
update (only supplied fields change), and POST /reset-halt is the one
explicit, human action that can clear a circuit-breaker halt (see
app/risk/circuit_breaker.py's module docstring on why that's never
automatic).

Before this existed, GET /risk introspected size_position's own Python
keyword defaults purely for display -- there was no way to change a
value, and no code path anywhere used a per-user setting for anything.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models.user import User
from app.risk_settings_service import get_or_create_risk_settings

router = APIRouter(prefix="/risk", tags=["risk"])


class RiskControlsOut(BaseModel):
    max_order_qty: int
    kelly_multiplier: float
    max_position_fraction: float
    daily_max_drawdown_pct: float
    pairs_entry_z: float
    pairs_exit_z: float
    pairs_stop_z: float
    coint_pvalue_max: float
    trading_halted: bool

    model_config = {"from_attributes": True}


class RiskControlsUpdate(BaseModel):
    """Every field optional -- PUT applies a PARTIAL update. Deliberately
    NOT a way to clear trading_halted: that field is intentionally absent
    here, so the only way to resume trading after a halt is the explicit
    POST /reset-halt below, not a routine settings save that happens to
    omit it (which -- if trading_halted were settable here and defaulted
    on the request model -- would silently clear a real halt)."""

    max_order_qty: int | None = None
    kelly_multiplier: float | None = None
    max_position_fraction: float | None = None
    daily_max_drawdown_pct: float | None = None
    pairs_entry_z: float | None = None
    pairs_exit_z: float | None = None
    pairs_stop_z: float | None = None
    coint_pvalue_max: float | None = None


@router.get("", response_model=RiskControlsOut)
def get_risk_controls(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_or_create_risk_settings(db, user.id)


@router.put("", response_model=RiskControlsOut)
def update_risk_controls(
    body: RiskControlsUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    settings = get_or_create_risk_settings(db, user.id)
    # exclude_unset, not exclude_none: a caller explicitly sending
    # kelly_multiplier=0.0 must apply 0.0, not be silently treated the
    # same as "field omitted." Only a field truly ABSENT from the request
    # body is left alone.
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    return settings


@router.post("/reset-halt", response_model=RiskControlsOut)
def reset_halt(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = get_or_create_risk_settings(db, user.id)
    settings.trading_halted = False
    db.commit()
    db.refresh(settings)
    return settings
