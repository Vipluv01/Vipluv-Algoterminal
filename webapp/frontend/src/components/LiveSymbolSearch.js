import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";

// Every real NSE equity (~2000+), not just this app's own 7-symbol
// simulated universe -- fetched once (it's a local instrument-master
// lookup server-side, no real Angel One call, but still no reason to
// re-fetch on every keystroke).
function useLiveEquityNames() {
  const [names, setNames] = React.useState(null); // null = not loaded yet
  React.useEffect(() => {
    let cancelled = false;
    api.live.equities().then((rows) => { if (!cancelled) setNames(rows); }).catch(() => { if (!cancelled) setNames([]); });
    return () => { cancelled = true; };
  }, []);
  return names;
}

// Live-mode-only symbol search, shared between Terminal.js and
// ManualTrade.js -- each page's own curated symbol strip/picker still
// exists (and stays the quick-access set for the 7 names this app's own
// simulated engine also models), but live-mode trading/data endpoints
// already accept ANY resolvable real symbol string (confirmed:
// live_market_ws's own _connect(), get_live_history, and the live order
// submit/confirm path never validate against NAMED_INSTRUMENTS at all --
// the picker was the only thing actually narrower than what the backend
// supports). Results capped at 20 -- this is a type-to-filter search
// over ~2000 real names, not a browsable list.
export function LiveSymbolSearch({ onSelect }) {
  const allNames = useLiveEquityNames();
  const [query, setQuery] = React.useState("");
  const matches = query.trim() && allNames
    ? allNames.filter((n) => n.toUpperCase().includes(query.trim().toUpperCase())).slice(0, 20)
    : [];

  return html`
    <div class="live-symbol-search">
      <input class="input" type="text" value=${query} disabled=${!allNames}
             placeholder=${allNames ? `Search any of ${allNames.length.toLocaleString()} real NSE stocks…` : "Loading real equity list…"}
             onInput=${(e) => setQuery(e.target.value)} />
      ${matches.length > 0 && html`
        <div class="live-symbol-results">
          ${matches.map((n) => html`
            <div key=${n} class="live-symbol-result" onClick=${() => { onSelect(n); setQuery(""); }}>${n}</div>
          `)}
        </div>
      `}
    </div>
  `;
}
