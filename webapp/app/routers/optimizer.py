"""Thin wrapper over app/portfolio_optimizer.py -- the max-Sharpe allocation
across the 4 user-selectable strategies (market_maker isn't user-allocatable,
see strategy_runner.py), computed from this user's own real paper-mode fills
via app/optimizer_returns.py, not a synthetic backtest.
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounting import get_cached_realizations
from app.auth import get_current_user
from app.db import get_db
from app.models.trading import Mode
from app.models.user import User
from app.optimizer_returns import MIN_TRADING_DAYS, build_daily_return_series
from app.portfolio_optimizer import max_sharpe_allocation
from app.pairs_service import PAIRS_STRATEGY_KEY
from app.strategy_runner import SINGLE_INSTRUMENT_STRATEGIES

router = APIRouter(prefix="/optimizer", tags=["optimizer"])

STRATEGY_KEYS = [*SINGLE_INSTRUMENT_STRATEGIES.keys(), PAIRS_STRATEGY_KEY]


def _finite_or_none(x: float) -> float | None:
    # max_sharpe_allocation's own fallback for an all-zero-variance
    # portfolio is sharpe=-inf (see its docstring/source) -- python's
    # json encoder happily emits the non-standard `-Infinity` token, which
    # a strict JSON.parse() in the browser rejects outright. Same class of
    # bug as the numpy.bool_ leak found in app/routers/pairs.py: cast at
    # the API boundary, don't assume every finite-looking float actually is.
    return x if math.isfinite(x) else None


@router.get("")
def get_optimizer(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # get_cached_realizations, not a fresh db.query(Order)...all() + a
    # full compute_realizations walk -- this endpoint re-walked a user's
    # ENTIRE paper order history from scratch on every call, confirmed
    # live at ~2.8s against the real account. Same incremental cache GET
    # /dashboard/stats already uses.
    realizations = get_cached_realizations(db, user.id, Mode.paper)
    returns = build_daily_return_series(realizations, STRATEGY_KEYS)
    if returns is None:
        return {
            "insufficient_history": True,
            "strategy_keys": STRATEGY_KEYS,
            "min_trading_days_required": MIN_TRADING_DAYS,
        }

    result = max_sharpe_allocation(returns)
    return {
        "insufficient_history": False,
        "strategy_keys": result.strategy_keys,
        "weights": result.weights.tolist(),
        "expected_return": _finite_or_none(result.expected_return),
        "expected_volatility": _finite_or_none(result.expected_volatility),
        "sharpe_ratio": _finite_or_none(result.sharpe_ratio),
        "days_of_history": len(next(iter(returns.values()))),
    }
