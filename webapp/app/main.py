"""algoterminal's FastAPI app.

Phase 1 scope (see /Users/vipluv/.claude/plans/eager-wiggling-key.md):
paper-mode order execution, positions/P&L, market data -- no auth (see
app/auth.py's placeholder), no live broker, no strategy auto-execution
loop yet. Those are later routers, added the same way orders/account were.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import Base, engine
from app.markets import MarketRegistry
from app.routers import account, orders

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


@app.get("/healthz")
def healthz():
    return {"ok": True}
