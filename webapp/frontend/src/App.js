import React from "react";
import { html } from "./html.js";
import { Terminal } from "./pages/Terminal.js";
import { Charts } from "./pages/Charts.js";
import { Dashboard } from "./pages/Dashboard.js";
import { Strategies } from "./pages/Strategies.js";
import { Risk } from "./pages/Risk.js";
import { Pairs } from "./pages/Pairs.js";
import { Optimizer } from "./pages/Optimizer.js";
import { ManualTrade } from "./pages/ManualTrade.js";
import { Accounts } from "./pages/Accounts.js";
import { Journal } from "./pages/Journal.js";
import { Logs } from "./pages/Logs.js";
import { Vault } from "./pages/Vault.js";
import { Leaderboard } from "./pages/Leaderboard.js";
import { PortfolioIQ } from "./pages/PortfolioIQ.js";
import { Options } from "./pages/Options.js";
import { Landing } from "./pages/Landing.js";
import { Ticker } from "./components/Ticker.js";
import { StatusBar } from "./components/StatusBar.js";
import { ShortcutOverlay } from "./components/ShortcutOverlay.js";
import { ModeSwitcher } from "./components/ModeSwitcher.js";
import { CommandPalette, openCommandPalette } from "./components/CommandPalette.js";
import { useTradingHalted, refreshRiskStatus } from "./riskStatus.js";
import { api } from "./api.js";
import { useToast } from "./toast.js";
import { useShortcuts } from "./keyboard.js";
import { setTicketIntent } from "./ticketIntent.js";
import { NAV_ITEMS } from "./navItems.js";

const ROUTES = {
  "": Landing,
  "#/": Landing,
  "#/terminal": Terminal,
  "#/charts": Charts,
  "#/dashboard": Dashboard,
  "#/strategies": Strategies,
  "#/pairs": Pairs,
  "#/optimizer": Optimizer,
  "#/trade": ManualTrade,
  "#/risk": Risk,
  "#/accounts": Accounts,
  "#/journal": Journal,
  "#/logs": Logs,
  "#/settings": Vault,
  "#/leaderboard": Leaderboard,
  "#/portfolio-iq": PortfolioIQ,
  "#/options": Options,
};

