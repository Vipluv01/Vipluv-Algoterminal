"""Surfaces the risk controls that already exist in the code, rather than
inventing a separate "settings" concept to display. Read-only for now --
these are the values position_sizing.py and pairs_cointegration.py are
actually running with, not a UI the values would need to stay in sync
with by hand.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.orders_limits import MAX_ORDER_QTY
from app.strategy_runner import _PAIRS_STRATEGY

router = APIRouter(prefix="/risk", tags=["risk"])


class RiskControlsOut(BaseModel):
    max_order_qty: int
    kelly_multiplier: float
    max_position_fraction: float
    pairs_entry_z: float
    pairs_stop_z: float
    pairs_coint_pvalue_max: float


@router.get("", response_model=RiskControlsOut)
def get_risk_controls():
    from app.position_sizing import size_position
    defaults = size_position.__kwdefaults__ or {}
    return RiskControlsOut(
        max_order_qty=MAX_ORDER_QTY,
        kelly_multiplier=defaults.get("kelly_multiplier", 0.25),
        max_position_fraction=defaults.get("max_position_fraction", 0.5),
        pairs_entry_z=_PAIRS_STRATEGY.entry_z,
        pairs_stop_z=_PAIRS_STRATEGY.stop_z,
        pairs_coint_pvalue_max=_PAIRS_STRATEGY.coint_pvalue_max,
    )
