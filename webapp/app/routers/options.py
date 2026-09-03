"""Synthetic options: chain, order submission, and portfolio Greeks/stress
-- see app/options/{chain,execution,greeks}.py for the actual math. Kept as
one router (not split per module) the same way every other resource area
in this app gets exactly one router file.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.markets import DERIVED_INDICES, MarketRegistry, NAMED_INSTRUMENTS
from app.models.trading import InstrumentType, Mode, Order, OrderStatus, OrderType, Side
from app.models.user import User
from app.options.chain import ExpiryInfo, OptionChain, get_option_chain, list_expiries
from app.options.execution import EXECUTION_NOTICE, submit_option_paper_order
from app.options.greeks import GreeksResponse, get_portfolio_greeks
from app.orders_limits import MAX_ORDER_QTY
from app.risk_settings_service import raise_if_trading_halted

router = APIRouter(prefix="/options", tags=["options"])

# Every underlying an option chain can be requested for -- the derived
# indices (task 2) plus every real, directly-tradable equity. Unlike
# routers/orders.py's equity path, index underlyings are perfectly valid
# HERE: the whole point of the index existing is to have options on it.
_VALID_UNDERLYINGS = set(NAMED_INSTRUMENTS) | set(DERIVED_INDICES)


def get_registry(request: Request) -> MarketRegistry:
    return request.app.state.registry


def _require_valid_underlying(underlying: str) -> None:
    if underlying not in _VALID_UNDERLYINGS:
        raise HTTPException(status_code=404, detail=f"unknown underlying {underlying!r}")


# --- Chain ---------------------------------------------------------------

class ExpiryOut(BaseModel):
    date: str
    label: str
    kind: str

    model_config = {"from_attributes": True}


@router.get("/expiries", response_model=list[ExpiryOut])
def get_expiries():
    return list_expiries()


class OptionQuoteOut(BaseModel):
    contract_key: str
    strike: float
    option_type: str
    theoretical_price: float
    iv: float
    open_interest: int
    volume: int

    model_config = {"from_attributes": True}


class OptionChainRowOut(BaseModel):
    strike: float
    call: OptionQuoteOut
    put: OptionQuoteOut

    model_config = {"from_attributes": True}


class OptionChainOut(BaseModel):
    underlying: str
    spot: float
    expiry: str
    expiry_label: str
    rows: list[OptionChainRowOut]

    model_config = {"from_attributes": True}


@router.get("/chain", response_model=OptionChainOut)
def get_chain(
    underlying: str, expiry: str | None = None, registry: MarketRegistry = Depends(get_registry),
):
    _require_valid_underlying(underlying)
    return get_option_chain(underlying, registry, expiry=expiry)


# --- Order submission ------------------------------------------------------

class OptionOrderRequest(BaseModel):
    underlying: str
    option_type: Literal["CE", "PE"]
    strike: float = Field(gt=0)
    expiry: str  # ISO date, from GET /options/expiries (paper) or a real Angel One expiry string (live)
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0, le=MAX_ORDER_QTY)
    lot_size: int = Field(default=1, gt=0)
    multiplier: int = Field(default=1, gt=0)
    # "paper" is the pre-existing, unchanged default -- every current
    # caller (the Options screen's own order ticket, the 4 automated
    # options strategies via live_dispatch.py) keeps working exactly as
    # before. "live" is new: real order routing through Angel One,
    # mirroring routers/orders.py's own two-step confirm flow exactly --
    # this only ever creates a pending_confirmation row, zero broker
    # contact, the same safety property equity live orders already have.
    mode: Literal["paper", "live"] = "paper"


class OptionOrderOut(BaseModel):
    id: int
    symbol: str
    underlying: str
    strike: float
    expiry: str
    option_type: str
    side: str
    qty: int
    px: float | None  # always None here -- no limit-order support for options yet, paper or live
    avg_fill_px: float | None
    status: str
    execution_notice: str


@router.post("/orders", response_model=OptionOrderOut)
def submit_option_order(
    body: OptionOrderRequest,
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raise_if_trading_halted(db, user.id)

    if body.mode == "live":
        # Deliberately skips _require_valid_underlying -- that set is the
        # SYNTHETIC 9-symbol universe paper mode's chain is built from;
        # live mode's real universe is Angel One's own 221 real
        # underlyings (app/broker/instrument_master.py), a completely
        # different list. No instrument-master lookup happens here
        # either -- resolve_option_contract only runs at confirm time
        # (routers/orders.py's _resolve_and_place_option_order), same
        # "submit is pure DB work, confirm is the only real broker
        # contact" discipline equity live orders already established.
        order = Order(
            user_id=user.id, mode=Mode.live, strategy_key=None,
            # NOT build_contract_key -- that helper parses expiry as an
            # ISO date (correct for paper mode's GET /options/expiries,
            # which returns ISO dates), but live mode's expiry comes from
            # GET /live/options/expiries, Angel One's OWN real format
            # ("06OCT2026", not ISO) -- confirmed live, calling
            # build_contract_key with it raises ValueError outright. This
            # symbol is display/grouping only for a live order; the REAL
            # resolution at confirm time reads order.underlying/expiry/
            # strike/option_type directly (resolve_option_contract), never
            # re-parses this string, so it just needs to be a stable,
            # readable identifier, not any particular date format.
            symbol=f"{body.underlying}{body.expiry}{body.strike:g}{body.option_type}",
            side=Side(body.side), order_type=OrderType.market, qty=body.qty, px=None,
            status=OrderStatus.pending_confirmation, filled_qty=0, avg_fill_px=None,
            instrument_type=InstrumentType.option, underlying=body.underlying, strike=body.strike,
            expiry=body.expiry, option_type=body.option_type, lot_size=body.lot_size, multiplier=body.multiplier,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return OptionOrderOut(
            id=order.id, symbol=order.symbol, underlying=order.underlying, strike=order.strike,
            expiry=order.expiry, option_type=order.option_type, side=order.side.value, qty=order.qty,
            px=None, avg_fill_px=None, status=order.status.value,
            execution_notice="Pending confirmation -- no broker contact yet.",
        )

    _require_valid_underlying(body.underlying)
    result = submit_option_paper_order(
        db, registry, user_id=user.id, strategy_key=None, underlying=body.underlying,
        option_type=body.option_type, strike=body.strike, expiry_iso=body.expiry,
        side=body.side, qty=body.qty, lot_size=body.lot_size, multiplier=body.multiplier,
    )
    order = result.order
    return OptionOrderOut(
        id=order.id, symbol=order.symbol, underlying=order.underlying, strike=order.strike,
        expiry=order.expiry, option_type=order.option_type, side=order.side.value, qty=order.qty,
        px=None, avg_fill_px=order.avg_fill_px, status=order.status.value, execution_notice=EXECUTION_NOTICE,
    )


# --- Portfolio Greeks & stress --------------------------------------------

class GreeksOut(BaseModel):
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float

    model_config = {"from_attributes": True}


class OptionPositionGreeksOut(BaseModel):
    symbol: str
    underlying: str
    strike: float
    option_type: str
    qty: int
    greeks: GreeksOut

    model_config = {"from_attributes": True}


class StressRowOut(BaseModel):
    shift_pct: float
    shifted_spot: float
    pnl: float

    model_config = {"from_attributes": True}


class UnderlyingStressOut(BaseModel):
    underlying: str
    spot: float
    rows: list[StressRowOut]

    model_config = {"from_attributes": True}


class GreeksResponseOut(BaseModel):
    aggregate: GreeksOut
    positions: list[OptionPositionGreeksOut]
    stress: list[UnderlyingStressOut]

    model_config = {"from_attributes": True}


@router.get("/greeks", response_model=GreeksResponseOut)
def get_greeks(
    registry: MarketRegistry = Depends(get_registry),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_portfolio_greeks(db, user, registry)
