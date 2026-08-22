# Live demo

Watch price emerge from real order flow, against the actual Go matching
engine, in a browser -- no build step, no framework.

## Run it

```bash
./run_demo.sh
```

Then open **http://localhost:8765**.

## Deploy it

One `Dockerfile` builds the Go engine and the Python server into a single
image that serves the WebSocket feed and the static frontend on one port
(`$PORT`, defaulting to 8765) -- required for hosts that only route a
single public port to a service, which is most free tiers.

```bash
# from the repo root (bourse/) -- the build needs the full Go source tree
docker build -f sim/demo/Dockerfile -t bourse-demo .
docker run -p 8765:8765 bourse-demo
```

To put it on a public URL (e.g. [Render](https://render.com), free tier):
create a **Web Service**, point it at this repo, set:
- **Root Directory**: repo root (leave blank)
- **Dockerfile Path**: `sim/demo/Dockerfile`
- **Docker Build Context Directory**: `.`

Render sets `$PORT` automatically; `demo_server.py` reads it. No other
config needed -- there's no database, no secrets, nothing else to wire up.
Free-tier services on most platforms sleep after inactivity, so the first
visitor after a quiet period will see a cold-start delay (tens of seconds)
before the page loads.

## What it shows

- **Order book** -- live resting bids/asks with depth bars, updated every
  simulation step
- **Price chart** -- drawn directly from the mid-price series as it forms;
  this is the same series `stylized_facts.py` validates, watched live
  rather than analyzed after the fact
- **Trade tape** -- every fill as it happens
- **Market maker panel** -- inventory, mark-to-market P&L, quoted spread

## Controls

- **Pause / Resume** -- freeze the simulation
- **Step ×1** -- advance exactly one step, for watching a single decision
  cycle (quote refresh, then agent actions, then the resulting fills) in
  isolation
- **+ Informed trader** -- spawn another informed trader mid-run and watch
  the book react
- **Simulation speed** -- steps per second
- **Fundamental volatility (σ)** -- raise it live and watch the market
  maker's quoted spread widen in response (`vol_estimate` feeding
  `base_half_spread_ticks * (1.0 + vol_estimate)` in `agents.py`)

## Architecture

`demo_server.py` is a thin live-loop wrapper around the exact same
`NoiseTrader`, `InformedTrader`, and `MarketMaker` classes -- and the same
`Engine` subprocess bridge to the real Go book -- that produced every
validated result in this project. `run_simulation` (used for the stylized-
facts and maker-comparison work) runs a fixed number of steps and returns
arrays at the end; this runs indefinitely and broadcasts state over
WebSocket after every step instead. Same simulation primitives, different
consumption pattern -- not a separate toy version built for the demo.

`index.html` is a single self-contained file: vanilla JS, Canvas for the
price chart, no dependencies, no build step. It reconnects automatically if
the WebSocket drops.
