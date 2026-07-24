#!/usr/bin/env python3
"""Test 1 of the improvement roadmap: capacity-risk guardrail scoring.

score(candidate) = predicted_total_ttft_ms + lambda * candidate_capacity_pressure

This is a fixed, no-fitting combination of the current analytic formula and
the capacity-pressure heuristic that already beats it. Lambda controls how
much weight the guardrail term gets relative to the formula's TTFT estimate
(lambda=0 reduces to formula_only; lambda -> large approaches pure pressure
ranking since candidate_capacity_pressure has much smaller absolute range
than predicted_total_ttft_ms).

Lambda is selected per outer fold using an INNER leave-one-request-out search
over the remaining 17 requests only (never touching the held-out request),
so the reported outer metrics are not leaking the evaluation label into
model selection.
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


def choose_with_lambda(group, lam):
    score = group.predicted_total_ttft_ms + lam * group.candidate_capacity_pressure
    return group.loc[score.idxmin()]


def mean_regret_for_lambda(requests_by_id, request_ids, lam):
    regrets = []
    for rid in request_ids:
        group = requests_by_id[rid]
        chosen = choose_with_lambda(group, lam)
        _, regret = score_selection(group, chosen)
        regrets.append(regret)
    return float(np.mean(regrets))


def main():
    data = pd.read_csv(INPUT)
    complete = data[data.counterfactual_complete].copy()
    fold_requests = sorted(complete.request_id.unique())
    requests_by_id = {
        rid: complete[complete.request_id == rid].sort_values("candidate_instance_id")
        for rid in fold_requests
    }
    print(f"Evaluation folds (fully labeled requests): {len(fold_requests)} -> {fold_requests}")

    detail_rows = []
    chosen_lambdas = []

    for held_out in fold_requests:
        train_ids = [rid for rid in fold_requests if rid != held_out]

        # Inner leave-one-out over the training requests only: pick the
        # lambda that minimizes mean regret when each train request is in
        # turn held out from the other train requests.
        inner_scores = {}
        for lam in LAMBDA_GRID:
            per_inner_regret = []
            for inner_held_out in train_ids:
                # lambda itself does not need fitting data beyond the grid
                # value, so the "inner training set" only matters in that it
                # excludes the outer held-out request; reuse train_ids directly.
                group = requests_by_id[inner_held_out]
                chosen = choose_with_lambda(group, lam)
                _, regret = score_selection(group, chosen)
                per_inner_regret.append(regret)
            inner_scores[lam] = float(np.mean(per_inner_regret))
        best_lambda = min(inner_scores, key=lambda lam: (inner_scores[lam], lam))
        chosen_lambdas.append(best_lambda)

        group = requests_by_id[held_out]
        chosen = choose_with_lambda(group, best_lambda)
        top1, regret = score_selection(group, chosen)
        score = group.predicted_total_ttft_ms + best_lambda * group.candidate_capacity_pressure
        spearman = spearman_of(score, group)
        detail_rows.append(dict(
            variant="guardrail_nested_cv", request_id=held_out, lambda_used=best_lambda,
            chosen_gpu=int(chosen.candidate_instance_id),
            top1_correct=top1, regret_ms=regret, spearman=spearman,
        ))

        # Reference baselines on the same fold, for direct comparison.
        chosen_f = group.loc[group.predicted_total_ttft_ms.idxmin()]
        top1_f, regret_f = score_selection(group, chosen_f)
        spearman_f = spearman_of(group.predicted_total_ttft_ms, group)
        detail_rows.append(dict(
            variant="formula_only", request_id=held_out, lambda_used=np.nan,
            chosen_gpu=int(chosen_f.candidate_instance_id),
            top1_correct=top1_f, regret_ms=regret_f, spearman=spearman_f,
        ))

        chosen_p = group.loc[group.candidate_capacity_pressure.idxmin()]
        top1_p, regret_p = score_selection(group, chosen_p)
        spearman_p = spearman_of(group.candidate_capacity_pressure, group)
        detail_rows.append(dict(
            variant="pressure", request_id=held_out, lambda_used=np.nan,
            chosen_gpu=int(chosen_p.candidate_instance_id),
            top1_correct=top1_p, regret_ms=regret_p, spearman=spearman_p,
        ))

    detail = pd.DataFrame(detail_rows)
    detail.to_csv(ANALYSIS / "guardrail_by_request.csv", index=False)

    summary = detail.groupby("variant").agg(
        folds=("request_id", "count"),
        top1_accuracy=("top1_correct", "mean"),
        mean_regret_ms=("regret_ms", "mean"),
        median_regret_ms=("regret_ms", "median"),
        p95_regret_ms=("regret_ms", lambda s: s.quantile(0.95)),
        mean_spearman=("spearman", "mean"),
    ).reindex(["formula_only", "pressure", "guardrail_nested_cv"])
    summary.to_csv(ANALYSIS / "guardrail_summary.csv")

    print()
    print(summary.to_string())
    print()
    print("Per-fold lambda chosen by inner CV:")
    lam_detail = detail[detail.variant == "guardrail_nested_cv"][["request_id", "lambda_used"]]
    print(lam_detail.to_string(index=False))
    print()
    print(f"Lambda distribution: {pd.Series(chosen_lambdas).value_counts().sort_index().to_dict()}")


if __name__ == "__main__":
    main()
