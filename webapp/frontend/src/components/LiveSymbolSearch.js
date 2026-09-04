import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { SymbolSearch } from "./SymbolSearch.js";

// Every real NSE equity (~2000+), not just this app's own simulated
// universe -- fetched once (it's a local instrument-master lookup
// server-side, no real Angel One call, but still no reason to re-fetch on
// every keystroke). Exported so a page needing several independent
// SymbolSearch instances in live mode at once (Charts.js's per-pane
// pickers) can fetch this ONE shared list itself and pass it to each,
// rather than each pane duplicating its own fetch via LiveSymbolSearch.
export function useLiveEquityNames() {
  const [names, setNames] = React.useState(null); // null = not loaded yet
  React.useEffect(() => {
    let cancelled = false;
    api.live.equities().then((rows) => { if (!cancelled) setNames(rows); }).catch(() => { if (!cancelled) setNames([]); });
    return () => { cancelled = true; };
  }, []);
  return names;
}

// Live-mode-only symbol search, shared between Terminal.js and
// ManualTrade.js -- live-mode trading/data endpoints already accept ANY
// resolvable real symbol string (confirmed: live_market_ws's own
// _connect(), get_live_history, and the live order submit/confirm path
// never validate against NAMED_INSTRUMENTS at all). A thin wrapper over
// SymbolSearch (the generic version, also used by paper/virtual mode
// against their own, differently-sourced symbol list) supplying live
// mode's own async-loaded ~2000-name universe and wording.
export function LiveSymbolSearch({ onSelect }) {
  const allNames = useLiveEquityNames();
  const placeholder = allNames ? `Search any of ${allNames.length.toLocaleString()} real NSE stocks…` : "Loading real equity list…";
  return html`<${SymbolSearch} names=${allNames} onSelect=${onSelect} placeholder=${placeholder} />`;
}
