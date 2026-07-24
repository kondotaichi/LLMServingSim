#!/usr/bin/env python3
"""Item 3 production implementation check: recompute all 161 counterfactual
candidates with the new log1p-scheduler OfflineTtftFormula (trained by the
modified experiments/2026-07-16_ttft_component_regression/scripts/
fit_ttft_formula.py) and compare its candidate-ranking quality against the
currently-live (clip-based) formula, using the same actual-TTFT labels as
every other test in MODEL_ITERATION_HISTORY.md.

counterfactual_candidate_actuals.csv already stores every feature_* value
OfflineTtftFormula.predict() needs (it is the exact `_ttft_formula_features`
dict router.py builds, with a `feature_` prefix). router.py always evaluates
non-home candidates with policy='NEAREST_MIGRATE_KV'
(serving/core/router.py:1222-1224), so that's what's used here. No new
simulation needed -- this only re-evaluates the analytic formula.

Usage:
    python3 evaluate_log1p_scheduler_formula.py /path/to/trial/analysis/ttft_formula
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from serving.core.ttft_formula import NUMERIC_FEATURES, OfflineTtftFormula  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "analysis" / "counterfactual_candidate_actuals.csv"
ANALYSIS = ROOT / "analysis"


def score_selection(group, chosen_row):
    best_ttft = group.actual_ttft_ms.min()
    regret_ms = chosen_row.actual_ttft_ms - best_ttft
    top1_correct = int(np.isclose(chosen_row.actual_ttft_ms, best_ttft))
    return top1_correct, regret_ms


def spearman_of(predicted_values, group):
    ranked = group.assign(_pred=predicted_values)
    pred_rank = ranked._pred.rank(method="first")
    actual_rank = ranked.actual_ttft_ms.rank(method="first")
    return pred_rank.corr(actual_rank, method="spearman")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <trial artifact dir>")
    trial_dir = Path(sys.argv[1])
    new_formula = OfflineTtftFormula(artifact_dir=trial_dir)

    data = pd.read_csv(INPUT)
    complete = data[data.counterfactual_complete].copy()

    # router.py's ranking total is prediction['ttft_ms'] PLUS
    # request_migration_ms + kv_migration_ms + downlink_ms (router.py:1229-
    # 1236) -- those three are candidate-specific real costs unrelated to
    # the scheduler-component fix and must carry over unchanged, or this
    # comparison silently stops matching what the router actually ranks by.
    new_predictions = []
    for row in complete.itertuples(index=False):
        features = {
            name: getattr(row, f"feature_{name}") for name in NUMERIC_FEATURES
        }
        prediction = new_formula.predict(features, "NEAREST_MIGRATE_KV")
        new_predictions.append(
            prediction["ttft_ms"]
            + row.request_migration_ms
            + row.kv_migration_ms
            + row.downlink_ms
        )
    complete["predicted_total_ttft_ms_new"] = new_predictions

    fold_requests = sorted(complete.request_id.unique())

    def max_tie_fraction(col):
        sizes = complete.groupby(["request_id", col])[col].transform("count")
        return (sizes.groupby(complete.request_id).max() /
                complete.groupby("request_id").size()).mean()

    print(f"Mean per-request max-tie-fraction, old (clipped) formula: "
          f"{max_tie_fraction('predicted_total_ttft_ms'):.3f}")
    print(f"Mean per-request max-tie-fraction, new (log1p) formula:   "
          f"{max_tie_fraction('predicted_total_ttft_ms_new'):.3f}")
    print()

    detail_rows = []
    for rid in fold_requests:
        group = complete[complete.request_id == rid].sort_values("candidate_instance_id")

        chosen = group.loc[group.predicted_total_ttft_ms.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.predicted_total_ttft_ms, group)
        detail_rows.append(dict(variant="formula_old_clipped", request_id=rid,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        chosen = group.loc[group.predicted_total_ttft_ms_new.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.predicted_total_ttft_ms_new, group)
        detail_rows.append(dict(variant="formula_new_log1p", request_id=rid,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        chosen = group.loc[group.candidate_capacity_pressure.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.candidate_capacity_pressure, group)
        detail_rows.append(dict(variant="pressure", request_id=rid,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

    detail = pd.DataFrame(detail_rows)
    detail.to_csv(ANALYSIS / "log1p_scheduler_formula_by_request.csv", index=False)

    summary = detail.groupby("variant").agg(
        folds=("request_id", "count"),
        top1_accuracy=("top1_correct", "mean"),
        mean_regret_ms=("regret_ms", "mean"),
        median_regret_ms=("regret_ms", "median"),
        p95_regret_ms=("regret_ms", lambda s: s.quantile(0.95)),
        mean_spearman=("spearman", "mean"),
    ).reindex(["formula_old_clipped", "formula_new_log1p", "pressure"])
    summary.to_csv(ANALYSIS / "log1p_scheduler_formula_summary.csv")

    print(summary.to_string())
    print()
    print(f"NOTE: n={len(fold_requests)} fully labeled requests; treat as directional, not conclusive.")


if __name__ == "__main__":
    main()
