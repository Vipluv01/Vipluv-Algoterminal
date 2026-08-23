"""algoterminal's FastAPI app.

Phase 1 scope (see /Users/vipluv/.claude/plans/eager-wiggling-key.md):
paper-mode order execution, positions/P&L, market data, strategy
auto-execution -- no auth (see app/auth.py's placeholder), no live broker
yet. Those are later phases.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.brackets import monitor_brackets
from app.db import Base, SessionLocal, engine
from app.markets import MarketRegistry
from app.routers import account, dashboard, market_ws, optimizer, orders, pairs, risk, strategies
from app.strategy_runner import run_strategies_once

MARKET_TICK_SECONDS = 1.0

# Off by default in tests (see tests/conftest.py). The market ticks
# continuously in production, which is correct live-system behavior --
# but it also means the book's state is never frozen, so an API-contract
# test asserting "seed liquidity is still exactly there" is racing a
# background timer for no reason: found live when
# test_market_order_buy_fills_against_seed_liquidity failed nondeterministically
# because at least one tick (real bot activity, a momentarily one-sided
# book -- see bourse/sim/KNOWN_ISSUES.md, ~97% of steps are one-sided) had
# already run by the time the request landed. That's correct system
# behavior, not a bug; the fix is keeping deterministic API tests off the
# clock, not chasing timing.
DISABLE_MARKET_TICK = os.environ.get("DISABLE_MARKET_TICK") == "1"


async def _tick_loop(registry: MarketRegistry) -> None:
    while True:
        registry.step_all()
        # A fresh session per tick, not the per-request get_db() dependency
        # -- this runs outside any HTTP request, so there is no request
        # scope to borrow a session from.
        db = SessionLocal()
        try:
            run_strategies_once(db, registry)
            # A bracket-protected position closed some other way (a manual
            # order, a different strategy on the same symbol) has its
            # bracket cancelled at the moment that fill happens -- see
            # app.brackets.cancel_brackets_closed_elsewhere, called from
            # both routers/orders.py and strategy_runner.py, the two
            # places a fill can occur. monitor_brackets only ever sees
            # brackets that are still genuinely watching an intact position.
            monitor_brackets(db, registry)
        finally:
            db.close()
        await market_ws.broadcast_ticks(registry)
        await asyncio.sleep(MARKET_TICK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    registry = MarketRegistry()
    app.state.registry = registry
    tick_task = None if DISABLE_MARKET_TICK else asyncio.create_task(_tick_loop(registry))
    try:
        yield
    finally:
        if tick_task is not None:
            tick_task.cancel()
        registry.close()


app = FastAPI(title="algoterminal", lifespan=lifespan)

# Wide open for local dev -- Phase 3 (real auth) tightens this to the
# actual deployed frontend origin. No credentials/cookies are involved yet
# (auth.py's placeholder needs none), so this isn't a live security gap
# today, just not the final config.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(orders.router)
app.include_router(account.router)
app.include_router(strategies.router)
app.include_router(dashboard.router)
app.include_router(market_ws.router)
app.include_router(risk.router)
app.include_router(pairs.router)
app.include_router(optimizer.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/symbols")
def list_symbols():
    from app.markets import NAMED_INSTRUMENTS
    return [{"symbol": s, "reference_price": p} for s, p in NAMED_INSTRUMENTS.items()]
