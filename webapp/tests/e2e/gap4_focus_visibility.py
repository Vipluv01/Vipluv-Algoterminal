#!/usr/bin/env python3
"""Gap 4: Tab through every page and, for each stop, PROVE a visible
focus indicator exists by screenshotting a crop around the focused
element, blurring it, screenshotting the same crop again, and diffing the
two -- rather than just asserting a :focus-visible CSS rule is present in
the stylesheet (which says nothing about whether it's visually distinct,
covered by another element, or clipped).

Also cross-checks the set of elements actually reached by Tab against a
DOM query for everything that SHOULD be focusable, to catch a focusable
element Tab skips over entirely (tabindex="-1" on the wrong node, a
custom control missing a role/tabindex, a modal trapping focus before
reaching it, etc).

Usage: .venv/bin/python tests/e2e/gap4_focus_visibility.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PAGES, goto_page, wait_for_dom_settle  # noqa: E402

FOCUSABLE_SELECTOR = (
    "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), "
    "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"
)
CROP_PAD = 8
NO_DIFF_THRESHOLD = 4  # a handful of anti-aliased edge pixels is noise, not "no visible change"


def tag_focusable_candidates(page) -> int:
    return page.evaluate(
        f"""() => {{
            const els = Array.from(document.querySelectorAll({FOCUSABLE_SELECTOR!r}))
                .filter(el => el.offsetParent !== null || el === document.activeElement);
            els.forEach((el, i) => el.setAttribute('data-e2e-idx', String(i)));
            return els.length;
        }}"""
    )


def crop_around(page, rect: dict, viewport: tuple[int, int]) -> Image.Image:
    vw, vh = viewport
    left = max(0, int(rect["x"]) - CROP_PAD)
    top = max(0, int(rect["y"]) - CROP_PAD)
    right = min(vw, int(rect["x"] + rect["width"]) + CROP_PAD)
    bottom = min(vh, int(rect["y"] + rect["height"]) + CROP_PAD)
    if right <= left or bottom <= top:
        return None
    png_bytes = page.screenshot(clip={"x": left, "y": top, "width": right - left, "height": bottom - top})
    import io
    return Image.open(io.BytesIO(png_bytes))


def main() -> int:
    viewport = (1440, 900)
    findings: dict[str, list[str]] = {label: [] for label, _ in PAGES}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})

        for i, (label, route_hash) in enumerate(PAGES):
            if i == 0:
                goto_page(page, route_hash)
            else:
                page.goto(f"http://localhost:5173/{route_hash}", wait_until="networkidle")
            wait_for_dom_settle(page, FOCUSABLE_SELECTOR)

            n_candidates = tag_focusable_candidates(page)
            visited_idx: set[int] = set()
            no_visible_change: list[str] = []
            seen_signatures: set[str] = set()  # cycle detection, see loop below

            # document.body.focus() first so the very first Tab press starts
            # from a known, consistent place instead of wherever the
            # previous page's navigation happened to leave focus.
            page.evaluate("() => document.body.focus()")

            # Not a fixed budget derived from n_candidates: that count can
            # itself be a lower bound if anything mounts asynchronously
            # after wait_for_dom_settle's window, and a too-small budget
            # then makes real, reachable elements look "unreachable" purely
            # because the walk ran out of Tab presses before getting there
            # (this bit an earlier version of this script on the
            # Strategies page). Instead: keep tabbing until we revisit an
            # element we've already seen (real end of the tab cycle) or
            # hit a generous hard cap as a pure safety net against an
            # infinite loop, not as the expected stopping point.
            HARD_CAP = max(n_candidates * 3, 200)
            for _ in range(HARD_CAP):
                page.keyboard.press("Tab")
                # A live handle to the actually-focused element, NOT a
                # re-query by data-e2e-idx: an earlier version of this
                # script re-queried by idx to restore focus after blur()ing
                # for the diff, and silently did NOTHING when Tab landed on
                # an element that hadn't been pre-tagged (e.g. one rendered
                # dynamically after tagging ran) -- focus stayed cleared,
                # so the *next* Tab press restarted traversal from
                # document.body instead of continuing, burning the whole
                # tab budget and making every later element look
                # "unreachable" when the real bug was in this harness, not
                # the page. Holding the handle directly sidesteps needing
                # to re-find the element by any identifier at all.
                handle = page.evaluate_handle("() => document.activeElement")
                info = handle.evaluate(
                    """el => {
                        if (!el || el === document.body) return null;
                        const r = el.getBoundingClientRect();
                        return {
                            idx: el.getAttribute('data-e2e-idx'),
                            tag: el.tagName.toLowerCase(),
                            label: (el.getAttribute('aria-label') || el.textContent || el.value || '').trim().slice(0, 40),
                            rect: { x: r.x, y: r.y, width: r.width, height: r.height },
                        };
                    }"""
                )
                if info is None:
                    break  # Tab cycled back out to the browser chrome / body

                signature = (
                    f"idx:{info['idx']}"
                    if info["idx"] is not None
                    else f"{info['tag']}|{info['label']}|{round(info['rect']['x'])}|{round(info['rect']['y'])}"
                )
                if signature in seen_signatures:
                    break  # back to an element we've already visited -- full tab cycle complete
                seen_signatures.add(signature)

                if info["idx"] is not None:
                    visited_idx.add(int(info["idx"]))
                if info["rect"]["width"] <= 0 or info["rect"]["height"] <= 0:
                    continue

                focused_img = crop_around(page, info["rect"], viewport)
                handle.evaluate("el => el.blur()")
                unfocused_img = crop_around(page, info["rect"], viewport)
                handle.evaluate("el => el.focus()")  # restore focus so the next Tab continues in order

                if focused_img is not None and unfocused_img is not None and focused_img.size == unfocused_img.size:
                    diff = ImageChops.difference(focused_img.convert("RGB"), unfocused_img.convert("RGB"))
                    changed = sum(diff.convert("L").histogram()[1:])
                    if changed <= NO_DIFF_THRESHOLD:
                        no_visible_change.append(f"<{info['tag']}> \"{info['label']}\"")

            unreachable = sorted(set(range(n_candidates)) - visited_idx)
            if unreachable:
                unreachable_info = page.evaluate(
                    f"""(idxs) => idxs.map(i => {{
                        const el = document.querySelector(`[data-e2e-idx="${{i}}"]`);
                        if (!el) return `idx ${{i}} (not found)`;
                        return `<${{el.tagName.toLowerCase()}}> "${{(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40)}}"`;
                    }})""",
                    unreachable,
                )
                findings[label].append(f"{len(unreachable)} focusable element(s) never reached by Tab: {'; '.join(unreachable_info)}")

            if no_visible_change:
                findings[label].append(
                    f"{len(no_visible_change)} element(s) focused with NO visible difference from unfocused "
                    f"(<= {NO_DIFF_THRESHOLD}px changed): {'; '.join(no_visible_change)}"
                )

            print(f"{label:12s} candidates={n_candidates:3d}  visited={len(visited_idx):3d}  issues={len(findings[label])}")

        browser.close()

    total_issues = sum(len(v) for v in findings.values())
    if total_issues:
        print(f"\n{total_issues} issue(s) found:")
        for label, issues in findings.items():
            for issue in issues:
                print(f"  [{label}] {issue}")
        return 1

    print("\nall pages: every focusable element reached by Tab, every stop shows a visible focus change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
