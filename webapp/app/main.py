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
from pathlib import Path

from dotenv import load_dotenv

# MUST run before any `from app...` import below -- app/db.py reads
# DATABASE_URL from the environment at MODULE IMPORT time (not lazily),
# so a .env file loaded any later than this would already be too late for
# it. `python-dotenv` has been a listed dependency since early on but was
# never actually wired up to load anything -- real gap, not a redundant
# addition: a plain `export BROKER_CREDENTIAL_KEY=...` in the shell that
# happened to start this process is exactly the kind of state that
# silently vanishes across a restart from a DIFFERENT shell (confirmed
# directly: a routine dev-server restart during this same phase did just
# that, and POST /vault/credential started 500ing with no clue why until
# traced back to crypto.py's MissingCredentialKeyError). A `.env` file
# next to this one (webapp/.env, already `.gitignore`d) survives that.
# python-dotenv's own default (override=False) never clobbers a real
# env var that IS already set, so this changes nothing for a deployment
# that sets these directly.
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.brackets import monitor_brackets
from app.db import SessionLocal
from app.execution.slicer import run_algo_orders_once
from app.migrate import run_migrations
from app.markets import MarketRegistry
from app.pairs_service import refresh_pair_telemetry_once, reset_pair_telemetry
from app.telemetry import reset_order_submit_latencies
from app.risk.circuit_breaker import run_circuit_breakers_once
from app.routers import (
    account, dashboard, journal, leaderboard, live_market, live_options, market, market_ws, optimizer, options,
    orders, pairs, portfolio, risk, strategies, telemetry, vault, virtual,
)
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
            # Advances every active TWAP/VWAP parent order by one bar --
            # alongside strategy signals, not gated behind them, since an
            # algo order's schedule is driven by elapsed bars, not by
            # whether any strategy happened to fire this tick.
            run_algo_orders_once(db, registry)
            # A bracket-protected position closed some other way (a manual
            # order, a different strategy on the same symbol) has its
            # bracket cancelled at the moment that fill happens -- see
            # app.brackets.cancel_brackets_closed_elsewhere, called from
            # both routers/orders.py and strategy_runner.py, the two
            # places a fill can occur. monitor_brackets only ever sees
            # brackets that are still genuinely watching an intact position.
            monitor_brackets(db, registry)
            # After strategies/brackets, not before: the breaker needs to
            # see whatever P&L this tick's own fills just produced, not
            # last tick's state.
            run_circuit_breakers_once(db, registry)
        finally:
            db.close()
        # No db session needed -- pure statistics over price history, same
        # reasoning as why this runs on the tick cadence at all (see
        # refresh_pair_telemetry_once's own docstring).
        refresh_pair_telemetry_once(registry)
        await market_ws.broadcast_ticks(registry)
        await asyncio.sleep(MARKET_TICK_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    registry = MarketRegistry()
    app.state.registry = registry
    reset_pair_telemetry()
    reset_order_submit_latencies()
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
    # Response headers are NOT readable cross-origin by default regardless
    # of allow_headers (that governs REQUEST headers) -- GET /orders' real
    # total count travels in X-Total-Count specifically so the frontend,
    # on a different origin (:5173 vs this server's :8001), can read it.
    expose_headers=["X-Total-Count"],
)


@app.exception_handler(RequestValidationError)
async def _validation_error_without_echoing_vault_input(request: Request, exc: RequestValidationError):
    """FastAPI's DEFAULT validation-error handler echoes the raw submitted
    value back in each error's "input" field -- fine for an order qty or a
    strategy key, but under /vault a malformed request body (e.g. a
    non-string api_key/api_secret) would otherwise reflect a secret-shaped
    value straight into a 422 response. Stripped ONLY for /vault paths --
    verified directly (grepping the actual response text, not just
    reasoning about it) in tests/test_vault_api.py -- every other route's
    error shape is untouched, going through FastAPI's own default handler
    exactly as before.
    """
    if request.url.path.startswith("/vault"):
        errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})
    return await request_validation_exception_handler(request, exc)

app.include_router(orders.router)
app.include_router(account.router)
app.include_router(strategies.router)
app.include_router(dashboard.router)
app.include_router(market_ws.router)
app.include_router(risk.router)
app.include_router(pairs.router)
app.include_router(optimizer.router)
app.include_router(options.router)
app.include_router(journal.router)
app.include_router(market.router)
app.include_router(portfolio.router)
app.include_router(vault.router)
app.include_router(leaderboard.router)
app.include_router(telemetry.router)
app.include_router(virtual.router)
app.include_router(live_market.router)
app.include_router(live_options.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/symbols")
def list_symbols():
    from app.markets import DERIVED_INDICES, NAMED_INSTRUMENTS, compute_derived_index
    symbols = [{"symbol": s, "reference_price": p, "is_derived": False} for s, p in NAMED_INSTRUMENTS.items()]
    # reference_price for a derived index is computed from the SAME static
    # NAMED_INSTRUMENTS reference prices its constituents use above --
    # illustrative, not a live quote, same convention as every other
    # reference_price this endpoint already returns.
    symbols.extend(
        {"symbol": s, "reference_price": compute_derived_index(s, NAMED_INSTRUMENTS), "is_derived": True}
        for s in DERIVED_INDICES
    )
    return symbols


# Serves the frontend on the SAME port/process as the API -- required for
# hosts (Render's free tier included) that only route one public port to a
# service, the same constraint sim/demo/Dockerfile's serve_static_or_upgrade
# already solves for the old demo. Mounted last, deliberately: Starlette
# matches routes in registration order, and a mount at "/" would otherwise
# shadow every API route registered after it. The frontend uses hash-based
# routing (#/terminal etc., never sent to the server), so every page loads
# via a plain GET "/" -- html=True's default-document behavior is enough,
# no SPA catch-all fallback route is needed.
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
