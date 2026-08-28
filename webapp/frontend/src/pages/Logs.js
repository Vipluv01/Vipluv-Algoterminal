import React from "react";
import { html } from "../html.js";
import { api } from "../api.js";
import { useDensity } from "../theme.js";
import { DataTable } from "../components/DataTable.js";
import { EmptyState } from "../components/EmptyState.js";
import { ErrorBoundary } from "../components/ErrorBoundary.js";
import { SkeletonRows } from "../components/Skeleton.js";
import { px, dash } from "../format.js";

const STATUSES = ["pending_confirmation", "submitted", "filled", "partially_filled", "rejected", "cancelled"];
const PAGE_SIZE = 50;

const STATUS_BADGE = {
  filled: "badge-live",
  partially_filled: "badge-accent",
  submitted: "badge-off",
  pending_confirmation: "badge-off",
  rejected: "badge-neg",
  cancelled: "badge-neg",
};

// Route ("#/logs") lives in App.js's ROUTES; everything after "?" here is
// this screen's own filter state, not part of routing (see App.js's
// routePath split). Round-tripping through URLSearchParams both ways
// keeps this the single source of truth for "what's the current filter",
// so reload/share/back-button all just work off the URL.
function parseHashQuery() {
  const q = (window.location.hash.split("?")[1]) || "";
  const params = new URLSearchParams(q);
  return {
    mode: params.get("mode") || "",
    status: params.get("status") || "",
    symbol: params.get("symbol") || "",
    strategy: params.get("strategy") || "",
    dateFrom: params.get("date_from") || "",
    dateTo: params.get("date_to") || "",
    offset: Number(params.get("offset") || 0),
  };
}

function writeHashQuery(filters) {
  const params = new URLSearchParams();
  if (filters.mode) params.set("mode", filters.mode);
  if (filters.status) params.set("status", filters.status);
  if (filters.symbol) params.set("symbol", filters.symbol);
  if (filters.strategy) params.set("strategy", filters.strategy);
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  if (filters.offset) params.set("offset", String(filters.offset));
  const qs = params.toString();
  window.location.hash = `#/logs${qs ? `?${qs}` : ""}`;
}

