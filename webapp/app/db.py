"""SQLAlchemy engine/session setup.

SQLite for now -- this is a placement-portfolio deployment, not a system
expected to handle concurrent-writer load, and SQLite's WAL mode is more
than sufficient at that scale (the same reasoning bourse's own WAL package
uses for the matching engine's durability, at a much larger scale than this
needs). Swapping to Postgres later would mean changing DATABASE_URL only --
nothing above this module talks to sqlite3 directly.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./algoterminal.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

if DATABASE_URL.startswith("sqlite"):
    # This module's own docstring above claims WAL mode -- SQLite defaults
    # to rollback-journal mode instead, where a writer holds an exclusive
    # lock on the whole file. app/main.py's _tick_loop writes to this DB
    # every MARKET_TICK_SECONDS forever (strategies/algo-orders/brackets/
    # circuit-breakers), so without WAL, any concurrent read landing
    # mid-write queues up behind that lock -- reproduced directly as
    # request pile-ups/500s under concurrent load on read endpoints like
    # GET /account. busy_timeout=5000 makes a reader/writer that DOES
    # collide (WAL still serializes writers against each other) wait up
    # to 5s and retry instead of failing immediately.
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=5000")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
