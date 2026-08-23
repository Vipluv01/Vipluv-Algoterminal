import React from "react";
import { html } from "./html.js";
import { Terminal } from "./pages/Terminal.js";
import { Charts } from "./pages/Charts.js";
import { Dashboard } from "./pages/Dashboard.js";
import { Strategies } from "./pages/Strategies.js";
import { Risk } from "./pages/Risk.js";
import { Pairs } from "./pages/Pairs.js";
import { Optimizer } from "./pages/Optimizer.js";
import { Accounts } from "./pages/Accounts.js";
import { Landing } from "./pages/Landing.js";

const ROUTES = {
  "": Landing,
  "#/": Landing,
  "#/terminal": Terminal,
  "#/charts": Charts,
  "#/dashboard": Dashboard,
  "#/strategies": Strategies,
  "#/pairs": Pairs,
  "#/optimizer": Optimizer,
  "#/risk": Risk,
  "#/accounts": Accounts,
};

const NAV_ITEMS = [
  { hash: "#/terminal", label: "Terminal" },
  { hash: "#/charts", label: "Charts" },
  { hash: "#/dashboard", label: "Dashboard" },
  { hash: "#/strategies", label: "Strategies" },
  { hash: "#/pairs", label: "Pairs" },
  { hash: "#/optimizer", label: "Optimizer" },
  { hash: "#/risk", label: "Risk" },
  { hash: "#/accounts", label: "Accounts" },
];

function useHashRoute() {
  const [hash, setHash] = React.useState(window.location.hash || "");
  React.useEffect(() => {
    const onChange = () => setHash(window.location.hash || "");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export function App() {
  const hash = useHashRoute();
  const Page = ROUTES[hash] || Terminal;
  const isLanding = Page === Landing;

  return html`
    <${React.Fragment}>
      ${!isLanding && html`
        <nav class="topnav">
          <a href="#/" class="brand" style=${{ textDecoration: "none" }}>
            <span class="brand-mark">A</span>
            <span>algoterminal</span>
          </a>
          <div class="nav-links">
            ${NAV_ITEMS.map((item) => html`
              <a key=${item.hash} href=${item.hash}
                 class=${`nav-link ${hash === item.hash ? "active" : ""}`}>
                ${item.label}
              </a>
            `)}
          </div>
          <span class="badge badge-off">PAPER MODE</span>
        </nav>
      `}
      <${Page} />
    <//>
  `;
}
