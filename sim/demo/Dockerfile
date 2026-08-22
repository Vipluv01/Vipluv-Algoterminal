# Builds and serves the live demo as one deployable image: the Go engine
# binary in a build stage (kept out of the final image -- only the compiled
# binary is copied forward), then a slim Python runtime that serves both
# the WebSocket simulation feed and the static frontend on a single port
# (see demo_server.py's serve_static_or_upgrade), which is what makes this
# deployable on hosts that only route one public port to a service.
#
# Build context must be the REPO ROOT (bourse/), not sim/demo/, because the
# build stage needs the full Go source tree:
#   docker build -f sim/demo/Dockerfile -t bourse-demo .

FROM golang:1.27 AS gobuild
WORKDIR /src
COPY go.mod ./
RUN go mod download 2>/dev/null || true
COPY cmd ./cmd
COPY internal ./internal
RUN go build -o /out/simserver ./cmd/simserver

FROM python:3.12-slim
WORKDIR /app

# Only the three packages demo_server.py's import chain actually needs at
# runtime (scipy is a stylized_facts.py-only dependency, not loaded here).
RUN pip install --no-cache-dir numpy websockets

# Preserves the repo's relative layout (bin/ next to sim/) because
# engine.py's find_simserver_binary() resolves the binary path from its own
# file location, not from an env var -- matching local dev exactly rather
# than requiring a parallel code path for containers.
COPY --from=gobuild /out/simserver ./bin/simserver
COPY sim/bourse_sim ./sim/bourse_sim
COPY sim/demo/index.html ./sim/demo/index.html

ENV HOST=0.0.0.0
EXPOSE 8765
CMD ["python", "-u", "sim/bourse_sim/demo_server.py"]
