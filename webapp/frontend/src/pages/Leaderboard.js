import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useDensity } from "../theme.js";
import { DataTable } from "../components/DataTable.js";
import { ErrorBoundary } from "../components/ErrorBoundary.js";
import { EmptyState } from "../components/EmptyState.js";
import { pnl, pnlClass, dash } from "../format.js";

const LAST_VISIT_KEY = "algoterminal:leaderboard:lastVisit";
const POLL_MS = 4000;

const ROLE_LABEL = {
  human: "You",
  market_maker: "Market Maker",
  noise_trader: "Noise Trader",
  informed_trader: "Informed Trader",
};

function readLastVisit() {
  try {
    return window.localStorage.getItem(LAST_VISIT_KEY);
  } catch {
    return null;
  }
}

function writeLastVisit(iso) {
  try {
    window.localStorage.setItem(LAST_VISIT_KEY, iso);
  } catch {
    /* non-fatal -- this session just won't get a delta baseline next time */
  }
}

function RankDelta({ delta }) {
  if (delta === null || delta === undefined) return html`<span style=${{ color: "var(--text-faint)" }}>${dash()}</span>`;
  if (delta === 0) return html`<span style=${{ color: "var(--text-faint)" }}>—</span>`;
  const up = delta > 0; // positive = moved UP (rank number decreased), per LeaderboardEntryOut's own docstring
  return html`<span class=${up ? "pos" : "neg"}>${up ? "▲" : "▼"} ${Math.abs(delta)}</span>`;
}

export function Leaderboard() {
  const density = useDensity();
  const [since, setSince] = React.useState(null); // baseline captured once, at mount
  const [sinceCaptured, setSinceCaptured] = React.useState(false);
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState(null);

  // The baseline for "delta since last visit" is READ once (whatever the
  // previous visit left behind) and the NEW "last visit" timestamp is
  // written immediately after -- so the delta shown for the rest of THIS
  // session stays anchored to when this visit started, not to whatever
  // `now` is on each poll (which would make the delta shrink toward zero
  // in real time instead of meaning anything).
  React.useEffect(() => {
    const prev = readLastVisit();
    writeLastVisit(new Date().toISOString());
    setSince(prev);
    setSinceCaptured(true);
  }, []);

  const load = React.useCallback(() => {
    if (!sinceCaptured) return;
    api.leaderboard(since || undefined)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message || "Could not load the leaderboard"));
  }, [since, sinceCaptured]);

  React.useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const columns = [
    { key: "rank", label: "Rank", width: "70px", render: (r) => `#${r.rank}` },
    {
      key: "label", label: "Trader",
      render: (r) => html`
        <span style=${{ fontWeight: r.role === "human" ? 700 : 400 }}>
          ${r.label}
          ${r.role === "human" && html`<span class="badge badge-accent" style=${{ marginLeft: "8px" }}>YOU</span>`}
        </span>
      `,
    },
    { key: "role", label: "Type", render: (r) => ROLE_LABEL[r.role] || r.role },
    { key: "pnl", label: "P&L", align: "right", render: (r) => html`<span class=${pnlClass(r.pnl)}>${pnl(r.pnl)}</span>` },
    { key: "pnl_delta", label: "Δ P&L", align: "right", render: (r) => (r.pnl_delta == null ? dash() : html`<span class=${pnlClass(r.pnl_delta)}>${pnl(r.pnl_delta)}</span>`) },
    { key: "rank_delta", label: "Δ Rank", align: "right", render: (r) => html`<${RankDelta} delta=${r.rank_delta} />` },
  ];

  return html`
    <div class="page fade-in">
      <div style=${{ marginBottom: "20px" }}>
        <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Leaderboard</h1>
        <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>
          Real P&L from recorded fills, across every bot and you.
          ${since ? " Δ columns are the change since your last visit." : " No prior visit on record yet — Δ columns will populate from here on."}
        </div>
      </div>

      <${ErrorBoundary} label="Leaderboard">
        ${data === null && !error && html`<div class="skeleton" style=${{ height: "360px" }} />`}
        ${error && html`
          <div class="error-state">
            <div>
              <div class="error-state-title">Could not load the leaderboard</div>
              <div class="error-state-detail">${error}</div>
            </div>
            <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
          </div>
        `}
        ${data !== null && !error && data.entries.length === 0 && html`
          <${EmptyState} message="No participants registered yet." />
        `}
        ${data !== null && !error && data.entries.length > 0 && html`
          <${React.Fragment}>
            <${DataTable} columns=${columns} rows=${data.entries} rowKey="owner_id" sortable density=${density} />
            ${data.since != null && !data.since_coverage_complete && html`
              <div style=${{ marginTop: "12px", fontSize: "12px", color: "var(--warn)" }} title=${data.fill_window_note}>
                ⚠ Some history since your last visit was already evicted from the retained fill window — Δ figures above are a partial reconstruction, not the full since-last-visit change.
              </div>
            `}
          <//>
        `}
      <//>
    </div>
  `;
}
