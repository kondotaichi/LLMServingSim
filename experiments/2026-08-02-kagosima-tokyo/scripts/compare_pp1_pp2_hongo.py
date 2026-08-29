#!/usr/bin/env python3
"""Compare PP=1 and PP=2 Hongo sweeps for TTFT and electricity cost."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "analysis/hongo_pp1_vs_pp2"
RUNS = {
    1: ROOT / "results/hongo_1x10x_pp1",
    2: ROOT / "results/hongo_1x10x",
}
ARMS = (
    ("all_tokyo_baseline", "All Tokyo", "#4c78a8", False),
    ("kg_baseline", "Tokyo + Kagoshima", "#f58518", True),
    ("kg_proposed", "Tokyo + Kagoshima + prewarm", "#54a24b", True),
)
COMPONENTS = (
    ("mean_network_residual_ms", "Access network / residual", "#72a0c1"),
    ("mean_router_wait_ms", "Router capacity wait", "#b279a2"),
    ("mean_queue_ms", "Scheduler queueing", "#e7a34b"),
    ("mean_prefill_ms", "Prefill service", "#4c956c"),
)
IDLE_W = 50.0
TDP_W = 450.0
TOKYO_YEN_KWH = 23.0
KAGOSHIMA_YEN_KWH = 14.7


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes"}


def energy(util_rows: list[dict[str, str]], pp_size: int, split: bool) -> dict[str, float]:
    """Integrate PP=1 directly and project PP=2 across both pipeline stages."""
    joules = cost_yen = busy_ns = duration_ns_total = 0.0
    for row in util_rows:
        gpu_id = int(row["gpu_id"])
        duration_ns = float(row["window_duration_ns"])
        busy_ns_row = float(row["busy_time_ns"])
        if pp_size == 2 and gpu_id >= 6:
            continue
        multiplier = 2 if pp_size == 2 else 1
        region_id = gpu_id
        if pp_size == 2:
            is_kagoshima = split and gpu_id >= 3
        else:
            is_kagoshima = split and region_id >= 6
        rate = KAGOSHIMA_YEN_KWH if is_kagoshima else TOKYO_YEN_KWH
        this_joules = (
            IDLE_W * duration_ns + (TDP_W - IDLE_W) * busy_ns_row
        ) / 1e9 * multiplier
        joules += this_joules
        cost_yen += this_joules / 3.6e6 * rate
        busy_ns += busy_ns_row * multiplier
        duration_ns_total += duration_ns * multiplier
    return {
        "energy_wh": joules / 3600,
        "cost_yen": cost_yen,
        "utilization_pct": 100 * busy_ns / duration_ns_total,
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    for pp_size, result_root in RUNS.items():
        for load in range(1, 11):
            for arm, label, _, split in ARMS:
                run_dir = result_root / f"{load}x" / arm
                requests = read_csv(run_dir / "requests.csv")
                util = read_csv(run_dir / "gpu_utilization_timeseries.csv")
                values = lambda key: np.asarray(
                    [float(row[key]) / 1e6 for row in requests], dtype=float
                )
                ttft = values("e2e_ttft_ns")
                queue = values("queueing_before_ttft_ns")
                prefill = values("prefill_service_ns")
                router_wait = values("router_capacity_wait_ns")
                network_residual = ttft - queue - prefill - router_wait
                power = energy(util, pp_size, split)
                summaries.append({
                    "pp_size": pp_size,
                    "load_multiplier": load,
                    "arm": arm,
                    "label": label,
                    "requests": len(requests),
                    "mean_ttft_ms": float(np.mean(ttft)),
                    "p50_ttft_ms": float(np.percentile(ttft, 50)),
                    "p95_ttft_ms": float(np.percentile(ttft, 95)),
                    "p99_ttft_ms": float(np.percentile(ttft, 99)),
                    "mean_communication_ms": float(np.mean(values("communication_latency_ns"))),
                    "mean_network_residual_ms": float(np.mean(network_residual)),
                    "mean_router_wait_ms": float(np.mean(router_wait)),
                    "mean_queue_ms": float(np.mean(queue)),
                    "mean_prefill_ms": float(np.mean(prefill)),
                    "redirects": sum(truthy(row.get("rerouted")) for row in requests),
                    "prewarm_hits": sum(truthy(row.get("proactive_kv_prewarm_hit")) for row in requests),
                    "prewarm_wasted": sum(truthy(row.get("proactive_kv_prewarm_wasted")) for row in requests),
                    **power,
                    "energy_mwh_per_request": power["energy_wh"] * 1000 / len(requests),
                })

    lookup = {(r["pp_size"], r["load_multiplier"], r["arm"]): r for r in summaries}
    for row in summaries:
        other_pp = 2 if row["pp_size"] == 1 else 1
        other = lookup[(other_pp, row["load_multiplier"], row["arm"])]
        row["ttft_vs_other_pp_pct"] = (
            float(row["mean_ttft_ms"]) / float(other["mean_ttft_ms"]) - 1
        ) * 100
        row["cost_vs_other_pp_pct"] = (
            float(row["cost_yen"]) / float(other["cost_yen"]) - 1
        ) * 100

    with (OUTPUT / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summaries)

    loads = np.arange(1, 11)
    for arm, label, color, _ in ARMS:
        fig, axes = plt.subplots(1, 2, figsize=(13, 5.3), sharex=True)
        for pp_size, linestyle in ((1, "-"), (2, "--")):
            rows = [r for r in summaries if r["pp_size"] == pp_size and r["arm"] == arm]
            axes[0].plot(loads, [r["mean_ttft_ms"] for r in rows], marker="o",
                         color=color, linestyle=linestyle, label=f"PP={pp_size} mean")
            axes[0].plot(loads, [r["p95_ttft_ms"] for r in rows], marker="x",
                         color=color, linestyle=linestyle, alpha=0.65, label=f"PP={pp_size} p95")
            axes[1].plot(loads, [r["cost_yen"] for r in rows], marker="o",
                         color=color, linestyle=linestyle, label=f"PP={pp_size}")
        axes[0].set_ylabel("TTFT (ms)")
        axes[1].set_ylabel("Estimated electricity cost (yen)")
        for axis in axes:
            axis.set_xlabel("Hongo workload multiplier")
            axis.set_xticks(loads)
            axis.grid(alpha=0.25)
            axis.legend(frameon=False)
        fig.suptitle(label)
        fig.tight_layout()
        fig.savefig(OUTPUT / f"{arm}_ttft_cost.png", dpi=200)
        plt.close(fig)

    for load in (1, 4, 7, 10):
        fig, axes = plt.subplots(1, 3, figsize=(16, 5.5), sharey=True)
        for axis, (arm, label, _, _) in zip(axes, ARMS):
            rows = [lookup[(pp, load, arm)] for pp in (1, 2)]
            bottom = np.zeros(2)
            for metric, component_label, color in COMPONENTS:
                heights = np.asarray([float(row[metric]) for row in rows])
                axis.bar([0, 1], heights, bottom=bottom, color=color,
                         label=component_label)
                bottom += heights
            observed = [float(row["mean_ttft_ms"]) for row in rows]
            axis.scatter([0, 1], observed, marker="D", color="#222222", zorder=5,
                         label="Observed E2E TTFT")
            axis.set_xticks([0, 1], ["PP=1", "PP=2"])
            axis.set_title(label)
            axis.grid(axis="y", alpha=0.25)
        axes[0].set_ylabel("Mean TTFT components (ms)")
        handles, labels = axes[-1].get_legend_handles_labels()
        fig.suptitle(f"TTFT breakdown at Hongo {load}x", y=0.98)
        fig.legend(handles, labels, frameon=False, ncol=3, loc="upper center",
                   bbox_to_anchor=(0.5, 0.925))
        fig.tight_layout(rect=(0, 0, 1, 0.82))
        fig.savefig(OUTPUT / f"ttft_breakdown_{load}x.png", dpi=200, bbox_inches="tight")
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for arm, label, color, _ in ARMS:
        for pp_size, linestyle in ((1, "-"), (2, "--")):
            rows = [r for r in summaries if r["pp_size"] == pp_size and r["arm"] == arm]
            axes[0].plot(loads, [r["cost_yen"] for r in rows], marker="o",
                         color=color, linestyle=linestyle, label=f"{label}, PP={pp_size}")
            axes[1].plot(loads, [r["energy_wh"] for r in rows], marker="o",
                         color=color, linestyle=linestyle, label=f"{label}, PP={pp_size}")
    axes[0].set_ylabel("Estimated electricity cost (yen)")
    axes[1].set_ylabel("Estimated GPU energy (Wh)")
    for axis in axes:
        axis.set_xlabel("Hongo workload multiplier")
        axis.set_xticks(loads)
        axis.grid(alpha=0.25)
    axes[0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(OUTPUT / "power_cost_comparison.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    main()
