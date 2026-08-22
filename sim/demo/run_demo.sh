#!/usr/bin/env bash
# Launches the live demo: one process serves both the WebSocket simulation
# feed and the static frontend on the same port (see demo_server.py's
# serve_static_or_upgrade), so this matches exactly how the deployed
# version runs -- no separate local-only static file server to fall out of
# sync with production.
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8765}"
echo "Starting demo server on http://localhost:$PORT ..."
echo "Open that URL in a browser. Press Ctrl+C to stop."
(cd .. && PORT="$PORT" .venv/bin/python -u bourse_sim/demo_server.py)
