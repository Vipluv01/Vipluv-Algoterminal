"""Aggregate portfolio Greeks and underlying-price stress testing over a
user's open option positions.

Reuses app.accounting.compute_account for position sizing (an option
position is just another symbol-keyed position -- see Order.instrument_type's
own docstring on why that works unmodified) rather than re-deriving
qty-per-contract a second way here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.accounting import compute_account
from app.markets import MarketRegistry
from app.models.trading import InstrumentType, Mode, Order
from app.models.user import User
from app.options.chain import RISK_FREE_RATE, live_atm_sigma, smile_iv, time_to_expiry_years
from app.options.execution import mark_option_positions
from app.quant.black_scholes import Greeks, bsm_greeks, bsm_price

# +-2% and +-5% spot moves, per spec. Applied ONE underlying at a time --
# a user's book can span both NIFTY50 and BANKNIFTY options (or an equity
# underlying too), and those don't move together, so stressing "the"
# spot as if there were only one underlying would either be meaningless
# for a multi-underlying book or silently only describe one of them.
STRESS_SHIFTS: tuple[float, ...] = (-0.05, -0.02, 0.02, 0.05)


def _scale(g: Greeks, qty: int) -> Greeks:
    return Greeks(delta=g.delta * qty, gamma=g.gamma * qty, theta=g.theta * qty, vega=g.vega * qty, rho=g.rho * qty)


def _sum_greeks(greeks: list[Greeks]) -> Greeks:
    if not greeks:
        return Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)
    return Greeks(
        delta=sum(g.delta for g in greeks), gamma=sum(g.gamma for g in greeks),
        theta=sum(g.theta for g in greeks), vega=sum(g.vega for g in greeks), rho=sum(g.rho for g in greeks),
    )


@dataclass(frozen=True)
class OptionPositionGreeks:
    symbol: str
    underlying: str
    strike: float
    option_type: str
    qty: int
    greeks: Greeks  # already position-scaled (per-contract greek * qty)


@dataclass(frozen=True)
class StressRow:
    shift_pct: float
    shifted_spot: float
    pnl: float  # portfolio option book P&L for THIS underlying's legs only, vs. current marks


@dataclass(frozen=True)
class UnderlyingStress:
    underlying: str
    spot: float
    rows: list[StressRow]


@dataclass(frozen=True)
class GreeksResponse:
    aggregate: Greeks
    positions: list[OptionPositionGreeks]
    stress: list[UnderlyingStress]


def _stress_underlying(underlying: str, spot: float, legs: list[tuple[Order, int]], sigma0: float) -> UnderlyingStress:
    """sigma0 is held FIXED across the shifted-spot scenarios below --
    that's a deliberate simplification (a real move would also shift
    realized/implied vol, a "vol smile moving with spot" effect this
    model doesn't attempt), not an oversight: the stress test isolates
    the underlying PRICE's effect on the book, the same "one variable at
    a time" convention a real desk's spot-only stress ladder uses."""
    rows = []
    for shift in STRESS_SHIFTS:
        shifted_spot = spot * (1.0 + shift)
        pnl = 0.0
        for meta, qty in legs:
            T = time_to_expiry_years(meta.expiry)
            current_price = bsm_price(spot, meta.strike, T, RISK_FREE_RATE, smile_iv(meta.strike, spot, sigma0), meta.option_type)
            shifted_price = bsm_price(
                shifted_spot, meta.strike, T, RISK_FREE_RATE, smile_iv(meta.strike, shifted_spot, sigma0), meta.option_type,
            )
            pnl += qty * (shifted_price - current_price)
        rows.append(StressRow(shift_pct=shift, shifted_spot=shifted_spot, pnl=pnl))
    return UnderlyingStress(underlying=underlying, spot=spot, rows=rows)


def get_portfolio_greeks(db: Session, user: User, registry: MarketRegistry) -> GreeksResponse:
    orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
    option_orders = [o for o in orders if o.instrument_type == InstrumentType.option]

    base_prices = registry.current_prices()
    option_marks = mark_option_positions(db, user.id, registry)
    prices = {**base_prices, **option_marks}

    # Every order for a given option symbol carries IDENTICAL underlying/
    # strike/expiry/option_type by construction (they're set once, atomically,
    # at submit_option_paper_order time and a contract key encodes exactly
    # those fields) -- so any one order for a symbol is a valid source of
    # that symbol's option identity; last one found simply wins.
    meta_by_symbol: dict[str, Order] = {o.symbol: o for o in option_orders}

    snapshot = compute_account(orders, prices, only_primary=True)

    positions: list[OptionPositionGreeks] = []
    per_position_greeks: list[Greeks] = []
    by_underlying: dict[str, list[tuple[Order, int]]] = {}
    sigma0_by_underlying: dict[str, float] = {}

    for symbol, pos in snapshot.positions.items():
        meta = meta_by_symbol.get(symbol)
        if meta is None or pos.qty == 0:
            continue
        spot = prices[meta.underlying]
        T = time_to_expiry_years(meta.expiry)
        if meta.underlying not in sigma0_by_underlying:
            sigma0_by_underlying[meta.underlying] = live_atm_sigma(meta.underlying, registry)
        iv = smile_iv(meta.strike, spot, sigma0_by_underlying[meta.underlying])
        g = _scale(bsm_greeks(spot, meta.strike, T, RISK_FREE_RATE, iv, meta.option_type), pos.qty)

        positions.append(OptionPositionGreeks(
            symbol=symbol, underlying=meta.underlying, strike=meta.strike,
            option_type=meta.option_type, qty=pos.qty, greeks=g,
        ))
        per_position_greeks.append(g)
        by_underlying.setdefault(meta.underlying, []).append((meta, pos.qty))

    stress = [
        _stress_underlying(underlying, prices[underlying], legs, sigma0_by_underlying[underlying])
        for underlying, legs in by_underlying.items()
    ]

    return GreeksResponse(aggregate=_sum_greeks(per_position_greeks), positions=positions, stress=stress)
