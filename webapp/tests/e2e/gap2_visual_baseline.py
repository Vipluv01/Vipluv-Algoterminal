#!/usr/bin/env python3
"""Gap 2: capture the CURRENT state (post Part-1 typography changes) as the
permanent visual baseline for all 10 pages x {dark,light} x
{comfortable,compact} = 40 screenshots, and report any page that fails to
render or throws a console error on load.

This does NOT attempt to recover a true pre-Task-1 "before" -- Task 1 has
already landed and stashing/reverting mid-multi-session-edit is a real
corruption risk (see the relayed task's own instruction). This baseline is
the reference future phases diff against instead.

Usage: .venv/bin/python tests/e2e/gap2_visual_baseline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BASE_URL, DENSITIES, PAGES, THEMES, apply_theme_density, wait_for_dom_settle  # noqa: E402

SCREENSHOT_DIR = Path(__file__).resolve().parent / "screenshots" / "baseline"


def main() -> int:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        # Establish a document first (apply_theme_density's reload() needs
        # one to already exist) using a PLAIN goto with no init script --
        # add_init_script() registrations are permanent for the page's
        # lifetime with no way to remove one, so if goto_page's own
        # init-script helper were used here, its (theme, density) values
        # would keep re-firing on every later reload() and silently
        # override apply_theme_density's fresh evaluate() call each time
        # (this is exactly the bug the first version of this script had --
        # every group after the first came out "dark/comfortable" again).
        page.goto(f"{BASE_URL}/{PAGES[0][1]}", wait_until="networkidle")

        for theme in THEMES:
            for density in DENSITIES:
                apply_theme_density(page, theme, density)

                for label, route_hash in PAGES:
                    console_errors: list[str] = []
                    page_errors: list[str] = []
                    handler = lambda msg: console_errors.append(msg.text) if msg.type == "error" else None
                    err_handler = lambda exc: page_errors.append(str(exc))
                    page.on("console", handler)
                    page.on("pageerror", err_handler)

                    try:
                        page.goto(f"{BASE_URL}/{route_hash}", wait_until="networkidle", timeout=15000)
                        # networkidle fires once HTTP requests finish, which can be
                        # BEFORE React finishes painting setState results on a
                        # data-heavy page (Strategies' 8 cards, Optimizer's Kelly
                        # panel, Charts' 4 seeded candle charts) -- especially under
                        # concurrent-session CPU contention. wait_for_dom_settle
                        # polls until the DOM actually stops changing instead of
                        # trusting a fixed delay; without it this script has
                        # intermittently captured a page mid-skeleton as if that
                        # were its final state, which silently corrupts the
                        # baseline it's supposed to be the source of truth for.
                        wait_for_dom_settle(page, timeout_ms=6000)
                        page.wait_for_timeout(150)  # let any post-settle layout (fonts, chart repaint) finish
                        actual_theme = page.eval_on_selector("html", "el => el.getAttribute('data-theme')")
                        actual_density = page.eval_on_selector("html", "el => el.getAttribute('data-density')")
                        if actual_theme != theme or actual_density != density:
                            problems.append(
                                f"{label} [{theme}/{density}]: DOM attrs mismatch "
                                f"(got data-theme={actual_theme!r} data-density={actual_density!r})"
                            )
                        fname = SCREENSHOT_DIR / f"{label}__{theme}__{density}.png"
                        page.screenshot(path=str(fname), full_page=True)
                    except Exception as exc:  # noqa: BLE001
                        problems.append(f"{label} [{theme}/{density}]: FAILED TO RENDER -- {exc}")
                    finally:
                        page.remove_listener("console", handler)
                        page.remove_listener("pageerror", err_handler)

                    if console_errors:
                        for e in console_errors:
                            problems.append(f"{label} [{theme}/{density}]: console error -- {e}")
                    if page_errors:
                        for e in page_errors:
                            problems.append(f"{label} [{theme}/{density}]: page error -- {e}")

        browser.close()

    total = len(PAGES) * len(THEMES) * len(DENSITIES)
    captured = len(list(SCREENSHOT_DIR.glob("*.png")))
    print(f"captured {captured}/{total} screenshots into {SCREENSHOT_DIR}")

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("no render failures, no console/page errors on any page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
