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
