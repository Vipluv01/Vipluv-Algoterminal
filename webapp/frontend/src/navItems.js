// The app's own page list -- extracted from App.js so CommandPalette.js
// can share the SAME list without App.js importing CommandPalette (which
// mounts globally in App.js) creating a circular import between the two.
export const NAV_ITEMS = [
  { hash: "#/terminal", label: "Terminal", chord: "G T" },
  { hash: "#/charts", label: "Charts", chord: "G C" },
  { hash: "#/dashboard", label: "Dashboard", chord: "G D" },
  { hash: "#/strategies", label: "Strategies" },
  { hash: "#/pairs", label: "Pairs", chord: "G P" },
  { hash: "#/options", label: "Options", chord: "G O" },
  { hash: "#/optimizer", label: "Optimizer" },
  { hash: "#/trade", label: "Trade" },
  { hash: "#/risk", label: "Risk" },
  { hash: "#/accounts", label: "Accounts" },
  { hash: "#/journal", label: "Journal", chord: "G J" },
  { hash: "#/logs", label: "Logs", chord: "G L" },
  { hash: "#/settings", label: "Vault", chord: "G V" },
  { hash: "#/leaderboard", label: "Leaderboard", chord: "G B" },
  { hash: "#/portfolio-iq", label: "Portfolio IQ", chord: "G I" },
];
