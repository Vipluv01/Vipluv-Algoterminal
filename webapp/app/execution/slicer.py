"""Algorithmic order execution: TWAP and VWAP slicing of a large parent
order into smaller child market orders, submitted one per bar over a
horizon rather than all at once.

Advancement is bar-COUNT-based, not child-order-count-based: bars_elapsed
= registry.current_step - parent.start_bar is the authoritative measure of
"how many slices should have happened by now," computed fresh every call
rather than inferred by counting Order rows tagged to this parent. That
matters because a slice can legitimately be SKIPPED (zero VWAP weight on a
quiet bar, or the account being circuit-broker-halted mid-execution) --
counting child orders would then under-count elapsed bars and desync the
schedule from the real clock. run_algo_orders_once is called exactly once
per tick (app/main.py's _tick_loop), which is what makes "one call = one
bar" a safe assumption here.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.markets import MarketRegistry
from app.models.execution import ParentOrder, ParentOrderStatus, SlicerAlgo
from app.pairs_service import submit_paper_order
from app.risk_settings_service import get_or_create_risk_settings


class TWAPSlicer:
    """Equal-sized slices, one per bar over horizon_bars -- the defining
    property that distinguishes TWAP from VWAP: it doesn't look at the
    market at all, just divides total_qty by horizon_bars."""

    algo = SlicerAlgo.twap

    def slice_qty_for_bar(self, registry: MarketRegistry, parent: ParentOrder, bar_index: int) -> int:
        if bar_index == parent.horizon_bars - 1:
            # Remainder on the FINAL slice, not floor-division loss on
            # every slice -- guarantees sum(slices) == total_qty exactly
            # even when total_qty doesn't divide evenly by horizon_bars
            # (e.g. 100 shares / 3 bars = 33, 33, 34, not 33, 33, 33 with
            # 1 share silently never sent).
            return parent.total_qty - parent.filled_qty
        return parent.total_qty // parent.horizon_bars


class VWAPSlicer:
    """Each bar's slice is weighted by that bar's OWN share of the recent
    trailing volume profile (SymbolMarket.recent_volume, Phase 1's ring
    buffer) -- w_i = v_i / sum(v) over a window matching this order's own
    horizon_bars, using the just-observed bar's volume as v_i.

    This is necessarily a CAUSAL approximation, not textbook VWAP (which
    assumes the FULL day's volume profile is known in advance, e.g. from
    historical averages): a live system can't know how much will trade in
    a future bar before it happens. Weighting each slice by how much
    volume the market is ACTUALLY showing right now is the standard
    practical substitute -- participate more when the market is
    genuinely active, less when it's quiet -- and is exactly what the
    already-tracked recent_volume ring buffer (built for zero extra IPC
    cost in Phase 1) is for.
    """

    algo = SlicerAlgo.vwap

    def slice_qty_for_bar(self, registry: MarketRegistry, parent: ParentOrder, bar_index: int) -> int:
        if bar_index == parent.horizon_bars - 1:
            return parent.total_qty - parent.filled_qty  # same exact-total guarantee as TWAP

        market = registry[parent.symbol]
        window = list(market.recent_volume)[-parent.horizon_bars:]
        total_volume = sum(window)
        if not window or total_volume <= 0:
            # No volume signal to weight by yet -- fall back to an equal
            # TWAP-style slice rather than divide by zero or stall.
            return parent.total_qty // parent.horizon_bars

        this_bar_volume = window[-1]  # the bar that just happened -- "now"
        weight = this_bar_volume / total_volume
        return round(weight * parent.total_qty)


_SLICERS: dict[SlicerAlgo, TWAPSlicer | VWAPSlicer] = {
    SlicerAlgo.twap: TWAPSlicer(),
    SlicerAlgo.vwap: VWAPSlicer(),
}

ALGO_STRATEGY_KEY_PREFIX = "algo_"  # e.g. "algo_twap", "algo_vwap" -- lets
# dashboard/journal views group algo-driven fills as a category, separate
# from Order.parent_order_id, which is the precise per-parent-order link.


def run_algo_orders_once(db: Session, registry: MarketRegistry) -> None:
    """Called once per tick (app/main.py's _tick_loop), alongside
    run_strategies_once and monitor_brackets -- advances every user's
    active ParentOrder by exactly one bar."""
    active = db.query(ParentOrder).filter(ParentOrder.status == ParentOrderStatus.active).all()
    for parent in active:
        _advance_one_bar(db, registry, parent)
    db.commit()


def _advance_one_bar(db: Session, registry: MarketRegistry, parent: ParentOrder) -> None:
    current_bar = registry.current_step
    bars_elapsed = current_bar - parent.start_bar
    if bars_elapsed <= 0:
        return  # created this same tick, before any bar has elapsed since -- nothing to slice yet

    # bars_elapsed is a COUNT (1 after the first bar has passed since
    # creation); bar_index is the 0-based slice this count corresponds to.
    # Using bars_elapsed directly as bar_index was a real off-by-one here:
    # it made the FINAL slice (bar_index == horizon_bars - 1) fire a full
    # tick early -- e.g. horizon_bars=3 completed in just 2 ticks with 2
    # child orders instead of 3, confirmed directly before this fix.
    bar_index = bars_elapsed - 1
    if bar_index >= parent.horizon_bars:
        parent.status = ParentOrderStatus.completed
        return

    is_final_bar = bar_index == parent.horizon_bars - 1

    # The circuit breaker (app/risk/circuit_breaker.py) must apply to
    # algo-sliced orders exactly as it does to strategy-driven ones -- a
    # halted account should not keep quietly slicing a parent order out
    # from under the halt. Skipping this bar's slice (rather than
    # cancelling the whole parent) means execution simply resumes on the
    # next active tick after a reset, the same "pause, not abort" spirit
    # the breaker itself is built around.
    if get_or_create_risk_settings(db, parent.user_id).trading_halted:
        if is_final_bar:
            parent.status = ParentOrderStatus.completed
        return

    slicer = _SLICERS[parent.algo]
    qty = slicer.slice_qty_for_bar(registry, parent, bar_index)

    if qty > 0:
        submit_paper_order(
            db, registry, user_id=parent.user_id,
            strategy_key=f"{ALGO_STRATEGY_KEY_PREFIX}{parent.algo.value}", symbol=parent.symbol,
            side=parent.side, qty=qty, order_type="market", px=None,
            parent_order_id=parent.id,
        )
        # Tracks cumulative qty REQUESTED across child slices, not
        # necessarily actually FILLED by the engine -- a child slice can
        # partially fill against thin liquidity the same way circuit_
        # breaker.py's liquidation orders can. Good enough for "did this
        # parent order finish being SENT," which is what horizon-based
        # completion and the final-slice remainder calculation both need;
        # a caller wanting real fill totals should sum the child Orders'
        # own filled_qty via GET /orders/algo/{id} instead.
        parent.filled_qty += qty

    if is_final_bar:
        parent.status = ParentOrderStatus.completed
