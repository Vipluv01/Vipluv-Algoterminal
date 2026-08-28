"""Trade journal -- promoted from the Dashboard's own /dashboard/notes
panel to its own screen (#/journal). Same JournalNote table, extended with
tags, an optional trade link, and a P&L snapshot frozen at write time; the
three GET/POST/DELETE endpoints below fully replace /dashboard/notes'
(removed from app/routers/dashboard.py, not duplicated -- there is now
exactly one place notes are read or written).
"""

from __future__ import annotations

import nh3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.accounting import compute_realizations
from app.auth import get_current_user
from app.dashboard_stats import compute_trade_stats
from app.db import get_db
from app.models.trading import JournalNote, Mode, Order
from app.models.user import User

router = APIRouter(prefix="/journal", tags=["journal"])


class NoteIn(BaseModel):
    text: str
    tags: list[str] | None = None
    trade_id: int | None = None


class NoteOut(BaseModel):
    id: int
    text: str
    created_at: object
    tags: list[str] | None
    trade_id: int | None
    pnl_snapshot: float | None

    model_config = {"from_attributes": True}


@router.get("/notes", response_model=list[NoteOut])
def list_notes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(JournalNote)
        .filter(JournalNote.user_id == user.id)
        .order_by(JournalNote.created_at.desc())
        .all()
    )


@router.post("/notes", response_model=NoteOut)
def create_note(body: NoteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Sanitized here, server-side, BEFORE this ever reaches the database --
    # the frontend renders this text as markdown through `marked`, which
    # does not escape raw HTML by default. Order matters: strip whitespace
    # first so a note that's only padding is rejected below, then
    # sanitize, then check emptiness AGAIN -- a note that was nothing but
    # a <script> payload sanitizes down to "" and must be rejected the
    # same as one that was always blank, not silently stored as an empty
    # row.
    text = nh3.clean(body.text.strip())
    if not text:
        raise HTTPException(status_code=400, detail="note text cannot be empty")

    if body.trade_id is not None:
        trade = db.get(Order, body.trade_id)
        if trade is None or trade.user_id != user.id:
            raise HTTPException(status_code=404, detail="linked trade not found")

    # A SNAPSHOT: this account's net realized P&L at the moment this note
    # is written, computed once and frozen (see JournalNote.pnl_snapshot's
    # own docstring for why this is never recomputed on read).
    orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    trade_stats = compute_trade_stats(compute_realizations(orders))

    note = JournalNote(
        user_id=user.id, text=text, tags=body.tags, trade_id=body.trade_id,
        pnl_snapshot=trade_stats.net_pnl,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    note = db.get(JournalNote, note_id)
    if note is None or note.user_id != user.id:
        raise HTTPException(status_code=404, detail="note not found")
    db.delete(note)
    db.commit()
    return {"ok": True}
