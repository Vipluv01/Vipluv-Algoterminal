"""Schema management: Alembic is the single authority.

The app used to build its schema with `Base.metadata.create_all` at startup.
That works right up until it doesn't: `create_all` creates missing TABLES but
never alters an existing one, so adding a column to a model would appear to
work on a fresh database and silently do nothing to a database that already
had that table -- the bug reported as "the column exists in the model but not
in the deployed database".

Running migrations at startup instead means a fresh clone and a long-lived
deployment are built by exactly the same path, so there is one schema
authority rather than two that can disagree.

Tradeoff worth naming: auto-migrating on boot is the right call for a
single-instance deployment (this app ships as one container). It is the wrong
call for a horizontally-scaled one, where several instances starting at once
would race each other to run the same DDL -- that setup wants migrations as a
separate deploy step, which is what DISABLE_AUTO_MIGRATE exists to allow.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

log = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = WEBAPP_DIR / "alembic.ini"


def alembic_config() -> Config:
    """Alembic config pointed at this project, regardless of the working
    directory the app happened to be launched from."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(WEBAPP_DIR / "alembic"))
    return cfg


def run_migrations() -> None:
    """Bring the configured database up to head.

    A no-op when already current, so this is safe on every boot. Skipped when
    DISABLE_AUTO_MIGRATE=1 -- the test suite sets that because it builds its
    own in-memory schema per test, and a horizontally-scaled deployment would
    set it to run migrations as a separate step instead.
    """
    if os.environ.get("DISABLE_AUTO_MIGRATE") == "1":
        log.info("auto-migration disabled (DISABLE_AUTO_MIGRATE=1); skipping")
        return

    command.upgrade(alembic_config(), "head")
