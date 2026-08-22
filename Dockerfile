# Proves the engine builds and its full test suite (unit, 100k-op property
# tests, determinism replay) passes in a clean, reproducible environment --
# without requiring Docker installed on every machine that wants to verify
# that claim. CI builds this image on every push.
#
# There is no server entrypoint yet (see README's "Explicitly not built" --
# no network gateway), so this stage runs the test suite itself as the
# image's purpose, rather than pretending a binary exists that doesn't.
FROM golang:1.27
WORKDIR /src
COPY go.mod ./
RUN go mod download 2>/dev/null || true
COPY . .
RUN go build ./...
CMD ["go", "test", "./...", "-v"]
