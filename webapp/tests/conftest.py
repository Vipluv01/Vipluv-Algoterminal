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

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app


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
        yield c
    app.dependency_overrides.clear()
