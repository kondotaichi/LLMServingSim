#!/usr/bin/env python3
"""Leave-one-request-out comparison of candidate-selection model variants.

Items covered (see experiments/2026-07-21-add_gpu_utilization/README.md):
  6. Candidate-specific feature expansion
  7. Two-stage model (analytic formula + residual correction)
  8. Absolute-TTFT residual model versus a regret-targeted ranking model

Only requests with a fully labeled candidate set (counterfactual_complete)
are used as evaluation folds. Rows from partially labeled requests are still
used as auxiliary training signal (never as evaluation targets), since they
carry real actual_ttft_ms labels even though their request's ranking cannot
be scored yet.

Caveat: as of this run only a handful of requests are fully labeled, so the
per-fold counts below are small and every metric here is a rough, noisy
signal, not a validated result. Re-run once more counterfactual simulations
land.
"""

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "analysis" / "counterfactual_candidate_actuals.csv"
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

# Item 6: candidate-specific features not used by the original residual model.
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
    """Return (top1_correct, regret_ms, spearman) for one request's fold."""
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
    data = pd.read_csv(INPUT)
    complete = data[data.counterfactual_complete].copy()
    fold_requests = sorted(complete.request_id.unique())
    print(f"Evaluation folds (fully labeled requests): {len(fold_requests)} -> {fold_requests}")
    if len(fold_requests) < 2:
        raise ValueError("Need at least 2 fully labeled requests for leave-one-out evaluation.")

    variants = ["formula_only", "pressure", "stage2_base", "stage2_expanded", "rank_model"]
    detail_rows = []

    for held_out in fold_requests:
        train = data[data.request_id != held_out]
        group = complete[complete.request_id == held_out].sort_values("candidate_instance_id")

        # --- formula_only: current production analytic formula, no fitting ---
        chosen = group.loc[group.predicted_total_ttft_ms.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.predicted_total_ttft_ms, group)
        detail_rows.append(dict(variant="formula_only", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        # --- pressure: capacity-pressure heuristic, no fitting ---
        chosen = group.loc[group.candidate_capacity_pressure.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.candidate_capacity_pressure, group)
        detail_rows.append(dict(variant="pressure", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        # --- stage2_base: item 7 with the original 15-feature residual model ---
        train_x = train[BASE_FEATURES].astype(float).to_numpy()
        train_y = (train.actual_ttft_ms - train.predicted_total_ttft_ms).to_numpy()
        mean, scale, coeff = fit_ridge(train_x, train_y)
        eval_x = group[BASE_FEATURES].astype(float).to_numpy()
        corrected = group.predicted_total_ttft_ms.to_numpy() + predict_ridge(eval_x, mean, scale, coeff)
        chosen = group.loc[group.index[np.argmin(corrected)]]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(corrected, group)
        detail_rows.append(dict(variant="stage2_base", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        # --- stage2_expanded: item 6 + 7, candidate-specific features added ---
        train_x = train[EXPANDED_FEATURES].astype(float).to_numpy()
        mean, scale, coeff = fit_ridge(train_x, train_y)
        eval_x = group[EXPANDED_FEATURES].astype(float).to_numpy()
        corrected = group.predicted_total_ttft_ms.to_numpy() + predict_ridge(eval_x, mean, scale, coeff)
        chosen = group.loc[group.index[np.argmin(corrected)]]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(corrected, group)
        detail_rows.append(dict(variant="stage2_expanded", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        # --- rank_model: item 8, target actual_regret_ms directly instead of absolute TTFT ---
        train_x = train[EXPANDED_FEATURES].astype(float).to_numpy()
        train_regret_y = train.actual_regret_ms.to_numpy()
        mean, scale, coeff = fit_ridge(train_x, train_regret_y)
        eval_x = group[EXPANDED_FEATURES].astype(float).to_numpy()
        predicted_regret = predict_ridge(eval_x, mean, scale, coeff)
        chosen = group.loc[group.index[np.argmin(predicted_regret)]]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(predicted_regret, group)
        detail_rows.append(dict(variant="rank_model", request_id=held_out,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

    detail = pd.DataFrame(detail_rows)
    detail.to_csv(ANALYSIS / "model_variant_comparison_by_request.csv", index=False)

    summary = detail.groupby("variant").agg(
        folds=("request_id", "count"),
        top1_accuracy=("top1_correct", "mean"),
        mean_regret_ms=("regret_ms", "mean"),
        p95_regret_ms=("regret_ms", lambda s: s.quantile(0.95)),
        mean_spearman=("spearman", "mean"),
    ).reindex(variants)
    summary.to_csv(ANALYSIS / "model_variant_comparison_summary.csv")

    print()
    print(summary.to_string())
    print()
    print(f"NOTE: only {len(fold_requests)} fully labeled requests available; treat as directional, not conclusive.")


if __name__ == "__main__":
    main()
