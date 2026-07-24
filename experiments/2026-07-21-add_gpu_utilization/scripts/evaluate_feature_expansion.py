#!/usr/bin/env python3
"""Test 2 of the improvement roadmap: add previously-unused candidate-varying
features to the stage2 residual model.

`counterfactual_candidate_actuals.csv` already carries 9 feature_* columns
that vary by candidate within a request (confirmed via nunique() per
request) but were never added to evaluate_candidate_models.py's
EXPANDED_FEATURES: recent home-instance arrival/workload-share signals and
router-initial-state snapshots per candidate GPU. Everything else under
feature_* is constant within a request, so it cannot change candidate
ranking under a linear no-interaction model and is intentionally excluded
here.

This adds one new variant, stage2_full_features, on top of the existing
formula_only / pressure / stage2_base / stage2_expanded comparison, and
pulls the already-computed guardrail_nested_cv numbers in for a single
combined table. Same leave-one-request-out protocol as
evaluate_candidate_models.py: no request's own candidates are ever in its
own training fold.
"""

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "analysis" / "counterfactual_candidate_actuals.csv"
GUARDRAIL_DETAIL = ROOT / "analysis" / "guardrail_by_request.csv"
ANALYSIS = ROOT / "analysis"

BASE_FEATURES = [
    "predicted_total_ttft_ms",
    "predicted_scheduler_raw_ms",
    "predicted_scheduler_ms",
    "predicted_route_probability",
    "candidate_waiting_reqs",
    "candidate_running_reqs",
    "candidate_available_kv_bytes",
    "candidate_projected_active_kv_bytes",
    "candidate_capacity_pressure",
    "candidate_slot_pressure",
    "candidate_reserved_slots",
    "candidate_reserved_prefill_tokens",
    "request_migration_ms",
    "kv_migration_ms",
    "downlink_ms",
]

CANDIDATE_EXTRA_FEATURES = [
    "candidate_max_num_seqs",
    "candidate_required_kv_bytes",
    "candidate_free_npu_bytes",
    "candidate_kv_budget_bytes",
    "candidate_reserved_kv_bytes",
    "capacity_pressure_rank",
    "model_ttft_rank",
    "scheduler_clipped_ms",
    "rank_delta_model_minus_pressure",
]

EXPANDED_FEATURES = BASE_FEATURES + CANDIDATE_EXTRA_FEATURES

# New in this test: the 9 feature_* columns that actually vary by candidate
# within a request (verified via groupby(request_id)[feature_*].nunique()).
NEW_CANDIDATE_FEATURES = [
    "feature_home_arrivals_1s",
    "feature_home_arrivals_5s",
    "feature_home_workload_share",
    "feature_router_initial_waiting_reqs",
    "feature_router_initial_running_reqs",
    "feature_router_initial_available_kv_bytes",
    "feature_router_initial_projected_active_kv_bytes",
    "feature_router_initial_capacity_pressure",
    "feature_router_initial_slot_pressure",
]

FULL_FEATURES = EXPANDED_FEATURES + NEW_CANDIDATE_FEATURES


def fit_ridge(features, target, alpha=10.0):
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale < 1e-12] = 1.0
    normalized = (features - mean) / scale
    design = np.column_stack([np.ones(len(normalized)), normalized])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ target)
    return mean, scale, coefficients


def predict_ridge(features, mean, scale, coefficients):
    normalized = (features - mean) / scale
    return coefficients[0] + normalized @ coefficients[1:]


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


def fit_and_choose(train, group, feature_cols, target_col, baseline_col=None):
    train_x = train[feature_cols].astype(float).to_numpy()
    if baseline_col is None:
        train_y = train[target_col].to_numpy()
    else:
        train_y = (train[target_col] - train[baseline_col]).to_numpy()
    mean, scale, coeff = fit_ridge(train_x, train_y)
    eval_x = group[feature_cols].astype(float).to_numpy()
    predicted = predict_ridge(eval_x, mean, scale, coeff)
    if baseline_col is not None:
        predicted = group[baseline_col].to_numpy() + predicted
    return predicted


def main():
    data = pd.read_csv(INPUT)
    complete = data[data.counterfactual_complete].copy()
    fold_requests = sorted(complete.request_id.unique())
    print(f"Evaluation folds (fully labeled requests): {len(fold_requests)} -> {fold_requests}")

    detail_rows = []
    for held_out in fold_requests:
        train = data[data.request_id != held_out]
        group = complete[complete.request_id == held_out].sort_values("candidate_instance_id")

        chosen = group.loc[group.predicted_total_ttft_ms.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.predicted_total_ttft_ms, group)
        detail_rows.append(dict(variant="formula_only", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        chosen = group.loc[group.candidate_capacity_pressure.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.candidate_capacity_pressure, group)
        detail_rows.append(dict(variant="pressure", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        corrected = fit_and_choose(train, group, BASE_FEATURES, "actual_ttft_ms", "predicted_total_ttft_ms")
        chosen = group.loc[group.index[np.argmin(corrected)]]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(corrected, group)
        detail_rows.append(dict(variant="stage2_base", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        corrected = fit_and_choose(train, group, EXPANDED_FEATURES, "actual_ttft_ms", "predicted_total_ttft_ms")
        chosen = group.loc[group.index[np.argmin(corrected)]]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(corrected, group)
        detail_rows.append(dict(variant="stage2_expanded", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        # --- new in this test: item 2, add the 9 candidate-varying feature_* columns ---
        corrected = fit_and_choose(train, group, FULL_FEATURES, "actual_ttft_ms", "predicted_total_ttft_ms")
        chosen = group.loc[group.index[np.argmin(corrected)]]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(corrected, group)
        detail_rows.append(dict(variant="stage2_full_features", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

    detail = pd.DataFrame(detail_rows)
    detail.to_csv(ANALYSIS / "feature_expansion_by_request.csv", index=False)

    variants = ["formula_only", "pressure", "stage2_base", "stage2_expanded", "stage2_full_features"]
    summary = detail.groupby("variant").agg(
        folds=("request_id", "count"),
        top1_accuracy=("top1_correct", "mean"),
        mean_regret_ms=("regret_ms", "mean"),
        median_regret_ms=("regret_ms", "median"),
        p95_regret_ms=("regret_ms", lambda s: s.quantile(0.95)),
        mean_spearman=("spearman", "mean"),
    ).reindex(variants)

    # Fold in the already-computed guardrail_nested_cv numbers for a single
    # side-by-side table, if that analysis has been run.
    if GUARDRAIL_DETAIL.exists():
        guardrail_detail = pd.read_csv(GUARDRAIL_DETAIL)
        guardrail_rows = guardrail_detail[guardrail_detail.variant == "guardrail_nested_cv"]
        guardrail_summary = guardrail_rows.agg(
            folds=("request_id", "count"),
        )
        row = pd.DataFrame([{
            "folds": len(guardrail_rows),
            "top1_accuracy": guardrail_rows.top1_correct.mean(),
            "mean_regret_ms": guardrail_rows.regret_ms.mean(),
            "median_regret_ms": guardrail_rows.regret_ms.median(),
            "p95_regret_ms": guardrail_rows.regret_ms.quantile(0.95),
            "mean_spearman": guardrail_rows.spearman.mean(),
        }], index=["guardrail_nested_cv"])
        summary = pd.concat([summary, row])

    summary.to_csv(ANALYSIS / "feature_expansion_summary.csv")

    print()
    print(summary.to_string())
    print()
    print(f"NOTE: n={len(fold_requests)} fully labeled requests; treat as directional, not conclusive.")


if __name__ == "__main__":
    main()
