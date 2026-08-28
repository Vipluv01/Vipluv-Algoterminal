"""Alembic environment.

Two things here are load-bearing and deliberately not the generated defaults:

1. The database URL comes from `app.db.DATABASE_URL`, not from alembic.ini.
   The app already resolves it from the DATABASE_URL environment variable
   (defaulting to sqlite:///./algoterminal.db); duplicating that string in
   alembic.ini would mean migrations could silently run against a different
   database than the app uses.

2. `render_as_batch=True`. SQLite cannot ALTER a column or add a foreign key
   in place -- it only supports a narrow subset of ALTER TABLE. Batch mode
   makes Alembic emit the create-new-table / copy / drop / rename dance
   instead. Without it, any migration that changes a column type, flips
   nullability, or adds an FK fails outright on SQLite. The planned schema
   work does all three, so this is required, not defensive.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the `app` package importable when alembic is invoked from webapp/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import DATABASE_URL, Base  # noqa: E402

# Importing app.models registers every mapped class on Base.metadata. Without
# this, autogenerate would see a metadata object missing most tables and
# helpfully propose dropping them.
import app.models  # noqa: E402,F401

config = context.config
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# SQLite needs batch mode; other backends do not and are better off without
# the extra table rewrites.
RENDER_AS_BATCH = DATABASE_URL.startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=RENDER_AS_BATCH,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = DATABASE_URL

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=RENDER_AS_BATCH,
            # compare_type catches a column whose Python type changed but whose
            # name did not -- otherwise silently ignored by autogenerate.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
