from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounting import compute_realizations
from app.auth import get_current_user
from app.dashboard_stats import compute_day_stats, compute_day_win_rate, compute_trade_stats
from app.db import get_db
from app.models.trading import Mode, Order
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class TradeStatsOut(BaseModel):
    net_pnl: float
    win_rate: float | None
    day_win_rate: float | None
    profit_factor: float | None
    avg_win: float | None
    avg_loss: float | None
    n_trades: int


@router.get("/stats", response_model=TradeStatsOut)
def get_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    realizations = compute_realizations(orders)
    trade_stats = compute_trade_stats(realizations)
    day_win_rate = compute_day_win_rate(compute_day_stats(realizations))
    return TradeStatsOut(
        net_pnl=trade_stats.net_pnl, win_rate=trade_stats.win_rate, day_win_rate=day_win_rate,
        profit_factor=trade_stats.profit_factor, avg_win=trade_stats.avg_win,
        avg_loss=trade_stats.avg_loss, n_trades=trade_stats.n_trades,
    )


class DayOut(BaseModel):
    day: date
    pnl: float
    n_trades: int


@router.get("/calendar", response_model=list[DayOut])
def get_calendar(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    realizations = compute_realizations(orders)
    return [DayOut(day=d.day, pnl=d.pnl, n_trades=d.n_trades) for d in compute_day_stats(realizations)]


# Notes moved to app/routers/journal.py (#/journal screen) -- GET/POST
# /dashboard/notes no longer exist; use GET/POST/DELETE /journal/notes.
