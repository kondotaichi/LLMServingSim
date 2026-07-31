#!/usr/bin/env python3
"""Route-tail regressor hyperparameter sweep, scenario-held-out.

Motivation: fit_ttft_formula.py's route component dominates overall TTFT
regression error (route MAE 630.2ms out of TTFT MAE 714.6ms -- 88% of total
error, while scheduler/compute are well-calibrated: predicted mean 56.7ms vs
actual 60.3ms for scheduler, 706.9ms vs 703.6ms for compute). Decomposing the
route MAE shows it is almost entirely (613 of 630ms) concentrated in the 15%
of rows where a queue actually occurred, and the OOF calibration by actual-
value decile shows a compressed-range pattern: low actual deciles are
over-predicted (actual 41.9ms -> predicted 727.3ms) and high actual deciles
are severely under-predicted (actual 24313.5ms -> predicted 8975.3ms). This
looks like the depth-2 / learning_rate=0.05 / min_samples_leaf=20
GradientBoostingRegressor (loss=absolute_error) under-fitting the tail rather
than a feature problem, since route_probability calibration in the same
deciles stays flat at ~0.97-0.99 (the classifier is not the bottleneck).

This script reuses fit_ttft_formula.py's dataset/preprocessing/event model
unchanged and only sweeps the route_tail GradientBoostingRegressor
hyperparameters, under the exact same leave-one-scenario-out protocol, to
see whether more capacity (more trees, higher learning rate, deeper trees,
squared_error loss) closes the compression gap without hurting held-out MAE.
No production artifact is touched; this is read-only analysis.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit_ttft_formula as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis" / "ttft_formula"

VARIANTS = {
    "baseline_current_prod": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.05,
        max_depth=2, min_samples_leaf=20,
    ),
    "more_trees_300": dict(
        loss="absolute_error", n_estimators=300, learning_rate=0.05,
        max_depth=2, min_samples_leaf=20,
    ),
    "higher_lr_0.1": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.1,
        max_depth=2, min_samples_leaf=20,
    ),
    "deeper_depth3": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.05,
        max_depth=3, min_samples_leaf=20,
    ),
    "squared_error_loss": dict(
        loss="squared_error", n_estimators=100, learning_rate=0.05,
        max_depth=2, min_samples_leaf=20,
    ),
    "combo_300trees_lr0.1_depth3": dict(
        loss="absolute_error", n_estimators=300, learning_rate=0.1,
        max_depth=3, min_samples_leaf=20,
    ),
    "smaller_leaf_10": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.05,
        max_depth=2, min_samples_leaf=10,
    ),
    "lr_0.08": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.08,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.15": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.15,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.2": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.2,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.1_150trees": dict(
        loss="absolute_error", n_estimators=150, learning_rate=0.1,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.1_leaf30": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.1,
        max_depth=2, min_samples_leaf=30,
    ),
    "lr_0.12": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.12,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.18": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.18,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.25": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.25,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.3": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.3,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.15_leaf30": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.15,
        max_depth=2, min_samples_leaf=30,
    ),
    "lr_0.15_leaf15": dict(
        loss="absolute_error", n_estimators=100, learning_rate=0.15,
        max_depth=2, min_samples_leaf=15,
    ),
    "lr_0.15_150trees": dict(
        loss="absolute_error", n_estimators=150, learning_rate=0.15,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.15_70trees": dict(
        loss="absolute_error", n_estimators=70, learning_rate=0.15,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.15_40trees": dict(
        loss="absolute_error", n_estimators=40, learning_rate=0.15,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.15_50trees": dict(
        loss="absolute_error", n_estimators=50, learning_rate=0.15,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.15_60trees": dict(
        loss="absolute_error", n_estimators=60, learning_rate=0.15,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.15_80trees": dict(
        loss="absolute_error", n_estimators=80, learning_rate=0.15,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.2_70trees": dict(
        loss="absolute_error", n_estimators=70, learning_rate=0.2,
        max_depth=2, min_samples_leaf=20,
    ),
    "lr_0.1_70trees": dict(
        loss="absolute_error", n_estimators=70, learning_rate=0.1,
        max_depth=2, min_samples_leaf=20,
    ),
}


def run_variant(dataset, hyperparams):
    rows = []
    for scenario in sorted(dataset.scenario_id.unique()):
        train = dataset[dataset.scenario_id != scenario]
        test = dataset[dataset.scenario_id == scenario]

        preprocessor = base.make_preprocessor()
        train_x = preprocessor.fit_transform(train[base.FEATURES])
        test_x = preprocessor.transform(test[base.FEATURES])

        from sklearn.linear_model import LogisticRegression
        event_model = LogisticRegression(
            C=0.1, class_weight="balanced", max_iter=2000,
            random_state=base.RANDOM_STATE,
        ).fit(train_x, train.route_positive)
        route_probability = event_model.predict_proba(test_x)[:, 1]

        positive = train.route_positive.to_numpy(dtype=bool)
        positive_log_route = np.log1p(train.loc[positive, "router_queue_ms"])
        tail_model = GradientBoostingRegressor(
            random_state=base.RANDOM_STATE, **hyperparams,
        ).fit(train_x[positive], positive_log_route)

        log_bounds = (float(positive_log_route.min()), float(positive_log_route.max()))
        route_log = np.clip(tail_model.predict(test_x), *log_bounds)
        route_positive_ms = np.expm1(route_log)
        predicted_route_ms = route_probability * route_positive_ms

        rows.append(pd.DataFrame({
            "test_scenario": scenario,
            "predicted_route_ms": predicted_route_ms,
            "route_probability": route_probability,
            "actual_route_positive": test.route_positive.to_numpy(),
            "actual_route_ms": test.router_queue_ms.to_numpy(),
        }))
    return pd.concat(rows, ignore_index=True)


def summarize(predictions, label):
    mae = mean_absolute_error(predictions.actual_route_ms, predictions.predicted_route_ms)
    r2 = r2_score(predictions.actual_route_ms, predictions.predicted_route_ms)
    pos = predictions[predictions.actual_route_positive == 1]
    pos_mae = mean_absolute_error(pos.actual_route_ms, pos.predicted_route_ms)

    decile = pos.copy()
    decile["actual_decile"] = pd.qcut(decile.actual_route_ms, 10, duplicates="drop", labels=False)
    top_decile = decile[decile.actual_decile == decile.actual_decile.max()]
    top_ratio = top_decile.predicted_route_ms.mean() / top_decile.actual_route_ms.mean()

    return dict(
        variant=label, route_mae_ms=mae, route_r2=r2,
        positive_subset_mae_ms=pos_mae,
        top_decile_actual_mean=top_decile.actual_route_ms.mean(),
        top_decile_predicted_mean=top_decile.predicted_route_ms.mean(),
        top_decile_predicted_over_actual_ratio=top_ratio,
    )


def main():
    dataset = base.prepare_dataset()
    print(f"n={len(dataset)}, n_scenarios={dataset.scenario_id.nunique()}, "
          f"n_positive={(dataset.route_positive == 1).sum()}")
    print()

    summaries = []
    detail_by_variant = {}
    for label, hyperparams in VARIANTS.items():
        start = time.time()
        predictions = run_variant(dataset, hyperparams)
        elapsed = time.time() - start
        summary = summarize(predictions, label)
        summary["seconds"] = round(elapsed, 1)
        summaries.append(summary)
        detail_by_variant[label] = predictions
        print(f"[{label}] done in {elapsed:.1f}s: route_mae={summary['route_mae_ms']:.1f}ms "
              f"top_decile_ratio={summary['top_decile_predicted_over_actual_ratio']:.3f}")

    result = pd.DataFrame(summaries).set_index("variant")
    result.to_csv(ANALYSIS / "route_tail_hyperparameter_sweep.csv")
    print()
    print(result.to_string())

    best_predictions = detail_by_variant
    for label in ["baseline_current_prod"] + [
        row.variant for row in result.reset_index().itertuples()
        if row.variant != "baseline_current_prod"
    ]:
        pos = best_predictions[label][best_predictions[label].actual_route_positive == 1].copy()
        pos["actual_decile"] = pd.qcut(pos.actual_route_ms, 10, duplicates="drop", labels=False)
        calib = pos.groupby("actual_decile").agg(
            n=("actual_route_ms", "size"),
            mean_actual=("actual_route_ms", "mean"),
            mean_predicted=("predicted_route_ms", "mean"),
        )
        calib.to_csv(ANALYSIS / f"route_tail_calibration_{label}.csv")


if __name__ == "__main__":
    main()
