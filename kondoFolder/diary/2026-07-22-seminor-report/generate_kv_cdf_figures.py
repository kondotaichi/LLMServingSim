#!/usr/bin/env python3
"""Generate E2E TTFT CDF figures for the same conditions/methods as
generate_kv_breakdown_figures.py (same METHODS, MULTI_CANDIDATES, CONDITIONS,
and requests.csv sourcing, so the two figure sets stay directly comparable).
"""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from generate_kv_breakdown_figures import CONDITIONS, SUBSETS, read_methods


OUTPUT_DIR = Path(__file__).resolve().parent

# Reference percentiles drawn as recessive horizontal guides.
REFERENCE_QUANTILES = [0.5, 0.9, 0.95, 0.99]


def subset_rows(rows, predicate):
    return [row for row in rows if predicate(row)]


def ecdf_ms(rows):
    values = np.sort(np.array([float(row["e2e_ttft_ns"]) for row in rows]) / 1e6)
    fractions = (np.arange(1, len(values) + 1)) / len(values)
    return values, fractions


def quantiles_ms(values):
    return {f"p{int(q * 100)}": float(np.quantile(values, q)) for q in REFERENCE_QUANTILES} | {
        "max": float(values.max())
    }


def plot_condition(key, title, runs, csv_rows):
    figure, axes = plt.subplots(1, 3, figsize=(21, 6.4))
    panels = []
    for subset_label, predicate in SUBSETS:
        entries = []
        for method_label, method_name, color, rows in runs:
            selected = subset_rows(rows, predicate)
            entries.append((method_label, method_name, color, selected))
        panels.append((subset_label, entries))

    for axis, (subset_label, entries) in zip(axes, panels):
        any_data = False
        axis_max = 1.0
        axis_min = float("inf")
        for label, name, color, rows in entries:
            if not rows:
                continue
            any_data = True
            values, fractions = ecdf_ms(rows)
            axis_max = max(axis_max, values.max())
            axis_min = min(axis_min, values.min())
            axis.step(values, fractions, where="post", color=color, linewidth=2,
                      label=label, solid_capstyle="round")
            quantiles = quantiles_ms(values)
            csv_rows.append({
                "condition": key,
                "method": name,
                "method_label": label,
                "subset": subset_label,
                "request_count": len(rows),
                "p50_ms": quantiles["p50"],
                "p90_ms": quantiles["p90"],
                "p95_ms": quantiles["p95"],
                "p99_ms": quantiles["p99"],
                "max_ms": quantiles["max"],
            })

        if any_data:
            for q in REFERENCE_QUANTILES:
                axis.axhline(q, color="#c9c2b6", linewidth=0.8, linestyle=(0, (2, 2)), zorder=0)
                axis.text(0.995, q, f"p{int(q * 100)}", transform=axis.get_yaxis_transform(),
                          va="center", ha="right", fontsize=7.5, color="#8a8378")
            axis.set_xscale("log")
            axis.set_xlim(10 ** np.floor(np.log10(axis_min)), 10 ** np.ceil(np.log10(axis_max)))
        else:
            axis.text(0.5, 0.5, "no requests", transform=axis.transAxes,
                      ha="center", va="center", fontsize=9, color="#777777")
            axis.set_xticks([])

        axis.set_ylim(0, 1.02)
        axis.set_title(subset_label, fontsize=13)
        axis.set_xlabel("E2E TTFT (ms, log scale)")
        axis.set_ylabel("cumulative fraction of requests")
        axis.grid(axis="y", color="#e3ded4", linewidth=0.7)
        axis.grid(axis="x", which="major", color="#e3ded4", linewidth=0.7)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)

    handles, labels = axes[0].get_legend_handles_labels()
    if not handles:
        for axis in axes:
            handles, labels = axis.get_legend_handles_labels()
            if handles:
                break
    figure.legend(handles, labels, loc="lower center", ncol=max(len(handles), 1), frameon=False)
    figure.suptitle(f"{title}: E2E TTFT CDF by redirect status", fontsize=16)
    figure.tight_layout(rect=(0, 0.08, 1, 0.93))
    output = OUTPUT_DIR / f"{key}_ttft_cdf.png"
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
            print("  SKIPPED (no method results found)")
            continue
        output = plot_condition(key, title, runs, csv_rows)
        generated.append(output)
        print(f"  wrote {output}")

    csv_output = OUTPUT_DIR / "all_conditions_ttft_cdf_quantiles.csv"
    fields = ["condition", "method", "method_label", "subset", "request_count",
              "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms"]
    with csv_output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"wrote {csv_output}")
    print(f"Generated {len(generated)} figures.")


if __name__ == "__main__":
    main()
