#!/usr/bin/env python3
"""Compare the three proposal_rate4 arms and plot TTFT and energy estimates."""

from __future__ import annotations

import csv
import html
from collections import Counter
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/proposal_rate4"
OUTPUT = ROOT / "analysis/proposal_rate4"
ARMS = (
    ("all_tokyo_baseline", "All Tokyo", "#4c78a8"),
    ("kg_baseline", "Tokyo + Kagoshima", "#f58518"),
    ("kg_proposed", "Tokyo + Kagoshima + prewarm", "#54a24b"),
)
COMPONENTS = (
    ("communication_latency_ns", "Communication", "#72a0c1"),
    ("queueing_before_ttft_ns", "Queueing", "#e7a34b"),
    ("prefill_service_ns", "Prefill service", "#4c956c"),
)
IDLE_W = 50.0
TDP_W = 450.0
TOKYO_YEN_KWH = 23.0
KAGOSHIMA_YEN_KWH = 14.7


def text_color_for_fill(fill: str) -> str:
    fill = fill.lstrip("#")
    red = int(fill[0:2], 16)
    green = int(fill[2:4], 16)
    blue = int(fill[4:6], 16)
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return "#fffdf8" if luminance < 145 else "#1f1d1a"


def render_ttft_breakdown_svg(data: list[tuple], out_path: Path) -> None:
    """Render the attribution as a publication-friendly horizontal SVG."""
    components = [
        (key, label, color) for key, label, color in COMPONENTS
    ]
    rows = []
    for _, label, _, requests, _ in data:
        values = [float(np.mean(values_ms(requests, key))) for key, _, _ in components]
        rows.append((label, values, float(np.mean(values_ms(requests, "e2e_ttft_ns")))))

    width = 1500
    left = 360
    right = 190
    top = 135
    row_h = 92
    bar_h = 46
    legend_y = top + row_h * len(rows) + 50
    height = legend_y + 95
    plot_w = width - left - right
    max_value = max(max(sum(values), observed) for _, values, observed in rows)
    axis_max = max_value * 1.18 if max_value else 1.0
    scale = plot_w / axis_max
    plot_bottom = top + row_h * len(rows) - 26

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        f'<text x="{width / 2:.1f}" y="43" text-anchor="middle" font-family="Arial, sans-serif" font-size="28" font-weight="700">Rate-4 workload: mean TTFT attribution</text>',
        f'<text x="{width / 2:.1f}" y="72" text-anchor="middle" font-family="Arial, sans-serif" font-size="17" fill="#5f5b55">Components can overlap; diamond shows observed E2E TTFT</text>',
    ]

    for tick in range(6):
        value = axis_max * tick / 5
        x = left + value * scale
        lines.append(
            f'<line x1="{x:.1f}" y1="{top - 15}" x2="{x:.1f}" y2="{plot_bottom}" stroke="#d9d2c8" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{x:.1f}" y="{plot_bottom + 28}" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" fill="#5f5b55">{value:.0f}</text>'
        )

    for index, (label, values, observed) in enumerate(rows):
        bar_y = top + index * row_h
        center_y = bar_y + bar_h / 2
        lines.append(
            f'<text x="{left - 22}" y="{center_y + 7:.1f}" text-anchor="end" font-family="Arial, sans-serif" font-size="21" fill="#2b2926">{html.escape(label)}</text>'
        )
        start_x = left
        for value, (_, _, color) in zip(values, components):
            rect_w = value * scale
            lines.append(
                f'<rect x="{start_x:.1f}" y="{bar_y:.1f}" width="{rect_w:.1f}" height="{bar_h}" fill="{color}" stroke="#faf8f4" stroke-width="2"/>'
            )
            if rect_w >= 56:
                lines.append(
                    f'<text x="{start_x + rect_w / 2:.1f}" y="{center_y + 6:.1f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="15" font-weight="700" fill="{text_color_for_fill(color)}">{value:.1f}</text>'
                )
            start_x += rect_w

        lines.append(
            f'<text x="{start_x + 14:.1f}" y="{center_y + 7:.1f}" font-family="Arial, sans-serif" font-size="19" font-weight="700" fill="#2b2926">{sum(values):.0f} ms</text>'
        )
        marker_x = left + observed * scale
        marker_y = bar_y - 10
        diamond = (
            f'{marker_x:.1f},{marker_y - 10:.1f} '
            f'{marker_x + 10:.1f},{marker_y:.1f} '
            f'{marker_x:.1f},{marker_y + 10:.1f} '
            f'{marker_x - 10:.1f},{marker_y:.1f}'
        )
        lines.append(f'<polygon points="{diamond}" fill="#222222"/>')
        lines.append(
            f'<text x="{marker_x:.1f}" y="{marker_y - 16:.1f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" font-weight="700" fill="#222222">E2E {observed:.1f} ms</text>'
        )

    legend_items = [(label, color) for _, label, color in components]
    legend_items.append(("Observed E2E TTFT", "#222222"))
    item_w = 300
    legend_start = (width - item_w * len(legend_items)) / 2
    for index, (label, color) in enumerate(legend_items):
        x = legend_start + index * item_w
        if label == "Observed E2E TTFT":
            cx = x + 11
            lines.append(
                f'<polygon points="{cx},{legend_y - 12} {cx + 10},{legend_y - 2} {cx},{legend_y + 8} {cx - 10},{legend_y - 2}" fill="{color}"/>'
            )
        else:
            lines.append(f'<rect x="{x}" y="{legend_y - 14}" width="22" height="22" fill="{color}" rx="4"/>')
        lines.append(
            f'<text x="{x + 34}" y="{legend_y + 3}" font-family="Arial, sans-serif" font-size="18" fill="#2b2926">{html.escape(label)}</text>'
        )
    lines.append(
        f'<text x="{width / 2:.1f}" y="{height - 20}" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">mean TTFT components (ms)</text>'
    )
    lines.append("</svg>")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def values_ms(rows: list[dict[str, str]], key: str) -> np.ndarray:
    return np.asarray([float(row[key]) / 1e6 for row in rows])


