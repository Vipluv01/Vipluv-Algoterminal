# algoterminal frontend

React, no build step. Node.js isn't installed on the machine this was
built on, so instead of Vite/webpack, every dependency (React, ReactDOM,
`htm` for JSX-like syntax, `klinecharts`) loads straight from esm.sh via
an import map in `index.html`. `htm` compiles tagged template literals
(`` html`<div>...</div>` ``) to `React.createElement` calls at runtime --
same component model as JSX, zero bundler.

If Node ever is available and a real build (code-splitting, minification,
a proper production bundle) is wanted, this maps cleanly onto Vite: drop
the import map, `npm install react react-dom htm klinecharts`, adjust the
few CDN-specific import paths (`htm/react`, `react-dom/client`) to their
npm equivalents. Nothing about the component code itself is CDN-specific.

## Run it locally

Needs the backend running first (`cd .. && .venv/bin/uvicorn app.main:app --port 8001`),
then serve this directory as static files:

```bash
python3 -m http.server 5173
```

Open http://localhost:5173. The API base defaults to `http://localhost:8001`;
override by setting `window.__ALGOTERMINAL_API__` before `main.js` loads
(e.g. a small inline `<script>` in `index.html`) for a different backend URL.

## Structure

```
index.html          shell + import map
src/theme.css        design system (colors, type, components)
src/html.js           re-exports htm's pre-bound `html` tag
src/api.js            REST client + WebSocket subscription helper
src/App.js             hash-based router + top nav
src/pages/             one file per page (Terminal, Dashboard, Strategies, Landing)
src/components/        shared pieces (CandleChart, OrderBook, OrderEntry, AccountPanel, Calendar)
```

## Known gaps (not yet built)

- Auth (Phase 3 of the plan) -- every request currently resolves to one
  fixed dev user (see `../app/auth.py`).
- Live trading UI -- the backend already 501s any `mode: "live"` order
  until Phase 4's broker adapter exists; the frontend has no live-mode
  toggle yet since there's nothing for it to do.
- Multi-chart grid view and the Portfolio-IQ/Risk pages from the reviewed
  video/screenshots aren't built -- Terminal, Dashboard, and Strategies
  cover the core loop (trade, review performance, manage strategies).
