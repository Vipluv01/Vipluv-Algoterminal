"""List available strategies and manage a user's StrategyAllocation rows
-- separate from strategy_runner.py, which only reads these rows (never
writes them); this router is the only writer."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.markets import NAMED_INSTRUMENTS
from app.models.trading import Mode, StrategyAllocation
from app.models.user import User
from app.strategy_runner import PAIRS_STRATEGY_KEY, SINGLE_INSTRUMENT_STRATEGIES

router = APIRouter(prefix="/strategies", tags=["strategies"])


class StrategyInfo(BaseModel):
    key: str
    name: str
    kind: Literal["single_instrument", "pairs"]


@router.get("", response_model=list[StrategyInfo])
def list_strategies():
    infos = [
        StrategyInfo(key=s.key, name=s.name, kind="single_instrument")
        for s in SINGLE_INSTRUMENT_STRATEGIES.values()
    ]
    infos.append(StrategyInfo(
        key=PAIRS_STRATEGY_KEY, name="Pairs Trading (cointegration + Kalman hedge ratio)", kind="pairs",
    ))
    return infos


class AllocationOut(BaseModel):
    strategy_key: str
    mode: str
    enabled: bool
    weight: float
    symbol: str | None

    model_config = {"from_attributes": True}


@router.get("/allocations", response_model=list[AllocationOut])
def list_allocations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(StrategyAllocation).filter(StrategyAllocation.user_id == user.id).all()


class SetAllocationRequest(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    enabled: bool
    weight: float = 0.0
    symbol: str | None = None


@router.put("/allocations/{strategy_key}", response_model=AllocationOut)
def set_allocation(
    strategy_key: str,
    body: SetAllocationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    is_pairs = strategy_key == PAIRS_STRATEGY_KEY
    if not is_pairs and strategy_key not in SINGLE_INSTRUMENT_STRATEGIES:
        raise HTTPException(status_code=404, detail=f"unknown strategy_key {strategy_key!r}")
    if body.mode == "live":
        # Same 501-not-silent-accept discipline as orders.py: Phase 4's
        # broker adapter doesn't exist yet, so accepting this would enable
        # a strategy that can never actually place a live order it thinks
        # it's placing.
        raise HTTPException(status_code=501, detail="live strategy execution is not yet available")
    if not is_pairs and body.enabled and not body.symbol:
        raise HTTPException(status_code=400, detail="single-instrument strategies require a symbol")
    if not is_pairs and body.symbol is not None and body.symbol not in NAMED_INSTRUMENTS:
        raise HTTPException(status_code=404, detail=f"unknown symbol {body.symbol!r}")

    alloc = (
        db.query(StrategyAllocation)
        .filter(StrategyAllocation.user_id == user.id, StrategyAllocation.strategy_key == strategy_key,
                StrategyAllocation.mode == Mode.paper)
        .first()
    )
    if alloc is None:
        alloc = StrategyAllocation(user_id=user.id, strategy_key=strategy_key, mode=Mode.paper)
        db.add(alloc)

    alloc.enabled = body.enabled
    alloc.weight = body.weight
    alloc.symbol = None if is_pairs else body.symbol
    db.commit()
    db.refresh(alloc)
    return alloc
