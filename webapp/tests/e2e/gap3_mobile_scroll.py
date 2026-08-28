#!/usr/bin/env python3
"""Gap 3: at viewport width 375, confirm
document.documentElement.scrollWidth <= document.documentElement.clientWidth
for all 10 pages -- i.e. nothing pushes the BODY wider than the viewport.
Reports actual measured scrollWidth/clientWidth per page, not just
pass/fail, and separately flags any element wider than the viewport so a
wide table pushing the body (bad) can be told apart from a wide table that
correctly scrolls inside its own overflow-x container (fine).

Usage: .venv/bin/python tests/e2e/gap3_mobile_scroll.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PAGES  # noqa: E402
from common import goto_page  # noqa: E402

VIEWPORT_WIDTH = 375
VIEWPORT_HEIGHT = 812  # a real phone aspect ratio, not just a narrow desktop window


def main() -> int:
    failures: list[str] = []
    report_lines: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})

        for i, (label, route_hash) in enumerate(PAGES):
            if i == 0:
                goto_page(page, route_hash)
            else:
                page.goto(f"http://localhost:5173/{route_hash}", wait_until="networkidle")
            page.wait_for_timeout(150)

            measurements = page.evaluate(
                """() => {
                    const html = document.documentElement;
                    const overflowing = [];
                    for (const el of document.querySelectorAll('body *')) {
                        const r = el.getBoundingClientRect();
                        if (r.right > window.innerWidth + 1) {  // +1px rounding tolerance
                            // Walk up: is this element (or an ancestor between it and body)
                            // itself horizontally scrollable? If so it's contained, not a leak.
                            let node = el, containedBySelfScroll = false;
                            while (node && node !== document.body) {
                                const style = getComputedStyle(node);
                                if ((style.overflowX === 'auto' || style.overflowX === 'scroll') && node.scrollWidth > node.clientWidth) {
                                    containedBySelfScroll = true;
                                    break;
                                }
                                node = node.parentElement;
                            }
                            if (!containedBySelfScroll) {
                                overflowing.push({
                                    tag: el.tagName.toLowerCase(),
                                    cls: (el.className && typeof el.className === 'string') ? el.className.slice(0, 60) : '',
                                    right: Math.round(r.right),
                                });
                            }
                        }
                    }
                    return {
                        scrollWidth: html.scrollWidth,
                        clientWidth: html.clientWidth,
                        overflowing: overflowing.slice(0, 10),
                    };
                }"""
            )

            sw, cw = measurements["scrollWidth"], measurements["clientWidth"]
            ok = sw <= cw
            status = "OK" if ok else "OVERFLOW"
            report_lines.append(f"{label:12s} scrollWidth={sw:4d}  clientWidth={cw:4d}  [{status}]")
            if not ok:
                failures.append(label)
                for el in measurements["overflowing"]:
                    report_lines.append(f"             -> <{el['tag']} class=\"{el['cls']}\"> right edge at {el['right']}px (viewport {VIEWPORT_WIDTH}px)")

        browser.close()

    print(f"viewport: {VIEWPORT_WIDTH}x{VIEWPORT_HEIGHT}\n")
    for line in report_lines:
        print(line)

    if failures:
        print(f"\n{len(failures)}/{len(PAGES)} page(s) cause body-level horizontal scroll at 375px: {', '.join(failures)}")
        return 1

    print(f"\nall {len(PAGES)} pages: no body-level horizontal scroll at 375px.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
