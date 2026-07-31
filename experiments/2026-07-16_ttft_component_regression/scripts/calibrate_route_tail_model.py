#!/usr/bin/env python3
"""Post-hoc isotonic calibration on top of the tuned route_tail model
(learning_rate=0.15, n_estimators=70 -- see route_tail_hyperparameter_tuning.md).

The decile table in that report shows a systematic, monotonic miscalibration
even after the hyperparameter tune: low actual values are over-predicted,
high actual values are under-predicted. Isotonic regression is the natural
fix -- a monotonic, non-parametric remap of raw prediction -> calibrated
prediction fit by least squares (pool-adjacent-violators), applied in ms
space directly on route_positive_ms.

To avoid leaking the outer scenario-held-out evaluation, the calibration
curve for each outer test scenario is fit only on *inner* leave-one-scenario-
out predictions from the other 14 scenarios (i.e. the calibration curve
never sees a model prediction made using the outer scenario's own data,
directly or indirectly), mirroring the nested-CV protocol already used for
guardrail lambda selection in experiments/2026-07-21-add_gpu_utilization/
scripts/evaluate_guardrail.py.
"""

import time
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import mean_absolute_error, r2_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit_ttft_formula as base  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis" / "ttft_formula"

TUNED_HYPERPARAMS = dict(
    loss="absolute_error", n_estimators=70, learning_rate=0.15,
    max_depth=2, min_samples_leaf=20,
)


def fit_predict_fold(train, test, hyperparams):
    """One leave-one-scenario-out fold: event model + route_tail, ms-space
    route_positive_ms predictions for `test`, restricted to columns needed."""
    preprocessor = base.make_preprocessor()
    train_x = preprocessor.fit_transform(train[base.FEATURES])
    test_x = preprocessor.transform(test[base.FEATURES])

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

    return pd.DataFrame({
        "route_probability": route_probability,
        "route_positive_ms": route_positive_ms,
        "actual_route_positive": test.route_positive.to_numpy(),
        "actual_route_ms": test.router_queue_ms.to_numpy(),
    }, index=test.index)


