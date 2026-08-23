"""Evaluates enabled strategies against live market data each tick and
turns their signals into real (paper) orders.

market_maker is deliberately NOT one of the user-selectable strategies
here, even though it's one of the 5 in the strategy inventory: it already
runs continuously inside every SymbolMarket (app/markets.py's own
self.maker), providing the liquidity backdrop the OTHER 4 strategies trade
against. A user "enabling" it as a personal strategy would mean something
different (running their own competing quoting bot) and needs its own
continuous-refresh execution model, not the periodic-signal-check loop
the other 4 use -- tracked as a follow-up, not bolted on here just to hit
a count of 5.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.brackets import cancel_brackets_closed_elsewhere
from app.markets import MarketRegistry
from app.models.trading import Mode, Order, OrderStatus, OrderType, Side, StrategyAllocation
from app.strategies.alpha import AlphaRSIEMAStrategy
from app.strategies.base import MarketSnapshot
from app.strategies.mean_reversion_bb import MeanReversionBollingerStrategy
from app.strategies.momentum import MomentumMACDStrategy
from app.strategies.pairs_cointegration import PairSnapshot, PairsCointegrationStrategy

SINGLE_INSTRUMENT_STRATEGIES = {
    "alpha_rsi_ema": AlphaRSIEMAStrategy(),
    "momentum_macd": MomentumMACDStrategy(),
    "mean_reversion_bb": MeanReversionBollingerStrategy(),
}

PAIRS_STRATEGY_KEY = "pairs_cointegration"
PAIRS_SYMBOL_A = "ICICIBANK"
PAIRS_SYMBOL_B = "HDFCBANK"
_PAIRS_STRATEGY = PairsCointegrationStrategy()

# One PairsCointegrationStrategy instance is process-wide, not per-user,
# because pair_position tracking flows from actual filled orders (queried
# fresh below), not from anything the strategy object itself remembers --
# see pairs_cointegration.py's own docstring on why it's deliberately NOT
# stateless about position but also doesn't hide that state internally.


def _current_pair_position(db: Session, user_id: int) -> str:
    """Derives which side of the spread (if any) this user currently
    holds, from filled orders tagged with this strategy -- not a separate
    tracked field, same "derive it, don't store a second copy" discipline
    app/accounting.py already uses for positions."""
    orders = (
        db.query(Order)
        .filter(Order.user_id == user_id, Order.strategy_key == PAIRS_STRATEGY_KEY,
                Order.mode == Mode.paper, Order.status.in_([OrderStatus.filled, OrderStatus.partially_filled]))
        .order_by(Order.created_at)
        .all()
    )
    net_a = sum((o.filled_qty if o.side == Side.buy else -o.filled_qty) for o in orders if o.symbol == PAIRS_SYMBOL_A)
    if net_a > 0:
        return "long_spread"
    if net_a < 0:
        return "short_spread"
    return "none"


def _submit_paper_order(db: Session, registry: MarketRegistry, *, user_id: int, strategy_key: str,
                         symbol: str, side: str, qty: int, order_type: str, px: float | None) -> None:
    market = registry[symbol]
    px_ticks = 0
    if order_type == "limit":
        from simulate import to_ticks_static
        px_ticks = to_ticks_static(px, market.tick_size)

    order_id = market.next_order_id()
    result = market.eng.submit(order_id=order_id, side=side, qty=qty, px=px_ticks,
                                owner=1, order_type=order_type, tif="gtc")

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
    ))

    if result.filled_qty > 0:
        # A strategy's own fill can reduce or close a position a manually-
        # placed bracket is still watching -- same reasoning as
        # routers/orders.py's own call to this, see brackets.py's
        # docstring on why cancelling (not resizing) is the safe response.
        cancel_brackets_closed_elsewhere(db, user_id=user_id, symbol=symbol, order_side=side)


def run_strategies_once(db: Session, registry: MarketRegistry) -> None:
    """Called once per market tick (see app/main.py's tick loop). Evaluates
    every user's enabled paper-mode StrategyAllocation rows and submits
    whatever orders the signals call for."""
    allocations = (
        db.query(StrategyAllocation)
        .filter(StrategyAllocation.enabled.is_(True), StrategyAllocation.mode == Mode.paper)
        .all()
    )

    for alloc in allocations:
        if alloc.strategy_key in SINGLE_INSTRUMENT_STRATEGIES:
            _run_single_instrument(db, registry, alloc)
        elif alloc.strategy_key == PAIRS_STRATEGY_KEY:
            _run_pairs(db, registry, alloc)
        # An unrecognized strategy_key (typo, a since-removed strategy) is
        # silently skipped rather than raised here -- one bad allocation
        # row must not take down every other user's strategy evaluation
        # for the whole tick.

    db.commit()


def _run_single_instrument(db: Session, registry: MarketRegistry, alloc: StrategyAllocation) -> None:
    if not alloc.symbol:
        return
    strategy = SINGLE_INSTRUMENT_STRATEGIES[alloc.strategy_key]
    prices = registry.prices(alloc.symbol)
    signal = strategy.evaluate(MarketSnapshot(symbol=alloc.symbol, prices=prices))
    if signal is None:
        return
    _submit_paper_order(
        db, registry, user_id=alloc.user_id, strategy_key=alloc.strategy_key, symbol=alloc.symbol,
        side=signal.side, qty=signal.qty, order_type=signal.order_type, px=signal.px,
    )


def _run_pairs(db: Session, registry: MarketRegistry, alloc: StrategyAllocation) -> None:
    prices_a = registry.prices(PAIRS_SYMBOL_A)
    prices_b = registry.prices(PAIRS_SYMBOL_B)
    position = _current_pair_position(db, alloc.user_id)

    result = _PAIRS_STRATEGY.evaluate_pair(PairSnapshot(
        symbol_a=PAIRS_SYMBOL_A, symbol_b=PAIRS_SYMBOL_B,
        prices_a=prices_a, prices_b=prices_b, position=position,
    ))
    if result is None:
        return

    if result.signal_a is not None:
        _submit_paper_order(
            db, registry, user_id=alloc.user_id, strategy_key=PAIRS_STRATEGY_KEY, symbol=PAIRS_SYMBOL_A,
            side=result.signal_a.side, qty=result.signal_a.qty,
            order_type=result.signal_a.order_type, px=result.signal_a.px,
        )
    if result.signal_b is not None:
        _submit_paper_order(
            db, registry, user_id=alloc.user_id, strategy_key=PAIRS_STRATEGY_KEY, symbol=PAIRS_SYMBOL_B,
            side=result.signal_b.side, qty=result.signal_b.qty,
            order_type=result.signal_b.order_type, px=result.signal_b.px,
        )