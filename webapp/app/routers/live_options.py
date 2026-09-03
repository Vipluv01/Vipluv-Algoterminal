"""Real options chain data (strikes, expiries, live LTP) for any of the
221 real NSE underlyings Angel One's instrument master lists options for
-- app/options/chain.py's synthetic chain stays completely untouched
(paper/virtual modes keep using it), this is purely additive under its
own /live/options prefix, the same "swap the data source, not the shape"
pattern live_market.py already established for equity charts.

Underlying/expiry/strike discovery goes through app/broker/
instrument_master.py (a local file lookup, zero real Angel One traffic)
-- see that module's own docstring for why searchScrip-per-contract would
have repeated the exact rate-limit mistake already made once on the
equity side, just at a much larger scale (221 underlyings x dozens of
strikes each). Only the actual quote fetch touches Angel One for real,
via AngelOneAdapter.get_quote_batch's real batched quote endpoint (FULL
mode, not just LTP -- see that method's own docstring on a real,
confirmed staleness problem pure last-traded-price has for illiquid
contracts, and why best_bid/best_ask are surfaced here too rather than
LTP alone).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.broker.angelone import AngelOneError
from app.broker.instrument_master import (
    get_option_chain_contracts,
    list_expiries,
    list_option_underlyings,
    resolve_option_contract,
)
from app.db import get_db
from app.models.user import User
from app.routers.live_market import _get_adapter_or_400

router = APIRouter(prefix="/live/options", tags=["live-options"])


@router.get("/underlyings", response_model=list[str])
def get_live_option_underlyings(user: User = Depends(get_current_user)):
    """No broker credential needed -- purely a local instrument-master
    lookup, same reasoning as why this doesn't require _get_adapter_or_400
    the way the chain endpoint below does (that one needs a real session
    for the LTP fetch; this one never touches Angel One at all)."""
    return list_option_underlyings()


@router.get("/expiries", response_model=list[str])
def get_live_option_expiries(underlying: str, user: User = Depends(get_current_user)):
    expiries = list_expiries(underlying)
    if not expiries:
        raise HTTPException(status_code=404, detail=f"no listed options found for underlying {underlying!r}")
    return expiries


class ChainRowOut(BaseModel):
    strike: float
    call_token: str | None
    call_symbol: str | None
    call_ltp: float | None
    call_bid: float | None
    call_ask: float | None
    put_token: str | None
    put_symbol: str | None
    put_ltp: float | None
    put_bid: float | None
    put_ask: float | None
    lot_size: int | None


class ChainOut(BaseModel):
    underlying: str
    expiry: str
    rows: list[ChainRowOut]


@router.get("/chain", response_model=ChainOut)
def get_live_option_chain(
    underlying: str, expiry: str,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    contracts = get_option_chain_contracts(underlying, expiry)
    if not contracts:
        raise HTTPException(
            status_code=404, detail=f"no contracts found for underlying={underlying!r} expiry={expiry!r}",
        )

    adapter = _get_adapter_or_400(db, user.id)
    tokens_by_exchange: dict[str, list[str]] = {}
    for c in contracts:
        tokens_by_exchange.setdefault(c.exchange_segment, []).append(c.token)
    try:
        quote_by_token = adapter.get_quote_batch(tokens_by_exchange)
    except AngelOneError as e:
        raise HTTPException(status_code=502, detail=f"Angel One chain quote fetch failed: {e}")

    # One row per strike, call and put merged side by side -- the shape
    # a chain UI actually renders (CE columns | strike | PE columns), not
    # the flat per-contract list the instrument master itself returns.
    by_strike: dict[float, dict] = {}
    for c in contracts:
        row = by_strike.setdefault(c.strike, {"strike": c.strike, "lot_size": c.lot_size})
        prefix = "call" if c.option_type == "CE" else "put"
        quote = quote_by_token.get(c.token)
        row[f"{prefix}_token"] = c.token
        row[f"{prefix}_symbol"] = c.tradingsymbol
        row[f"{prefix}_ltp"] = quote.ltp if quote else None
        row[f"{prefix}_bid"] = quote.best_bid if quote else None
        row[f"{prefix}_ask"] = quote.best_ask if quote else None

    rows = [
        ChainRowOut(
            strike=r["strike"], lot_size=r.get("lot_size"),
            call_token=r.get("call_token"), call_symbol=r.get("call_symbol"), call_ltp=r.get("call_ltp"),
            call_bid=r.get("call_bid"), call_ask=r.get("call_ask"),
            put_token=r.get("put_token"), put_symbol=r.get("put_symbol"), put_ltp=r.get("put_ltp"),
            put_bid=r.get("put_bid"), put_ask=r.get("put_ask"),
        )
        for r in sorted(by_strike.values(), key=lambda r: r["strike"])
    ]
    return ChainOut(underlying=underlying, expiry=expiry, rows=rows)
