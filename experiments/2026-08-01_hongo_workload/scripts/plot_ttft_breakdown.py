#!/usr/bin/env python3
"""Render TTFT breakdown and CDF charts from experiment requests.csv files.

This version avoids third-party dependencies and writes:
- analysis/<scenario>_ttft_breakdown.csv
- analysis/<scenario>_ttft_cdf.csv
- figures/<scenario>_ttft_breakdown.svg
- figures/<scenario>_ttft_cdf.svg
"""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HIDDEN_SIZE = 4096
FP_BYTES = 2
LINK_BANDWIDTH_GBPS = 16.0
LINK_LATENCY_NS = 20000.0

ARMS = [
    ("1: no_redirect", "1_no_redirect", 1),
    ("2: redirect_no_kv", "2_redirect_no_kv", 1),
    ("3: redirect_kv_nopp", "3_redirect_kv_nopp", 1),
    ("4: redirect_kv_pp2", "4_redirect_kv_pp2", 2),
    ("5: redirect_kv_pp2_c", "5_redirect_kv_pp2_c", 2),
]

COMPONENTS = [
    ("Router queue", "#c74b3a"),
    ("Scheduler queue", "#e79b37"),
    ("KV transfer", "#865bd6"),
    ("PP transfer (est.)", "#e0459b"),
    ("Compute / prefill", "#31866f"),
    ("RTT / other comm", "#4f83c2"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="peak_5x_seed1")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--analysis-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--figures-dir", type=Path, default=ROOT / "figures")
    parser.add_argument("--min-requests", type=int, default=1)
    parser.add_argument("--title", default="")
    parser.add_argument("--cdf-title", default="")
    return parser.parse_args()


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def estimate_pp_transfer_ms(input_tokens: float, pp_size: int) -> float:
    if pp_size <= 1:
        return 0.0
    boundary_bytes = input_tokens * HIDDEN_SIZE * FP_BYTES * (pp_size - 1)
    transfer_ns = boundary_bytes / LINK_BANDWIDTH_GBPS + LINK_LATENCY_NS * (pp_size - 1)
    return transfer_ns / 1e6


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def breakdown(rows: list[dict[str, str]], pp_size: int) -> dict[str, float]:
    comm_ms = mean([float(row["communication_latency_ns"]) / 1e6 for row in rows])
    kv_ms = mean([float(row["kv_migration_latency_ns"]) / 1e6 for row in rows])
    pp_ms = mean([estimate_pp_transfer_ms(float(row["input"]), pp_size) for row in rows])
    prefill_ms = mean([float(row["prefill_service_ns"]) / 1e6 for row in rows])
    scheduler_ms = mean([float(row["queueing_before_ttft_ns"]) / 1e6 for row in rows])
    ttft_ms = [float(row["e2e_ttft_ns"]) / 1e6 for row in rows]
    router_ms = mean([
        max(
            0.0,
            float(row["e2e_ttft_ns"])
            - float(row["prefill_service_ns"])
            - float(row["communication_latency_ns"])
            - float(row["queueing_before_ttft_ns"]),
        ) / 1e6
        for row in rows
    ])
    return {
        "Router queue": router_ms,
        "Scheduler queue": scheduler_ms,
        "KV transfer": kv_ms,
        "PP transfer (est.)": pp_ms,
        "Compute / prefill": max(0.0, prefill_ms - pp_ms),
        "RTT / other comm": max(0.0, comm_ms - kv_ms),
        "Total": mean(ttft_ms),
    }


def summarize_arm(
    label: str, rows: list[dict[str, str]], pp_size: int
) -> tuple[dict[str, float | int | str], list[float]]:
    ttft_ms = sorted(float(row["e2e_ttft_ns"]) / 1e6 for row in rows)
    redirects = sum(int(row.get("rerouted", "0") or "0") for row in rows)
    stats = breakdown(rows, pp_size)
    return {
        "arm": label,
        "requests": len(rows),
        "mean_ttft_ms": mean(ttft_ms),
        "p50_ttft_ms": percentile(ttft_ms, 0.50),
        "p95_ttft_ms": percentile(ttft_ms, 0.95),
        "p99_ttft_ms": percentile(ttft_ms, 0.99),
        "max_ttft_ms": max(ttft_ms) if ttft_ms else 0.0,
        "redirects": redirects,
        **stats,
    }, ttft_ms


def load_available(
    prefix: str, results_dir: Path, min_requests: int
) -> tuple[list[dict[str, float | int | str]], dict[str, list[float]], list[str]]:
    summaries = []
    cdf_data = {}
    notices = []
    for label, suffix, pp_size in ARMS:
        path = results_dir / f"{prefix}_{suffix}" / "requests.csv"
        try:
            rows = load_rows(path)
        except FileNotFoundError:
            notices.append(f"missing: {path}")
            continue
        except PermissionError:
            notices.append(f"permission denied: {path}")
            continue
        except OSError as exc:
            notices.append(f"{exc.__class__.__name__}: {path} ({exc})")
            continue
        if len(rows) < min_requests:
            notices.append(
                f"too few rows: {path} ({len(rows)} < {min_requests})"
            )
            continue
        summary, ttft_ms = summarize_arm(label, rows, pp_size)
        summaries.append(summary)
        cdf_data[label] = ttft_ms
    return summaries, cdf_data, notices


def write_csv(summary: list[dict[str, float | int | str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "arm", "requests", "mean_ttft_ms", "p50_ttft_ms", "p95_ttft_ms",
        "p99_ttft_ms", "max_ttft_ms", "redirects",
        "Router queue", "Scheduler queue", "KV transfer", "PP transfer (est.)",
        "Compute / prefill", "RTT / other comm", "Total",
    ]
    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)


def write_cdf_csv(cdf_data: dict[str, list[float]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["arm", "ttft_ms", "cdf"]
    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for arm, ttft_values in cdf_data.items():
            denom = len(ttft_values)
            for index, ttft_ms in enumerate(ttft_values, start=1):
                writer.writerow({
                    "arm": arm,
                    "ttft_ms": f"{ttft_ms:.9f}",
                    "cdf": f"{index / denom:.9f}",
                })


def fmt_ms(value: float) -> str:
    return f"{value:.0f} ms"


def text_color_for_fill(fill: str) -> str:
    fill = fill.lstrip("#")
    red = int(fill[0:2], 16)
    green = int(fill[2:4], 16)
    blue = int(fill[4:6], 16)
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return "#fffdf8" if luminance < 145 else "#1f1d1a"


def render_svg(summary: list[dict[str, float | int | str]], title: str, out_path: Path) -> None:
    row_h = 76
    top = 90
    left = 260
    right = 170
    bottom = 120
    legend_h = 90
    width = 1500
    height = top + bottom + legend_h + row_h * len(summary)
    plot_w = width - left - right
    max_total = max(float(row["Total"]) for row in summary)
    scale = plot_w / (max_total * 1.2 if max_total else 1.0)
    ticks = 6

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        f'<text x="{width / 2:.1f}" y="42" text-anchor="middle" font-family="Arial, sans-serif" font-size="28" font-weight="700">{html.escape(title)}</text>',
        f'<text x="{width / 2:.1f}" y="{height - 28}" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">mean E2E TTFT components (ms)</text>',
    ]

    for tick in range(ticks + 1):
        value = (max_total * 1.2) * tick / ticks if max_total else tick
        x = left + value * scale
        lines.append(f'<line x1="{x:.1f}" y1="{top - 10}" x2="{x:.1f}" y2="{height - bottom - legend_h + 10}" stroke="#d9d2c8" stroke-width="1"/>')
        lines.append(f'<text x="{x:.1f}" y="{height - bottom - legend_h + 38}" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" fill="#5f5b55">{value:.0f}</text>')

    for index, row in enumerate(summary):
        y = top + index * row_h
        bar_y = y
        bar_h = 42
        lines.append(f'<text x="{left - 18}" y="{bar_y + 28}" text-anchor="end" font-family="Arial, sans-serif" font-size="21" fill="#2b2926">{html.escape(str(row["arm"]))}</text>')
        start_x = left
        for component, color in COMPONENTS:
            value = float(row[component])
            rect_w = value * scale
            lines.append(
                f'<rect x="{start_x:.1f}" y="{bar_y:.1f}" width="{rect_w:.1f}" height="{bar_h}" '
                f'fill="{color}" stroke="#faf8f4" stroke-width="2"/>'
            )
            if rect_w >= 52:
                lines.append(
                    f'<text x="{start_x + rect_w / 2:.1f}" y="{bar_y + 27}" text-anchor="middle" '
                    f'font-family="Arial, sans-serif" font-size="15" font-weight="700" '
                    f'fill="{text_color_for_fill(color)}">{value:.1f}</text>'
                )
            start_x += rect_w
        lines.append(
            f'<text x="{left + float(row["Total"]) * scale + 16:.1f}" y="{bar_y + 28}" '
            f'font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#2b2926">{fmt_ms(float(row["Total"]))}</text>'
        )

    legend_y = height - bottom - 26
    legend_x = left
    for index, (component, color) in enumerate(COMPONENTS):
        x = legend_x + (index % 3) * 360
        y = legend_y + (index // 3) * 34
        lines.append(f'<rect x="{x}" y="{y - 16}" width="22" height="22" fill="{color}" rx="4"/>')
        lines.append(f'<text x="{x + 34}" y="{y + 1}" font-family="Arial, sans-serif" font-size="18" fill="#2b2926">{html.escape(component)}</text>')

    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))


def render_cdf_svg(
    cdf_data: dict[str, list[float]], title: str, out_path: Path
) -> None:
    top = 85
    left = 105
    right = 50
    bottom = 95
    legend_h = 85
    width = 1320
    height = 840
    plot_w = width - left - right
    plot_h = height - top - bottom - legend_h
    colors = {component: color for component, color in COMPONENTS}
    arm_colors = {
        "1: no_redirect": colors["Router queue"],
        "2: redirect_no_kv": colors["Scheduler queue"],
        "3: redirect_kv_nopp": colors["KV transfer"],
        "4: redirect_kv_pp2": colors["PP transfer (est.)"],
        "5: redirect_kv_pp2_c": colors["Compute / prefill"],
    }
    max_ttft = max(max(values) for values in cdf_data.values())
    x_max = max_ttft * 1.03 if max_ttft else 1.0

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        f'<text x="{width / 2:.1f}" y="42" text-anchor="middle" font-family="Arial, sans-serif" font-size="28" font-weight="700">{html.escape(title)}</text>',
        f'<text x="{width / 2:.1f}" y="{height - 26}" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">TTFT (ms)</text>',
        f'<text x="28" y="{top + plot_h / 2:.1f}" transform="rotate(-90 28 {top + plot_h / 2:.1f})" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">CDF</text>',
    ]

    for tick in range(6):
        cdf = tick / 5
        y = top + plot_h - cdf * plot_h
        lines.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#d9d2c8" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{left - 16}" y="{y + 6:.1f}" text-anchor="end" font-family="Arial, sans-serif" font-size="16" fill="#5f5b55">{cdf:.1f}</text>'
        )
    for tick in range(7):
        x_value = x_max * tick / 6
        x = left + plot_w * tick / 6
        lines.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="#d9d2c8" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 34}" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" fill="#5f5b55">{x_value:.0f}</text>'
        )

    for arm, ttft_values in cdf_data.items():
        color = arm_colors.get(arm, "#2d6a4f")
        points = []
        denom = len(ttft_values)
        for index, ttft_ms in enumerate(ttft_values, start=1):
            x = left + (ttft_ms / x_max) * plot_w
            y = top + plot_h - (index / denom) * plot_h
            points.append(f"{x:.1f},{y:.1f}")
        lines.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="4" stroke-linejoin="round" '
            f'stroke-linecap="round" points="{" ".join(points)}"/>'
        )

    legend_y = height - 70
    legend_x = left
    arm_items = list(cdf_data.keys())
    for index, arm in enumerate(arm_items):
        x = legend_x + (index % 3) * 350
        y = legend_y + (index // 3) * 28
        color = arm_colors.get(arm, "#2d6a4f")
        lines.append(f'<line x1="{x}" y1="{y}" x2="{x + 28}" y2="{y}" stroke="{color}" stroke-width="5" stroke-linecap="round"/>')
        lines.append(f'<text x="{x + 40}" y="{y + 6}" font-family="Arial, sans-serif" font-size="18" fill="#2b2926">{html.escape(arm)}</text>')

    lines.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))


