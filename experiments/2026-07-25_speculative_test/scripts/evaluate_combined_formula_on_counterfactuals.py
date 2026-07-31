#!/usr/bin/env python3
"""Does today's regression-accuracy improvement (learning_rate=0.15/n=70 for
route_tail + capacity_overload/slot_overload hinge features + running/waiting
compute features -- see
experiments/2026-07-16_ttft_component_regression/reports/route_tail_hyperparameter_tuning.md)
actually improve the real decision this formula is used for: picking which
GPU to redirect a request to?

This is a DIFFERENT question from the scenario-held-out regression metrics
(TTFT MAE/R2, n=13,500) already validated. Those measure average point-
prediction accuracy on the training distribution. This script measures
redirect-*target-selection* quality on real counterfactual-labeled data
(n=18 redirect decisions, 161 candidates total, from
experiments/2026-07-21-add_gpu_utilization/) -- i.e. "does the model
correctly identify which of several already-redirect-eligible GPUs is
fastest," using the exact same methodology as
experiments/2026-07-21-add_gpu_utilization/scripts/evaluate_log1p_scheduler_formula.py.

IMPORTANT SCOPE NOTE: this evaluates redirect-*target* selection only. It
does NOT evaluate the home-vs-redirect decision itself (whether to
speculatively migrate KV at all), because none of these 18 baseline
decisions had a "stay home" candidate in the admissible set -- home was
*not* capacity-admissible for any of them, which is why they redirected in
the first place. Testing genuine home-vs-candidate accuracy would need a new
counterfactual mode that forces staying home despite predicted inadmissibility,
which does not exist yet (see report.md's "残っている限界" section).

The combined-improvement model is trained on the full
experiments/2026-07-16_ttft_component_regression Phase 1 dataset (13,500
rows, completely disjoint from these 18 redirect decisions -- no leakage),
then applied to the 161 candidates' feature_* columns.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression, LogisticRegression, Ridge
from sklearn.preprocessing import OneHotEncoder, StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "experiments/2026-07-16_ttft_component_regression/scripts"))
import fit_ttft_formula as base  # noqa: E402

sys.path.insert(0, str(REPO_ROOT))
from serving.core.ttft_formula import NUMERIC_FEATURES as PROD_NUMERIC_FEATURES  # noqa: E402
from serving.core.ttft_formula import OfflineTtftFormula  # noqa: E402


COUNTERFACTUAL_CSV = (
    REPO_ROOT / "experiments/2026-07-21-add_gpu_utilization/analysis/"
    "counterfactual_candidate_actuals_new_formula.csv"
)
THIS_DIR = Path(__file__).resolve().parents[1]
ANALYSIS = THIS_DIR / "analysis"

ROUTE_TAIL_HYPERPARAMS = dict(
    loss="absolute_error", n_estimators=70, learning_rate=0.15,
    max_depth=2, min_samples_leaf=20,
)
EXTRA_NUMERIC = ["capacity_overload", "slot_overload"]
NUMERIC_FEATURES = base.NUMERIC_FEATURES + EXTRA_NUMERIC
FEATURES = NUMERIC_FEATURES + base.CATEGORICAL_FEATURES
COMPUTE_FEATURES = base.COMPUTE_FEATURES + [
    "router_initial_running_reqs", "router_initial_waiting_reqs",
]


def add_derived_features(dataset):
    dataset = dataset.copy()
    dataset["capacity_overload"] = np.maximum(
        0.0, dataset.router_initial_capacity_pressure - 1.0
    )
    dataset["slot_overload"] = np.maximum(
        0.0, dataset.router_initial_slot_pressure - 1.0
    )
    return dataset


def fit_combined_models():
    """Train the combined-improvement models on the FULL Phase 1 training
    set (all 13,500 rows, no held-out split -- this mirrors what
    fit_ttft_formula.py's main() does for the production artifact)."""
    dataset = add_derived_features(base.prepare_dataset())

    preprocessor = ColumnTransformer([
        ("numeric", StandardScaler(), NUMERIC_FEATURES),
        ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
         base.CATEGORICAL_FEATURES),
    ])
    transformed = preprocessor.fit_transform(dataset[FEATURES])

    event_model = LogisticRegression(
        C=0.1, class_weight="balanced", max_iter=2000, random_state=base.RANDOM_STATE
    ).fit(transformed, dataset.route_positive)

    positive = dataset.route_positive.to_numpy(dtype=bool)
    positive_log_route = np.log1p(dataset.loc[positive, "router_queue_ms"])
    tail_model = GradientBoostingRegressor(
        random_state=base.RANDOM_STATE, **ROUTE_TAIL_HYPERPARAMS,
    ).fit(transformed[positive], positive_log_route)
    route_log_bounds = (float(positive_log_route.min()), float(positive_log_route.max()))

    log_scheduler_target = np.log1p(dataset.scheduler_queue_ms)
    scheduler_model = Ridge(alpha=1000.0).fit(transformed, log_scheduler_target)
    scheduler_log_bounds = (float(log_scheduler_target.min()), float(log_scheduler_target.max()))

    compute_model = LinearRegression().fit(dataset[COMPUTE_FEATURES], dataset.compute_prefill_ms)

    return dict(
        preprocessor=preprocessor, route_event=event_model, route_tail=tail_model,
        route_log_bounds=route_log_bounds, scheduler=scheduler_model,
        scheduler_log_bounds=scheduler_log_bounds, compute=compute_model,
    )


