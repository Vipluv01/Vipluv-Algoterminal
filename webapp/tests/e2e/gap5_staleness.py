#!/usr/bin/env python3
"""Gap 5: a safety behaviour, not a cosmetic one. With the backend
actually killed mid-session (not simulated), confirm the connection
indicator degrades, the last known price stays visibly on screen (never
blanked), a data-age appears, and reconnection is real once the backend
comes back. Separately verifies prefers-reduced-motion swaps the tick
flash animation for a static underline with no background pulse.

Runs its OWN dedicated backend on :8002, NOT the shared dev instance on
:8001 -- an earlier run of this script killed :8001 directly and briefly
disrupted another session's test run that happened to be mid-suite
against it at the same time (project-0c, PART A re-verification). The
frontend has an explicit override hook for exactly this
(window.__ALGOTERMINAL_API__, see api.js) so the page under test can
point at an isolated backend instance without touching whatever anyone
else has running on :8001. This instance is started and torn down
entirely within this script; :8001 is never touched.

Usage: .venv/bin/python tests/e2e/gap5_staleness.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import THEME_KEY, DENSITY_KEY  # noqa: E402

WEBAPP_DIR = Path(__file__).resolve().parents[2]
TEST_PORT = 8002
WAIT_AFTER_KILL_S = 5.0


def start_backend(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [".venv/bin/uvicorn", "app.main:app", "--port", str(port)],
        cwd=str(WEBAPP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_backend_up(port: int, timeout_s: float = 20.0) -> bool:
    import httpx

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"http://localhost:{port}/", timeout=1.0)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def goto_terminal_against(page, port: int) -> None:
    """Loads the Terminal page pointed at a specific backend port via the
    window.__ALGOTERMINAL_API__ override, with default theme/density set
    the same way common.goto_page does."""
    page.add_init_script(
        f"""
        window.__ALGOTERMINAL_API__ = 'http://localhost:{port}';
        window.localStorage.setItem({THEME_KEY!r}, 'dark');
        window.localStorage.setItem({DENSITY_KEY!r}, 'comfortable');
        """
    )
    page.goto("http://localhost:5173/#/terminal", wait_until="networkidle")


def get_status_bar_state(page) -> dict:
    return page.evaluate(
        """() => {
            const dot = document.querySelector('.conn-dot');
            const label = document.querySelector('.statusbar-item, [class*=status]');
            return {
                dotClass: dot ? dot.className : null,
                bodyText: document.querySelector('.status-bar, [class*=statusbar]')
                    ? document.querySelector('.status-bar, [class*=statusbar]').innerText.slice(0, 300)
                    : document.body.innerText.slice(0, 500),
            };
        }"""
    )


def get_terminal_badge(page) -> str:
    return page.evaluate(
        """() => {
            const el = document.querySelector('.status-pill');
            return el ? el.innerText.trim() : null;
        }"""
    )


def main() -> int:
    failures: list[str] = []

    print(f"starting a dedicated backend on :{TEST_PORT} (leaving :8001 untouched)...")
    proc = start_backend(TEST_PORT)
    try:
        if not wait_for_backend_up(TEST_PORT):
            print(f"dedicated backend on :{TEST_PORT} did not come up in time", file=sys.stderr)
            return 2
        print(f"dedicated backend up, pid={proc.pid}")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            goto_terminal_against(page, TEST_PORT)
            page.wait_for_timeout(1500)  # let at least one real tick arrive so there's a "last price" to check

            before = get_status_bar_state(page)
            before_badge = get_terminal_badge(page)
            Path("tests/e2e/screenshots").mkdir(parents=True, exist_ok=True)
            page.screenshot(path="tests/e2e/screenshots/gap5_1_live.png", full_page=True)
            print("=== before kill ===")
            print(before["bodyText"])
            print(f"Terminal header badge: {before_badge!r}")

            if "LIVE" not in before["bodyText"].upper() and "live" not in (before["dotClass"] or ""):
                failures.append(f"page did not show a LIVE status before the kill (got: {before['bodyText'][:100]!r})")
            if before_badge != "Live":
                failures.append(f"Terminal header badge did not show 'Live' before the kill (got: {before_badge!r})")

            # --- kill the DEDICATED backend for real (never :8001) ---
            print(f"\nkilling dedicated backend pid={proc.pid} on :{TEST_PORT} (SIGKILL)...")
            proc.kill()
            proc.wait(timeout=5)
            time.sleep(WAIT_AFTER_KILL_S)

            after = get_status_bar_state(page)
            after_badge = get_terminal_badge(page)
            page.screenshot(path="tests/e2e/screenshots/gap5_2_stale.png", full_page=True)
            print(f"\n=== {WAIT_AFTER_KILL_S}s after kill ===")
            print(after["bodyText"])
            print(f"Terminal header badge: {after_badge!r}")

            degraded = any(w in after["bodyText"].upper() for w in ("RECONNECTING", "OFFLINE"))
            degraded = degraded or any(w in (after["dotClass"] or "") for w in ("reconnecting", "offline"))
            if not degraded:
                failures.append(f"connection indicator did not show reconnecting/offline {WAIT_AFTER_KILL_S}s after kill (got: {after['bodyText'][:150]!r}, dotClass={after['dotClass']!r})")
            if after_badge not in ("Reconnecting…", "Offline"):
                failures.append(f"Terminal header badge did not degrade after the kill (got: {after_badge!r}, expected Reconnecting…/Offline)")

            # last price must still be ON SCREEN, not blanked
            price_still_visible = page.evaluate(
                """() => {
                    const stale = document.querySelectorAll('.is-stale');
                    return { count: stale.length, sample: stale.length ? stale[0].textContent.trim() : null };
                }"""
            )
            print(f"\n.is-stale elements: {price_still_visible}")
            if price_still_visible["count"] == 0:
                failures.append(
                    "no element carries .is-stale after the kill -- the connection indicator (StatusBar text, "
                    "Terminal header badge) correctly degrades, but no PRICE display (order book, ticker, chart) "
                    "visually desaturates the way the spec describes. Last value stays visible (not blanked), "
                    "just not dimmed. Not fixed in this pass -- wiring .is-stale into OrderBook.js/Ticker.js/"
                    "CandleChart.js needs its own per-component lastUpdated tracking, a bigger lift than this "
                    "verification pass; flagged for the cross-cutting 'five states everywhere' work."
                )
            elif not price_still_visible["sample"]:
                failures.append(".is-stale element(s) present but rendered with EMPTY text -- last value was blanked, not just dimmed")

            # --- restart the DEDICATED backend and confirm recovery ---
            print(f"\nrestarting dedicated backend on :{TEST_PORT}...")
            proc = start_backend(TEST_PORT)
            up = wait_for_backend_up(TEST_PORT)
            if not up:
                failures.append("dedicated backend did not come back up within 20s of restart")
            else:
                print("backend is back up, waiting for the app to reconnect...")
                recovered = False
                recovery_deadline = time.monotonic() + 15
                while time.monotonic() < recovery_deadline:
                    state = get_status_bar_state(page)
                    if "LIVE" in state["bodyText"].upper() or "live" in (state["dotClass"] or ""):
                        recovered = True
                        break
                    page.wait_for_timeout(500)
                page.screenshot(path="tests/e2e/screenshots/gap5_3_recovered.png", full_page=True)
                recovered_badge = get_terminal_badge(page)
                print(f"Terminal header badge after recovery: {recovered_badge!r}")
                if not recovered:
                    failures.append("app did not return to LIVE status within 15s of the backend coming back up")
                else:
                    print("recovered to LIVE.")
                if recovered_badge != "Live":
                    failures.append(f"Terminal header badge did not recover to 'Live' (got: {recovered_badge!r})")

            # --- prefers-reduced-motion: verify the CSS mechanism itself ---
            page.emulate_media(reduced_motion="reduce")
            page.wait_for_timeout(100)
            motion_check = page.evaluate(
                """() => {
                    const probe = document.createElement('div');
                    probe.className = 'tick-flash-up';
                    document.body.appendChild(probe);
                    const cs = getComputedStyle(probe);
                    const result = { animationName: cs.animationName, boxShadow: cs.boxShadow };
                    probe.remove();
                    return result;
                }"""
            )
            print(f"\nreduced-motion .tick-flash-up computed style: {motion_check}")
            if motion_check["animationName"] not in ("none", None):
                failures.append(f"prefers-reduced-motion: .tick-flash-up still has an active animation ({motion_check['animationName']!r}), expected 'none'")
            if motion_check["boxShadow"] in ("none", "", None):
                failures.append("prefers-reduced-motion: .tick-flash-up has no static underline (box-shadow) fallback")

            conn_dot_motion = page.evaluate(
                """() => {
                    const probe = document.createElement('div');
                    probe.className = 'conn-dot reconnecting';
                    document.body.appendChild(probe);
                    const name = getComputedStyle(probe).animationName;
                    probe.remove();
                    return name;
                }"""
            )
            print(f"reduced-motion .conn-dot.reconnecting animationName: {conn_dot_motion!r}")
            if conn_dot_motion not in ("none", None):
                failures.append(f"prefers-reduced-motion: .conn-dot.reconnecting still pulses (animationName={conn_dot_motion!r}), expected 'none'")

            browser.close()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nall Gap 5 checks passed: staleness degrades visibly without blanking, reconnection recovers, reduced-motion honored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
