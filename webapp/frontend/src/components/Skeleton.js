import React from "react";
import { html } from "../html.js";

// Shaped placeholders at the FINAL dimensions the real content will
// occupy -- a skeleton's whole job is preventing layout shift once data
// arrives; a spinner (which has no size relationship to what's coming)
// just hides the shift instead of preventing it. Every shape here is a
// thin wrapper around the existing .skeleton shimmer class (theme.css) --
// several pages already reach for that class directly with an inline
// height; these give the common shapes (a line of text, a block, a table
// row matching .table-row's own grid) a reusable, named API instead of
// each page hand-rolling its own inline style object.

export function SkeletonLine({ width = "100%", style = {} }) {
  return html`<div class="skeleton skeleton-line" style=${{ width, ...style }} />`;
}

export function SkeletonBlock({ width = "100%", height = "80px", style = {} }) {
  return html`<div class="skeleton" style=${{ width, height, ...style }} />`;
}

// Mirrors .table-row's own 2fr/1fr/1fr/1fr grid (see theme.css) so a
// loading table's skeleton rows sit at the exact width/column positions
// the real data will render at, not just "some placeholder bars".
export function SkeletonRow({ columns = 4 }) {
  return html`
    <div class="table-row" style=${{ gridTemplateColumns: `2fr repeat(${columns - 1}, 1fr)` }}>
      ${Array.from({ length: columns }, (_, i) => html`
        <${SkeletonLine} key=${i} width=${i === 0 ? "70%" : "50%"}
          style=${{ marginLeft: i === 0 ? 0 : "auto" }} />
      `)}
    </div>
  `;
}

export function SkeletonRows({ count = 5, columns = 4 }) {
  return html`
    <div>
      ${Array.from({ length: count }, (_, i) => html`<${SkeletonRow} key=${i} columns=${columns} />`)}
    </div>
  `;
}
