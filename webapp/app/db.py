"""SQLAlchemy engine/session setup.

SQLite by default -- fine for a single-instance dev/demo deployment, but
NOT for Render's free/most-plans disk, which is ephemeral (wiped on every
restart, taking algoterminal.db with it). DATABASE_URL pointed at a real
Postgres instance (a Render-managed one, or any other) is how that's
avoided in production; nothing above this module talks to sqlite3 or
psycopg directly, so switching is genuinely just an env var, not a code
change -- this docstring's older claim ("changing DATABASE_URL only")
is now actually exercised, not just asserted.

URL normalization, not just documentation, because it's the actual
failure mode a pasted-verbatim Render connection string hits otherwise:
- Render's dashboard (like Heroku's older convention) can hand out a
  bare "postgres://" URL. SQLAlchemy 1.4+ does NOT recognize that scheme
  at all (raises NoSuchModuleError) -- it must be "postgresql://".
- A bare "postgresql://" resolves to the psycopg2 dialect by default.
  This project installs psycopg (v3) instead (see pyproject.toml), which
  needs the scheme spelled out explicitly as "postgresql+psycopg://".
Both are rewritten below so the exact string Render's dashboard shows can
be pasted into DATABASE_URL as-is, with no manual editing.

No Postgres-side equivalent of the SQLite PRAGMA block below is needed:
Postgres has proper MVCC (readers never block writers, writers never
block readers, the same property WAL mode above exists to get SQLite),
so there's nothing this module needs to configure for it -- said
explicitly here rather than left as a silent gap someone has to rediscover.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./algoterminal.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]

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
