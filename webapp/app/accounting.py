"""Derives positions, average entry price, and P&L purely from filled
Order rows -- there is no separate positions table to keep in sync (see
models/trading.py's own docstring on why: bourse's matching engine treats
Position() the same way, and a two-sources-of-truth bug already bit the
live demo's own order tracking earlier this session).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from app.models.trading import Mode, Order, OrderStatus, Side

# symbol, created_at (an Order's own field, left untyped here the same way
# Order.created_at is below) -> a historical mark for that symbol at that
# moment, or None when this lookup has no historical price for it (e.g. a
# synthetic option contract, which has no price_history -- see
# app/routers/account.py's own price-lookup builder). None means "fall
# back to that position's own average entry price," the same honest
# "no live price" fallback compute_account already uses for current_prices.
PriceLookup = Callable[[str, object], "float | None"]

STARTING_PAPER_CASH_DEFAULT = 100_000.0

# Rs 1,00,00,000 (1 crore) -- Mode.virtual's own starting capital, a
# deliberately different figure from paper's, not a copy-paste of it. This
# whole platform is NSE/rupee-denominated (every simulated price, and
# eventually every live Angel One price, is in Rs); an original-spec-style
# "$100k" figure here would be exactly the kind of inconsistent number this
# project has been careful to avoid. Virtual mode exists specifically to
# rehearse position sizing at real-money scale before going live, which is
# also why it's ~100x paper's figure rather than matching it.
STARTING_VIRTUAL_CASH_DEFAULT = 1_00_00_000.0


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
class RealizedPnlPoint:
    """starting_cash + cumulative realized P&L as of this fill --
    deliberately NOT cash + live mark-to-market. This is Portfolio IQ's
    walk (app/routers/portfolio.py), kept realized-only ON PURPOSE:
    Brinson-Fachler attribution wants clean per-period realized returns,
    not mark-to-market noise from an open position's paper gain/loss
    between fills. Named `realized_pnl`, never `equity` -- see
    EquityPoint below for the genuine mark-to-market curve, and why the
    two must never share a label that lets them be read as disagreeing
    with each other."""

    order_id: int
    created_at: object
    realized_pnl: float


@dataclass(frozen=True)
class EquityPoint:
    """Genuine mark-to-market equity as of this fill: cash plus every
    then-open position valued at SymbolMarket.price_history's own value
    at that fill's timestamp (via the caller-supplied PriceLookup),
    falling back to that position's own average entry price when no
    historical mark exists for the symbol (e.g. a synthetic option
    contract). This is what GET /account/equity-curve exposes, and it
    now agrees with accounting.total_value at any point where no fill is
    pending -- the two used to disagree by exactly the unrealized P&L of
    every open position, which is what motivated this fix. Contrast with
    RealizedPnlPoint above, which is a deliberately different, realized-
    only curve for a different consumer (Portfolio IQ)."""

    order_id: int
    created_at: object
    equity: float


@dataclass
class _WalkState:
    """The CORE of a walk -- cash/qty/avg_px/realized_by_symbol -- kept
    as its own dataclass, separately from _WalkResult below, specifically
    because it's bounded in size (one dict entry per DISTINCT symbol
    ever traded, never one per order) where _WalkResult's own
    realizations/realized_pnl_points/equity_points lists are not (one
    entry per fill, forever). That boundedness is what makes this safe
    to cache and resume across calls (see get_cached_account_snapshot
    below) -- a growing order history doesn't grow this state's own
    memory footprint, only the cost of folding in whatever's new since
    last time.
    """
    cash: float
    qty: dict[str, int] = field(default_factory=dict)
    avg_px: dict[str, float] = field(default_factory=dict)
    realized_by_symbol: dict[str, float] = field(default_factory=dict)
    last_order_id: int = 0


@dataclass
class _WalkResult:
    cash: float
    qty: dict[str, int] = field(default_factory=dict)
    avg_px: dict[str, float] = field(default_factory=dict)
    realized_by_symbol: dict[str, float] = field(default_factory=dict)
    realizations: list[TradeRealization] = field(default_factory=list)
    realized_pnl_points: list[RealizedPnlPoint] = field(default_factory=list)
    equity_points: list[EquityPoint] = field(default_factory=list)


def _filled_orders_only(orders: list[Order]) -> list[Order]:
    return [o for o in orders
            if o.status in (OrderStatus.filled, OrderStatus.partially_filled) and o.filled_qty > 0]


def _apply_fill(state: _WalkState, o: Order) -> float | None:
    """Mutates `state` in place for exactly ONE filled order -- the same
    weighted-average-cost step _walk_fills' own loop used to inline
    directly, extracted so a full from-scratch walk (_walk_fills below)
    and an incremental, CACHED walk (get_cached_account_snapshot) share
    exactly one implementation of this logic, never two that could
    quietly drift apart over time. Returns the realized P&L amount if
    this fill closed/reduced/flipped a position, else None -- the caller
    decides what to do with a realization (record a TradeRealization, or
    just fold it into a running total)."""
    sym = o.symbol
    fill_qty = o.filled_qty
    fill_px = o.avg_fill_px if o.avg_fill_px is not None else (o.px or 0.0)
    signed_fill = fill_qty if o.side == Side.buy else -fill_qty

    state.cash -= signed_fill * fill_px

    prev_qty = state.qty.get(sym, 0)
    prev_avg = state.avg_px.get(sym, 0.0)
    state.realized_by_symbol.setdefault(sym, 0.0)

    new_qty = prev_qty + signed_fill
    realized_amount = None

    if prev_qty == 0 or (prev_qty > 0) == (signed_fill > 0):
        # Opening or ADDING to a position in the same direction --
        # blend the average entry price, don't realize anything yet.
        total_cost = prev_avg * abs(prev_qty) + fill_px * abs(signed_fill)
        state.avg_px[sym] = total_cost / abs(new_qty) if new_qty != 0 else 0.0
    else:
        # Reducing or flipping -- the portion that closes existing
        # exposure realizes P&L against the OLD average entry price;
        # any excess beyond that opens a fresh position at fill_px.
        closing_qty = min(abs(signed_fill), abs(prev_qty))
        direction = 1 if prev_qty > 0 else -1
        amount = closing_qty * direction * (fill_px - prev_avg)
        state.realized_by_symbol[sym] += amount
        realized_amount = amount
        if abs(signed_fill) > abs(prev_qty):
            # Flipped through flat -- the excess is a brand new
            # position on the other side, priced at this fill.
            state.avg_px[sym] = fill_px
        elif new_qty == 0:
            state.avg_px[sym] = 0.0
        # else: partial reduction, average entry price is unchanged.

    state.qty[sym] = new_qty
    state.last_order_id = o.id
    return realized_amount


def _walk_fills(orders: list[Order], starting_cash: float, price_lookup: PriceLookup | None = None) -> _WalkResult:
    """Single shared pass over every fill in chronological order,
    maintaining running qty/avg-entry-price/realized-P&L per symbol via
    standard weighted-average-cost accounting (_apply_fill above) --
    compute_account, compute_realizations, and compute_realized_pnl_curve
    are all thin views over this same walk, so the close/flip/partial-
    reduce logic exists in exactly one place rather than risking
    accounting implementations drifting apart.

    price_lookup, when given, additionally marks every then-open position
    to its historical price AT THIS FILL'S OWN TIMESTAMP after each fill,
    populating result.equity_points (compute_equity_curve's mark-to-market
    curve) alongside the always-computed realized_pnl_points. Omitted by
    every caller that only wants the realized-only view, so they don't
    pay for a price lookup they never use.
    """
    result = _WalkResult(cash=starting_cash)
    state = _WalkState(cash=starting_cash)
    running_realized = 0.0

    for o in sorted(_filled_orders_only(orders), key=lambda o: o.created_at):
        realized_amount = _apply_fill(state, o)
        result.cash = state.cash

        if realized_amount is not None:
            running_realized += realized_amount
            result.realizations.append(TradeRealization(
                order_id=o.id, symbol=o.symbol, strategy_key=o.strategy_key,
                amount=realized_amount, created_at=o.created_at,
            ))
        # A point after EVERY fill, not just realizing ones -- an opening
        # fill leaves realized_pnl unchanged (running_realized doesn't
        # move), which is correct: the curve should read flat while a
        # position is simply being held/built, not silently skip that
        # stretch of real trading activity.
        result.realized_pnl_points.append(RealizedPnlPoint(
            order_id=o.id, created_at=o.created_at, realized_pnl=starting_cash + running_realized,
        ))

        if price_lookup is not None:
            mark_to_market = state.cash
            for pos_sym, pos_qty in state.qty.items():
                if pos_qty == 0:
                    continue
                mark = price_lookup(pos_sym, o.created_at)
                if mark is None:
                    mark = state.avg_px[pos_sym]
                mark_to_market += pos_qty * mark
            result.equity_points.append(EquityPoint(
                order_id=o.id, created_at=o.created_at, equity=mark_to_market,
            ))

    result.qty = state.qty
    result.avg_px = state.avg_px
    result.realized_by_symbol = state.realized_by_symbol
    return result


def compute_account(
    orders: list[Order],
    current_prices: dict[str, float],
    starting_cash: float = STARTING_PAPER_CASH_DEFAULT,
    *,
    sub_account_id: int | None = None,
    only_primary: bool = False,
) -> AccountSnapshot:
    """sub_account_id, when given, restricts the walk to orders tagged
    with exactly that sub-account (app/models/trading.py's
    Order.sub_account_id) -- a sub-account is a FILTER over the same
    order history every other view of this account already uses, not a
    second accounting implementation with its own starting cash or fill
    logic. only_primary=True is the complementary filter: orders with NO
    sub_account_id at all (the user's own primary book), for a caller
    that wants the primary account's numbers to exclude every sub-
    account's activity -- passing neither argument preserves the
    original behavior exactly (every order, regardless of sub-account).
    """
    if sub_account_id is not None and only_primary:
        raise ValueError("sub_account_id and only_primary are mutually exclusive")
    if sub_account_id is not None:
        orders = [o for o in orders if o.sub_account_id == sub_account_id]
    elif only_primary:
        orders = [o for o in orders if o.sub_account_id is None]

    w = _walk_fills(orders, starting_cash)
    return _snapshot_from_walk_core(w.cash, w.qty, w.avg_px, w.realized_by_symbol, current_prices)


def _snapshot_from_walk_core(
    cash: float, qty: dict[str, int], avg_px: dict[str, float],
    realized_by_symbol: dict[str, float], current_prices: dict[str, float],
) -> AccountSnapshot:
    """The position/unrealized-P&L build-out shared by compute_account
    (a fresh _walk_fills result) and get_cached_account_snapshot below
    (a resumed, incrementally-updated _WalkState) -- same 4 raw numbers
    either way, so this exists once rather than being copy-pasted twice
    with the risk of the two drifting apart."""
    positions: dict[str, SymbolPosition] = {}
    total_unrealized = 0.0
    for sym, q in qty.items():
        if q == 0:
            continue
        mark = current_prices.get(sym, avg_px[sym])
        unrealized = q * (mark - avg_px[sym])
        total_unrealized += unrealized
        positions[sym] = SymbolPosition(
            symbol=sym, qty=q, avg_entry_px=avg_px[sym],
            realized_pnl=realized_by_symbol.get(sym, 0.0), unrealized_pnl=unrealized,
        )

    return AccountSnapshot(
        cash=cash, positions=positions,
        total_realized_pnl=sum(realized_by_symbol.values()),
        total_unrealized_pnl=total_unrealized,
    )


# Cached incremental walk state, keyed by (user_id, mode, sub_account_id,
# only_primary) -- process-local (this is a small, single-process dev
# deployment, the same scope every other in-memory cache in this
# codebase already assumes, e.g. app/broker/adapter_cache.py's own
# adapter cache). Guarded by a real lock: FastAPI runs a sync def route
# in a threadpool, so concurrent callers are already possible (the SAME
# real class of bug app/broker/instrument_master.py's own _load_lock was
# added to close, 2026-09-04) -- without one here, two requests arriving
# close together could each read the same stale state, apply the same
# new orders on top of it independently, and each write back their own
# now-diverged copy, silently corrupting the cached state for every
# subsequent caller.
_account_cache: dict[tuple[int, Mode, int | None, bool], _WalkState] = {}
_account_cache_lock = threading.Lock()


def get_cached_account_snapshot(
    db: Session, user_id: int, mode: Mode, current_prices: dict[str, float],
    starting_cash: float = STARTING_PAPER_CASH_DEFAULT,
    *, sub_account_id: int | None = None, only_primary: bool = False,
) -> AccountSnapshot:
    """Same real output as compute_account(<every order for this user/
    mode>, ...), but doesn't re-fetch and re-walk the user's ENTIRE order
    history on every single call. Confirmed live, 2026-09-04: a long-
    running paper account with 107,651 orders made GET /account
    (polled every few seconds by AccountPanel.js) genuinely slow --
    ~0.7s just to fetch and ORM-materialize that many rows, before the
    walk itself even starts, and that cost was paid again in full on
    every poll, forever, only growing as the account kept trading.

    The fix is incremental, not a shortcut around correctness: the walk
    state (_WalkState -- cash/qty/avg_px/realized_by_symbol, bounded by
    DISTINCT symbols ever traded, never by order count) is cached and
    resumed across calls, keyed by (user_id, mode, sub_account_id,
    only_primary). A repeat call only fetches orders with id > the last
    one already folded into the cached state -- a cheap, indexed query --
    and feeds ONLY those through _apply_fill, the exact same per-fill
    step _walk_fills itself uses (see that function's own docstring on
    why this is shared code, not a second implementation). The first
    call for a given key still does the full walk once, same as
    compute_account always has; every call after that is proportional to
    what's NEW since the last one, not to the account's whole history.

    id (not created_at) is both the fetch watermark and the ORDER BY --
    a real distinction from _walk_fills' own `sorted(..., key=created_at)`
    on the full list: id is DB-assigned, unique, and strictly monotonic
    with insertion order (this app's SQLite/Postgres session model has
    no concurrent-writer clock-skew risk to worry about), where
    created_at could in principle collide for two orders inserted in the
    same instant. Using id keeps the incremental walk deterministic and
    resumable without that ambiguity, and produces the SAME result as
    the full walk in practice since the two orderings agree here. Cross-
    checked directly (test_accounting.py's own incremental-vs-full-walk
    consistency test) rather than merely argued for.
    """
    if sub_account_id is not None and only_primary:
        raise ValueError("sub_account_id and only_primary are mutually exclusive")

    key = (user_id, mode, sub_account_id, only_primary)
    with _account_cache_lock:
        state = _account_cache.get(key)
        if state is not None and state.last_order_id:
            # Guard against a stale cache outliving the very order history
            # it was built from. No code path in this app ever deletes an
            # Order row (grep confirms it -- history is append-only,
            # cancelled/rejected orders included), so in real production
            # use `id` is a safe, permanent watermark. But this cache is
            # process-global (see the module docstring above), and the
            # database underneath it is not guaranteed to be: every
            # pytest `client` fixture stands up its OWN fresh in-memory
            # SQLite database (tests/conftest.py) reusing the same small
            # user/order ids each time, and in principle a real deploy
            # could restore/reset its DB without restarting the process.
            # Either way, "the last order id I folded in" can silently
            # stop meaning anything -- one cheap, indexed point lookup
            # here (not a size-dependent cost) catches that and falls
            # back to a full rebuild, the same cold-start cost the very
            # first call for a key already pays.
            watermark_still_exists = db.query(Order.id).filter(Order.id == state.last_order_id).first()
            if watermark_still_exists is None:
                state = None
        if state is None:
            state = _WalkState(cash=starting_cash)

        query = db.query(Order).filter(Order.user_id == user_id, Order.mode == mode)
        if sub_account_id is not None:
            query = query.filter(Order.sub_account_id == sub_account_id)
        elif only_primary:
            query = query.filter(Order.sub_account_id.is_(None))
        if state.last_order_id:
            query = query.filter(Order.id > state.last_order_id)

        new_orders = _filled_orders_only(query.order_by(Order.id).all())
        for o in new_orders:
            _apply_fill(state, o)

        _account_cache[key] = state
        # Copy out under the lock, not a reference to the live dict --
        # current_prices.get(sym, ...) below never mutates state, but
        # this keeps the invariant simple: nothing outside this function
        # ever touches `state` after this point.
        cash, qty, avg_px, realized = state.cash, dict(state.qty), dict(state.avg_px), dict(state.realized_by_symbol)

    return _snapshot_from_walk_core(cash, qty, avg_px, realized, current_prices)


def compute_realizations(orders: list[Order], starting_cash: float = STARTING_PAPER_CASH_DEFAULT) -> list[TradeRealization]:
    """Every individual close/reduce/flip event, in chronological order --
    the raw material for win rate, profit factor, and avg win/loss
    (app/routers/dashboard.py)."""
    return _walk_fills(orders, starting_cash).realizations


@dataclass
class _RealizationsCacheEntry:
    """Same shape of problem as _WalkState/get_cached_account_snapshot,
    for a different consumer: dashboard_stats.compute_trade_stats and
    compute_day_stats both need every realized close/reduce/flip EVER, not
    just a terminal number, so unlike _WalkState this cache's own output
    (`realizations`) genuinely does grow one entry per realizing fill,
    forever. That's fine here specifically because neither consumer cares
    about insertion order (compute_trade_stats only sums/counts;
    compute_day_stats buckets by day and re-sorts) -- so this only has to
    get the WALK right, via the same `state`/_apply_fill pair
    get_cached_account_snapshot already uses, and can just append."""
    state: _WalkState
    realizations: list[TradeRealization] = field(default_factory=list)


_realizations_cache: dict[tuple[int, Mode], _RealizationsCacheEntry] = {}
_realizations_cache_lock = threading.Lock()


def get_cached_realizations(
    db: Session, user_id: int, mode: Mode, starting_cash: float = STARTING_PAPER_CASH_DEFAULT,
) -> list[TradeRealization]:
    """Incremental counterpart to compute_realizations, same fix as
    get_cached_account_snapshot and for the same reason: GET /dashboard/
    stats and GET /dashboard/calendar (app/routers/dashboard.py) each
    independently fetched every order for this user/mode and re-walked it
    from scratch on every call -- confirmed live, 2026-09-04, ~2.4s each
    against the real 107,651-order paper account, ~4.8s combined since
    Dashboard.js fetches both on every page load. Deliberately NOT scoped
    by sub_account_id/only_primary -- dashboard.py's own queries never
    filtered on those either, so this keeps that exact existing scope
    rather than quietly narrowing it.

    Keyed by (user_id, mode) only, a separate cache from
    _account_cache -- this one grows an unbounded list on purpose (see
    _RealizationsCacheEntry's own docstring), so it deliberately isn't
    folded into the bounded _WalkState cache above."""
    key = (user_id, mode)
    with _realizations_cache_lock:
        entry = _realizations_cache.get(key)
        if entry is not None and entry.state.last_order_id:
            # Same staleness guard as get_cached_account_snapshot, and for
            # the same real reason -- see that function's own comment.
            watermark_still_exists = db.query(Order.id).filter(Order.id == entry.state.last_order_id).first()
            if watermark_still_exists is None:
                entry = None
        if entry is None:
            entry = _RealizationsCacheEntry(state=_WalkState(cash=starting_cash))

        query = db.query(Order).filter(Order.user_id == user_id, Order.mode == mode)
        if entry.state.last_order_id:
            query = query.filter(Order.id > entry.state.last_order_id)

        new_orders = _filled_orders_only(query.order_by(Order.id).all())
        for o in new_orders:
            realized_amount = _apply_fill(entry.state, o)
            if realized_amount is not None:
                entry.realizations.append(TradeRealization(
                    order_id=o.id, symbol=o.symbol, strategy_key=o.strategy_key,
                    amount=realized_amount, created_at=o.created_at,
                ))

        _realizations_cache[key] = entry
        return list(entry.realizations)


def compute_realized_pnl_curve(
    orders: list[Order], starting_cash: float = STARTING_PAPER_CASH_DEFAULT,
) -> list[RealizedPnlPoint]:
    """One point per fill, chronological -- see RealizedPnlPoint's own
    docstring for exactly what this does and doesn't represent (realized
    P&L, not live mark-to-market). This is Portfolio IQ's walk
    (app/routers/portfolio.py) -- GET /account/equity-curve wants
    compute_equity_curve below instead."""
    return _walk_fills(orders, starting_cash).realized_pnl_points


def compute_equity_curve(
    orders: list[Order], price_lookup: PriceLookup, starting_cash: float = STARTING_PAPER_CASH_DEFAULT,
) -> list[EquityPoint]:
    """One point per fill, chronological, genuinely mark-to-market -- see
    EquityPoint's own docstring. price_lookup is required (not defaulted
    to None) so a caller can't silently get an all-fallback-to-avg-entry-
    price curve by forgetting to pass one; app/routers/account.py builds
    a real one from SymbolMarket.price_history."""
    return _walk_fills(orders, starting_cash, price_lookup=price_lookup).equity_points
