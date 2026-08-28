// Zero-dependency test harness for format.js -- no test framework (no npm
// install, no bundler, matching this whole frontend's own constraint), just
// a tiny hand-rolled assert. Runs in any browser via format-tests.html
// (open it through nocache_server.py, NOT a plain http.server -- see that
// file's own README note on why: a plain static server serves stale
// cached modules).
//
// Results print to the console AND render into the page (id="results") if
// a DOM is present, so this is inspectable without opening devtools.

import { compact, dash, inr, pct, pnl, px } from "./format.js";

const results = [];

function test(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
  } catch (err) {
    results.push({ name, ok: false, error: err.message });
  }
}

function assertEqual(actual, expected, label) {
  if (actual !== expected) {
    throw new Error(`${label ? label + ": " : ""}expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ---------------------------------------------------------------------------
// inr() -- Indian grouping is the whole point here
// ---------------------------------------------------------------------------

test("inr: formats with Indian grouping (lakh boundary)", () => {
  assertEqual(inr(123456.78), "₹1,23,456.78");
});

test("inr: below the lakh boundary groups normally", () => {
  assertEqual(inr(4567.5), "₹4,567.50");
});

test("inr: crore boundary groups correctly", () => {
  assertEqual(inr(12345678.9), "₹1,23,45,678.90");
});

test("inr: negative values get a leading sign before the rupee symbol", () => {
  assertEqual(inr(-1234.5), "−₹1,234.50");
});

test("inr: negative zero formats as plain, unsigned zero", () => {
  assertEqual(inr(-0), "₹0.00");
});

test("inr: null/undefined/NaN all fall back to the dash sentinel", () => {
  assertEqual(inr(null), dash());
  assertEqual(inr(undefined), dash());
  assertEqual(inr(NaN), dash());
});

test("inr: zero decimals suppresses the fraction", () => {
  assertEqual(inr(150000, { decimals: 0 }), "₹1,50,000");
});

// ---------------------------------------------------------------------------
// compact() -- lakh/crore tile abbreviation
// ---------------------------------------------------------------------------

test("compact: lakh range", () => {
  assertEqual(compact(123456), "₹1.23L");
});

test("compact: crore range", () => {
  assertEqual(compact(45600000), "₹4.56Cr");
});

test("compact: below one lakh falls back to a plain integer rupee figure", () => {
  assertEqual(compact(4200), "₹4,200");
});

test("compact: negative crore value", () => {
  assertEqual(compact(-98765432), "−₹9.88Cr");
});

test("compact: null falls back to the dash sentinel", () => {
  assertEqual(compact(null), dash());
});

// ---------------------------------------------------------------------------
// px() -- precision derived from tick size
// ---------------------------------------------------------------------------

test("px: a 0.05 tick renders 2 decimal places", () => {
  assertEqual(px(1250.05, 0.05), "1,250.05");
});

test("px: a whole-rupee (1.0) tick renders no decimal places", () => {
  assertEqual(px(1250, 1), "1,250");
});

test("px: a half-rupee (0.5) tick renders 1 decimal place", () => {
  assertEqual(px(1250.5, 0.5), "1,250.5");
});

test("px: null falls back to the dash sentinel", () => {
  assertEqual(px(null, 0.05), dash());
});

// ---------------------------------------------------------------------------
// pnl() -- ALWAYS signed, including zero and negative zero
// ---------------------------------------------------------------------------

test("pnl: positive value gets an explicit + sign", () => {
  assertEqual(pnl(1240.5), "+1,240.50");
});

test("pnl: negative value gets a real minus sign (U+2212, not a hyphen)", () => {
  assertEqual(pnl(-312.75), "−312.75");
});

test("pnl: exactly zero still renders with an explicit + sign (ALWAYS signed)", () => {
  assertEqual(pnl(0), "+0.00");
});

test("pnl: negative zero renders as +0.00, never -0.00", () => {
  assertEqual(pnl(-0), "+0.00");
});

test("pnl: null/undefined fall back to the dash sentinel, not a signed zero", () => {
  assertEqual(pnl(null), dash());
  assertEqual(pnl(undefined), dash());
});

test("pnl: respects a custom decimals option", () => {
  // An exact integer input -- deliberately not a X.5 boundary value, whose
  // rounding direction under Intl.NumberFormat's default "halfExpand" mode
  // (round half AWAY from zero) differs from a naive round-half-to-even
  // assumption and isn't worth entangling this test with.
  assertEqual(pnl(1244, { decimals: 0 }), "+1,244");
});

// ---------------------------------------------------------------------------
// pct() -- fixed 2dp, never reflows
// ---------------------------------------------------------------------------

test("pct: fixed 2 decimal places even for a round number", () => {
  assertEqual(pct(0.05), "5.00%");
});

test("pct: does not round away real precision at 2dp", () => {
  assertEqual(pct(0.12345), "12.35%");
});

test("pct: null falls back to the dash sentinel", () => {
  assertEqual(pct(null), dash());
});

// ---------------------------------------------------------------------------
// dash()
// ---------------------------------------------------------------------------

test("dash: returns the em-dash sentinel", () => {
  assertEqual(dash(), "—");
});

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------

const passed = results.filter((r) => r.ok).length;
const failed = results.filter((r) => !r.ok);

console.log(`format.js: ${passed}/${results.length} passed`);
for (const r of failed) console.error(`FAIL: ${r.name} -- ${r.error}`);

if (typeof document !== "undefined") {
  const el = document.getElementById("results") || document.body.appendChild(document.createElement("pre"));
  el.id = "results";
  el.style.cssText = "font-family: monospace; font-size: 13px; white-space: pre-wrap; padding: 16px;";
  el.textContent =
    `format.js: ${passed}/${results.length} passed\n\n` +
    results.map((r) => (r.ok ? `PASS  ${r.name}` : `FAIL  ${r.name}\n      ${r.error}`)).join("\n");
  el.style.color = failed.length ? "#f2794f" : "#4ad9ac";
}

export { results };
