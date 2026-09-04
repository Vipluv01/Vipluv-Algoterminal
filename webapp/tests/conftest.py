"""Shared pytest fixtures for API-level tests.

Each test gets its own in-memory SQLite database (StaticPool keeps it a
single shared connection for the test's lifetime, since SQLite's
":memory:" is otherwise a fresh empty database per connection) via
FastAPI's own documented dependency-override pattern -- NOT the module-
level engine/SessionLocal in app/db.py, which stays untouched and is what
the real app uses outside tests.
"""

import os

# Must be set before `app.main` is imported (it reads this at module load
# time) -- disables the live market tick loop so API-contract tests get a
# deterministic, un-ticked book instead of racing a background timer.
os.environ["DISABLE_MARKET_TICK"] = "1"

# Each test builds its own in-memory schema below, so the app's startup
# migration would only be running Alembic against the developer's real
# on-disk algoterminal.db -- once per TestClient, i.e. once per test. Off.
os.environ["DISABLE_AUTO_MIGRATE"] = "1"

# Off for the same reason as DISABLE_MARKET_TICK above, for a sharper
# reason: this warm-up builds a real AngelOneAdapter and makes real
# Angel One network calls off whatever LiveBrokerCredential row happens
# to be in the test's own DB. A test inserting a fake/dummy credential
# row (to exercise the vault or live-order flow) would otherwise have
# this try to log in to the real Angel One with garbage values on every
# single test run -- confirmed live: the full suite hung well past its
# normal runtime the one time this ran here unguarded.
os.environ["DISABLE_LIVE_WARMUP"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.accounting import (
    _account_cache,
    _equity_curve_cache,
    _realizations_cache,
    _realized_pnl_curve_cache,
)
from app.db import Base, get_db
from app.main import app
from app.pairs_service import _pair_position_cache


@pytest.fixture(autouse=True)
def _clear_process_global_caches():
    """Every incremental accounting/pairs cache in this app is process-
    global BY DESIGN (a small, single-process deployment -- see
    accounting.get_cached_account_snapshot's own docstring), keyed by
    things like (user_id, mode) that assume ids are unique and monotonic
    within ONE persistent database, which is true in real production but
    NOT across this test suite's own tests: every `client`/`db` fixture
    stands up its own fresh in-memory SQLite database, and small ids
    (1, 2, 3...) routinely repeat across tests for a completely different
    user or order. A cache's own staleness check (verifying a watermark
    order still exists) cannot tell "the same order" apart from "a
    different database's different order that happens to reuse the same
    id" -- confirmed live: exactly this coincidence let one test's cached
    pair-position state leak into another's (test_strategy_runner.py) and
    one mode's cached data leak into another's (test_portfolio_api.py)
    before this fixture existed. Clearing every cache before AND after
    each test is the actual fix -- not a smarter per-check heuristic,
    since the ids genuinely can collide with no way to tell from data
    alone."""
    _account_cache.clear()
    _realizations_cache.clear()
    _realized_pnl_curve_cache.clear()
    _equity_curve_cache.clear()
    _pair_position_cache.clear()
    yield
    _account_cache.clear()
    _realizations_cache.clear()
    _realized_pnl_curve_cache.clear()
    _equity_curve_cache.clear()
    _pair_position_cache.clear()


@pytest.fixture
def client():
    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        # Exposed for tests that need to manipulate DB state directly
        # alongside API calls (e.g. forcing a RiskSettings.trading_halted
        # flag to set up a scenario) -- app.db.SessionLocal would connect
        # to the wrong database entirely here, since it's the real app's
        # engine, untouched by this override.
        c.db_session_factory = TestingSessionLocal
        yield c
    app.dependency_overrides.clear()
