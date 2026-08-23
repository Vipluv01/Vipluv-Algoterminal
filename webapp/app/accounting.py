"""Derives positions, average entry price, and P&L purely from filled
Order rows -- there is no separate positions table to keep in sync (see
models/trading.py's own docstring on why: bourse's matching engine treats
Position() the same way, and a two-sources-of-truth bug already bit the
live demo's own order tracking earlier this session).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.trading import Order, OrderStatus, Side

STARTING_PAPER_CASH_DEFAULT = 100_000.0


@dataclass(frozen=True)
class SymbolPosition:
    symbol: str
    qty: int          # signed: positive = long, negative = short
    avg_entry_px: float
    realized_pnl: float
    unrealized_pnl: float


@dataclass(frozen=True)
class AccountSnapshot:
    cash: float
    positions: dict[str, SymbolPosition]
    total_realized_pnl: float
    total_unrealized_pnl: float

    @property
    def total_value(self) -> float:
        return self.cash + sum(p.unrealized_pnl + p.qty * p.avg_entry_px for p in self.positions.values())


@dataclass(frozen=True)
class TradeRealization:
    """One individual close/reduce/flip event -- the unit win-rate,
    profit factor, and avg-win/avg-loss are computed over. A single Order
    row produces at most one of these (opening or adding to a position
    never realizes anything)."""

    order_id: int
    symbol: str
    strategy_key: str | None
    amount: float
    created_at: object  # datetime, left untyped here to avoid importing datetime just for a hint


@dataclass(frozen=True)
class EquityPoint:
    """starting_cash + cumulative realized P&L as of this fill --
    deliberately NOT cash + live mark-to-market (that would need a real
    price at every past moment this curve passes through, which nothing
    records). This is "closed-book" equity: it moves only when a trade
    actually realizes P&L, so it's real and reconstructible from Order
    history alone, at the cost of not reflecting an open position's
    paper gain/loss between fills -- an honest tradeoff, not an
    approximation dressed up as a live equity curve."""

    order_id: int
    created_at: object
    equity: float


@dataclass
class _WalkResult:
    cash: float
    qty: dict[str, int] = field(default_factory=dict)
    avg_px: dict[str, float] = field(default_factory=dict)
    realized_by_symbol: dict[str, float] = field(default_factory=dict)
    realizations: list[TradeRealization] = field(default_factory=list)
    equity_points: list[EquityPoint] = field(default_factory=list)


def _filled_orders_only(orders: list[Order]) -> list[Order]:
    return [o for o in orders
            if o.status in (OrderStatus.filled, OrderStatus.partially_filled) and o.filled_qty > 0]


def _walk_fills(orders: list[Order], starting_cash: float) -> _WalkResult:
    """Single shared pass over every fill in chronological order,
    maintaining running qty/avg-entry-price/realized-P&L per symbol via
    standard weighted-average-cost accounting -- both compute_account and
    compute_realizations are thin views over this same walk, so the
    close/flip/partial-reduce logic exists in exactly one place rather
    than risking two accounting implementations drifting apart.
    """
    result = _WalkResult(cash=starting_cash)
    running_realized = 0.0

    for o in sorted(_filled_orders_only(orders), key=lambda o: o.created_at):
        sym = o.symbol
        fill_qty = o.filled_qty
        fill_px = o.avg_fill_px if o.avg_fill_px is not None else (o.px or 0.0)
        signed_fill = fill_qty if o.side == Side.buy else -fill_qty

        result.cash -= signed_fill * fill_px

        prev_qty = result.qty.get(sym, 0)
        prev_avg = result.avg_px.get(sym, 0.0)
        result.realized_by_symbol.setdefault(sym, 0.0)

        new_qty = prev_qty + signed_fill

        if prev_qty == 0 or (prev_qty > 0) == (signed_fill > 0):
            # Opening or ADDING to a position in the same direction --
            # blend the average entry price, don't realize anything yet.
            total_cost = prev_avg * abs(prev_qty) + fill_px * abs(signed_fill)
            result.avg_px[sym] = total_cost / abs(new_qty) if new_qty != 0 else 0.0
        else:
            # Reducing or flipping -- the portion that closes existing
            # exposure realizes P&L against the OLD average entry price;
            # any excess beyond that opens a fresh position at fill_px.
            closing_qty = min(abs(signed_fill), abs(prev_qty))
            direction = 1 if prev_qty > 0 else -1
            amount = closing_qty * direction * (fill_px - prev_avg)
            result.realized_by_symbol[sym] += amount
            running_realized += amount
            result.realizations.append(TradeRealization(
                order_id=o.id, symbol=sym, strategy_key=o.strategy_key,
                amount=amount, created_at=o.created_at,
            ))
            if abs(signed_fill) > abs(prev_qty):
                # Flipped through flat -- the excess is a brand new
                # position on the other side, priced at this fill.
                result.avg_px[sym] = fill_px
            elif new_qty == 0:
                result.avg_px[sym] = 0.0
            # else: partial reduction, average entry price is unchanged.

        result.qty[sym] = new_qty
        # A point after EVERY fill, not just realizing ones -- an opening
        # fill leaves equity unchanged (running_realized doesn't move),
        # which is correct: the curve should read flat while a position
        # is simply being held/built, not silently skip that stretch of
        # real trading activity.
        result.equity_points.append(EquityPoint(
            order_id=o.id, created_at=o.created_at, equity=starting_cash + running_realized,
        ))

    return result


def compute_account(
    orders: list[Order],
    current_prices: dict[str, float],
    starting_cash: float = STARTING_PAPER_CASH_DEFAULT,
) -> AccountSnapshot:
    w = _walk_fills(orders, starting_cash)

    positions: dict[str, SymbolPosition] = {}
    total_unrealized = 0.0
    for sym, q in w.qty.items():
        if q == 0:
            continue
        mark = current_prices.get(sym, w.avg_px[sym])
        unrealized = q * (mark - w.avg_px[sym])
        total_unrealized += unrealized
        positions[sym] = SymbolPosition(
            symbol=sym, qty=q, avg_entry_px=w.avg_px[sym],
            realized_pnl=w.realized_by_symbol.get(sym, 0.0), unrealized_pnl=unrealized,
        )

    return AccountSnapshot(
        cash=w.cash, positions=positions,
        total_realized_pnl=sum(w.realized_by_symbol.values()),
        total_unrealized_pnl=total_unrealized,
    )


def compute_realizations(orders: list[Order], starting_cash: float = STARTING_PAPER_CASH_DEFAULT) -> list[TradeRealization]:
    """Every individual close/reduce/flip event, in chronological order --
    the raw material for win rate, profit factor, and avg win/loss
    (app/routers/dashboard.py)."""
    return _walk_fills(orders, starting_cash).realizations


def compute_equity_curve(orders: list[Order], starting_cash: float = STARTING_PAPER_CASH_DEFAULT) -> list[EquityPoint]:
    """One point per fill, chronological -- see EquityPoint's own
    docstring for exactly what this does and doesn't represent (realized
    equity, not live mark-to-market)."""
    return _walk_fills(orders, starting_cash).equity_points