def main():
    dataset = base.prepare_dataset()
    scenarios = sorted(dataset.scenario_id.unique())
    print(f"n={len(dataset)}, n_scenarios={len(scenarios)}")

    outer_rows_uncalibrated = []
    outer_rows_calibrated = []
    outer_rows_log_calibrated = []
    outer_rows_median_calibrated = []
    start_all = time.time()

    for outer_scenario in scenarios:
        outer_train = dataset[dataset.scenario_id != outer_scenario]
        outer_test = dataset[dataset.scenario_id == outer_scenario]

        # Outer prediction: model trained on all 14 other scenarios.
        outer_pred = fit_predict_fold(outer_train, outer_test, TUNED_HYPERPARAMS)
        outer_pred["scenario"] = outer_scenario
        outer_rows_uncalibrated.append(outer_pred.copy())

        # Inner leave-one-scenario-out among the 14 training scenarios only,
        # to build a calibration-fitting set that never used the outer
        # scenario's data (directly or via model fitting).
        inner_scenarios = [s for s in scenarios if s != outer_scenario]
        inner_preds = []
        for inner_scenario in inner_scenarios:
            inner_train = outer_train[outer_train.scenario_id != inner_scenario]
            inner_test = outer_train[outer_train.scenario_id == inner_scenario]
            inner_preds.append(fit_predict_fold(inner_train, inner_test, TUNED_HYPERPARAMS))
        inner_pred = pd.concat(inner_preds)
        inner_positive = inner_pred[inner_pred.actual_route_positive == 1]

        calibrator = IsotonicRegression(out_of_bounds="clip", increasing=True)
        calibrator.fit(inner_positive.route_positive_ms, inner_positive.actual_route_ms)

        calibrated = outer_pred.copy()
        calibrated["route_positive_ms"] = calibrator.predict(outer_pred.route_positive_ms)
        outer_rows_calibrated.append(calibrated)

        # log-space variant: isotonic regression still minimizes squared
        # error internally, but doing so on log1p(ms) instead of raw ms
        # keeps the huge right-tail values (up to 53,045ms) from dominating
        # the fit and dragging mid/low predictions upward -- MAE cares about
        # absolute ms error, which log-space fitting is much closer to.
        log_calibrator = IsotonicRegression(out_of_bounds="clip", increasing=True)
        log_calibrator.fit(
            np.log1p(inner_positive.route_positive_ms),
            np.log1p(inner_positive.actual_route_ms),
        )
        log_calibrated = outer_pred.copy()
        log_calibrated["route_positive_ms"] = np.expm1(
            log_calibrator.predict(np.log1p(outer_pred.route_positive_ms))
        )
        outer_rows_log_calibrated.append(log_calibrated)

        # binned-median calibration: isotonic regression minimizes squared
        # error and gets dragged around by the huge right-tail actuals (up
        # to 53,045ms). MAE is minimized by the *median*, not the mean, so
        # bin the inner calibration set by raw prediction quantile, map each
        # bin's median raw value to that bin's median actual value, force
        # monotonicity on the resulting step sequence, and linearly
        # interpolate for the outer fold.
        n_bins = min(20, max(2, len(inner_positive) // 50))
        binned = inner_positive.copy()
        binned["bin"] = pd.qcut(binned.route_positive_ms, n_bins, duplicates="drop")
        bin_stats = binned.groupby("bin", observed=True).agg(
            x=("route_positive_ms", "median"), y=("actual_route_ms", "median"),
        ).sort_values("x")
        bin_x = bin_stats.x.to_numpy()
        bin_y = np.maximum.accumulate(bin_stats.y.to_numpy())  # enforce monotonic
        median_calibrated = outer_pred.copy()
        median_calibrated["route_positive_ms"] = np.interp(
            outer_pred.route_positive_ms, bin_x, bin_y,
            left=bin_y[0], right=bin_y[-1],
        )
        outer_rows_median_calibrated.append(median_calibrated)

        print(f"  scenario={outer_scenario}: outer_n={len(outer_test)}, "
              f"inner_calibration_n={len(inner_positive)}")

    uncalibrated = pd.concat(outer_rows_uncalibrated, ignore_index=True)
    calibrated = pd.concat(outer_rows_calibrated, ignore_index=True)
    log_calibrated = pd.concat(outer_rows_log_calibrated, ignore_index=True)
    median_calibrated = pd.concat(outer_rows_median_calibrated, ignore_index=True)
    print(f"\nTotal wall time: {time.time() - start_all:.1f}s")

    def route_ms(frame):
        return frame.route_probability * frame.route_positive_ms

    def summarize(frame, label):
        pred_route_ms = route_ms(frame)
        mae = mean_absolute_error(frame.actual_route_ms, pred_route_ms)
        r2 = r2_score(frame.actual_route_ms, pred_route_ms)
        pos = frame[frame.actual_route_positive == 1].copy()
        pos_pred = route_ms(pos)
        pos_mae = mean_absolute_error(pos.actual_route_ms, pos_pred)
        decile = pos.copy()
        decile["pred"] = pos_pred
        decile["actual_decile"] = pd.qcut(decile.actual_route_ms, 10, duplicates="drop", labels=False)
        top = decile[decile.actual_decile == decile.actual_decile.max()]
        top_ratio = top.pred.mean() / top.actual_route_ms.mean()
        print(f"[{label}] route_mae={mae:.2f}ms route_r2={r2:.4f} "
              f"positive_mae={pos_mae:.2f}ms top_decile_ratio={top_ratio:.3f}")
        return dict(variant=label, route_mae_ms=mae, route_r2=r2,
                    positive_subset_mae_ms=pos_mae, top_decile_ratio=top_ratio)

    print()
    summaries = [
        summarize(uncalibrated, "tuned_uncalibrated_lr015_70"),
        summarize(calibrated, "tuned_isotonic_calibrated_ms_space"),
        summarize(log_calibrated, "tuned_isotonic_calibrated_log_space"),
        summarize(median_calibrated, "tuned_binned_median_calibrated"),
    ]
    pd.DataFrame(summaries).to_csv(
        ANALYSIS / "route_tail_isotonic_calibration_summary.csv", index=False
    )

    for label, frame in [
        ("uncalibrated", uncalibrated), ("calibrated_ms", calibrated),
        ("calibrated_log", log_calibrated), ("calibrated_median", median_calibrated),
    ]:
        pos = frame[frame.actual_route_positive == 1].copy()
        pos["pred"] = route_ms(pos)
        pos["actual_decile"] = pd.qcut(pos.actual_route_ms, 10, duplicates="drop", labels=False)
        table = pos.groupby("actual_decile").agg(
            n=("actual_route_ms", "size"),
            mean_actual=("actual_route_ms", "mean"),
            mean_predicted=("pred", "mean"),
        )
        print(f"\n=== {label} calibration by decile ===")
        print(table.to_string())
        table.to_csv(ANALYSIS / f"route_tail_isotonic_calibration_decile_{label}.csv")

    uncalibrated.to_csv(ANALYSIS / "route_tail_isotonic_oof_uncalibrated.csv", index=False)
    calibrated.to_csv(ANALYSIS / "route_tail_isotonic_oof_calibrated.csv", index=False)


if __name__ == "__main__":
    main()
