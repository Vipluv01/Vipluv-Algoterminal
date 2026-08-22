"""Regenerates results/stylized_facts.json: the persisted, citable evidence
that "price emerges from order flow" (see stylized_facts.py's module
docstring) rather than being asserted in prose.

Runs the project's default, validated single-maker pipeline
(bourse_sim.simulate.run_simulation with no overrides) across multiple
seeds, so the report reflects genuine run-to-run variation rather than one
lucky seed -- exactly the discipline results/latency.json already applies
on the Go side (go test ./bench/... -run TestLatencyReport).

The honest result here is mixed, and the report says so rather than hiding
it: fat tails and weak return autocorrelation pass on every seed; volatility
clustering has the wrong sign on every seed. That is a real, understood,
open finding -- see ../KNOWN_ISSUES.md for the investigation that ruled out
several causes and landed on a best-supported hypothesis. This script does
not chase that finding further; it just makes the current state of it
reproducible and checkable, not something you have to re-run ad hoc to
verify.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bourse_sim"))

from simulate import run_simulation
from stylized_facts import analyze

SEEDS = (0, 1, 2, 3, 4)
STEPS = 8000


def build_report(seeds: tuple[int, ...] = SEEDS, steps: int = STEPS) -> dict:
    per_seed = []
    for seed in seeds:
        res = run_simulation(steps=steps, seed=seed)
        report = analyze(res.mid_price_path)
        per_seed.append({
            "seed": seed,
            "n_returns": report.n_returns,
            "fat_tails": {
                "pass": report.has_fat_tails,
                "excess_kurtosis": report.excess_kurtosis,
                "pvalue": report.kurtosis_pvalue,
            },
            "weak_return_autocorrelation": {
                "pass": report.returns_are_weakly_autocorrelated,
                "lag1": report.return_autocorr_lag1,
                "pvalue": report.return_autocorr_pvalue,
            },
            "volatility_clustering": {
                "pass": report.has_volatility_clustering,
                "abs_return_lag1_autocorr": report.abs_return_autocorr_lag1,
                "pvalue": report.abs_return_autocorr_pvalue,
            },
        })

    n = len(per_seed)
    fat_tails_pass_rate = sum(s["fat_tails"]["pass"] for s in per_seed) / n
    weak_autocorr_pass_rate = sum(s["weak_return_autocorrelation"]["pass"] for s in per_seed) / n
    vol_clustering_pass_rate = sum(s["volatility_clustering"]["pass"] for s in per_seed) / n

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {"steps": steps, "seeds": list(seeds), "maker": "single default MarketMaker"},
        "per_seed": per_seed,
        "summary": {
            "fat_tails_pass_rate": fat_tails_pass_rate,
            "weak_return_autocorrelation_pass_rate": weak_autocorr_pass_rate,
            "volatility_clustering_pass_rate": vol_clustering_pass_rate,
        },
        "note": (
            "Volatility clustering fails by design here, not by bug -- it is a real, "
            "investigated, open finding (wrong-signed |return| autocorrelation), not "
            "unexamined noise. See sim/KNOWN_ISSUES.md for the ruled-out hypotheses and "
            "the current best-supported explanation before assuming this is a defect."
        ),
    }


def main() -> None:
    out = build_report()
    out_path = Path(__file__).resolve().parents[2] / "results" / "stylized_facts.json"
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {out_path}")
    for s in out["per_seed"]:
        print(f"seed={s['seed']}: fat_tails={s['fat_tails']['pass']} "
              f"weak_autocorr={s['weak_return_autocorrelation']['pass']} "
              f"vol_clustering={s['volatility_clustering']['pass']}")


if __name__ == "__main__":
    main()
