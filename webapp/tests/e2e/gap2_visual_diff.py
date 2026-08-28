#!/usr/bin/env python3
"""Reusable visual regression check -- the frontend equivalent of
bench/report.go's TestReadmeMatchesMeasuredResults: a future phase runs
gap2_visual_baseline.py again into a NEW directory and diffs it against
the frozen tests/e2e/screenshots/baseline/ committed here, so a change
that silently restyles a page shows up as a nonzero pixel count instead
of just "looking a bit different" in review.

Usage:
    # 1. Re-capture the current state into a fresh directory:
    .venv/bin/python tests/e2e/gap2_visual_baseline.py  # edit SCREENSHOT_DIR first, or:
    .venv/bin/python tests/e2e/gap2_visual_diff.py --recapture-to /tmp/current

    # 2. Diff against the committed baseline:
    .venv/bin/python tests/e2e/gap2_visual_diff.py --against /tmp/current

Exits nonzero if any page differs by more than --threshold changed pixels
(default 0 -- ANY pixel difference is reported; a threshold only silences
the report, it still lists every page's actual count).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops

BASELINE_DIR = Path(__file__).resolve().parent / "screenshots" / "baseline"
DIFF_DIR = Path(__file__).resolve().parent / "screenshots" / "diff"


def changed_pixel_count(img_a: Image.Image, img_b: Image.Image) -> tuple[int, Image.Image | None]:
    if img_a.size != img_b.size:
        # A size change (e.g. a page growing taller) is itself the finding
        # -- there's no pixel-for-pixel diff to compute, so report it as
        # "everything changed" rather than silently cropping/padding to fit.
        return max(img_a.size[0] * img_a.size[1], img_b.size[0] * img_b.size[1]), None
    diff = ImageChops.difference(img_a.convert("RGB"), img_b.convert("RGB"))
    bbox = diff.getbbox()
    if bbox is None:
        return 0, None
    # Count pixels with ANY visible difference, not just the bbox area --
    # a bbox can contain plenty of identical pixels around a small change.
    # convert("L") luma-weights the 3 channel diffs into one 0-255 value;
    # histogram bucket 0 is "no visible difference," everything above it
    # is a changed pixel.
    hist = diff.convert("L").histogram()
    changed = sum(hist[1:])
    return changed, diff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--against", type=Path, required=True, help="directory of newly captured screenshots to diff against the baseline")
    parser.add_argument("--threshold", type=int, default=0, help="changed-pixel count above which a page is flagged (default: 0, any difference)")
    args = parser.parse_args()

    if not BASELINE_DIR.exists():
        print(f"no baseline at {BASELINE_DIR} -- run gap2_visual_baseline.py first", file=sys.stderr)
        return 2
    if not args.against.exists():
        print(f"comparison directory {args.against} does not exist", file=sys.stderr)
        return 2

    DIFF_DIR.mkdir(parents=True, exist_ok=True)
    baseline_files = sorted(BASELINE_DIR.glob("*.png"))
    flagged: list[tuple[str, int]] = []
    missing: list[str] = []

    for baseline_path in baseline_files:
        candidate_path = args.against / baseline_path.name
        if not candidate_path.exists():
            missing.append(baseline_path.name)
            continue
        img_a = Image.open(baseline_path)
        img_b = Image.open(candidate_path)
        changed, diff_img = changed_pixel_count(img_a, img_b)
        if changed > args.threshold:
            flagged.append((baseline_path.name, changed))
            if diff_img is not None:
                diff_img.save(DIFF_DIR / baseline_path.name)

    print(f"compared {len(baseline_files) - len(missing)}/{len(baseline_files)} pages against {args.against}")
    if missing:
        print(f"\n{len(missing)} page(s) missing from comparison set:")
        for m in missing:
            print(f"  - {m}")

    if flagged:
        print(f"\n{len(flagged)} page(s) changed beyond threshold ({args.threshold} px):")
        for name, changed in sorted(flagged, key=lambda x: -x[1]):
            print(f"  - {name}: {changed} changed pixels (diff saved to {DIFF_DIR / name})")
        return 1

    print("\nno page exceeded the diff threshold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
