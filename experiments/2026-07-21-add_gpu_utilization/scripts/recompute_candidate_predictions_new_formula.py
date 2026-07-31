#!/usr/bin/env python3
"""Item 1 of the item-13 follow-up: rebuild counterfactual_candidate_actuals.csv
with predictions from the current production OfflineTtftFormula.

counterfactual_candidate_actuals.csv's predicted_* columns were captured at
simulation time, before item 13 (MODEL_ITERATION_HISTORY.md) replaced the
scheduler component's ms-space clipped regression with a log1p/expm1 fit and
overwrote the production artifact
(experiments/2026-07-16_ttft_component_regression/analysis/ttft_formula/).
The guardrail and stage2 evaluations (evaluate_guardrail.py,
evaluate_feature_expansion.py) still read those stale predictions. This
script re-evaluates OfflineTtftFormula() (default artifact_dir = current
production artifact) for all 161 candidates and writes a refreshed CSV with
predicted_* and the derived rank columns updated, so downstream evaluators
can be pointed at it.

Usage:
    python3 recompute_candidate_predictions_new_formula.py
"""

from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from serving.core.ttft_formula import NUMERIC_FEATURES, OfflineTtftFormula  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "analysis" / "counterfactual_candidate_actuals.csv"
OUTPUT = ROOT / "analysis" / "counterfactual_candidate_actuals_new_formula.csv"


def main():
    formula = OfflineTtftFormula()  # default artifact_dir: current production
    print(f"Using artifact_dir: {formula.artifact_dir}")
    print(f"scheduler_log_bounds: {formula.scheduler_log_bounds}")

    data = pd.read_csv(INPUT)

    predicted_total_ttft_ms = []
    predicted_route_probability = []
    predicted_route_positive_ms = []
    predicted_route_upper_ms = []
    predicted_route_ms = []
    predicted_scheduler_raw_ms = []
    predicted_scheduler_ms = []
    predicted_compute_ms = []
    predicted_formula_ttft_ms = []

    for row in data.itertuples(index=False):
        features = {
            name: getattr(row, f"feature_{name}") for name in NUMERIC_FEATURES
        }
        prediction = formula.predict(features, "NEAREST_MIGRATE_KV")
        total_ttft_ms = (
            prediction["ttft_ms"]
            + row.request_migration_ms
            + row.kv_migration_ms
            + row.downlink_ms
        )
        predicted_total_ttft_ms.append(total_ttft_ms)
        predicted_route_probability.append(prediction["route_probability"])
        predicted_route_positive_ms.append(prediction["route_positive_ms"])
        predicted_route_upper_ms.append(prediction["route_upper_ms"])
        predicted_route_ms.append(prediction["route_ms"])
        predicted_scheduler_raw_ms.append(prediction["scheduler_raw_ms"])
        predicted_scheduler_ms.append(prediction["scheduler_ms"])
        predicted_compute_ms.append(prediction["compute_ms"])
        predicted_formula_ttft_ms.append(prediction["ttft_ms"])

    data["predicted_total_ttft_ms"] = predicted_total_ttft_ms
    data["predicted_route_probability"] = predicted_route_probability
    data["predicted_route_positive_ms"] = predicted_route_positive_ms
    data["predicted_route_upper_ms"] = predicted_route_upper_ms
    data["predicted_route_ms"] = predicted_route_ms
    data["predicted_scheduler_raw_ms"] = predicted_scheduler_raw_ms
    data["predicted_scheduler_ms"] = predicted_scheduler_ms
    data["predicted_compute_ms"] = predicted_compute_ms
    data["predicted_formula_ttft_ms"] = predicted_formula_ttft_ms

    # Re-derive rank columns: capacity_pressure_rank is unaffected (it never
    # depended on the formula), but model_ttft_rank and the delta against it
    # must be recomputed against the refreshed predicted_total_ttft_ms, using
    # the same (total, target_id) tie-break as router.py:1297-1301.
    def rerank(group):
        order = group.sort_values(
            ["predicted_total_ttft_ms", "candidate_instance_id"]
        )
        ranks = pd.Series(
            range(1, len(order) + 1), index=order.index, name="model_ttft_rank"
        )
        return ranks

    new_ranks = data.groupby("request_id", group_keys=False).apply(rerank)
    data["model_ttft_rank"] = new_ranks
    data["scheduler_clipped_ms"] = (
        data.predicted_scheduler_ms - data.predicted_scheduler_raw_ms
    )
    data["rank_delta_model_minus_pressure"] = (
        data.model_ttft_rank - data.capacity_pressure_rank
    )

    data.to_csv(OUTPUT, index=False)
    print(f"Wrote {OUTPUT} ({len(data)} rows)")

    def max_tie_fraction(frame, col):
        sizes = frame.groupby(["request_id", col])[col].transform("count")
        return (sizes.groupby(frame.request_id).max() /
                frame.groupby("request_id").size()).mean()

    print(f"Mean per-request max-tie-fraction (new formula): "
          f"{max_tie_fraction(data, 'predicted_total_ttft_ms'):.3f}")


if __name__ == "__main__":
    main()
