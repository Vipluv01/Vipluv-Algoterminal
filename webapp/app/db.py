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

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./algoterminal.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
