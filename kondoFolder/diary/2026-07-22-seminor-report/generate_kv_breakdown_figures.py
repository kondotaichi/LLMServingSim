#!/usr/bin/env python3
"""Generate KV-migration on/off TTFT breakdown figures across token sizes.

Adapted from experiments/plot_ttft_redirect_comparison.py (same visual style:
horizontal stacked bars, 3 panels split by redirect status). This script
sweeps every token-size / reuse-ratio condition in the repo that has both
NEAREST_MIGRATE (no KV handoff) and NEAREST_MIGRATE_KV (with KV handoff)
result sets, plus NEAREST_KV as a no-redirect baseline where available.
"""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS = REPO_ROOT / "experiments"
OUTPUT_DIR = Path(__file__).resolve().parent

METHODS = [
    ("Nearest KV", "NEAREST_KV", "#2f5f9f"),
    ("Migrate", "NEAREST_MIGRATE", "#d18120"),
    ("Migrate + KV", "NEAREST_MIGRATE_KV", "#31866f"),
]

# Multi-candidate variants, added only where a run under the *same* workload
# (same jsonl / cluster config / duration) exists. Each entry is
# (label, policy_dir_name, color, results_dir_override_or_None).
MULTI_PRESSURE = ("Multi (no-model, pressure)", "#b03a8e")
MULTI_FORMULA = ("Multi (learned model)", "#5a4fcf")

MULTI_SWEEP_DIR = REPO_ROOT / "experiments" / "2026-07-21_multi_pressure_vs_kv_input_reuse_sweep" / "results"

MULTI_CANDIDATES = {
    "prompt6000_90s": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE", None),
        (*MULTI_FORMULA, "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE", None),
    ],
    "input512_reuse00": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input512_reuse00"),
    ],
    "input512_reuse025": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input512_reuse025"),
    ],
    "input512_reuse05": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input512_reuse05"),
    ],
    "input2000_reuse00": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input2000_reuse00"),
    ],
    "input2000_reuse025": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input2000_reuse025"),
    ],
    "input2000_reuse05": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input2000_reuse05"),
    ],
    "input10000_reuse00": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input10000_reuse00"),
    ],
    "input10000_reuse025": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input10000_reuse025"),
    ],
    "input10000_reuse05": [
        (*MULTI_PRESSURE, "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE", MULTI_SWEEP_DIR / "input10000_reuse05"),
    ],
    "input10000_reuse05_180s": [
        (*MULTI_FORMULA, "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720", None),
    ],
}

SUBSETS = [
    ("All requests", lambda row: True),
    ("Redirected only", lambda row: int(float(row["rerouted"])) == 1),
    ("Not redirected only", lambda row: int(float(row["rerouted"])) == 0),
]

COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]

# (condition_key, title, results_dir, output_png_basename)
CONDITIONS = [
    ("prompt6000_90s", "Prompt 6000 / 90s (ShareGPT, reuse~50%)",
     EXPERIMENTS / "2026-07-21-add_gpu_utilization" / "results"),
    ("input512_reuse00", "Input 512 / reuse 0% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input512_reuse00"),
    ("input512_reuse025", "Input 512 / reuse 25% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input512_reuse025"),
    ("input512_reuse05", "Input 512 / reuse 50% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input512_reuse05"),
    ("input2000_reuse00", "Input 2000 / reuse 0% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input2000_reuse00"),
    ("input2000_reuse025", "Input 2000 / reuse 25% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input2000_reuse025"),
    ("input2000_reuse05", "Input 2000 / reuse 50% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input2000_reuse05"),
    ("input4000_rate2p5_reuse00", "Input 4000 / rate 2.5rps / reuse 0% (phase1, seed1)",
     EXPERIMENTS / "2026-07-16_ttft_component_regression" / "results" / "phase1" / "input4000_rate2p5_reuse00_seed1"),
    ("input6000_rate3p33_reuse00", "Input 6000 / rate 3.33rps / reuse 0% (phase1, seed1)",
     EXPERIMENTS / "2026-07-16_ttft_component_regression" / "results" / "phase1" / "input6000_rate3p33_reuse00_seed1"),
    ("input6000_rate3p33_reuse50", "Input 6000 / rate 3.33rps / reuse 50% (phase1, seed1)",
     EXPERIMENTS / "2026-07-16_ttft_component_regression" / "results" / "phase1" / "input6000_rate3p33_reuse50_seed1"),
    ("input8000_rate2p5_reuse00", "Input 8000 / rate 2.5rps / reuse 0% (phase1, seed1)",
     EXPERIMENTS / "2026-07-16_ttft_component_regression" / "results" / "phase1" / "input8000_rate2p5_reuse00_seed1"),
    ("input8000_rate2p5_reuse50", "Input 8000 / rate 2.5rps / reuse 50% (phase1, seed1)",
     EXPERIMENTS / "2026-07-16_ttft_component_regression" / "results" / "phase1" / "input8000_rate2p5_reuse50_seed1"),
    ("input10000_reuse00", "Input 10000 / reuse 0% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input10000_reuse00"),
    ("input10000_reuse025", "Input 10000 / reuse 25% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input10000_reuse025"),
    ("input10000_reuse05", "Input 10000 / reuse 50% / 90s",
     EXPERIMENTS / "2026-07-14_input_reuse_90s_sweep" / "results" / "input10000_reuse05"),
    ("input10000_reuse05_180s", "Input 10000 / reuse 50% / 180s",
     EXPERIMENTS / "2026-07-16_input10000_reuse05_180s_three_policy_良結果" / "results"),
]


