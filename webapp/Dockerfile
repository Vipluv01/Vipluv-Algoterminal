# Builds and serves algoterminal as one deployable image, on the exact same
# pattern already proven working on Render for sim/demo/Dockerfile: the Go
# engine binary compiled in a build stage (kept out of the final image, only
# the compiled binary is copied forward), then a slim Python runtime that
# serves both the FastAPI backend and the static frontend on a single port
# (app/main.py mounts StaticFiles at "/"), which is what makes this
# deployable on hosts that only route one public port to a service.
#
# Build context must be the REPO ROOT (bourse/), not webapp/, because the
# build stage needs the full Go source tree AND app/markets.py's own
# sys.path.insert (and sim/bourse_sim/engine.py's find_simserver_binary())
# resolve paths relative to the repo root -- the container's directory
# layout below preserves that same relative arrangement, exactly like local
# dev, rather than inventing a parallel path-resolution scheme for containers:
#   docker build -f webapp/Dockerfile -t algoterminal .

FROM golang:1.27 AS gobuild
WORKDIR /src
COPY go.mod ./
RUN go mod download 2>/dev/null || true
COPY cmd ./cmd
COPY internal ./internal
RUN go build -o /out/simserver ./cmd/simserver

FROM python:3.12-slim
WORKDIR /app

# Keep in step with webapp/pyproject.toml's [project].dependencies.
# pandas is explicit: app/strategies/pairs_cointegration.py imports it
# directly and must not rely on statsmodels continuing to pull it in.
# alembic is required at startup -- app/main.py's lifespan runs migrations.
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" sqlalchemy authlib httpx python-dotenv \
    itsdangerous cryptography numpy scipy statsmodels pandas alembic nh3 \
    smartapi-python pyotp websocket-client logzero "psycopg[binary]"

# Repo-root-relative layout, matching local dev exactly:
#   /app/bin/simserver         <- app/markets.py + engine.py's find_simserver_binary()
#   /app/sim/bourse_sim        <- app/markets.py's sys.path.insert target
#   /app/webapp/app            <- the FastAPI app package
#   /app/webapp/frontend       <- the static frontend StaticFiles serves
COPY --from=gobuild /out/simserver ./bin/simserver
COPY sim/bourse_sim ./sim/bourse_sim
COPY webapp/app ./webapp/app
COPY webapp/frontend ./webapp/frontend
# Migrations build the schema on boot (app/migrate.py). Without these the
# container starts against an empty database and every query 500s.
COPY webapp/alembic ./webapp/alembic
COPY webapp/alembic.ini ./webapp/alembic.ini

WORKDIR /app/webapp
ENV HOST=0.0.0.0
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
