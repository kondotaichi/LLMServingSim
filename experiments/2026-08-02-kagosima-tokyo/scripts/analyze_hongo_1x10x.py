#!/usr/bin/env python3
"""Analyze latency and estimated GPU energy for the Hongo 1x-10x sweep."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/hongo_1x10x"
OUTPUT = ROOT / "analysis/hongo_1x10x"
ARMS = (
    ("all_tokyo_baseline", "All Tokyo", "#4c78a8", False),
    ("kg_baseline", "Tokyo + Kagoshima", "#f58518", True),
    ("kg_proposed", "Tokyo + Kagoshima + prewarm", "#54a24b", True),
)
IDLE_W = 50.0
TDP_W = 450.0
TOKYO_YEN_KWH = 23.0
KAGOSHIMA_YEN_KWH = 14.7
PP_SIZE = 2


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes"}


def integrate_power(util_rows: list[dict[str, str]], split: bool) -> dict[str, float]:
    """Return recorded and PP=2-projected energy estimates.

    The PP=2 output currently records work on logical GPU IDs 0-5 and leaves IDs
    6-11 at zero utilization. The projected estimate replicates each logical
    instance's utilization across its two pipeline stages.
    """
    recorded_j = recorded_cost = recorded_busy = recorded_duration = 0.0
    projected_j = projected_cost = projected_busy = projected_duration = 0.0
    for row in util_rows:
        gpu_id = int(row["gpu_id"])
        duration_ns = float(row["window_duration_ns"])
        busy_ns = float(row["busy_time_ns"])
        power_j = (IDLE_W * duration_ns + (TDP_W - IDLE_W) * busy_ns) / 1e9

        recorded_rate = KAGOSHIMA_YEN_KWH if split and gpu_id >= 6 else TOKYO_YEN_KWH
        recorded_j += power_j
        recorded_cost += power_j / 3.6e6 * recorded_rate
        recorded_busy += busy_ns
        recorded_duration += duration_ns

        if gpu_id < 6:
            projected_rate = (
                KAGOSHIMA_YEN_KWH if split and gpu_id >= 3 else TOKYO_YEN_KWH
            )
            projected_j += PP_SIZE * power_j
            projected_cost += PP_SIZE * power_j / 3.6e6 * projected_rate
            projected_busy += PP_SIZE * busy_ns
            projected_duration += PP_SIZE * duration_ns

    return {
        "recorded_energy_wh": recorded_j / 3600,
        "recorded_cost_yen": recorded_cost,
        "recorded_util_pct": 100 * recorded_busy / recorded_duration,
        "projected_energy_wh": projected_j / 3600,
        "projected_cost_yen": projected_cost,
        "projected_util_pct": 100 * projected_busy / projected_duration,
    }


def percentile(values: np.ndarray, q: int) -> float:
    return float(np.percentile(values, q))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows_out: list[dict[str, object]] = []
    for load in range(1, 11):
        load_rows: list[dict[str, object]] = []
        for arm, label, _, split in ARMS:
            run_dir = RESULTS / f"{load}x" / arm
            requests = read_csv(run_dir / "requests.csv")
            util = read_csv(run_dir / "gpu_utilization_timeseries.csv")
            ttft = np.asarray([float(row["e2e_ttft_ns"]) / 1e6 for row in requests])
            queue = np.asarray([float(row["queueing_before_ttft_ns"]) / 1e6 for row in requests])
            communication = np.asarray([float(row["communication_latency_ns"]) / 1e6 for row in requests])
            prefill = np.asarray([float(row["prefill_service_ns"]) / 1e6 for row in requests])
            arrival = np.asarray([float(row["arrival"]) for row in requests])
            completion = np.asarray([float(row["request_end_time_ns"]) for row in requests])
            power = integrate_power(util, split)
            summary: dict[str, object] = {
                "load_multiplier": load,
                "arm": arm,
                "label": label,
                "requests": len(requests),
                "mean_ttft_ms": float(np.mean(ttft)),
                "p50_ttft_ms": percentile(ttft, 50),
                "p95_ttft_ms": percentile(ttft, 95),
                "p99_ttft_ms": percentile(ttft, 99),
                "mean_queue_ms": float(np.mean(queue)),
                "mean_communication_ms": float(np.mean(communication)),
                "mean_prefill_ms": float(np.mean(prefill)),
                "makespan_s": float((np.max(completion) - np.min(arrival)) / 1e9),
                "redirects": sum(truthy(row.get("rerouted")) for row in requests),
                "prewarm_hits": sum(truthy(row.get("proactive_kv_prewarm_hit")) for row in requests),
                "prewarm_wasted": sum(truthy(row.get("proactive_kv_prewarm_wasted")) for row in requests),
                **power,
                "projected_energy_mwh_per_request": power["projected_energy_wh"] * 1000 / len(requests),
            }
            load_rows.append(summary)

        all_tokyo = load_rows[0]
        kg_baseline = load_rows[1]
        for summary in load_rows:
            summary["mean_ttft_vs_all_tokyo_pct"] = (
                float(summary["mean_ttft_ms"]) / float(all_tokyo["mean_ttft_ms"]) - 1
            ) * 100
            summary["p95_ttft_vs_all_tokyo_pct"] = (
                float(summary["p95_ttft_ms"]) / float(all_tokyo["p95_ttft_ms"]) - 1
            ) * 100
            summary["projected_energy_vs_all_tokyo_pct"] = (
                float(summary["projected_energy_wh"]) / float(all_tokyo["projected_energy_wh"]) - 1
            ) * 100
            summary["projected_cost_vs_all_tokyo_pct"] = (
                float(summary["projected_cost_yen"]) / float(all_tokyo["projected_cost_yen"]) - 1
            ) * 100
            summary["mean_ttft_vs_kg_baseline_pct"] = (
                float(summary["mean_ttft_ms"]) / float(kg_baseline["mean_ttft_ms"]) - 1
            ) * 100
            summary["p95_ttft_vs_kg_baseline_pct"] = (
                float(summary["p95_ttft_ms"]) / float(kg_baseline["p95_ttft_ms"]) - 1
            ) * 100
        rows_out.extend(load_rows)

    with (OUTPUT / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows_out[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows_out)

    loads = np.arange(1, 11)
    for metric, ylabel, filename in (
        ("mean_ttft_ms", "Mean TTFT (ms)", "mean_ttft_vs_load.png"),
        ("p95_ttft_ms", "p95 TTFT (ms)", "p95_ttft_vs_load.png"),
        ("projected_energy_wh", "Projected GPU energy (Wh)", "energy_vs_load.png"),
        ("projected_cost_yen", "Projected electricity cost (yen)", "cost_vs_load.png"),
    ):
        fig, axis = plt.subplots(figsize=(9, 5.5))
        for arm, label, color, _ in ARMS:
            values = [float(row[metric]) for row in rows_out if row["arm"] == arm]
            axis.plot(loads, values, marker="o", linewidth=2, label=label, color=color)
        axis.set_xlabel("Hongo workload multiplier")
        axis.set_ylabel(ylabel)
        axis.set_xticks(loads)
        axis.grid(alpha=0.25)
        axis.legend(frameon=False)
        fig.tight_layout()
        fig.savefig(OUTPUT / filename, dpi=200)
        plt.close(fig)

    fig, axis = plt.subplots(figsize=(9, 6))
    for arm, label, color, _ in ARMS:
        arm_rows = [row for row in rows_out if row["arm"] == arm]
        x = [float(row["projected_energy_wh"]) for row in arm_rows]
        y = [float(row["mean_ttft_ms"]) for row in arm_rows]
        axis.plot(x, y, marker="o", linewidth=2, label=label, color=color)
        for load, xpos, ypos in zip(loads, x, y):
            axis.annotate(f"{load}x", (xpos, ypos), xytext=(4, 4), textcoords="offset points", fontsize=8)
    axis.set_xlabel("Projected GPU energy (Wh)")
    axis.set_ylabel("Mean TTFT (ms)")
    axis.grid(alpha=0.25)
    axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUTPUT / "latency_energy_tradeoff.png", dpi=200)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(9, 5.5))
    baseline_rows = [row for row in rows_out if row["arm"] == "all_tokyo_baseline"]
    for metric, label, color in (
        ("mean_queue_ms", "Queueing", "#e7a34b"),
        ("mean_prefill_ms", "Prefill service", "#4c956c"),
        ("mean_communication_ms", "Communication", "#72a0c1"),
    ):
        values = [float(row[metric]) for row in baseline_rows]
        axis.plot(loads, values, marker="o", linewidth=2, label=label, color=color)
    axis.set_xlabel("Hongo workload multiplier")
    axis.set_ylabel("Mean TTFT component (ms)")
    axis.set_xticks(loads)
    axis.grid(alpha=0.25)
    axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUTPUT / "ttft_components_vs_load.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    main()
