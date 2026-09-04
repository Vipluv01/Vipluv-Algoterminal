"""The pairs_cointegration strategy's shared state and execution helpers.

This module exists to break a coupling that had gotten real: `routers/
pairs.py` (the Pair Overview / Pair Analytics pages -- see that module's
docstring on why this strategy gets dedicated pages) was reaching into
FOUR names inside `strategy_runner.py`, three of them underscore-prefixed
privates (`_current_pair_position`, `_PAIRS_STRATEGY`, `_submit_paper_
order`). `strategy_runner.py` is the tick-loop dispatcher for EVERY
strategy, not just pairs -- a router importing another module's private
implementation details, across an unrelated boundary, is exactly the kind
of coupling that turns "rename a helper" into "grep every router first."

Everything the pairs strategy and its API need to derive live state now
lives here instead, with no leading underscores: this is public API for
its two real callers (`strategy_runner.py`'s tick-loop dispatch, and
`routers/pairs.py`'s read-only views), not an implementation detail either
one owns privately.

`submit_paper_order` is admittedly not pairs-specific -- both the four
single-instrument strategies and pairs use it to turn a Signal into a real
paper order. It is colocated here anyway, rather than in a fourth module,
because pairs.py's `force_close` is the one place OUTSIDE strategy_runner
that needs to submit an order at all (closing a position manually), and
that need is what created the coupling problem in the first place. If a
second such need shows up outside strategy_runner, that is the signal to
split it out into its own module.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.brackets import cancel_brackets_closed_elsewhere
from app.markets import HUMAN_USER_OWNER_ID, MarketRegistry
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side
from app.quant.stationarity import ADFResult, JohansenResult, adf_test, half_life, hurst_exponent, johansen_test
from app.strategies.pairs_cointegration import PairsCointegrationStrategy, compute_pair_stats

PAIRS_STRATEGY_KEY = "pairs_cointegration"
PAIRS_SYMBOL_A = "ICICIBANK"
PAIRS_SYMBOL_B = "HDFCBANK"

# One instance is process-wide, not per-user, because pair_position tracking
# flows from actual filled orders (queried fresh in current_pair_position
# below), not from anything the strategy object itself remembers -- see
# pairs_cointegration.py's own docstring on why it's deliberately NOT
# stateless about position but also doesn't hide that state internally.
PAIRS_STRATEGY = PairsCointegrationStrategy()


# --- Stationarity telemetry (ADF / Johansen / Hurst / half-life) --------
#
# Computed on the SAME tick cadence app/main.py's _tick_loop already runs
# strategies/brackets/circuit-breakers on (once per real second, via
# refresh_pair_telemetry_once below), NOT per HTTP request. The live
# pairs_cointegration strategy's own coint() call is already the
# documented per-bar cost hotspot (see pairs_cointegration.py's own
# NOT-YET-DONE comment on evaluate_pair, and sim/KNOWN_ISSUES.md's fee-
# drag section, which records the measured 5.5ms->76.2ms->499ms superlinear
# growth) -- adding a SECOND full-history statistical battery as a new
# per-request hot path on top of that would make the same mistake again.
# Caching a value computed once per tick and serving it here, with its own
# age, is the fix: a screen showing this can display "as of 2s ago"
# honestly instead of pretending each request re-measured it.
#
# Bounded to the last PAIR_TELEMETRY_WINDOW points, not the full unbounded
# price history (SymbolMarket.price_history has no maxlen -- see its own
# definition) -- for the same reason the trailing-window comment on
# evaluate_pair exists: an ever-growing input to an O(n) or worse
# statistical routine, run forever on a fixed cadence, eventually stops
# being cheap regardless of how rarely it's called.
PAIR_TELEMETRY_WINDOW = 300


@dataclass(frozen=True)
class PairTelemetry:
    computed_at: datetime
    n_points: int
    adf: ADFResult
    johansen: JohansenResult
    hurst: float                # < 0.5 mean-reverting, 0.5 random walk, > 0.5 trending
    half_life_bars: float       # OU mean-reversion half-life, in bars; +inf if not mean-reverting


_pair_telemetry: PairTelemetry | None = None


def get_pair_telemetry() -> PairTelemetry | None:
    """None until the first tick after startup has had enough history to
    compute from (PAIRS_STRATEGY.min_history bars) -- never a fabricated
    placeholder in the meantime."""
    return _pair_telemetry


def reset_pair_telemetry() -> None:
    """Called once per app lifespan startup (app/main.py), right after a
    fresh MarketRegistry is created -- this cache is a plain MODULE-level
    global, so without an explicit reset it would otherwise survive past
    the registry it was computed against (a real leak across successive
    TestClient(app) lifespans in the test suite, and the conceptually
    correct thing to do on any real restart too: a telemetry reading from
    a previous process's price history has no meaning against a freshly
    seeded one)."""
    global _pair_telemetry
    _pair_telemetry = None


def refresh_pair_telemetry_once(registry: MarketRegistry) -> None:
    global _pair_telemetry
    prices_a = registry.prices(PAIRS_SYMBOL_A)
    prices_b = registry.prices(PAIRS_SYMBOL_B)
    if len(prices_a) < PAIRS_STRATEGY.min_history or len(prices_a) != len(prices_b):
        return

    window = PAIR_TELEMETRY_WINDOW
    a = prices_a[-window:]
    b = prices_b[-window:]

    stats = compute_pair_stats(
        a, b, zscore_window=PAIRS_STRATEGY.zscore_window, coint_pvalue_max=PAIRS_STRATEGY.coint_pvalue_max,
        min_history=PAIRS_STRATEGY.min_history, series_length=len(a),
    )
    if stats is None:
        return
    spread = stats.spread_series

    try:
        _pair_telemetry = PairTelemetry(
            computed_at=datetime.now(timezone.utc),
            n_points=len(spread),
            adf=adf_test(spread),
            johansen=johansen_test(a, b),
            hurst=hurst_exponent(spread),
            half_life_bars=half_life(spread),
        )
    except ValueError:
        # hurst_exponent/half_life can legitimately refuse on a
        # degenerate window (e.g. a perfectly flat spread on a
        # freshly-seeded market) -- leave the previous cached value (or
        # None) in place rather than crash the tick loop over a display
        # statistic.
        pass


@dataclass(frozen=True)
class PairPositionState:
    position: str  # "none" | "long_spread" | "short_spread"
    qty_a: int      # ABSOLUTE held quantity on symbol_a, 0 when flat


@dataclass
class _PairPositionCacheEntry:
    """Bounded, resumable state for compute_pair_position_state's own
    incremental cache below -- just the running signed sum on symbol_a
    plus the watermark it was folded in through, not a full accounting
    walk (this function never needed avg-entry-price/realized-P&L, only
    "how many shares of symbol_a, net, has this strategy filled").
    """
    net_a: int = 0
    last_order_id: int = 0


# Keyed by (user_id, strategy_key, symbol_a) -- process-local, same scope
# as every other cache in this app (app/accounting.py's own
# get_cached_account_snapshot, app/broker/adapter_cache.py). Confirmed
# live, 2026-09-04: this function is called from BOTH the tick loop
# (strategy_runner.py's run_strategies_once, once per second for every
# active pairs-shaped allocation) and routers/pairs.py's read-only Pair
# Overview/Analytics pages (polled every 5s) -- a fresh
# db.query(Order)...all() + Python-side sum on every one of those calls
# was a real, continuously-paid cost on the SAME strategy_key-tagged
# order history GET /account's own latency fix (2026-09-04) already
# solved for the primary account view, just not for this one.
_pair_position_cache: dict[tuple[int, str, str], _PairPositionCacheEntry] = {}
_pair_position_cache_lock = threading.Lock()


def compute_pair_position_state(
    db: Session, user_id: int, *, strategy_key: str = PAIRS_STRATEGY_KEY, symbol_a: str = PAIRS_SYMBOL_A,
) -> PairPositionState:
    """Derives which side of the spread (if any) this user currently
    holds, from filled orders tagged with THIS strategy_key -- not a
    separate tracked field, same "derive it, don't store a second copy"
    discipline app/accounting.py already uses for positions.

    Parameterized by strategy_key/symbol_a (both default to the original
    pairs_cointegration pair) because more than one pairs-shaped strategy
    now exists: pairs_kelly.py trades the SAME symbol pair but must track
    ITS OWN position independently -- one strategy's open spread must
    never be mistaken for another's, the same isolation
    routers/pairs.py's _open_legs already relies on for manual trades vs.
    strategy fills.

    Incremental, same shape as accounting.get_cached_account_snapshot:
    only orders with id > the last one already folded in are fetched on a
    repeat call, and a plain running sum (not the full weighted-average-
    cost walk _apply_fill does) is all this ever needed, since a pairs
    position is always entered/closed in full round-trip-sized clips, not
    scaled in and out the way a single-instrument position can be.
    id (not created_at) as the watermark/fetch order is safe here for the
    same reason it already is elsewhere: summation doesn't care what
    order the terms arrive in, only that none are double-counted or
    skipped, and id is DB-assigned monotonically with insertion order in
    this app's single-writer session model.
    """
    key = (user_id, strategy_key, symbol_a)
    with _pair_position_cache_lock:
        entry = _pair_position_cache.get(key)
        base_query = db.query(Order).filter(
            Order.user_id == user_id, Order.strategy_key == strategy_key,
            Order.mode == Mode.paper, Order.status.in_([OrderStatus.filled, OrderStatus.partially_filled]),
        )

        if entry is not None and entry.last_order_id:
            # Same staleness guard as accounting.get_cached_account_
            # snapshot, scoped through base_query for the same reason --
            # see that function's own comment on why a bare Order.id ==
            # ... lookup isn't enough (a fresh test database, or a
            # different user/strategy_key, can satisfy it by coincidence).
            watermark_still_exists = base_query.filter(Order.id == entry.last_order_id).first()
            if watermark_still_exists is None:
                entry = None
        if entry is None:
            entry = _PairPositionCacheEntry()

        query = base_query
        if entry.last_order_id:
            query = query.filter(Order.id > entry.last_order_id)

        for o in query.order_by(Order.id).all():
            if o.symbol == symbol_a:
                entry.net_a += o.filled_qty if o.side == Side.buy else -o.filled_qty
            entry.last_order_id = o.id

        _pair_position_cache[key] = entry
        net_a = entry.net_a

    if net_a > 0:
        return PairPositionState(position="long_spread", qty_a=net_a)
    if net_a < 0:
        return PairPositionState(position="short_spread", qty_a=-net_a)
    return PairPositionState(position="none", qty_a=0)


def current_pair_position(
    db: Session, user_id: int, *, strategy_key: str = PAIRS_STRATEGY_KEY, symbol_a: str = PAIRS_SYMBOL_A,
) -> str:
    """Thin wrapper over compute_pair_position_state for callers (routers/
    pairs.py) that only need the position string, not the held quantity --
    kept as its own function so those call sites don't change."""
    return compute_pair_position_state(db, user_id, strategy_key=strategy_key, symbol_a=symbol_a).position


