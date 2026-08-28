import React from "react";
import { html } from "../html.js";

// comfortable/compact row heights, matching theme.css's --row-h tokens --
// duplicated as numbers (not read from the CSS custom property) because
// virtualization needs a real number to compute which rows are in the
// visible window; a CSS var isn't readable synchronously at layout time
// without a getComputedStyle round trip per render.
const ROW_HEIGHT_BY_DENSITY = { comfortable: 32, compact: 24 };
const VIRTUALIZE_THRESHOLD = 200;
const OVERSCAN_ROWS = 8;

function VirtualRows({ rows, columns, rowKey, gridTemplate, rowHeight, viewportHeight }) {
  const [scrollTop, setScrollTop] = React.useState(0);
  const totalHeight = rows.length * rowHeight;
  const firstVisible = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN_ROWS);
  const visibleCount = Math.ceil(viewportHeight / rowHeight) + OVERSCAN_ROWS * 2;
  const lastVisible = Math.min(rows.length, firstVisible + visibleCount);
  const slice = rows.slice(firstVisible, lastVisible);

  return html`
    <div class="data-table-virtual-viewport" style=${{ height: `${viewportHeight}px` }}
         onScroll=${(e) => setScrollTop(e.target.scrollTop)}>
      <div style=${{ height: `${totalHeight}px`, position: "relative" }}>
        ${slice.map((row, i) => html`
          <div key=${row[rowKey]} class="data-table-row"
               style=${{ gridTemplateColumns: gridTemplate, position: "absolute", top: `${(firstVisible + i) * rowHeight}px`, left: 0, right: 0 }}>
            ${columns.map((c) => html`
              <div key=${c.key} class=${c.align === "right" ? "align-right mono" : ""}>${c.render ? c.render(row) : row[c.key]}</div>
            `)}
          </div>
        `)}
      </div>
    </div>
  `;
}

// N columns from a `columns` array ({key, label, align, width, render,
// sortValue}), not the fixed 4-column grid .table-row uses elsewhere.
// rowKey is REQUIRED: without a stable identity, a streaming row update
// (a live order book, a ticking P&L table) makes React re-mount every row
// on every update instead of patching in place, which is exactly the
// flicker this primitive exists to avoid.
export function DataTable({ columns, rows, rowKey, sortable = false, page, pageSize, density, emptyState }) {
  if (!rowKey) {
    throw new Error("DataTable requires a rowKey prop -- see the component's own module comment for why.");
  }

  const [sortState, setSortState] = React.useState(null); // { key, dir } | null
  const [internalPage, setInternalPage] = React.useState(page ?? 0);

  const sorted = React.useMemo(() => {
    if (!sortState) return rows;
    const col = columns.find((c) => c.key === sortState.key);
    const accessor = (col && col.sortValue) || ((row) => row[sortState.key]);
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = accessor(a), bv = accessor(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return sortState.dir === "asc" ? -1 : 1;
      if (av > bv) return sortState.dir === "asc" ? 1 : -1;
      return 0;
    });
    return copy;
  }, [rows, sortState, columns]);

  function toggleSort(key) {
    if (!sortable) return;
    setSortState((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      if (prev.dir === "asc") return { key, dir: "desc" };
      return null; // third click: back to unsorted
    });
  }

  const totalPages = pageSize ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const currentPage = Math.min(internalPage, totalPages - 1);
  const visible = pageSize ? sorted.slice(currentPage * pageSize, (currentPage + 1) * pageSize) : sorted;

  const gridTemplate = columns.map((c) => c.width || "1fr").join(" ");
  const rowHeight = ROW_HEIGHT_BY_DENSITY[density] || ROW_HEIGHT_BY_DENSITY.comfortable;
  const shouldVirtualize = !pageSize && visible.length > VIRTUALIZE_THRESHOLD;

  if (!rows.length) {
    return emptyState || html`<div style=${{ padding: "24px", textAlign: "center", color: "var(--text-faint)", fontSize: "13px" }}>No data</div>`;
  }

  return html`
    <div class="data-table" data-density=${density || undefined}>
      <div class="data-table-header" style=${{ gridTemplateColumns: gridTemplate }}>
        ${columns.map((c) => html`
          <div key=${c.key} class=${`data-table-th ${c.align === "right" ? "align-right" : ""} ${sortable ? "sortable" : ""}`}
               role=${sortable ? "button" : undefined} tabindex=${sortable ? "0" : undefined}
               onClick=${sortable ? () => toggleSort(c.key) : undefined}
               onKeyDown=${sortable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSort(c.key); } } : undefined}>
            <span>${c.label}</span>
            ${sortable && sortState && sortState.key === c.key && html`
              <span class="sort-indicator">${sortState.dir === "asc" ? "▲" : "▼"}</span>
            `}
          </div>
        `)}
      </div>
      ${shouldVirtualize
        ? html`<${VirtualRows} rows=${visible} columns=${columns} rowKey=${rowKey} gridTemplate=${gridTemplate} rowHeight=${rowHeight} viewportHeight=${Math.min(600, rowHeight * 15)} />`
        : visible.map((row) => html`
            <div key=${row[rowKey]} class="data-table-row" style=${{ gridTemplateColumns: gridTemplate }}>
              ${columns.map((c) => html`
                <div key=${c.key} class=${c.align === "right" ? "align-right mono" : ""}>${c.render ? c.render(row) : row[c.key]}</div>
              `)}
            </div>
          `)}
      ${pageSize && totalPages > 1 && html`
        <div class="data-table-pager">
          <button class="btn btn-sm btn-ghost" disabled=${currentPage === 0} onClick=${() => setInternalPage((p) => Math.max(0, p - 1))}>Prev</button>
          <span>Page ${currentPage + 1} of ${totalPages}</span>
          <button class="btn btn-sm btn-ghost" disabled=${currentPage >= totalPages - 1} onClick=${() => setInternalPage((p) => Math.min(totalPages - 1, p + 1))}>Next</button>
        </div>
      `}
    </div>
  `;
}
