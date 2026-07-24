#!/usr/bin/env python3
"""Join candidate predictions with one-decision counterfactual actual TTFT."""

import argparse

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
POLICY = "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE"
MANIFEST = ROOT / "configs" / "counterfactual_manifest.csv"
PREDICTIONS = ROOT / "analysis" / "redirect_candidate_predictions.csv"
BASELINE = ROOT / "results" / POLICY / "requests.csv"
RESULTS = ROOT / "counterfactual" / "results"
ANALYSIS = ROOT / "analysis"
FIGURES = ROOT / "figures"


def request_metrics(row):
    communication_ms = row.communication_latency_ns / 1e6
    transfer_ms = row.kv_migration_latency_ns / 1e6
    router_ms = max(0.0, (
        row.e2e_ttft_ns - row.prefill_service_ns
        - row.communication_latency_ns - row.queueing_before_ttft_ns
    ) / 1e6)
    return {
        "actual_gpu_id": int(row.gpu_id),
        "actual_ttft_ms": row.e2e_ttft_ns / 1e6,
        "actual_router_ms": router_ms,
        "actual_scheduler_ms": row.queueing_before_ttft_ns / 1e6,
        "actual_kv_transfer_ms": transfer_ms,
        "actual_compute_ms": row.prefill_service_ns / 1e6,
        "actual_other_comm_ms": max(0.0, communication_ms - transfer_ms),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Write the currently available labels instead of requiring all runs.",
    )
    args = parser.parse_args()
    manifest = pd.read_csv(MANIFEST)
    predictions = pd.read_csv(PREDICTIONS)
    baseline = pd.read_csv(BASELINE).set_index("request id")
    rows = []
    missing = []
    for item in manifest.itertuples(index=False):
        if item.selected_by_model:
            request_row = baseline.loc[item.request_id]
            source = "baseline"
        else:
            path = RESULTS / item.case_id / "requests.csv"
            if not path.exists():
                missing.append(item.case_id)
                continue
            result = pd.read_csv(path).set_index("request id")
            request_row = result.loc[item.request_id]
            source = "counterfactual"
        if int(request_row.gpu_id) != int(item.candidate_instance_id):
            raise ValueError(
                f"Forced target mismatch for {item.case_id}: "
                f"expected {item.candidate_instance_id}, got {request_row.gpu_id}"
            )
        rows.append({
            "case_id": item.case_id,
            "request_id": item.request_id,
            "candidate_instance_id": item.candidate_instance_id,
            "selected_by_model": item.selected_by_model,
            "source": source,
            **request_metrics(request_row),
        })
    if missing and not args.allow_partial:
        raise FileNotFoundError(
            f"Missing {len(missing)} counterfactual outputs; first: {missing[:5]}"
        )

    actual = pd.DataFrame(rows)
    combined = predictions.merge(
        actual,
        on=["request_id", "candidate_instance_id", "selected_by_model"],
        validate="one_to_one",
    )
    combined["actual_ttft_rank"] = combined.groupby("request_id").actual_ttft_ms.rank(
        method="first"
    ).astype(int)
    combined["actual_regret_ms"] = combined.actual_ttft_ms - combined.groupby(
        "request_id"
    ).actual_ttft_ms.transform("min")
    combined["prediction_error_ms"] = (
        combined.actual_ttft_ms - combined.predicted_total_ttft_ms
    )
    expected_counts = manifest.groupby("request_id").size()
    observed_counts = combined.groupby("request_id").size()
    combined["observed_candidate_count"] = combined.request_id.map(observed_counts)
    combined["expected_candidate_count"] = combined.request_id.map(expected_counts)
    combined["counterfactual_complete"] = (
        combined.observed_candidate_count == combined.expected_candidate_count
    )
    combined.to_csv(ANALYSIS / "counterfactual_candidate_actuals.csv", index=False)

    request_rows = []
    for request_id, group in combined.groupby("request_id"):
        if len(group) != expected_counts.loc[request_id]:
            continue
        actual_best = group.sort_values(["actual_ttft_ms", "candidate_instance_id"]).iloc[0]
        model_choice = group[group.model_ttft_rank.eq(1)].iloc[0]
        pressure_choice = group[group.capacity_pressure_rank.eq(1)].iloc[0]
        request_rows.append({
            "request_id": request_id,
            "actual_best_gpu": int(actual_best.candidate_instance_id),
            "actual_best_ttft_ms": actual_best.actual_ttft_ms,
            "model_gpu": int(model_choice.candidate_instance_id),
            "model_actual_ttft_ms": model_choice.actual_ttft_ms,
            "model_regret_ms": model_choice.actual_regret_ms,
            "model_top1_correct": int(model_choice.actual_ttft_rank == 1),
            "pressure_gpu": int(pressure_choice.candidate_instance_id),
            "pressure_actual_ttft_ms": pressure_choice.actual_ttft_ms,
            "pressure_regret_ms": pressure_choice.actual_regret_ms,
            "pressure_top1_correct": int(pressure_choice.actual_ttft_rank == 1),
            "model_spearman": group.model_ttft_rank.corr(
                group.actual_ttft_rank, method="spearman"
            ),
            "pressure_spearman": group.capacity_pressure_rank.corr(
                group.actual_ttft_rank, method="spearman"
            ),
        })
    request_summary = pd.DataFrame(request_rows, columns=[
        "request_id", "actual_best_gpu", "actual_best_ttft_ms", "model_gpu",
        "model_actual_ttft_ms", "model_regret_ms", "model_top1_correct",
        "pressure_gpu", "pressure_actual_ttft_ms", "pressure_regret_ms",
        "pressure_top1_correct", "model_spearman", "pressure_spearman",
    ])
    request_summary.to_csv(
        ANALYSIS / "counterfactual_ranking_by_request.csv", index=False
    )
    complete_summary = request_summary
    metrics_source = complete_summary
    metrics = pd.DataFrame([{
        "missing_simulations": len(missing),
        "decisions": len(request_summary),
        "complete_decisions": len(complete_summary),
        "candidates": len(combined),
        "model_top1_accuracy": metrics_source.model_top1_correct.mean(),
        "pressure_top1_accuracy": metrics_source.pressure_top1_correct.mean(),
        "model_mean_regret_ms": metrics_source.model_regret_ms.mean(),
        "pressure_mean_regret_ms": metrics_source.pressure_regret_ms.mean(),
        "model_p95_regret_ms": metrics_source.model_regret_ms.quantile(0.95),
        "pressure_p95_regret_ms": metrics_source.pressure_regret_ms.quantile(0.95),
        "model_mean_spearman": metrics_source.model_spearman.mean(),
        "pressure_mean_spearman": metrics_source.pressure_spearman.mean(),
        "prediction_mae_ms": combined.prediction_error_ms.abs().mean(),
    }])
    metrics.to_csv(ANALYSIS / "counterfactual_ranking_metrics.csv", index=False)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    axes[0].scatter(combined.predicted_total_ttft_ms, combined.actual_ttft_ms,
                    alpha=0.65, color="#4f83c2")
    low = min(combined.predicted_total_ttft_ms.min(), combined.actual_ttft_ms.min())
    high = max(combined.predicted_total_ttft_ms.max(), combined.actual_ttft_ms.max())
    axes[0].plot([low, high], [low, high], linestyle="--", color="#77736d")
    axes[0].set_xlabel("predicted candidate TTFT (ms)")
    axes[0].set_ylabel("counterfactual actual TTFT (ms)")
    axes[0].set_title("Absolute prediction")

    axes[1].scatter(combined.model_ttft_rank, combined.actual_ttft_rank,
                    alpha=0.55, color="#865bd6")
    axes[1].set_xlabel("model rank")
    axes[1].set_ylabel("actual rank")
    axes[1].set_title("Model ranking")

    if len(request_summary):
        regret = [request_summary.model_regret_ms, request_summary.pressure_regret_ms]
        axes[2].boxplot(regret, labels=["Model", "Capacity pressure"], showmeans=True)
    else:
        axes[2].text(0.5, 0.5, "No complete decisions yet", ha="center", va="center")
    axes[2].set_ylabel("selected-candidate actual regret (ms)")
    axes[2].set_title("Selection regret")
    for axis in axes:
        axis.grid(color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(FIGURES / "counterfactual_candidate_ranking.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)

    print(metrics.to_string(index=False))
    if missing:
        print(f"Partial analysis: {len(missing)} simulations are still missing.")


if __name__ == "__main__":
    main()
