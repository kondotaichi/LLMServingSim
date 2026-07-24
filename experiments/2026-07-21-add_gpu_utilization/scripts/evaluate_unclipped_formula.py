#!/usr/bin/env python3
"""Test 3 of the improvement roadmap: does the scheduler's hard 0-ms clip
explain the formula's degenerate ties?

Root cause found while starting item 3: `OfflineTtftFormula.predict()`
(serving/core/ttft_formula.py) computes `compute_ms` from `input_tokens` and
`home_cached_prefix_tokens` only -- both request-level, not candidate-level.
So the *only* candidate-varying signal in `ttft_ms = route_ms + scheduler_ms
+ compute_ms` is `route_ms` and `scheduler_ms`. `route_ms` underflows to
~0 whenever the logistic route-probability is near 0 (common), and
`scheduler_ms = max(0.0, scheduler_raw_ms)` clips ~47.8% of candidates to
exactly 0. When both vanish simultaneously, `predicted_total_ttft_ms`
collapses to the *exact same* request-level constant across every affected
candidate -- the model provides literally zero ranking signal for that
subset. Confirmed directly: request 49 has 7 of 9 candidates tied at
727.791547 ms, spanning an actual TTFT range of 698.9-967.0 ms (268 ms) that
the formula cannot see. 7 of 18 fully-labeled requests show some tie.

This script tests the simplest fix: stop clipping the scheduler term for
*ranking* purposes (use the raw, possibly-negative scheduler_raw_ms instead
of the clipped scheduler_ms). This is a zero-parameter, deterministic
recomputation from columns already in counterfactual_candidate_actuals.csv
(predicted_route_ms + predicted_scheduler_raw_ms + predicted_compute_ms) --
no new simulation or fitting needed. It also reports how a guardrail layer
on top of the unclipped formula compares to item 9's guardrail (which was
built on the clipped formula).
"""

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "analysis" / "counterfactual_candidate_actuals.csv"
ANALYSIS = ROOT / "analysis"

LAMBDA_GRID = [
    0, 10, 25, 50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600,
    51200, 76800, 102400, 153600, 204800, 307200, 512000, 1024000,
]


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


def choose_by_column(group, col):
    return group.loc[group[col].idxmin()]


def choose_with_lambda(group, score_col, lam):
    score = group[score_col] + lam * group.candidate_capacity_pressure
    return group.loc[score.idxmin()]


def evaluate_deterministic(requests_by_id, fold_requests, variant, score_col):
    rows = []
    for rid in fold_requests:
        group = requests_by_id[rid]
        chosen = choose_by_column(group, score_col)
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group[score_col], group)
        rows.append(dict(variant=variant, request_id=rid,
                          chosen_gpu=int(chosen.candidate_instance_id),
                          top1_correct=top1, regret_ms=regret, spearman=spearman))
    return rows


def evaluate_guardrail(requests_by_id, fold_requests, variant, score_col):
    rows = []
    for held_out in fold_requests:
        train_ids = [rid for rid in fold_requests if rid != held_out]
        inner_scores = {}
        for lam in LAMBDA_GRID:
            regrets = []
            for inner_held_out in train_ids:
                group = requests_by_id[inner_held_out]
                chosen = choose_with_lambda(group, score_col, lam)
                _, regret = score_selection(group, chosen)
                regrets.append(regret)
            inner_scores[lam] = float(np.mean(regrets))
        best_lambda = min(inner_scores, key=lambda lam: (inner_scores[lam], lam))

        group = requests_by_id[held_out]
        chosen = choose_with_lambda(group, score_col, best_lambda)
        top1, regret = score_selection(group, chosen)
        score = group[score_col] + best_lambda * group.candidate_capacity_pressure
        spearman = spearman_of(score, group)
        rows.append(dict(variant=variant, request_id=held_out, lambda_used=best_lambda,
                          chosen_gpu=int(chosen.candidate_instance_id),
                          top1_correct=top1, regret_ms=regret, spearman=spearman))
    return rows


def main():
    data = pd.read_csv(INPUT)
    complete = data[data.counterfactual_complete].copy()
    complete["predicted_total_unclipped_ms"] = (
        complete.predicted_route_ms
        + complete.predicted_scheduler_raw_ms
        + complete.predicted_compute_ms
    )
    fold_requests = sorted(complete.request_id.unique())
    requests_by_id = {
        rid: complete[complete.request_id == rid].sort_values("candidate_instance_id")
        for rid in fold_requests
    }
    print(f"Evaluation folds (fully labeled requests): {len(fold_requests)} -> {fold_requests}")

    # Degeneracy check: how many candidates share their request's most
    # common predicted_total_ttft_ms value, clipped vs unclipped.
    def max_tie_fraction(col):
        sizes = complete.groupby(["request_id", col])[col].transform("count")
        return (sizes.groupby(complete.request_id).max() /
                complete.groupby("request_id").size()).mean()

    print(f"Mean per-request max-tie-fraction, clipped formula:   "
          f"{max_tie_fraction('predicted_total_ttft_ms'):.3f}")
    print(f"Mean per-request max-tie-fraction, unclipped formula: "
          f"{max_tie_fraction('predicted_total_unclipped_ms'):.3f}")
    print()

    detail_rows = []
    detail_rows += evaluate_deterministic(
        requests_by_id, fold_requests, "formula_only_clipped", "predicted_total_ttft_ms")
    detail_rows += evaluate_deterministic(
        requests_by_id, fold_requests, "formula_unclipped", "predicted_total_unclipped_ms")
    detail_rows += evaluate_deterministic(
        requests_by_id, fold_requests, "pressure", "candidate_capacity_pressure")
    detail_rows += evaluate_guardrail(
        requests_by_id, fold_requests, "guardrail_clipped", "predicted_total_ttft_ms")
    detail_rows += evaluate_guardrail(
        requests_by_id, fold_requests, "guardrail_unclipped", "predicted_total_unclipped_ms")

    detail = pd.DataFrame(detail_rows)
    detail.to_csv(ANALYSIS / "unclipped_formula_by_request.csv", index=False)

    variants = [
        "formula_only_clipped", "formula_unclipped", "pressure",
        "guardrail_clipped", "guardrail_unclipped",
    ]
    summary = detail.groupby("variant").agg(
        folds=("request_id", "count"),
        top1_accuracy=("top1_correct", "mean"),
        mean_regret_ms=("regret_ms", "mean"),
        median_regret_ms=("regret_ms", "median"),
        p95_regret_ms=("regret_ms", lambda s: s.quantile(0.95)),
        mean_spearman=("spearman", "mean"),
    ).reindex(variants)
    summary.to_csv(ANALYSIS / "unclipped_formula_summary.csv")

    print(summary.to_string())
    print()
    print(f"NOTE: n={len(fold_requests)} fully labeled requests; treat as directional, not conclusive.")


if __name__ == "__main__":
    main()