def read_methods(key, results_dir):
    runs = []
    for label, name, color in METHODS:
        path = results_dir / name / "requests.csv"
        if not path.is_file():
            print(f"  skip {results_dir}/{name}: requests.csv is missing")
            continue
        with path.open(newline="") as file:
            runs.append((label, name, color, list(csv.DictReader(file))))
    for label, color, name, override_dir in MULTI_CANDIDATES.get(key, []):
        base_dir = override_dir if override_dir is not None else results_dir
        path = base_dir / name / "requests.csv"
        if not path.is_file():
            print(f"  skip {base_dir}/{name}: requests.csv is missing")
            continue
        with path.open(newline="") as file:
            runs.append((label, name, color, list(csv.DictReader(file))))
    return runs


def mean_ms(rows, column):
    return sum(float(row[column]) for row in rows) / len(rows) / 1e6


def breakdown(rows):
    total = mean_ms(rows, "e2e_ttft_ns")
    compute = mean_ms(rows, "prefill_service_ns")
    communication = mean_ms(rows, "communication_latency_ns")
    scheduler = mean_ms(rows, "queueing_before_ttft_ns")
    transfer = mean_ms(rows, "kv_migration_latency_ns")
    return {
        "Router queue": max(0.0, total - compute - communication - scheduler),
        "Scheduler queue": scheduler,
        "KV transfer": transfer,
        "Compute / prefill": compute,
        "RTT / other comm": max(0.0, communication - transfer),
        "Total": total,
    }


def subset_rows(rows, predicate):
    return [row for row in rows if predicate(row)]


def plot_condition(key, title, runs, csv_rows):
    figure, axes = plt.subplots(1, 3, figsize=(21, 6.4), sharex=True)
    maximum = 0.0
    panels = []
    for subset_label, predicate in SUBSETS:
        entries = []
        for method_label, method_name, color, rows in runs:
            selected = subset_rows(rows, predicate)
            values = breakdown(selected) if selected else None
            if values:
                maximum = max(maximum, values["Total"])
            entries.append((method_label, method_name, color, selected, values))
        panels.append((subset_label, entries))

    for axis, (subset_label, entries) in zip(axes, panels):
        positions = list(range(len(entries)))
        left = [0.0] * len(entries)
        for component, component_color in COMPONENTS:
            widths = [values[component] if values else 0.0
                      for _, _, _, _, values in entries]
            axis.barh(positions, widths, left=left, height=0.58,
                      color=component_color, edgecolor="#faf8f4", linewidth=1.2,
                      label=component)
            left = [old + width for old, width in zip(left, widths)]
        for position, (label, name, _, rows, values) in enumerate(entries):
            if values:
                axis.text(values["Total"] + max(maximum, 1.0) * 0.015, position,
                          f'{values["Total"]:.0f} ms\n(n={len(rows)})',
                          va="center", fontsize=8.5, fontweight="bold")
                csv_rows.append({
                    "condition": key,
                    "method": name,
                    "method_label": label,
                    "subset": subset_label,
                    "request_count": len(rows),
                    **values,
                })
            else:
                axis.text(max(maximum, 1.0) * 0.015, position, "no requests",
                          va="center", fontsize=9, color="#777777")
        axis.set_yticks(positions, [entry[0] for entry in entries], fontsize=9.5)
        axis.invert_yaxis()
        axis.set_title(subset_label, fontsize=13)
        axis.set_xlabel("mean E2E TTFT components (ms)")
        axis.set_xlim(0, max(maximum, 1.0) * 1.28)
        axis.grid(axis="x", color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)

    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=len(COMPONENTS), frameon=False)
    figure.suptitle(f"{title}: mean E2E TTFT breakdown by redirect status", fontsize=16)
    figure.tight_layout(rect=(0, 0.08, 1, 0.93))
    output = OUTPUT_DIR / f"{key}_ttft_breakdown.png"
    figure.savefig(output, dpi=170, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(figure)
    return output


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_rows = []
    generated = []
    for key, title, results_dir in CONDITIONS:
        print(f"{key}: {results_dir}")
        runs = read_methods(key, results_dir)
        if not runs:
            print(f"  SKIPPED (no method results found)")
            continue
        output = plot_condition(key, title, runs, csv_rows)
        generated.append(output)
        print(f"  wrote {output}")

    csv_output = OUTPUT_DIR / "all_conditions_ttft_breakdown.csv"
    fields = ["condition", "method", "method_label", "subset", "request_count",
              *[name for name, _ in COMPONENTS], "Total"]
    with csv_output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"wrote {csv_output}")
    print(f"Generated {len(generated)} figures.")


if __name__ == "__main__":
    main()
