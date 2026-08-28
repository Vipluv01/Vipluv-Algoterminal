export function fmtMoney(v, { decimals = 2 } = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "-" : "";
  return sign + "₹" + Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function fmtPct(v, { decimals = 1 } = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v * 100).toFixed(decimals) + "%";
}

export function fmtNum(v, { decimals = 0 } = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function pnlClass(v) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "pos" : v < 0 ? "neg" : "";
}

// ---------------------------------------------------------------------------
// New formatters (design-system foundation, phase 6 task 1). fmtMoney/
// fmtPct/fmtNum/pnlClass above are UNTOUCHED -- every one of the 10 shipped
// pages already imports them, and this task doesn't rewire any screen.
// Everything below is additive, for the 14 screens this phase builds next.
// ---------------------------------------------------------------------------

const INR_2DP = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const INR_0DP = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 0 });

export function dash() {
  return "—";
}

// ₹1,23,456.78 -- Indian digit grouping (lakh/crore, groups of 2 after the
// first 3 digits), not the 123,456.78 a bare toLocaleString("en-US") or a
// naive replace(/\B(?=(\d{3})+(?!\d))/g, ",") would produce. Intl.
// NumberFormat("en-IN") is the ICU-correct implementation of this grouping
// rule -- hand-rolling it is exactly the kind of "looks right for 4-digit
// numbers, wrong the first time it crosses a lakh" bug this delegates away.
export function inr(v, { decimals = 2 } = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return dash();
  const fmt = decimals === 0 ? INR_0DP : INR_2DP;
  // -0 must format as "0", not "-0" -- Object.is, not === , is what
  // actually distinguishes -0 from 0 (0 === -0 is true in JS).
  const magnitude = Object.is(v, -0) ? 0 : v;
  const sign = magnitude < 0 ? "−" : "";
  return sign + "₹" + fmt.format(Math.abs(magnitude));
}

// Tile-only compact form: ₹1.23L (lakh, >=1,00,000) / ₹4.56Cr (crore,
// >=1,00,00,000). Never used in a ledger or table -- a table needs the
// exact figure (inr()), a tile just needs the shape of the number at a
// glance. Below 1 lakh there's nothing meaningful to abbreviate, so it
// falls back to inr() with no decimals.
export function compact(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return dash();
  const magnitude = Object.is(v, -0) ? 0 : v;
  const sign = magnitude < 0 ? "−" : "";
  const abs = Math.abs(magnitude);
  if (abs >= 1_00_00_000) return sign + "₹" + (abs / 1_00_00_000).toFixed(2) + "Cr";
  if (abs >= 1_00_000) return sign + "₹" + (abs / 1_00_000).toFixed(2) + "L";
  return inr(magnitude, { decimals: 0 });
}

// Decimal precision derived from the instrument's own tick size (0.05 tick
// -> 2dp, 1.0 tick -> 0dp, 0.5 tick -> 1dp) rather than a fixed 2dp for
// every symbol -- a price column mixing instruments with different tick
// sizes at one fixed precision either loses real precision (a sub-rupee
// tick rounded away) or shows fake precision (trailing zeros a whole-
// rupee-tick instrument never actually quotes).
export function px(v, tick = 0.05) {
  if (v === null || v === undefined || Number.isNaN(v)) return dash();
  const decimals = tick > 0 && tick < 1 ? Math.max(0, -Math.floor(Math.log10(tick))) : 0;
  return v.toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

// ALWAYS signed -- +1,240.50 / −312.75 / +0.00 (never a bare "0.00" with
// no sign, and never a "-0.00" for a value that's exactly negative zero:
// Object.is is what actually tells -0 apart from 0, since 0 === -0 is
// true in JS). A real Unicode minus (U+2212), not a hyphen -- it's the
// glyph IBM Plex Mono actually draws at the same advance width as "+",
// which is what keeps a signed numeral column from reflowing by a pixel
// depending on which sign a given row happens to have.
export function pnl(v, { decimals = 2 } = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return dash();
  const magnitude = Object.is(v, -0) ? 0 : v;
  const sign = magnitude < 0 ? "−" : "+";
  const fmt = new Intl.NumberFormat("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return sign + fmt.format(Math.abs(magnitude));
}

// Fixed 2dp, always -- "12.30%" not "12.3%", so a column of percentages
// never reflows width as trailing zeros come and go.
export function pct(v, { decimals = 2 } = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return dash();
  return (v * 100).toFixed(decimals) + "%";
}
