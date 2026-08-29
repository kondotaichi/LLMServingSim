#!/usr/bin/env python3
"""Plot the number of unique users whose E2E TTFT meets an SLA."""

from __future__ import annotations

import argparse
import csv
import html
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOADS = ["busy_hour", *[f"peak_{value}x" for value in range(2, 11)]]
ARMS = [
    ("1: no redirect", "1_no_redirect", "#c74b3a"),
    ("2: redirect, no KV", "2_redirect_no_kv", "#e79b37"),
    ("3: redirect + KV, PP1", "3_redirect_kv_nopp", "#865bd6"),
    ("4: redirect + KV, PP2", "4_redirect_kv_pp2", "#e0459b"),
    ("5: redirect + KV, PP2-C", "5_redirect_kv_pp2_c", "#31866f"),
    ("6: redirect + KV, PP1-C", "6_redirect_kv_nopp_c", "#4f83c2"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--analysis-dir", type=Path, default=ROOT / "analysis")
    parser.add_argument("--figures-dir", type=Path, default=ROOT / "figures")
    parser.add_argument("--threshold-seconds", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=1)
    return parser.parse_args()


def load_sort_key(load: str) -> int:
    if load == "busy_hour":
        return 1
    match = re.fullmatch(r"peak_(\d+)x", load)
    return int(match.group(1)) if match else 10_000


def load_label(load: str) -> str:
    if load == "busy_hour":
        return "Busy hour"
    return load.removeprefix("peak_").upper()


def summarize(path: Path, threshold_ns: float) -> dict[str, int | float]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    per_user: dict[str, list[bool]] = {}
    for row in rows:
        meets_sla = float(row["e2e_ttft_ns"]) <= threshold_ns
        per_user.setdefault(row["user_id"], []).append(meets_sla)

    qualifying_requests = sum(sum(outcomes) for outcomes in per_user.values())
    return {
        "requests": len(rows),
        "total_users": len(per_user),
        "users_with_qualifying_request": sum(any(values) for values in per_user.values()),
        "users_all_requests_qualify": sum(all(values) for values in per_user.values()),
        "qualifying_requests": qualifying_requests,
        "qualifying_request_rate": qualifying_requests / len(rows) if rows else 0.0,
    }


def collect(results_dir: Path, seed: int, threshold_ns: float) -> list[dict[str, str | int | float]]:
    data = []
    for load in sorted(LOADS, key=load_sort_key):
        for arm, suffix, _color in ARMS:
            path = results_dir / f"{load}_seed{seed}_{suffix}" / "requests.csv"
            if not path.exists():
                print(f"Skipped missing result: {path}")
                continue
            data.append({"load": load, "arm": arm, **summarize(path, threshold_ns)})
    return data


def write_csv(rows: list[dict[str, str | int | float]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "load", "arm", "requests", "total_users",
        "users_with_qualifying_request", "users_all_requests_qualify",
        "qualifying_requests", "qualifying_request_rate",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_svg(
    rows: list[dict[str, str | int | float]], threshold_seconds: float, path: Path
) -> None:
    top, left, right, bottom, legend_h = 100, 105, 50, 100, 145
    width, height = 1400, 900
    plot_w = width - left - right
    plot_h = height - top - bottom - legend_h
    loads = sorted({str(row["load"]) for row in rows}, key=load_sort_key)
    max_users = max(int(row["total_users"]) for row in rows)
    row_by_key = {(str(row["load"]), str(row["arm"])): row for row in rows}

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f4"/>',
        f'<text x="{width / 2:.1f}" y="45" text-anchor="middle" font-family="Arial, sans-serif" font-size="30" font-weight="700">Users served within {threshold_seconds:g} s TTFT</text>',
        f'<text x="{width / 2:.1f}" y="77" text-anchor="middle" font-family="Arial, sans-serif" font-size="17" fill="#5f5b55">Unique users with at least one qualifying request</text>',
        f'<text x="{width / 2:.1f}" y="{height - 28}" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">Offered load</text>',
        f'<text x="28" y="{top + plot_h / 2:.1f}" transform="rotate(-90 28 {top + plot_h / 2:.1f})" text-anchor="middle" font-family="Arial, sans-serif" font-size="20">Qualifying users</text>',
    ]

    for tick in range(7):
        value = max_users * tick / 6
        y = top + plot_h - plot_h * tick / 6
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#d9d2c8"/>')
        lines.append(f'<text x="{left - 15}" y="{y + 6:.1f}" text-anchor="end" font-family="Arial, sans-serif" font-size="16" fill="#5f5b55">{value:.0f}</text>')

    x_positions: dict[str, float] = {}
    for index, load in enumerate(loads):
        x = left + plot_w * index / max(1, len(loads) - 1)
        x_positions[load] = x
        lines.append(f'<text x="{x:.1f}" y="{top + plot_h + 35}" text-anchor="middle" font-family="Arial, sans-serif" font-size="16" fill="#5f5b55">{html.escape(load_label(load))}</text>')

    for arm, _suffix, color in ARMS:
        points = []
        values = []
        for load in loads:
            row = row_by_key.get((load, arm))
            if row is None:
                continue
            value = int(row["users_with_qualifying_request"])
            x = x_positions[load]
            y = top + plot_h - value / max_users * plot_h
            points.append(f"{x:.1f},{y:.1f}")
            values.append((x, y, value))
        if len(points) > 1:
            lines.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="4" stroke-linejoin="round"/>')
        for x, y, value in values:
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{color}" stroke="#faf8f4" stroke-width="2"><title>{html.escape(arm)}: {value} users</title></circle>')

    legend_y = top + plot_h + 85
    for index, (arm, _suffix, color) in enumerate(ARMS):
        x = left + (index % 3) * 410
        y = legend_y + (index // 3) * 34
        lines.append(f'<line x1="{x}" y1="{y}" x2="{x + 30}" y2="{y}" stroke="{color}" stroke-width="5"/>')
        lines.append(f'<circle cx="{x + 15}" cy="{y}" r="5" fill="{color}"/>')
        lines.append(f'<text x="{x + 42}" y="{y + 6}" font-family="Arial, sans-serif" font-size="17" fill="#2b2926">{html.escape(arm)}</text>')

    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def main() -> int:
    args = parse_args()
    threshold_ns = args.threshold_seconds * 1e9
    rows = collect(args.results_dir, args.seed, threshold_ns)
    if not rows:
        print("No results found.")
        return 1

    suffix = f"{args.threshold_seconds:g}s".replace(".", "p")
    csv_path = args.analysis_dir / f"capacity_ttft_{suffix}.csv"
    svg_path = args.figures_dir / f"capacity_ttft_{suffix}.svg"
    write_csv(rows, csv_path)
    render_svg(rows, args.threshold_seconds, svg_path)
    print(f"Saved capacity data to {csv_path}")
    print(f"Saved capacity figure to {svg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