def submit_paper_order(db: Session, registry: MarketRegistry, *, user_id: int, strategy_key: str,
                        symbol: str, side: str, qty: int, order_type: str, px: float | None,
                        entry_zscore: float | None = None, sub_account_id: int | None = None,
                        parent_order_id: int | None = None, clone_to_sub_accounts: bool = True) -> None:
    """clone_to_sub_accounts (default True) is what makes a strategy's
    signal apply at every ACTIVE sub-account's own size, not just the
    primary book -- see app/models/trading.py's SubAccount docstring. It
    is automatically forced off for the recursive per-sub-account calls
    below (sub_account_id is not None), so a sub-account's own order is
    never itself re-cloned, and callers that already know exactly which
    single scope they want (app/risk/circuit_breaker.py's liquidation,
    which iterates every scope itself and submits one order per scope)
    pass it explicitly to opt out too.
    """
    market = registry[symbol]
    px_ticks = 0
    if order_type == "limit":
        from simulate import to_ticks_static
        px_ticks = to_ticks_static(px, market.tick_size)

    order_id = market.next_order_id()
    result = market.eng.submit(order_id=order_id, side=side, qty=qty, px=px_ticks,
                                owner=HUMAN_USER_OWNER_ID, order_type=order_type, tif="gtc")

    avg_fill_px = None
    if result.filled_qty > 0:
        total = sum(f.px * f.qty for f in result.fills)
        avg_fill_px = (total / result.filled_qty) * market.tick_size

    if result.accepted:
        status = OrderStatus.filled if result.filled_qty == qty else (
            OrderStatus.partially_filled if result.filled_qty > 0 else OrderStatus.submitted
        )
    else:
        status = OrderStatus.rejected

    db.add(Order(
        user_id=user_id, mode=Mode.paper, strategy_key=strategy_key, symbol=symbol,
        side=Side(side), order_type=OrderType(order_type), qty=qty, px=px, status=status,
        filled_qty=result.filled_qty, avg_fill_px=avg_fill_px, engine_order_id=order_id,
        entry_zscore=entry_zscore, sub_account_id=sub_account_id, parent_order_id=parent_order_id,
    ))

    if result.filled_qty > 0:
        # A strategy's own fill can reduce or close a position a manually-
        # placed bracket is still watching -- same reasoning as
        # routers/orders.py's own call to this, see brackets.py's
        # docstring on why cancelling (not resizing) is the safe response.
        # Sub-account clones are excluded: brackets are placed against the
        # PRIMARY account's own orders (routers/orders.py has no
        # sub-account concept yet), so a sub-account fill has no bracket
        # of its own that could need cancelling.
        if sub_account_id is None:
            cancel_brackets_closed_elsewhere(db, user_id=user_id, symbol=symbol, order_side=side)

    if clone_to_sub_accounts and sub_account_id is None:
        _clone_order_to_active_sub_accounts(
            db, registry, user_id=user_id, strategy_key=strategy_key, symbol=symbol,
            side=side, qty=qty, order_type=order_type, px=px, entry_zscore=entry_zscore,
            parent_order_id=parent_order_id,
        )