const GOTO = (hash) => () => { window.location.hash = hash; };

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
  // A screen can append its own "?filters" onto the hash (Logs does, so a
  // filtered view is shareable/reload-safe) without becoming an unknown
  // route -- ROUTES/nav-link matching both need the bare path, not the
  // query part, or e.g. "#/logs?status=filled" falls through to Terminal
  // and never highlights as "Logs" in the nav.
  const routePath = hash.split("?")[0];
  const Page = ROUTES[routePath] || Terminal;
  const isLanding = Page === Landing;
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  const [shortcutsOpen, setShortcutsOpen] = React.useState(false);
  const halted = useTradingHalted();
  const [resettingHalt, setResettingHalt] = React.useState(false);
  const toast = useToast();

  async function resetHalt() {
    setResettingHalt(true);
    try {
      await api.risk.resetHalt();
      await refreshRiskStatus();
      toast("Trading halt cleared", "ok");
    } catch (e) {
      toast(e.message || "Could not clear the halt", "err");
    } finally {
      setResettingHalt(false);
    }
  }

  // The dropdown must close itself on navigation -- without this, tapping
  // a link on mobile would swap the page underneath while the menu stayed
  // open on top of it, since a hash change doesn't unmount this component.
  React.useEffect(() => { setMobileNavOpen(false); }, [hash]);

  // App-wide bindings, registered once here (the root, always-mounted
  // component) rather than per-page -- a page-scoped shortcut would stop
  // working the moment you navigate away from that page, which is wrong
  // for global navigation chords. useMemo (not useState/inline) keeps the
  // bindings array's IDENTITY stable across renders so useShortcuts'
  // effect doesn't re-register on every render; the handlers close over
  // setMobileNavOpen/setShortcutsOpen, which React guarantees are
  // stable-identity across renders, so this is safe with an empty dep list.
  const bindings = React.useMemo(() => [
    { chord: "g d", description: "Go to Dashboard", group: "Navigation", handler: GOTO("#/dashboard") },
    { chord: "g t", description: "Go to Terminal", group: "Navigation", handler: GOTO("#/terminal") },
    { chord: "g c", description: "Go to Charts", group: "Navigation", handler: GOTO("#/charts") },
    { chord: "g o", description: "Go to Options", group: "Navigation", handler: GOTO("#/options") },
    { chord: "g p", description: "Go to Pairs", group: "Navigation", handler: GOTO("#/pairs") },
    { chord: "g j", description: "Go to Journal", group: "Navigation", handler: GOTO("#/journal") },
    { chord: "g l", description: "Go to Logs", group: "Navigation", handler: GOTO("#/logs") },
    { chord: "g v", description: "Go to Vault", group: "Navigation", handler: GOTO("#/settings") },
    { chord: "g b", description: "Go to Leaderboard", group: "Navigation", handler: GOTO("#/leaderboard") },
    { chord: "g i", description: "Go to Portfolio IQ", group: "Navigation", handler: GOTO("#/portfolio-iq") },
    {
      chord: "b", description: "New buy ticket (pre-filled -- never submits)", group: "Trading",
      // SAFETY: this sets an INITIAL form value and navigates, full stop.
      // See ticketIntent.js's module comment -- there is no code path from
      // a single keystroke to an order leaving this browser.
      handler: () => { setTicketIntent({ side: "buy" }); window.location.hash = "#/trade"; },
    },
    {
      chord: "s", description: "New sell ticket (pre-filled -- never submits)", group: "Trading",
      handler: () => { setTicketIntent({ side: "sell" }); window.location.hash = "#/trade"; },
    },
    { chord: "esc", description: "Close menu / overlay", group: "General", handler: () => { setMobileNavOpen(false); setShortcutsOpen(false); } },
    { chord: "?", description: "Show keyboard shortcuts", group: "General", handler: () => setShortcutsOpen(true) },
    // Cmd+K is registered separately, inside CommandPalette.js itself --
    // this registry deliberately ignores every keydown with a modifier
    // held, so a held-modifier shortcut can't go through it. "/" has no
    // modifier and fits the registry fine, same open action either way.
    { chord: "/", description: "Command palette (or Cmd+K)", group: "General", handler: () => openCommandPalette() },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], []);
  useShortcuts(bindings);

  return html`
    <${React.Fragment}>
      <div class="mode-strip" />
      ${halted && html`
        <div class="halt-banner">
          <span>⛔ TRADING HALTED — the circuit breaker has stopped all new orders</span>
          <button class="btn btn-sm" disabled=${resettingHalt} onClick=${resetHalt}>
            ${resettingHalt ? "Clearing…" : "Clear Halt"}
          </button>
        </div>
      `}
      ${!isLanding && html`
        <${Ticker} />
        <nav class="topnav">
          <a href="#/" class="brand" style=${{ textDecoration: "none" }}>
            <span class="brand-mark">A</span>
            <span>algoterminal</span>
          </a>
          <div class=${`nav-links ${mobileNavOpen ? "open" : ""}`}>
            ${NAV_ITEMS.map((item) => html`
              <a key=${item.hash} href=${item.hash}
                 class=${`nav-link ${routePath === item.hash ? "active" : ""}`}>
                ${item.label}
                ${item.chord && html`<span class="shortcut-hint">${item.chord}</span>`}
              </a>
            `)}
          </div>
          <${ModeSwitcher} />
          <button class="nav-toggle" aria-label="Menu" onClick=${() => setMobileNavOpen((v) => !v)}>
            ${mobileNavOpen ? "✕" : "☰"}
          </button>
        </nav>
      `}
      <${Page} />
      ${!isLanding && html`<${StatusBar} />`}
      ${shortcutsOpen && html`<${ShortcutOverlay} onClose=${() => setShortcutsOpen(false)} />`}
      <${CommandPalette} />
    <//>
  `;
}
