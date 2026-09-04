import React from "react";
import { html } from "../html.js";

// Generic type-to-filter search over WHATEVER symbol universe the caller
// hands it -- extracted from LiveSymbolSearch.js (which now wraps this)
// so paper/virtual mode's own tradable universe (app.markets.NAMED_
// INSTRUMENTS, sourced locally from GET /symbols, not a live fetch) gets
// the exact same search UI live mode already had, rather than a second,
// differently-behaved picker. `names` is `null` while still loading (the
// live-mode caller fetches asynchronously; the paper/virtual caller
// already has its list synchronously by the time this renders, so it
// only ever sees the loaded state) -- results capped at 20, since this is
// a type-to-filter search, not a browsable list, even for a small universe.
export function SymbolSearch({ names, onSelect, placeholder }) {
  const [query, setQuery] = React.useState("");
  const matches = query.trim() && names
    ? names.filter((n) => n.toUpperCase().includes(query.trim().toUpperCase())).slice(0, 20)
    : [];

  return html`
    <div class="symbol-search">
      <input class="input" type="text" value=${query} disabled=${!names}
             placeholder=${names ? placeholder : "Loading…"}
             onInput=${(e) => setQuery(e.target.value)} />
      ${matches.length > 0 && html`
        <div class="symbol-search-results">
          ${matches.map((n) => html`
            <div key=${n} class="symbol-search-result" onClick=${() => { onSelect(n); setQuery(""); }}>${n}</div>
          `)}
        </div>
      `}
    </div>
  `;
}