def scenario_from_prefix(prefix: str) -> str:
    if prefix.endswith("_seed1"):
        return prefix[:-6]
    return prefix


def main() -> int:
    args = parse_args()
    summary, cdf_data, notices = load_available(
        args.prefix, args.results_dir, args.min_requests
    )
    if not summary:
        for notice in notices:
            print(notice)
        print("No readable arms found.")
        return 1

    scenario = scenario_from_prefix(args.prefix)
    csv_path = args.analysis_dir / f"{scenario}_ttft_breakdown.csv"
    svg_path = args.figures_dir / f"{scenario}_ttft_breakdown.svg"
    cdf_csv_path = args.analysis_dir / f"{scenario}_ttft_cdf.csv"
    cdf_svg_path = args.figures_dir / f"{scenario}_ttft_cdf.svg"
    title = args.title or f"Hongo {scenario}: five-arm TTFT breakdown"
    cdf_title = args.cdf_title or f"Hongo {scenario}: TTFT CDF"

    write_csv(summary, csv_path)
    write_cdf_csv(cdf_data, cdf_csv_path)
    render_svg(summary, title, svg_path)
    render_cdf_svg(cdf_data, cdf_title, cdf_svg_path)

    print(f"Saved summary to {csv_path}")
    print(f"Saved figure to {svg_path}")
    print(f"Saved CDF data to {cdf_csv_path}")
    print(f"Saved CDF figure to {cdf_svg_path}")
    for notice in notices:
        print(f"Skipped: {notice}")
    for row in summary:
        print(
            f'{row["arm"]}: mean={float(row["mean_ttft_ms"]):.3f} ms, '
            f'p95={float(row["p95_ttft_ms"]):.3f} ms, total={float(row["Total"]):.3f} ms'
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
