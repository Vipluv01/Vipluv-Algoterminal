#!/usr/bin/env python3
"""Gap 1: actually EXECUTE format.test.js through a real browser, rather
than trusting a hand-trace of expected values. Loads format-tests.html
through the running nocache_server.py (NOT a raw file:// open -- ES module
imports need an HTTP origin), captures every console message, and reports
pass/fail per assertion from format.test.js's own printed summary.

Usage: .venv/bin/python tests/e2e/gap1_format_tests.py [base_url]
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173"


def main() -> int:
    console_lines: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda msg: console_lines.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        page.goto(f"{BASE_URL}/format-tests.html", wait_until="networkidle")
        page.wait_for_selector("#results", timeout=5000)

        results_text = page.inner_text("#results")
        browser.close()

    print("=== console output ===")
    for line in console_lines:
        print(line)
    if page_errors:
        print("=== page errors ===")
        for e in page_errors:
            print(e)

    print("\n=== #results DOM content ===")
    print(results_text)

    failed = "FAIL" in results_text
    if page_errors:
        print("\nFAILED: uncaught page error(s) during module load/execution.")
        return 1
    if failed:
        print("\nFAILED: one or more format.js assertions failed.")
        return 1
    print("\nPASSED: all format.js assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
