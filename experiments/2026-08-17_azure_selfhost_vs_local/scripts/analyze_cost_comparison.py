#!/usr/bin/env python3
"""Estimate workload cost for local RTX 4090 GPUs and Azure H100 VMs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt


EXP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EXP_ROOT.parents[1]
LOCAL_ROOT = REPO_ROOT / "experiments/2026-08-01_hongo_workload/results"
REMOTE_ROOT = EXP_ROOT / "results/remote"
ANALYSIS_PATH = EXP_ROOT / "analysis/cost_proposed_vs_azure.csv"
FIGURE_DIR = EXP_ROOT / "figures"

LEVELS = (1, 5, 10)
LOCAL_RUNS = {
    1: "busy_hour_seed1_4_redirect_kv_pp2",
    5: "peak_5x_seed1_4_redirect_kv_pp2",
    10: "peak_10x_seed1_4_redirect_kv_pp2",
}
RTX4090_BUSY_W = 450.0
RTX4090_IDLE_W = 19.0
ELECTRICITY_JPY_PER_KWH = 31.0
AZURE_USD_PER_VM_HOUR = 10.121
AZURE_VM_COUNT = 8
JPY_PER_USD = 150.0


def local_metrics(level: int) -> tuple[float, float]:
    run_dir = LOCAL_ROOT / LOCAL_RUNS[level]
    with (run_dir / "requests.csv").open(newline="", encoding="utf-8") as stream:
        requests = list(csv.DictReader(stream))
    start_ns = min(int(row["request_send_time_ns"]) for row in requests)
    end_ns = max(int(row["request_end_time_ns"]) for row in requests)
    duration_s = (end_ns - start_ns) / 1e9

    with (run_dir / "gpus.csv").open(newline="", encoding="utf-8") as stream:
        gpus = list(csv.DictReader(stream))
    energy_wh = sum(
        (
            float(row["busy_time_ns"]) * RTX4090_BUSY_W
            + float(row["idle_time_ns"]) * RTX4090_IDLE_W
        )
        / 1e9
        / 3600
        for row in gpus
    )
    return duration_s, energy_wh


def azure_duration_s(level: int) -> float:
    path = (
        REMOTE_ROOT
        / f"peak_{level}x_repeat600_v3_20260819"
        / f"azure-peak-{level}x-repeat600-v3-20260819/requests.jsonl"
    )
    attempts = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    primary = [
        row
        for row in attempts
        if int(row["attempt"]) == 1 and int(row["request_id"]) < 300
    ]
    return max(int(row["completion_ns"]) for row in primary) / 1e9


def build_rows() -> list[dict]:
    rows = []
    for level in LEVELS:
        local_duration_s, local_energy_wh = local_metrics(level)
        cloud_duration_s = azure_duration_s(level)
        local_cost_jpy = local_energy_wh / 1000 * ELECTRICITY_JPY_PER_KWH
        cloud_cost_usd = (
            cloud_duration_s / 3600 * AZURE_VM_COUNT * AZURE_USD_PER_VM_HOUR
        )
        cloud_cost_jpy = cloud_cost_usd * JPY_PER_USD
        rows.append(
            {
                "level": level,
                "requests": 300,
                "local_duration_s": local_duration_s,
                "local_gpu_energy_wh": local_energy_wh,
                "local_electricity_jpy_per_kwh": ELECTRICITY_JPY_PER_KWH,
                "local_gpu_energy_cost_jpy": local_cost_jpy,
                "local_gpu_energy_cost_usd": local_cost_jpy / JPY_PER_USD,
                "azure_duration_s": cloud_duration_s,
                "azure_vm_count": AZURE_VM_COUNT,
                "azure_usd_per_vm_hour": AZURE_USD_PER_VM_HOUR,
                "azure_compute_cost_usd": cloud_cost_usd,
                "azure_compute_cost_jpy": cloud_cost_jpy,
                "assumed_jpy_per_usd": JPY_PER_USD,
                "azure_to_local_cost_ratio": cloud_cost_jpy / local_cost_jpy,
            }
        )
    return rows


def write_csv(rows: list[dict]) -> None:
    ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ANALYSIS_PATH.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def plot(rows: list[dict]) -> None:
    positions = list(range(len(rows)))
    width = 0.36
    local = [float(row["local_gpu_energy_cost_jpy"]) for row in rows]
    azure = [float(row["azure_compute_cost_jpy"]) for row in rows]

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    local_bars = ax.bar(
        [position - width / 2 for position in positions],
        local,
        width,
        label="Proposed local: estimated GPU electricity",
        color="#4C78A8",
    )
    azure_bars = ax.bar(
        [position + width / 2 for position in positions],
        azure,
        width,
        label="Azure: 8 VM PAYG compute",
        color="#F58518",
    )
    ax.bar_label(local_bars, labels=[f"¥{value:.2f}" for value in local], padding=3)
    ax.bar_label(azure_bars, labels=[f"¥{value:.1f}" for value in azure], padding=3)
    ax.set_yscale("log")
    ax.set_xticks(positions, [f"Peak {row['level']}x" for row in rows])
    ax.set_ylabel("Estimated cost for 300 requests (JPY, log scale)")
    ax.set_title("Hongo workload cost: Proposed local method vs Azure PAYG")
    ax.grid(axis="y", which="both", alpha=0.25)
    ax.set_axisbelow(True)
    ax.legend(frameon=False)
    ax.text(
        0.01,
        -0.19,
        "Local: RTX 4090 busy 450 W / idle 19 W, ¥31/kWh. Azure: 8 x $10.121/VM-h. FX: $1=¥150.",
        transform=ax.transAxes,
        fontsize=8.5,
        color="#555555",
    )
    fig.tight_layout()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / "cost_proposed_vs_azure.svg", bbox_inches="tight")
    fig.savefig(FIGURE_DIR / "cost_proposed_vs_azure.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    rows = build_rows()
    write_csv(rows)
    plot(rows)
    print(f"wrote {ANALYSIS_PATH}")
    print(f"wrote {FIGURE_DIR / 'cost_proposed_vs_azure.svg'}")
    print(f"wrote {FIGURE_DIR / 'cost_proposed_vs_azure.png'}")


if __name__ == "__main__":
    main()
