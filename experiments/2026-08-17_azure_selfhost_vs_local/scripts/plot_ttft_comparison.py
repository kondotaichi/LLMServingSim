#!/usr/bin/env python3
"""Plot mean TTFT for the proposed local method and Azure vLLM."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt


EXP_ROOT = Path(__file__).resolve().parents[1]
INPUT = EXP_ROOT / "analysis/azure_vs_local_proposed_pp2.csv"
OUTPUT_DIR = EXP_ROOT / "figures"
LEVELS = (1, 5, 10)


def load_values() -> tuple[list[float], list[float]]:
    with INPUT.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))

    azure = []
    local = []
    for level in LEVELS:
        row = next(
            item
            for item in rows
            if int(item["level"]) == level and item["remote_window"] == "first_300"
        )
        azure.append(float(row["remote_ttft_mean_ms"]))
        local.append(float(row["local_ttft_mean_ms"]))
    return azure, local


def main() -> None:
    azure, local = load_values()
    positions = list(range(len(LEVELS)))
    width = 0.36

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    local_bars = ax.bar(
        [position - width / 2 for position in positions],
        local,
        width,
        label="Proposed: PP=2 + KV migrate (Local RTX 4090 x12, simulated)",
        color="#4C78A8",
    )
    azure_bars = ax.bar(
        [position + width / 2 for position in positions],
        azure,
        width,
        label="Azure H100 NVL x8 (measured E2E)",
        color="#F58518",
    )

    ax.bar_label(local_bars, fmt="%.1f ms", padding=3, fontsize=9)
    ax.bar_label(azure_bars, fmt="%.1f ms", padding=3, fontsize=9)
    ax.set_xticks(positions, [f"Peak {level}x" for level in LEVELS])
    ax.set_ylabel("Mean TTFT (ms, lower is better)")
    ax.set_title("Hongo workload: Proposed local method vs Azure vLLM")
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", frameon=False)
    ax.text(
        0.01,
        -0.18,
        "All bars use the same original 300-request sweep; Azure uses its first 300 requests",
        transform=ax.transAxes,
        fontsize=8.5,
        color="#555555",
    )
    fig.tight_layout()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_DIR / "ttft_mean_proposed_vs_azure.svg", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "ttft_mean_proposed_vs_azure.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {OUTPUT_DIR / 'ttft_mean_proposed_vs_azure.svg'}")
    print(f"wrote {OUTPUT_DIR / 'ttft_mean_proposed_vs_azure.png'}")


if __name__ == "__main__":
    main()
