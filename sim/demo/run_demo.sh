#!/usr/bin/env bash
# Launches the live demo: the WebSocket simulation server plus a static
# file server for the frontend, then prints the URL to open.
set -e
cd "$(dirname "$0")"

echo "Starting simulation server (ws://localhost:8765)..."
(cd .. && .venv/bin/python -u bourse_sim/demo_server.py) &
SIM_PID=$!

echo "Starting static file server (http://localhost:8080)..."
python3 -m http.server 8080 &
HTTP_PID=$!

trap "kill $SIM_PID $HTTP_PID 2>/dev/null" EXIT INT TERM

echo
echo "Open http://localhost:8080/index.html in a browser."
echo "Press Ctrl+C to stop both servers."
wait
