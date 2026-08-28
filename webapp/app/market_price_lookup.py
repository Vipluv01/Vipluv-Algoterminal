"""Shared PriceLookup builder (accounting.PriceLookup) over
SymbolMarket.price_history -- used by every mark-to-market equity curve
that needs a HISTORICAL price, not just registry.current_prices()'s
snapshot of now. Originally lived in routers/account.py; factored out here
once routers/virtual.py needed the identical logic (paper and virtual mode
both mark against the same simulated engine, just different starting
capital -- see accounting.STARTING_VIRTUAL_CASH_DEFAULT's own docstring),
rather than duplicating it a second time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.markets import DERIVED_INDICES, MarketRegistry


def historical_price_lookup(registry: MarketRegistry) -> Callable[[str, object], "float | None"]:
    """Builds the PriceLookup compute_equity_curve needs, from
    SymbolMarket.price_history -- the SAME wall-clock-to-step mapping GET
    /leaderboard already uses for its own `since` cutoff (one real second
    per tick, app/main.py's MARKET_TICK_SECONDS=1.0; see
    routers/leaderboard.py's _price_at_step and the cutoff computation in
    get_leaderboard for the pattern this mirrors), so a fill's own
    created_at maps back to "how many steps before the latest
    price_history point that was."

    Returns None for a symbol with no price_history at all (a synthetic
    option contract, priced by app/options/execution.py against a live
    spot only -- there is no historical series to look one up from) --
    compute_equity_curve then falls back to that position's own average
    entry price, the same honest fallback compute_account already uses
    for current_prices.
    """
    now = datetime.now(timezone.utc)

    def lookup(symbol: str, at) -> float | None:
        market = registry.markets.get(symbol)
        if market is not None:
            history = market.price_history
            step_count = market.step_count
        elif symbol in DERIVED_INDICES:
            history = registry.price_history_for(symbol).tolist()
            step_count = len(history) - 1
        else:
            return None
        if not history:
            return None

        at_utc = at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
        seconds_ago = (now - at_utc).total_seconds()
        idx = max(0, min(step_count - int(seconds_ago), len(history) - 1))
        return float(history[idx])

    return lookup
