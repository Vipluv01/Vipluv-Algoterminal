"""The Dockerfile's dependency list must cover pyproject's.

webapp/Dockerfile pins its runtime dependencies as a literal `pip install`
line, and nothing builds it in CI -- the docker job at .github/workflows/ci.yml
builds the repo-root Dockerfile (the Go image), not this one. So a dependency
added to pyproject.toml can be missing from the image with nothing catching it
until the container starts and fails on import.

That is not hypothetical: adding alembic (imported by app/main.py at startup
via app/migrate.py) and pandas (imported directly by
app/strategies/pairs_cointegration.py) left the Dockerfile installing neither.

This test is the substitute for building the image. It is not as good as
building the image, but it runs in milliseconds and catches the failure mode
that actually occurred.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

WEBAPP_DIR = Path(__file__).resolve().parents[1]
PYPROJECT = WEBAPP_DIR / "pyproject.toml"
DOCKERFILE = WEBAPP_DIR / "Dockerfile"


def _declared_dependencies() -> set[str]:
    """Base package names from pyproject's [project].dependencies.

    Strips extras and version specifiers: `uvicorn[standard]>=0.30` -> `uvicorn`.
    """
    data = tomllib.loads(PYPROJECT.read_text())
    names = set()
    for spec in data["project"]["dependencies"]:
        name = re.split(r"[<>=!~\[;]", spec, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower())
    return names


def test_dockerfile_installs_every_declared_dependency() -> None:
    dockerfile = DOCKERFILE.read_text().lower()
    missing = sorted(dep for dep in _declared_dependencies() if dep not in dockerfile)
    assert not missing, (
        f"webapp/Dockerfile does not install: {missing}\n"
        "Add them to the `pip install` line in webapp/Dockerfile — the image is "
        "not built in CI, so nothing else will catch this until deploy."
    )


def test_dockerfile_ships_the_migrations() -> None:
    """app/main.py's lifespan runs `alembic upgrade head` on boot.

    If alembic/ and alembic.ini are not copied into the image, that call fails
    and the container never serves a request.
    """
    dockerfile = DOCKERFILE.read_text()
    for required in ("webapp/alembic", "webapp/alembic.ini"):
        assert required in dockerfile, (
            f"webapp/Dockerfile does not COPY {required}. Startup migrations "
            "(app/migrate.py) will fail in the container."
        )


def test_pairs_strategy_dependency_is_declared_not_transitive() -> None:
    """pandas must be a declared dependency, not a statsmodels side effect.

    app/strategies/pairs_cointegration.py imports pandas directly. Relying on
    statsmodels to keep pulling it in means a statsmodels release could break
    the pairs strategy with no change on our side.
    """
    source = (WEBAPP_DIR / "app" / "strategies" / "pairs_cointegration.py").read_text()
    if "import pandas" in source:
        assert "pandas" in _declared_dependencies(), (
            "pairs_cointegration.py imports pandas but pyproject.toml does not "
            "declare it — it is only present transitively via statsmodels."
        )
