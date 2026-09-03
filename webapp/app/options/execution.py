"""Synthetic options execution: fills happen at BSM theoretical price plus
an explicit modeled half-spread, never against a real order book.

Why there's no book to match against: bourse's Go matching engine is
deliberately single-instrument, integer-tick, symbol-agnostic (see
app/markets.py's own docstring) -- giving it a strikes/expiries concept
would be a rewrite of the engine itself, not a wiring change. Every option
fill here is unconditional and immediate, 100% of the requested qty, at a
price this module computes -- there is no partial fill, no resting order,
no rejection for an option order the way there is for an equity one.

EXECUTION_NOTICE is returned on every option order response specifically so
a client can never mistake this for a real matched fill, the way every
OTHER order in the app genuinely is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.markets import MarketRegistry
from app.models.trading import InstrumentType, Mode, Order, OrderStatus, OrderType, Side
from app.options.chain import RISK_FREE_RATE, build_contract_key, live_atm_sigma, smile_iv, time_to_expiry_years
from app.quant.black_scholes import OptionType, bsm_price

EXECUTION_NOTICE = "Model-priced synthetic option execution (theoretical BSM + modeled half-spread)"

MIN_SPREAD_ABSOLUTE = 0.5      # currency floor -- a near-worthless deep-OTM
                                  # contract's spread must never round down
                                  # to an effectively-zero cost to cross
SPREAD_FRACTION_OF_PRICE = 0.01  # the other half of spec's max(0.5, 1% of price)


def modeled_spread(theo_price: float) -> float:
    return max(MIN_SPREAD_ABSOLUTE, SPREAD_FRACTION_OF_PRICE * theo_price)


def option_fill_price(theo_price: float, side: str) -> float:
    """A buyer crosses the (synthetic) ask, a seller crosses the
    (synthetic) bid -- theo +/- half the modeled spread, the same
    convention a real matched market order pays/receives relative to mid."""
    half_spread = 0.5 * modeled_spread(theo_price)
    return theo_price + half_spread if side == "buy" else theo_price - half_spread


def option_theoretical_price(
    underlying: str, strike: float, expiry_iso: str, option_type: OptionType,
    registry: MarketRegistry, as_of: date | None = None,
) -> float:
    """The mid/theo price -- what MARKS a position, as opposed to
    option_fill_price above, which is what actually TRADING it costs.
    Spot is read from MarketRegistry.current_prices(), which already
    merges in derived index values (app/markets.py) -- this works
    identically for an equity underlying or an index one."""
    spot = registry.current_prices()[underlying]
    T = time_to_expiry_years(expiry_iso, as_of=as_of)
    iv = smile_iv(strike, spot, live_atm_sigma(underlying, registry))
    return bsm_price(spot, strike, T, RISK_FREE_RATE, iv, option_type)


@dataclass(frozen=True)
class OptionOrderResult:
    order: Order
    execution_notice: str = EXECUTION_NOTICE


def submit_option_paper_order(
    db: Session, registry: MarketRegistry, *, user_id: int, strategy_key: str | None,
    underlying: str, option_type: OptionType, strike: float, expiry_iso: str,
    side: str, qty: int, lot_size: int = 1, multiplier: int = 1,
) -> OptionOrderResult:
    theo = option_theoretical_price(underlying, strike, expiry_iso, option_type, registry)
    fill_px = option_fill_price(theo, side)
    contract_key = build_contract_key(underlying, expiry_iso, strike, option_type)

    order = Order(
        user_id=user_id, mode=Mode.paper, strategy_key=strategy_key, symbol=contract_key,
        side=Side(side), order_type=OrderType.market, qty=qty, px=fill_px, status=OrderStatus.filled,
        filled_qty=qty, avg_fill_px=fill_px, instrument_type=InstrumentType.option,
        underlying=underlying, strike=strike, expiry=expiry_iso, option_type=option_type,
        lot_size=lot_size, multiplier=multiplier,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return OptionOrderResult(order=order)


def mark_option_positions(db: Session, user_id: int, registry: MarketRegistry) -> dict[str, float]:
    """Live BSM theoretical marks (NO spread -- a mark is what a position
    is genuinely worth, not what it would cost to trade out of right now)
    for every distinct option contract this user has ever filled an order
    in. The caller merges this into MarketRegistry.current_prices()'s own
    dict BEFORE it reaches accounting.compute_account (see Order.
    instrument_type's docstring and app/routers/account.py) -- without
    this, compute_account's current_prices.get(symbol, avg_entry_px)
    fallback would silently report every option position as flat P&L,
    since an option contract key is never a key current_prices() already
    knows about on its own.
    """
    # Only the 5 columns actually needed, not full ORM Order objects --
    # confirmed live, 2026-09-04: a long-running paper account (real
    # automated options strategies firing continuously all session) had
    # 63,736 filled/partial option orders behind just 19 distinct
    # contracts. Materializing all 63,736 into full Order ORM objects
    # (every column, identity-mapped) just to throw away everything but
    # (symbol, underlying, strike, expiry, option_type) and de-dupe by
    # symbol was a real, measurable chunk of GET /account's latency --
    # this account panel is polled every few seconds (AccountPanel.js),
    # so that cost was being paid over and over. De-duped in Python, NOT
    # via .distinct(Order.symbol) at the SQL level -- that emits
    # PostgreSQL's own DISTINCT ON syntax, which SQLite (this app's local
    # dev/test database) doesn't support at all.
    rows = (
        db.query(Order.symbol, Order.underlying, Order.strike, Order.expiry, Order.option_type)
        .filter(Order.user_id == user_id, Order.instrument_type == InstrumentType.option,
                Order.status.in_([OrderStatus.filled, OrderStatus.partially_filled]))
        .all()
    )
    marks: dict[str, float] = {}
    for symbol, underlying, strike, expiry, option_type in rows:
        if symbol in marks:
            continue
        marks[symbol] = option_theoretical_price(underlying, strike, expiry, option_type, registry)
    return marks