function useHashQuery() {
  const [filters, setFilters] = React.useState(parseHashQuery);
  React.useEffect(() => {
    const onChange = () => setFilters(parseHashQuery());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return filters;
}

export function Logs() {
  const filters = useHashQuery();
  const density = useDensity();
  const [symbols, setSymbols] = React.useState([]);
  const [strategies, setStrategies] = React.useState([]);
  const [result, setResult] = React.useState(null); // { items, total } | null
  const [error, setError] = React.useState(null);

  React.useEffect(() => {
    api.symbols().then(setSymbols).catch(() => setSymbols([]));
    api.strategies.list().then(setStrategies).catch(() => setStrategies([]));
  }, []);

  const load = React.useCallback(() => {
    setError(null);
    api.orders.listPage({
      mode: filters.mode || undefined,
      status: filters.status || undefined,
      symbol: filters.symbol || undefined,
      strategy: filters.strategy || undefined,
      dateFrom: filters.dateFrom || undefined,
      dateTo: filters.dateTo || undefined,
      limit: PAGE_SIZE,
      offset: filters.offset,
    })
      .then(setResult)
      .catch((e) => setError(e.message || "Could not load orders"));
  }, [filters.mode, filters.status, filters.symbol, filters.strategy, filters.dateFrom, filters.dateTo, filters.offset]);
  React.useEffect(() => { load(); }, [load]);

  function patchFilters(patch) {
    // Any real filter change resets to the first page -- keeping the old
    // offset against a new filter set can point past the end (or just at
    // a confusing, unrelated page) of the new result set.
    writeHashQuery({ ...filters, ...patch, offset: "offset" in patch ? patch.offset : 0 });
  }

  const hasAnyFilter = !!(filters.mode || filters.status || filters.symbol || filters.strategy || filters.dateFrom || filters.dateTo);
  const total = result ? result.total : 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(filters.offset / PAGE_SIZE) + 1;

  const columns = [
    { key: "created_at", label: "Time", width: "1.3fr", sortValue: (r) => r.created_at, render: (r) => new Date(r.created_at).toLocaleString() },
    { key: "symbol", label: "Symbol" },
    { key: "side", label: "Side", render: (r) => html`<span class=${r.side === "buy" ? "pos" : "neg"}>${r.side}</span>` },
    { key: "order_type", label: "Type" },
    { key: "qty", label: "Qty", align: "right" },
    { key: "px", label: "Price", align: "right", render: (r) => (r.px == null ? "mkt" : px(r.px)) },
    {
      key: "status", label: "Status",
      render: (r) => html`<span class=${`badge ${STATUS_BADGE[r.status] || "badge-off"}`}>${r.status.replace(/_/g, " ")}</span>`,
    },
    { key: "filled_qty", label: "Filled", align: "right" },
    { key: "avg_fill_px", label: "Avg Fill", align: "right", render: (r) => (r.avg_fill_px == null ? dash() : px(r.avg_fill_px)) },
    { key: "strategy_key", label: "Strategy", render: (r) => r.strategy_key || html`<span title="Manual order, not strategy-generated">${dash()}</span>` },
  ];

  return html`
    <div class="page fade-in">
      <div style=${{ marginBottom: "20px" }}>
        <h1 style=${{ margin: "0 0 4px", fontSize: "20px", fontWeight: 800, letterSpacing: "-0.01em" }}>Logs</h1>
        <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>Full order history. Filters live in the URL — reload or share this link and they hold.</div>
      </div>

      <div style=${{ display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center", marginBottom: "16px" }}>
        <select class="input" style=${{ maxWidth: "140px" }} value=${filters.mode} onChange=${(e) => patchFilters({ mode: e.target.value })}>
          <option value="">All modes</option>
          <option value="paper">Paper</option>
          <option value="live">Live</option>
        </select>
        <select class="input" style=${{ maxWidth: "170px" }} value=${filters.status} onChange=${(e) => patchFilters({ status: e.target.value })}>
          <option value="">All statuses</option>
          ${STATUSES.map((s) => html`<option key=${s} value=${s}>${s.replace(/_/g, " ")}</option>`)}
        </select>
        <select class="input" style=${{ maxWidth: "160px" }} value=${filters.symbol} onChange=${(e) => patchFilters({ symbol: e.target.value })}>
          <option value="">All symbols</option>
          ${symbols.map((s) => html`<option key=${s.symbol} value=${s.symbol}>${s.symbol}</option>`)}
        </select>
        <select class="input" style=${{ maxWidth: "220px" }} value=${filters.strategy} onChange=${(e) => patchFilters({ strategy: e.target.value })}>
          <option value="">All strategies</option>
          ${strategies.map((s) => html`<option key=${s.key} value=${s.key}>${s.name}</option>`)}
        </select>
        <input class="input" type="datetime-local" style=${{ maxWidth: "190px" }} value=${filters.dateFrom}
               onChange=${(e) => patchFilters({ dateFrom: e.target.value })} title="From" />
        <input class="input" type="datetime-local" style=${{ maxWidth: "190px" }} value=${filters.dateTo}
               onChange=${(e) => patchFilters({ dateTo: e.target.value })} title="To" />
        ${hasAnyFilter && html`<button class="btn btn-sm btn-ghost" onClick=${() => writeHashQuery({})}>Clear Filters</button>`}
      </div>

      <${ErrorBoundary} label="Logs">
        ${result === null && !error && html`<${SkeletonRows} count=${8} columns=${9} />`}
        ${error && html`
          <div class="error-state">
            <div>
              <div class="error-state-title">Could not load orders</div>
              <div class="error-state-detail">${error}</div>
            </div>
            <button class="btn btn-sm btn-ghost" onClick=${load}>Retry</button>
          </div>
        `}
        ${result !== null && !error && result.items.length === 0 && html`
          <${EmptyState}
            message=${hasAnyFilter ? "No orders match these filters." : "No orders placed yet."}
            actionLabel=${hasAnyFilter ? "Clear Filters" : undefined}
            onAction=${hasAnyFilter ? () => writeHashQuery({}) : undefined} />
        `}
        ${result !== null && !error && result.items.length > 0 && html`
          <${React.Fragment}>
            <${DataTable} columns=${columns} rows=${result.items} rowKey="id" sortable density=${density} />
            <div style=${{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "12px" }}>
              <div style=${{ color: "var(--text-faint)", fontSize: "12px" }}>${total} order${total === 1 ? "" : "s"} total</div>
              <div style=${{ display: "flex", gap: "8px", alignItems: "center" }}>
                <button class="btn btn-sm btn-ghost" disabled=${filters.offset === 0}
                        onClick=${() => patchFilters({ offset: Math.max(0, filters.offset - PAGE_SIZE) })}>Prev</button>
                <span style=${{ fontSize: "12px", color: "var(--text-faint)" }}>Page ${currentPage} of ${totalPages}</span>
                <button class="btn btn-sm btn-ghost" disabled=${filters.offset + PAGE_SIZE >= total}
                        onClick=${() => patchFilters({ offset: filters.offset + PAGE_SIZE })}>Next</button>
              </div>
            </div>
          <//>
        `}
      <//>
    </div>
  `;
}