def _clone_order_to_active_sub_accounts(db: Session, registry: MarketRegistry, *, user_id: int, strategy_key: str,
                                         symbol: str, side: str, qty: int, order_type: str, px: float | None,
                                         entry_zscore: float | None, parent_order_id: int | None = None) -> None:
    from app.models.trading import SubAccount

    active = db.query(SubAccount).filter(SubAccount.user_id == user_id, SubAccount.is_active.is_(True)).all()
    for sub in active:
        # Never zero: a very small multiplier (e.g. 0.1x on a 1-share
        # signal) rounding to 0 would silently drop the sub-account's
        # trade entirely rather than sizing it down -- same floor pairs_
        # kelly.py and multi_basket.py already apply to their own
        # weight-scaled leg quantities, for the identical reason.
        scaled_qty = max(1, round(qty * sub.sizing_multiplier))
        submit_paper_order(
            db, registry, user_id=user_id, strategy_key=strategy_key, symbol=symbol,
            side=side, qty=scaled_qty, order_type=order_type, px=px, entry_zscore=entry_zscore,
            sub_account_id=sub.id, clone_to_sub_accounts=False,
            # Forwarded, not dropped: an algo-sliced order's sub-account
            # clone must stay traceable back to the SAME ParentOrder --
            # found live via a full-integration smoke test (all Phase 4
            # subsystems running together), where GET /orders/algo/{id}
            # would otherwise silently miss every sub-account's share of
            # an algo execution once any sub-account existed.
            parent_order_id=parent_order_id,
        )
