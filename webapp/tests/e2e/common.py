"""Shared fixtures for the Playwright verification gaps (tests/e2e/gap*.py).

Route list and localStorage keys are read directly from App.js/theme.js --
duplicated here as plain constants rather than imported, since this harness
intentionally has no build step and can't import an ES module into Python.
If either page adds a route or renames a storage key, this file needs a
matching one-line update (there's no way to make that automatic without a
bundler, which is exactly what this frontend avoids).
"""
from __future__ import annotations

BASE_URL = "http://localhost:5173"

# (label, hash) for all 10 routed pages, per App.js's ROUTES table.
PAGES: list[tuple[str, str]] = [
    ("landing", "#/"),
    ("terminal", "#/terminal"),
    ("charts", "#/charts"),
    ("dashboard", "#/dashboard"),
    ("strategies", "#/strategies"),
    ("pairs", "#/pairs"),
    ("optimizer", "#/optimizer"),
    ("trade", "#/trade"),
    ("risk", "#/risk"),
    ("accounts", "#/accounts"),
    ("journal", "#/journal"),
    ("logs", "#/logs"),
    ("settings", "#/settings"),
    ("leaderboard", "#/leaderboard"),
    ("portfolio-iq", "#/portfolio-iq"),
    ("options", "#/options"),
]

THEMES = ["dark", "light"]
DENSITIES = ["comfortable", "compact"]

THEME_KEY = "algoterminal:theme"
DENSITY_KEY = "algoterminal:density"


def apply_theme_density(page, theme: str, density: str) -> None:
    """Sets theme.js's own localStorage keys, then forces a REAL full
    reload so theme.js's module-level readStored() (which only runs once,
    at module evaluation) re-reads them.

    page.goto() to a URL that only differs by hash from the current one is
    a same-document navigation in this SPA (App.js's hashchange listener
    intercepts it) -- it does NOT reload the document, so neither a fresh
    module evaluation nor an add_init_script() registered on the page
    fires. Empirically this meant every screenshot after the first in a
    naive loop silently kept whatever theme/density the FIRST navigation
    happened to load. page.reload() is unconditional (always a real
    navigation), so calling it right after writing localStorage is what
    actually makes the new values take effect. The caller must have
    navigated at least once already (reload() needs an existing document).
    """
    page.evaluate(
        "([tk, tv, dk, dv]) => { localStorage.setItem(tk, tv); localStorage.setItem(dk, dv); }",
        [THEME_KEY, theme, DENSITY_KEY, density],
    )
    page.reload(wait_until="networkidle")


def wait_for_dom_settle(page, selector: str = "*", *, poll_ms: int = 150, stable_polls: int = 3, timeout_ms: int = 5000) -> int:
    """Polls document.querySelectorAll(selector).length until it stops
    changing for `stable_polls` consecutive polls, or `timeout_ms` elapses.
    Returns the final count.

    Exists because this SPA's own hash-only route changes don't count as a
    Playwright "navigation" -- goto()'s wait_until="networkidle" resolves
    almost immediately (no real network request happens for a same-
    document hash change), well before the new route's own async data
    fetch (strategies list, symbols, etc.) has resolved and rendered. A
    fixed short sleep after navigation is a race: it happened to be enough
    for content-light pages and not for data-heavy ones (Strategies'
    12 cards x 3 fields each), which silently undercounted focusable
    elements on exactly those pages in an earlier version of this harness.
    """
    import time

    start = time.monotonic()
    last_count = -1
    consecutive_stable = 0
    while (time.monotonic() - start) * 1000 < timeout_ms:
        count = page.evaluate(f"() => document.querySelectorAll({selector!r}).length")
        if count == last_count:
            consecutive_stable += 1
            if consecutive_stable >= stable_polls:
                return count
        else:
            consecutive_stable = 0
        last_count = count
        page.wait_for_timeout(poll_ms)
    return last_count


def goto_page(page, route_hash: str, *, theme: str = "dark", density: str = "comfortable") -> None:
    """Full navigation to `route_hash` with `theme`/`density` already
    correct on first paint -- safe to call as the very first navigation on
    a fresh page (no existing document required, unlike
    apply_theme_density's reload)."""
    page.add_init_script(
        f"""
        window.localStorage.setItem({THEME_KEY!r}, {theme!r});
        window.localStorage.setItem({DENSITY_KEY!r}, {density!r});
        """
    )
    page.goto(f"{BASE_URL}/{route_hash}", wait_until="networkidle")
