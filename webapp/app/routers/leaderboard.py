"""User vs the real bot swarm -- P&L reconstructed from actual fills
(SymbolMarket.recent_fills, app/markets.py), not simulated for display.
Every symbol already runs 20 NoiseTrader + 5 InformedTrader + 1 MarketMaker
with distinct owner ids (see app/markets.py's SymbolMarket.__post_init__),
and the human user now has its own reserved id too (HUMAN_USER_OWNER_ID --
see that constant's own docstring for the real bug this fixed: the human
user and the MarketMaker bot used to share owner id 1).

HONEST LIMITATION, stated once here rather than left implicit: recent_fills
is a BOUNDED ring buffer (maxlen=500 PER SYMBOL), not a full since-inception
fill ledger. Every P&L figure here is reconstructed from whatever fills are
currently retained -- for an actively-trading symbol that can be a window
of well under a minute (see recent_fills' own comment: "500 is a few
seconds of ordinary tick activity"). `since_coverage_complete` in the
response says whether the requested `since` window is actually fully
covered by what's retained, rather than silently under-reporting a bot
that's been trading longer than the buffer remembers.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.markets import HUMAN_USER_OWNER_ID, MarketRegistry, NAMED_INSTRUMENTS
from app.routers.orders import get_registry

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])

# Owner id -> (label, role). Built once from the SAME ranges app/markets.py's
# own SymbolMarket.__post_init__ assigns (noise=100-119, informed=200-204,
# maker=1) plus the human user's own reserved id -- NOT the seed-liquidity
# owner (999), which is a one-time book-seeding event, not a real
# participant, and is excluded entirely rather than shown as a "trader"
# with a meaningless P&L.
def _owner_registry() -> dict[int, tuple[str, str]]:
    registry: dict[int, tuple[str, str]] = {HUMAN_USER_OWNER_ID: ("You", "human"), 1: ("Market Maker", "market_maker")}
    for i in range(20):
        registry[100 + i] = (f"Noise Trader #{i + 1}", "noise_trader")
    for i in range(5):
        registry[200 + i] = (f"Informed Trader #{i + 1}", "informed_trader")
    return registry


OWNER_REGISTRY = _owner_registry()


def _price_at_step(price_history: list[float], step: int) -> float:
    if not price_history:
        return 0.0
    idx = max(0, min(step, len(price_history) - 1))
    return price_history[idx]


def _owner_pnls(registry: MarketRegistry, max_step_by_symbol: dict[str, int] | None) -> dict[int, float]:
    """net_cash_flow + open_qty * mark_price per owner, across every real
    (non-derived) symbol's currently-retained fills. This identity needs
    no separate weighted-average-cost bookkeeping: cash flow from trading
    plus the mark-to-market value of whatever's left over IS total P&L
    (verified: buy 10@100 then sell 5@110, marked @120 -> cash flow -450,
    5*120=600, total 150 -- matches 50 realized + 100 unrealized exactly).

    max_step_by_symbol=None means "use every retained fill for every
    symbol, marked at CURRENT price" (the live leaderboard). A real dict
    means "for THIS symbol, only fills up to and including its own
    cutoff, marked at the price AT that step" -- deliberately PER-SYMBOL,
    not one global minimum cutoff: symbols normally advance in lockstep
    (MarketRegistry.step_all() steps every one together), but nothing
    here should assume that stays true forever, and collapsing to a
    single shared minimum would make one lagging (or newly added) symbol
    silently blow out the cutoff for every other symbol too.
    """
    cash_flow: dict[int, float] = {}
    open_qty: dict[tuple[int, str], int] = {}

    for symbol, market in registry.markets.items():
        cutoff = None if max_step_by_symbol is None else max_step_by_symbol[symbol]
        for fill in market.recent_fills:
            if cutoff is not None and fill.step > cutoff:
                continue
            taker_delta = fill.qty if fill.taker_side == "buy" else -fill.qty
            # taker_owner/maker_owner, NOT taker_id/maker_id -- those are
            # ENGINE ORDER ids (see RecordedFill's own docstring), and
            # never match anything in OWNER_REGISTRY.
            for owner_id, delta in ((fill.taker_owner, taker_delta), (fill.maker_owner, -taker_delta)):
                if owner_id not in OWNER_REGISTRY:
                    continue
                cash_flow[owner_id] = cash_flow.get(owner_id, 0.0) - delta * fill.px
                key = (owner_id, symbol)
                open_qty[key] = open_qty.get(key, 0) + delta

    pnl: dict[int, float] = dict(cash_flow)
    for (owner_id, symbol), qty in open_qty.items():
        if qty == 0:
            continue
        market = registry.markets[symbol]
        cutoff = None if max_step_by_symbol is None else max_step_by_symbol[symbol]
        price = market.current_price if cutoff is None else _price_at_step(market.price_history, cutoff)
        pnl[owner_id] = pnl.get(owner_id, 0.0) + qty * price

    # Every registered owner appears even with zero activity retained --
    # 0.0 here is a REAL measurement (no retained fills touched this
    # owner), not a placeholder, so it's fine as a literal 0.0 rather than
    # None.
    return {owner_id: pnl.get(owner_id, 0.0) for owner_id in OWNER_REGISTRY}


def _rank(pnls: dict[int, float]) -> dict[int, int]:
    """1 = highest P&L. Ties keep insertion (OWNER_REGISTRY) order, which
    is stable and deterministic rather than dependent on dict/sort
    implementation details."""
    ordered = sorted(pnls.items(), key=lambda kv: -kv[1])
    return {owner_id: rank + 1 for rank, (owner_id, _) in enumerate(ordered)}


class LeaderboardEntryOut(BaseModel):
    owner_id: int
    label: str
    role: str
    pnl: float
    rank: int
    # CHANGE in P&L since the requested timestamp (pnl_now - pnl_at_since),
    # not an absolute value -- None when `since` wasn't supplied.
    pnl_delta: float | None
    rank_delta: int | None  # positive = moved UP (rank number decreased)


class LeaderboardOut(BaseModel):
    entries: list[LeaderboardEntryOut]
    since: datetime | None
    # False if the requested `since` predates the oldest fill this
    # response could still find retained for at least one symbol -- the
    # pnl_delta/rank_delta figures are then a real but PARTIAL
    # reconstruction (missing fills that were already evicted from the
    # ring buffer before this request), not the true since-`since` figure.
    since_coverage_complete: bool
    fill_window_note: str = (
        "P&L is reconstructed from each symbol's currently-retained fills "
        "(a bounded recent window, not a full since-inception ledger) -- "
        "see since_coverage_complete for whether a requested `since` is "
        "fully covered by what's still retained."
    )


@router.get("", response_model=LeaderboardOut)
def get_leaderboard(
    since: datetime | None = Query(None),
    registry: MarketRegistry = Depends(get_registry),
):
    pnl_now = _owner_pnls(registry, max_step_by_symbol=None)
    rank_now = _rank(pnl_now)

    pnl_since: dict[int, float] | None = None
    rank_since: dict[int, int] | None = None
    coverage_complete = True

    if since is not None:
        # A naive datetime (no tzinfo) is treated as UTC, not the host's
        # local timezone -- .timestamp() on a naive datetime would
        # otherwise silently assume system-local time, making this
        # endpoint's behavior depend on which timezone it happens to be
        # deployed in.
        since_utc = since if since.tzinfo is not None else since.replace(tzinfo=timezone.utc)
        seconds_ago = (datetime.now(timezone.utc) - since_utc).total_seconds()  # may be negative for a future `since`

        # PER-SYMBOL cutoff, not one shared minimum -- see _owner_pnls'
        # own docstring for why. Deliberately UNCLAMPED at 0: a cutoff of
        # -1 (or lower) correctly excludes even a symbol's very first
        # fill (which can legitimately be recorded at step=0), whereas
        # clamping the cutoff to 0 would be indistinguishable from "as of
        # step 0" and wrongly include it.
        cutoff_by_symbol: dict[str, int] = {}
        for symbol, market in registry.markets.items():
            cutoff = market.step_count - int(seconds_ago)
            cutoff_by_symbol[symbol] = cutoff
            # Only flag incomplete when the ring buffer is actually FULL
            # (eviction is possible) AND its oldest retained fill is newer
            # than the cutoff. A buffer below its own maxlen has never
            # evicted anything -- every fill this symbol ever produced is
            # still here, so an oldest-fill step newer than cutoff just
            # means trading started after the requested moment, not that
            # data was lost.
            at_capacity = len(market.recent_fills) == market.recent_fills.maxlen
            if at_capacity and market.recent_fills[0].step > cutoff:
                coverage_complete = False

        pnl_since = _owner_pnls(registry, max_step_by_symbol=cutoff_by_symbol)
        rank_since = _rank(pnl_since)

    entries = []
    for owner_id, (label, role) in OWNER_REGISTRY.items():
        delta = None
        pnl_at_since = None
        if pnl_since is not None and rank_since is not None:
            pnl_at_since = pnl_now[owner_id] - pnl_since[owner_id]
            delta = rank_since[owner_id] - rank_now[owner_id]
        entries.append(LeaderboardEntryOut(
            owner_id=owner_id, label=label, role=role, pnl=pnl_now[owner_id], rank=rank_now[owner_id],
            pnl_delta=pnl_at_since, rank_delta=delta,
        ))
    entries.sort(key=lambda e: e.rank)

    return LeaderboardOut(
        entries=entries, since=since, since_coverage_complete=coverage_complete if since is not None else True,
    )
