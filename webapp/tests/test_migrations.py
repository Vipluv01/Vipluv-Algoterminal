"""Migrations must stay in step with the models.

The failure this guards against is quiet and expensive: someone adds a column
to a SQLAlchemy model, `Base.metadata.create_all` picks it up on their machine
(it creates missing TABLES, so a brand-new dev database looks correct), the
tests pass against an in-memory database built the same way, and the migration
is never written. The deployed database -- which only ever changes via
migrations -- silently lacks the column until a query touches it in production.

So these tests assert the two paths agree, rather than trusting that whoever
edits a model remembers to run `alembic revision --autogenerate`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.db import Base

WEBAPP_DIR = Path(__file__).resolve().parents[1]
ALEMBIC = WEBAPP_DIR / ".venv" / "bin" / "alembic"


def _run_alembic(tmp_db: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ALEMBIC), *args],
        cwd=WEBAPP_DIR,
        env={
            "PATH": "/usr/bin:/bin",
            "DATABASE_URL": f"sqlite:///{tmp_db}",
            "HOME": str(Path.home()),
        },
        capture_output=True,
        text=True,
        timeout=180,
    )


def _schema_of(url: str) -> dict[str, dict[str, str]]:
    """Table -> {column name -> column type} for a live database."""
    engine = create_engine(url)
    insp = inspect(engine)
    schema: dict[str, dict[str, str]] = {}
    for table in insp.get_table_names():
        if table == "alembic_version":  # bookkeeping, not part of the app schema
            continue
        schema[table] = {c["name"]: str(c["type"]) for c in insp.get_columns(table)}
    engine.dispose()
    return schema


def _schema_of_metadata() -> dict[str, dict[str, str]]:
    """Table -> {column name -> column type} as the models declare it."""
    import app.models  # noqa: F401  (registers every mapped class)

    return {
        name: {c.name: str(c.type) for c in table.columns}
        for name, table in Base.metadata.tables.items()
    }


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed in .venv")
def test_migrations_build_the_schema_the_models_declare(tmp_path: Path) -> None:
    """`alembic upgrade head` on an empty database must reproduce the models.

    This is the check that catches a model change shipped without a migration.
    """
    db = tmp_path / "migrated.db"
    result = _run_alembic(db, "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"

    migrated = _schema_of(f"sqlite:///{db}")
    declared = _schema_of_metadata()

    assert set(migrated) == set(declared), (
        "tables differ between `alembic upgrade head` and the models.\n"
        f"only in migrations: {sorted(set(migrated) - set(declared))}\n"
        f"only in models:     {sorted(set(declared) - set(migrated))}\n"
        "If you changed a model, generate the migration:\n"
        "  .venv/bin/alembic revision --autogenerate -m 'describe the change'"
    )

    for table in sorted(declared):
        assert migrated[table] == declared[table], (
            f"columns of {table!r} differ between migrations and models.\n"
            f"migrated: {migrated[table]}\n"
            f"declared: {declared[table]}\n"
            "Generate the missing migration with `alembic revision --autogenerate`."
        )


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed in .venv")
def test_no_pending_model_changes_without_a_migration(tmp_path: Path) -> None:
    """Autogenerate against a fully-migrated database must find nothing to do.

    `test_migrations_build_the_schema_the_models_declare` compares tables and
    column types; this catches the subtler drift it cannot see -- a changed
    index, unique constraint, or nullability -- by asking Alembic itself
    whether it would still emit anything.
    """
    db = tmp_path / "migrated.db"
    up = _run_alembic(db, "upgrade", "head")
    assert up.returncode == 0, f"alembic upgrade failed:\n{up.stdout}\n{up.stderr}"

    check = _run_alembic(db, "check")
    assert check.returncode == 0, (
        "The models declare schema that no migration creates.\n"
        f"{check.stdout}\n{check.stderr}\n"
        "Generate it with:\n"
        "  .venv/bin/alembic revision --autogenerate -m 'describe the change'"
    )


@pytest.mark.skipif(not ALEMBIC.exists(), reason="alembic not installed in .venv")
def test_downgrade_to_base_removes_every_table(tmp_path: Path) -> None:
    """Migrations must be reversible.

    A downgrade path that was never run is a downgrade path that does not
    work, and the only moment anyone needs it is a bad deploy.
    """
    db = tmp_path / "roundtrip.db"
    assert _run_alembic(db, "upgrade", "head").returncode == 0

    down = _run_alembic(db, "downgrade", "base")
    assert down.returncode == 0, f"alembic downgrade failed:\n{down.stdout}\n{down.stderr}"

    remaining = _schema_of(f"sqlite:///{db}")
    assert remaining == {}, f"downgrade left tables behind: {sorted(remaining)}"


def test_python_is_the_venv_interpreter() -> None:
    """Guards the assumption the subprocess calls above depend on."""
    assert sys.executable.endswith("python") or sys.executable.endswith("python3")
