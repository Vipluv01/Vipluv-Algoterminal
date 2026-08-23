import React from "react";
import { html } from "../html.js";

function useClock() {
  const [now, setNow] = React.useState(new Date());
  React.useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

// Deliberately lean: only stats that are actually true right now (paper
// mode, a real clock) -- not decorative filler like a fabricated latency
// or process-id number this codebase has no honest way to source, the
// same "don't fake a metric" discipline the rest of algoterminal's
// backend already follows (e.g. app/routers/risk.py introspects real
// values instead of hardcoding plausible-looking ones).
export function StatusBar() {
  const now = useClock();
  const ist = now.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });

  return html`
    <div class="statusbar">
      <span class="statusbar-item">
        <span class="status-dot live" />
        SYSTEM: LIVE
      </span>
      <span class="statusbar-item">MODE: PAPER</span>
      <span class="statusbar-spacer" />
      <span class="statusbar-item">IST ${ist}</span>
    </div>
  `;
}
