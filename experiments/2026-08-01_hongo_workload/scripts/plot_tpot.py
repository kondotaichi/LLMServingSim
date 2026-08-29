#!/usr/bin/env python3
"""Summarize and plot per-request TPOT for the Hongo experiment arms."""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ARMS = [
    ("1: no_redirect", "1_no_redirect", "#c74b3a"),
    ("2: redirect_no_kv", "2_redirect_no_kv", "#e79b37"),
    ("3: redirect_kv_nopp", "3_redirect_kv_nopp", "#865bd6"),
    ("4: redirect_kv_pp2", "4_redirect_kv_pp2", "#e0459b"),
    ("5: redirect_kv_pp2_c", "5_redirect_kv_pp2_c", "#31866f"),
    ("6: redirect_kv_nopp_c", "6_redirect_kv_nopp_c", "#4f83c2"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default="peak_5x_seed1")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--analysis-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--figures-dir", type=Path, default=ROOT / "figures")
    parser.add_argument("--min-requests", type=int, default=1)
    parser.add_argument("--max-arm", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--title", default="")
    parser.add_argument("--cdf-title", default="")
    return parser.parse_args()


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    weight = index - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def scenario_from_prefix(prefix: str) -> str:
    return prefix[:-6] if prefix.endswith("_seed1") else prefix


def load_available(
    prefix: str, results_dir: Path, min_requests: int, max_arm: int
) -> tuple[list[dict[str, float | int | str]], dict[str, list[float]], list[str]]:
    summaries = []
    cdf_data = {}
    notices = []
    for label, suffix, _color in ARMS[:max_arm]:
        path = results_dir / f"{prefix}_{suffix}" / "requests.csv"
        try:
            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
        except (FileNotFoundError, PermissionError, OSError) as exc:
            notices.append(f"{exc.__class__.__name__}: {path}")
            continue
        if len(rows) < min_requests:
            notices.append(f"too few rows: {path} ({len(rows)} < {min_requests})")
            continue
        tpot_ms = sorted(float(row["TPOT"]) / 1e6 for row in rows)
        summaries.append({
            "arm": label,
            "requests": len(rows),
            "mean_tpot_ms": mean(tpot_ms),
            "p50_tpot_ms": percentile(tpot_ms, 0.50),
            "p95_tpot_ms": percentile(tpot_ms, 0.95),
            "p99_tpot_ms": percentile(tpot_ms, 0.99),
            "max_tpot_ms": max(tpot_ms),
            "redirects": sum(int(row.get("rerouted", "0") or "0") for row in rows),
        })
        cdf_data[label] = tpot_ms
    return summaries, cdf_data, notices


def write_summary_csv(summary: list[dict[str, float | int | str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "arm", "requests", "mean_tpot_ms", "p50_tpot_ms", "p95_tpot_ms",
        "p99_tpot_ms", "max_tpot_ms", "redirects",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)


def write_cdf_csv(cdf_data: dict[str, list[float]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["arm", "tpot_ms", "cdf"])
        writer.writeheader()
        for arm, values in cdf_data.items():
            for index, value in enumerate(values, start=1):
                writer.writerow({
                    "arm": arm,
                    "tpot_ms": f"{value:.9f}",
                    "cdf": f"{index / len(values):.9f}",
                })


def render_summary_svg(
    summary: list[dict[str, float | int | str]], title: str, path: Path
) -> None:
    width, left, right, top, row_h = 1320, 270, 100, 90, 76
    height = top + row_h * len(summary) + 105
    plot_w = width - left - right
    x_max = max(float(row["p95_tpot_ms"]) for row in summary) * 1.2 or 1.0
    colors = {label: color for label, _suffix, color in ARMS}
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        f'<text x="{width / 2}" y="42" text-anchor="middle" font-family="Arial, sans-serif" font-size="28" font-weight="700">{html.escape(title)}</text>',
    ]
    for tick in range(6):
        value = x_max * tick / 5
        x = left + plot_w * tick / 5
        lines.append(f'<line x1="{x:.1f}" y1="{top - 10}" x2="{x:.1f}" y2="{height - 80}" stroke="#d9d2c8"/>')
        lines.append(f'<text x="{x:.1f}" y="{height - 48}" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" fill="#5f5b55">{value:.0f}</text>')
    for index, row in enumerate(summary):
        y = top + index * row_h
        label = str(row["arm"])
        p50 = float(row["p50_tpot_ms"])
        p95 = float(row["p95_tpot_ms"])
        lines.append(f'<text x="{left - 18}" y="{y + 29}" text-anchor="end" font-family="Arial, sans-serif" font-size="21">{html.escape(label)}</text>')
        lines.append(f'<rect x="{left}" y="{y}" width="{p95 / x_max * plot_w:.1f}" height="42" fill="{colors[label]}" opacity="0.82" rx="4"/>')
        lines.append(f'<line x1="{left + p50 / x_max * plot_w:.1f}" y1="{y - 4}" x2="{left + p50 / x_max * plot_w:.1f}" y2="{y + 46}" stroke="#1f1d1a" stroke-width="4"/>')
        lines.append(f'<text x="{left + p95 / x_max * plot_w + 12:.1f}" y="{y + 28}" font-family="Arial, sans-serif" font-size="17">p95 {p95:.1f} ms</text>')
    lines.append(f'<text x="{width / 2}" y="{height - 16}" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">TPOT (ms); bar = p95, marker = p50</text>')
    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def render_cdf_svg(cdf_data: dict[str, list[float]], title: str, path: Path) -> None:
    width, height, left, right, top, bottom = 1320, 800, 105, 50, 85, 110
    plot_w, plot_h = width - left - right, height - top - bottom
    x_max = max(max(values) for values in cdf_data.values()) * 1.03 or 1.0
    colors = {label: color for label, _suffix, color in ARMS}
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        f'<text x="{width / 2}" y="42" text-anchor="middle" font-family="Arial, sans-serif" font-size="28" font-weight="700">{html.escape(title)}</text>',
    ]
    for tick in range(6):
        cdf = tick / 5
        y = top + plot_h * (1 - cdf)
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#d9d2c8"/>')
        lines.append(f'<text x="{left - 16}" y="{y + 6:.1f}" text-anchor="end" font-family="Arial, sans-serif" font-size="16">{cdf:.1f}</text>')
        x = left + plot_w * tick / 5
        value = x_max * tick / 5
        lines.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="#d9d2c8"/>')
        lines.append(f'<text x="{x:.1f}" y="{top + plot_h + 32}" text-anchor="middle" font-family="Arial, sans-serif" font-size="16">{value:.0f}</text>')
    for arm, values in cdf_data.items():
        points = [
            f"{left + value / x_max * plot_w:.1f},{top + plot_h * (1 - index / len(values)):.1f}"
            for index, value in enumerate(values, start=1)
        ]
        lines.append(f'<polyline fill="none" stroke="{colors[arm]}" stroke-width="4" points="{" ".join(points)}"/>')
    for index, arm in enumerate(cdf_data):
        x = left + (index % 3) * 350
        y = height - 48 + (index // 3) * 25
        lines.append(f'<line x1="{x}" y1="{y}" x2="{x + 28}" y2="{y}" stroke="{colors[arm]}" stroke-width="5"/>')
        lines.append(f'<text x="{x + 40}" y="{y + 6}" font-family="Arial, sans-serif" font-size="17">{html.escape(arm)}</text>')
    lines.append(f'<text x="{width / 2}" y="{height - 75}" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">TPOT (ms)</text>')
    lines.append(f'<text x="28" y="{top + plot_h / 2}" transform="rotate(-90 28 {top + plot_h / 2})" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">CDF</text>')
    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> int:
    args = parse_args()
    summary, cdf_data, notices = load_available(
        args.prefix, args.results_dir, args.min_requests, args.max_arm
    )
    if not summary:
        print("\n".join(notices + ["No readable arms found."]))
        return 1
    scenario = scenario_from_prefix(args.prefix)
    summary_path = args.analysis_dir / f"{scenario}_tpot_summary.csv"
    cdf_path = args.analysis_dir / f"{scenario}_tpot_cdf.csv"
    summary_svg = args.figures_dir / f"{scenario}_tpot_summary.svg"
    cdf_svg = args.figures_dir / f"{scenario}_tpot_cdf.svg"
    write_summary_csv(summary, summary_path)
    write_cdf_csv(cdf_data, cdf_path)
    render_summary_svg(summary, args.title or f"Hongo {scenario}: TPOT summary", summary_svg)
    render_cdf_svg(cdf_data, args.cdf_title or f"Hongo {scenario}: TPOT CDF", cdf_svg)
    for path in (summary_path, cdf_path, summary_svg, cdf_svg):
        print(f"Saved {path}")
    for notice in notices:
        print(f"Skipped: {notice}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