def energy(rows: list[dict[str, str]], split: bool) -> dict[str, float]:
    """Integrate the linear idle-to-TDP model over each recorded GPU window."""
    joules = cost_yen = busy_ns = observed_ns = 0.0
    for row in rows:
        duration_ns = float(row["window_duration_ns"])
        this_busy_ns = float(row["busy_time_ns"])
        gpu_id = int(row["gpu_id"])
        rate = KAGOSHIMA_YEN_KWH if split and gpu_id >= 6 else TOKYO_YEN_KWH
        this_joules = (IDLE_W * duration_ns + (TDP_W - IDLE_W) * this_busy_ns) / 1e9
        joules += this_joules
        cost_yen += this_joules / 3.6e6 * rate
        busy_ns += this_busy_ns
        observed_ns += duration_ns
    return {
        "energy_wh": joules / 3600,
        "cost_yen": cost_yen,
        "recorded_util_pct": 100 * busy_ns / observed_ns,
        "gpu_observation_hours": observed_ns / 1e9 / 3600,
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    data = []
    for arm, label, color in ARMS:
        rows = read_csv(RESULTS / arm / "requests.csv")
        util = read_csv(RESULTS / arm / "gpu_utilization_timeseries.csv")
        data.append((arm, label, color, rows, energy(util, arm != "all_tokyo_baseline")))

    summary_rows = []
    baseline_mean = float(np.mean(values_ms(data[0][3], "e2e_ttft_ns")))
    baseline_energy = data[0][4]["energy_wh"]
    for arm, label, _, rows, power in data:
        ttft = values_ms(rows, "e2e_ttft_ns")
        component_means = {key: float(np.mean(values_ms(rows, key))) for key, _, _ in COMPONENTS}
        bottlenecks = Counter(row["ttft_bottleneck"] for row in rows)
        redirects = sum(str(row.get("rerouted", "")).lower() in ("1", "true") for row in rows)
        hits = sum(str(row.get("proactive_kv_prewarm_hit", "")).lower() in ("1", "true") for row in rows)
        summary_rows.append({
            "arm": arm, "label": label, "n": len(rows),
            "mean_ttft_ms": float(np.mean(ttft)), "p50_ttft_ms": float(np.percentile(ttft, 50)),
            "p95_ttft_ms": float(np.percentile(ttft, 95)), "p99_ttft_ms": float(np.percentile(ttft, 99)),
            "mean_vs_all_tokyo_pct": (float(np.mean(ttft)) / baseline_mean - 1) * 100,
            "mean_communication_ms": component_means["communication_latency_ns"],
            "mean_queueing_ms": component_means["queueing_before_ttft_ns"],
            "mean_prefill_ms": component_means["prefill_service_ns"],
            "prefill_bottleneck_pct": 100 * bottlenecks["prefill"] / len(rows),
            "queueing_bottleneck_pct": 100 * bottlenecks["queueing"] / len(rows),
            "communication_bottleneck_pct": 100 * bottlenecks["communication"] / len(rows),
            "redirect_count": redirects, "prewarm_hit_count": hits,
            **power,
            "energy_vs_all_tokyo_pct": (power["energy_wh"] / baseline_energy - 1) * 100,
        })

    with (OUTPUT / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=summary_rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(summary_rows)

    fig, axis = plt.subplots(figsize=(10, 5.8))
    x = np.arange(len(data))
    bottom = np.zeros(len(data))
    for key, component_label, color in COMPONENTS:
        heights = np.asarray([np.mean(values_ms(rows, key)) for _, _, _, rows, _ in data])
        axis.bar(x, heights, bottom=bottom, label=component_label, color=color)
        for xpos, base, height in zip(x, bottom, heights):
            axis.text(xpos, base + height / 2, f"{height:.1f}", ha="center", va="center", fontsize=9)
        bottom += heights
    e2e_means = np.asarray([np.mean(values_ms(rows, "e2e_ttft_ns")) for _, _, _, rows, _ in data])
    axis.scatter(x, e2e_means, marker="D", s=55, color="#222222", zorder=5,
                 label="Observed E2E TTFT")
    for xpos, total in zip(x, e2e_means):
        axis.text(xpos, total + 18, f"E2E {total:.1f} ms", ha="center", fontweight="bold")
    axis.set_xticks(x, [label for _, label, _, _, _ in data])
    axis.set_ylabel("Mean attributed time (ms)")
    axis.set_title("TTFT attribution: rate-4 workload (components can overlap)")
    axis.legend(frameon=False, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.0))
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUTPUT / "ttft_breakdown.png", dpi=200)
    plt.close(fig)
    render_ttft_breakdown_svg(data, OUTPUT / "ttft_breakdown.svg")

    fig, axis = plt.subplots(figsize=(9, 5.8))
    for _, label, color, rows, _ in data:
        ttft = np.sort(values_ms(rows, "e2e_ttft_ns"))
        axis.step(ttft, np.arange(1, len(ttft) + 1) / len(ttft), where="post", label=label, color=color, linewidth=2)
    axis.set_xlabel("E2E TTFT (ms)")
    axis.set_ylabel("CDF")
    axis.set_ylim(0, 1.01)
    axis.set_title("E2E TTFT CDF: rate-4 workload")
    axis.grid(alpha=0.25)
    axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUTPUT / "ttft_cdf.png", dpi=200)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(9, 5.8))
    energy_values = [power["energy_wh"] for _, _, _, _, power in data]
    bars = axis.bar(x, energy_values, color=[color for _, _, color, _, _ in data])
    axis.bar_label(bars, labels=[f"{value:.1f} Wh" for value in energy_values], padding=4)
    axis.set_xticks(x, [label for _, label, _, _, _ in data])
    axis.set_ylabel("Estimated GPU energy (Wh)")
    axis.set_title("GPU energy estimate (50 W idle, 450 W at 100% utilization)")
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUTPUT / "energy_estimate.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    main()
