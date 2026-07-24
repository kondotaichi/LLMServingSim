#!/usr/bin/env python3
"""Create one counterfactual task per non-selected admissible candidate."""

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
POLICY = "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE"
INPUT = ROOT / "results" / POLICY / "routing_candidates.csv"
OUTPUT = ROOT / "configs" / "counterfactual_manifest.csv"


def main():
    frame = pd.read_csv(INPUT)
    manifest = frame[[
        "request_id", "candidate_instance_id", "selected_by_model",
        "capacity_pressure_rank", "model_ttft_rank",
    ]].copy()
    manifest["case_id"] = manifest.apply(
        lambda row: (
            f"request{int(row.request_id)}_target{int(row.candidate_instance_id)}"
        ),
        axis=1,
    )
    manifest["needs_simulation"] = 1 - manifest.selected_by_model.astype(int)
    manifest = manifest[[
        "case_id", "request_id", "candidate_instance_id",
        "selected_by_model", "needs_simulation",
        "capacity_pressure_rank", "model_ttft_rank",
    ]].sort_values(["request_id", "candidate_instance_id"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(OUTPUT, index=False)
    print(f"Candidates: {len(manifest)}")
    print(f"Baseline reuse: {int(manifest.selected_by_model.sum())}")
    print(f"Counterfactual simulations: {int(manifest.needs_simulation.sum())}")
    print(f"Manifest: {OUTPUT}")


if __name__ == "__main__":
    main()
