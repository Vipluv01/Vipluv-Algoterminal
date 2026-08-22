"""Covers the report-generating script itself, not just stylized_facts.py --
a script whose only job is to run and persist results has failed its job if
it silently produces a malformed or incomplete report, even if the
underlying analysis (already covered by test_stylized_facts.py) is correct.

Uses tiny steps/seeds, unlike the real report's 5 seeds x 8000 steps, purely
to keep this test fast -- it's checking structure and JSON-serializability,
not statistical significance.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_stylized_facts_report import build_report


def test_build_report_has_one_entry_per_seed_and_is_json_serializable():
    report = build_report(seeds=(0, 1), steps=200)

    assert len(report["per_seed"]) == 2
    assert [s["seed"] for s in report["per_seed"]] == [0, 1]
    # Round-trips through JSON cleanly -- catches e.g. a stray numpy scalar
    # that looks fine in Python but isn't actually a plain float/bool.
    json.loads(json.dumps(report))


def test_build_report_summary_pass_rates_match_per_seed_fractions():
    report = build_report(seeds=(0, 1, 2), steps=200)
    fat_tails_passes = sum(s["fat_tails"]["pass"] for s in report["per_seed"])
    assert report["summary"]["fat_tails_pass_rate"] == fat_tails_passes / 3