def predict_combined(models, feature_rows):
    """feature_rows: DataFrame with columns = base.NUMERIC_FEATURES (raw,
    e.g. `feature_input_tokens` renamed to `input_tokens`) + 'policy'."""
    frame = add_derived_features(feature_rows)
    transformed = models["preprocessor"].transform(frame[FEATURES])
    route_probability = models["route_event"].predict_proba(transformed)[:, 1]
    route_log = np.clip(models["route_tail"].predict(transformed), *models["route_log_bounds"])
    route_positive_ms = np.expm1(route_log)
    route_ms = route_probability * route_positive_ms
    scheduler_log = np.clip(models["scheduler"].predict(transformed), *models["scheduler_log_bounds"])
    scheduler_ms = np.expm1(scheduler_log)
    compute_ms = np.maximum(0, models["compute"].predict(frame[COMPUTE_FEATURES]))
    return route_ms + scheduler_ms + compute_ms


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
    print("Training combined-improvement models on full Phase 1 dataset (13,500 rows)...")
    models = fit_combined_models()

    data = pd.read_csv(COUNTERFACTUAL_CSV)
    complete = data[data.counterfactual_complete].copy()
    print(f"n={len(complete)} candidates, {complete.request_id.nunique()} requests")

    feature_rows = pd.DataFrame({
        name: complete[f"feature_{name}"].to_numpy() for name in PROD_NUMERIC_FEATURES
    })
    feature_rows["policy"] = "NEAREST_MIGRATE_KV"  # matches router.py:1222-1224

    complete["predicted_formula_ttft_ms_combined"] = predict_combined(models, feature_rows)
    complete["predicted_total_ttft_ms_combined"] = (
        complete.predicted_formula_ttft_ms_combined
        + complete.request_migration_ms + complete.kv_migration_ms + complete.downlink_ms
    )

    # Also recompute today's already-deployed production formula (log1p
    # scheduler, item 13) for a clean three-way comparison, since
    # `predicted_total_ttft_ms` in this CSV was already refreshed to the
    # log1p formula upstream (recompute_candidate_predictions_new_formula.py).
    prod_formula = OfflineTtftFormula()  # current production artifact (item 13's log1p fix)
    print(f"Production artifact: {prod_formula.artifact_dir}")

    fold_requests = sorted(complete.request_id.unique())

    def max_tie_fraction(col):
        sizes = complete.groupby(["request_id", col])[col].transform("count")
        return (sizes.groupby(complete.request_id).max() /
                complete.groupby("request_id").size()).mean()

    print(f"Mean per-request max-tie-fraction, production (log1p) formula: "
          f"{max_tie_fraction('predicted_total_ttft_ms'):.3f}")
    print(f"Mean per-request max-tie-fraction, combined-improvement formula: "
          f"{max_tie_fraction('predicted_total_ttft_ms_combined'):.3f}")
    print()

    detail_rows = []
    for rid in fold_requests:
        group = complete[complete.request_id == rid].sort_values("candidate_instance_id")

        chosen = group.loc[group.predicted_total_ttft_ms.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.predicted_total_ttft_ms, group)
        detail_rows.append(dict(variant="production_log1p_formula", request_id=rid,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        chosen = group.loc[group.predicted_total_ttft_ms_combined.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.predicted_total_ttft_ms_combined, group)
        detail_rows.append(dict(variant="combined_improvement_formula", request_id=rid,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

        chosen = group.loc[group.candidate_capacity_pressure.idxmin()]
        top1, regret = score_selection(group, chosen)
        spearman = spearman_of(group.candidate_capacity_pressure, group)
        detail_rows.append(dict(variant="pressure", request_id=rid,
                                 chosen_gpu=int(chosen.candidate_instance_id),
                                 top1_correct=top1, regret_ms=regret, spearman=spearman))

    detail = pd.DataFrame(detail_rows)
    detail.to_csv(ANALYSIS / "combined_formula_vs_production_by_request.csv", index=False)

    summary = detail.groupby("variant").agg(
        folds=("request_id", "count"),
        top1_accuracy=("top1_correct", "mean"),
        mean_regret_ms=("regret_ms", "mean"),
        median_regret_ms=("regret_ms", "median"),
        p95_regret_ms=("regret_ms", lambda s: s.quantile(0.95)),
        mean_spearman=("spearman", "mean"),
    ).reindex(["production_log1p_formula", "combined_improvement_formula", "pressure"])
    summary.to_csv(ANALYSIS / "combined_formula_vs_production_summary.csv")

    print(summary.to_string())
    print()
    print(f"NOTE: n={len(fold_requests)} fully labeled requests; treat as directional, not conclusive.")

    complete.to_csv(ANALYSIS / "counterfactual_candidate_actuals_combined_formula.csv", index=False)


if __name__ == "__main__":
    main()
