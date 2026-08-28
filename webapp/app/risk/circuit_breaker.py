"""Daily drawdown circuit breaker: if a user's account has moved against
them by more than RiskSettings.daily_max_drawdown_pct since the start of
today, halt trading and flatten every open position.

The halt is DELIBERATELY not self-clearing at the next tick, the next
day, or anywhere else automatic -- once check_circuit_breaker sets
trading_halted, it stays set until a human calls POST /risk/reset-halt
(routers/risk.py). A breaker that quietly re-arms itself defeats the
entire point: "stop trading until a person looks at this," not "pause for
a bit." check_circuit_breaker itself is written to be a safe no-op once
already halted (it returns immediately), so re-running it every tick
forever costs nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.accounting import STARTING_PAPER_CASH_DEFAULT, compute_account, compute_realizations
from app.broker.notify import notify_circuit_breaker_trip
from app.markets import MarketRegistry
from app.models.trading import InstrumentType, Mode, Order, SubAccount
from app.models.user import User
from app.options.execution import mark_option_positions, submit_option_paper_order
from app.pairs_service import submit_paper_order
from app.risk_settings_service import get_or_create_risk_settings


def _prices_with_option_marks(db: Session, user_id: int, registry: MarketRegistry) -> dict[str, float]:
    """Every real/derived price PLUS every open option contract's live
    BSM mark -- the same merge app/routers/account.py's GET /account does
    before calling compute_account (see app/options/execution.py's
    mark_option_positions docstring). Without this, an open option
    position's genuine unrealized swing would be invisible to BOTH the
    drawdown check (current_equity would silently exclude it) and to
    _liquidate_every_position (which decides what qty to flatten from the
    exact same compute_account call)."""
    return {**registry.current_prices(), **mark_option_positions(db, user_id, registry)}

LIQUIDATION_STRATEGY_KEY = "circuit_breaker_liquidation"


def _today_utc() -> date:
    """Today's calendar date in UTC -- matches Order.created_at's own
    timezone (models/trading.py defaults it to datetime.now(timezone.utc)).

    Compared via .date(), not a full datetime comparison: SQLite has no
    native datetime type, and a value written as tz-AWARE comes back
    tz-NAIVE after a round trip through it (a real, already-encountered
    characteristic of this stack -- app/dashboard_stats.py and
    optimizer_returns.py both already group by created_at.date() for
    exactly this reason, never by comparing full datetimes). The numeric
    year/month/day the naive value carries still reflects the original
    UTC moment, so .date() stays correct across that round trip even
    though a raw `<` comparison between aware and naive values would
    raise TypeError.
    """
    return datetime.now(timezone.utc).date()


def _account_scopes(db: Session, user_id: int) -> list[int | None]:
    """None (the primary book) plus every currently-active sub-account --
    the circuit breaker treats each as its own risk pool with its own
    equity, matching how each already gets its own independent $100k
    starting-cash ledger (app.accounting.compute_account's sub_account_id
    filter)."""
    active_ids = [
        sub_id for (sub_id,) in
        db.query(SubAccount.id).filter(SubAccount.user_id == user_id, SubAccount.is_active.is_(True)).all()
    ]
    return [None, *active_ids]


def check_circuit_breaker(db: Session, user: User, registry: MarketRegistry) -> bool:
    """Returns True if trading is halted for this user (whether it was
    ALREADY halted coming in, or this call is what just halted it) --
    callers that only care about "should I block this?" can use the
    return value directly without a second RiskSettings lookup.
    """
    settings = get_or_create_risk_settings(db, user.id)
    if settings.trading_halted:
        # Already halted: keep attempting to flatten any residual position
        # -- a liquidation market order can PARTIALLY fill against thin
        # seeded liquidity (verified directly: an 80-share liquidation
        # order filled only 30 against this book's seeded depth), so one
        # attempt is not guaranteed to fully flatten. Retrying every tick
        # is safe and self-limiting, not an ever-growing order spam: once
        # a symbol's position is flat, compute_account reports qty=0 for
        # it and the loop below has nothing left to submit.
        current_prices = _prices_with_option_marks(db, user.id, registry)
        all_orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()
        _liquidate_every_position(db, registry, user_id=user.id, all_orders=all_orders, current_prices=current_prices)
        db.commit()
        return True

    current_prices = registry.current_prices()
    today = _today_utc()
    all_orders = db.query(Order).filter(Order.user_id == user.id, Order.mode == Mode.paper).all()

    # starting_equity is deliberately NOT compute_account(orders_before_
    # today, current_prices).total_value -- that marks yesterday's
    # position at TODAY's live price, which is identical to today's own
    # book value whenever no new order has happened yet today (same qty,
    # same mark), making day_pnl silently zero regardless of how far the
    # price actually moved against a position simply being HELD overnight.
    # Verified directly: a position bought yesterday, never touched today,
    # produced day_pnl=0 even after its unrealized P&L moved by tens of
    # thousands -- exactly the drawdown a circuit breaker exists to catch.
    #
    # Instead, starting_equity is built from REALIZED P&L only, up to
    # (not including) today -- a cost-basis baseline with no live mark
    # involved -- and current_equity is the real, fully mark-to-market
    # account value right now. Their difference is then, by construction,
    # today's realized P&L (from any orders that closed today) PLUS the
    # full unrealized P&L on whatever is open right now (which is exactly
    # the live exposure a circuit breaker needs to see, whether that
    # position was opened today or is simply still being held).
    # Neither sub_account_id nor only_primary given -- every order counts,
    # primary and every sub-account together, since the circuit breaker's
    # job is protecting the user's TOTAL paper risk, not one book at a time.
    realized_before_today = sum(
        r.amount for r in compute_realizations(all_orders) if r.created_at.date() < today
    )
    starting_equity = STARTING_PAPER_CASH_DEFAULT + realized_before_today
    current_equity = compute_account(all_orders, current_prices).total_value

    if starting_equity <= 0:
        return False  # nothing meaningful to compute a percentage against

    day_pnl = current_equity - starting_equity
    drawdown_fraction = abs(day_pnl) / starting_equity
    threshold = settings.daily_max_drawdown_pct / 100.0

    if drawdown_fraction < threshold:
        return False

    settings.trading_halted = True
    db.commit()
    # Fire-and-forget, off this tick's own critical path -- see
    # app/broker/notify.py's own docstring on why this can never become
    # another synchronous, unoffloaded call in the tick loop's hot path.
    # Only reached on the actual False->True transition (the already-
    # halted branch above returns before this point), so this fires once
    # per halt event, not once per tick while halted.
    notify_circuit_breaker_trip(user_id=user.id)

    _liquidate_every_position(db, registry, user_id=user.id, all_orders=all_orders, current_prices=current_prices)
    db.commit()
    return True


def _liquidate_every_position(
    db: Session, registry: MarketRegistry, *, user_id: int, all_orders: list[Order], current_prices: dict[str, float],
) -> None:
    """One market order per (scope, symbol) with an open position, sized
    to exactly that scope's net quantity, opposite side -- flattening the
    primary book AND every active sub-account independently, since a
    sub-account's position can differ from a simple multiple of the
    primary's (partial fills, different fill prices per clone).

    Submitted with clone_to_sub_accounts=False and an explicit
    sub_account_id: this function is ALREADY iterating every scope
    itself, so letting submit_paper_order's own auto-cloning run here too
    would double-submit into every sub-account.

    An open OPTION position is routed through submit_option_paper_order
    instead -- symbol alone isn't enough to submit an equity order (it's a
    contract key, not a NAMED_INSTRUMENTS ticker, and there is no
    SymbolMarket for it to route through -- see app/options/execution.py's
    own docstring), so submit_paper_order would raise KeyError on
    registry[symbol] the first time this ever ran against an open option
    position. Options are never cloned into sub-accounts (no strategy in
    this codebase does that), so an option symbol only ever appears in the
    scope=None (primary) iteration below.
    """
    option_meta_by_symbol = {o.symbol: o for o in all_orders if o.instrument_type == InstrumentType.option}

    for scope in _account_scopes(db, user_id):
        # compute_account does its own scope filtering over the FULL order
        # list -- passing an already-pre-filtered list here would just be
        # filtering twice for no benefit.
        snapshot = (
            compute_account(all_orders, current_prices, sub_account_id=scope) if scope is not None
            else compute_account(all_orders, current_prices, only_primary=True)
        )
        for symbol, position in snapshot.positions.items():
            if position.qty == 0:
                continue
            side = "sell" if position.qty > 0 else "buy"
            option_meta = option_meta_by_symbol.get(symbol)
            if option_meta is not None:
                submit_option_paper_order(
                    db, registry, user_id=user_id, strategy_key=LIQUIDATION_STRATEGY_KEY,
                    underlying=option_meta.underlying, option_type=option_meta.option_type,
                    strike=option_meta.strike, expiry_iso=option_meta.expiry,
                    side=side, qty=abs(position.qty), lot_size=option_meta.lot_size or 1,
                )
            else:
                submit_paper_order(
                    db, registry, user_id=user_id, strategy_key=LIQUIDATION_STRATEGY_KEY, symbol=symbol,
                    side=side, qty=abs(position.qty), order_type="market", px=None,
                    sub_account_id=scope, clone_to_sub_accounts=False,
                )


def run_circuit_breakers_once(db: Session, registry: MarketRegistry) -> None:
    """Per-tick entry point (app/main.py's _tick_loop) -- check_circuit_
    breaker itself is per-user (the natural unit: each user's own drawdown
    against their own equity), so this is the thin fan-out over every user
    who has ANY paper trading history, matching the same per-user-query
    pattern run_strategies_once already uses for allocations rather than
    assuming a single global user."""
    user_ids = [
        uid for (uid,) in
        db.query(Order.user_id).filter(Order.mode == Mode.paper).distinct().all()
    ]
    if not user_ids:
        return
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    for user in users:
        check_circuit_breaker(db, user, registry)
