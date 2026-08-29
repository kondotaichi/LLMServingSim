#!/usr/bin/env python3
"""Find the first peak-load point where TTFT or TPOT exceeds its SLA."""

from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis"
LOADS = range(2, 11)
METRICS = [
    ("TTFT", "mean", "mean_ttft_ms", 2000.0),
    ("TTFT", "p95", "p95_ttft_ms", 2000.0),
    ("TPOT", "mean", "mean_tpot_ms", 100.0),
    ("TPOT", "p95", "p95_tpot_ms", 100.0),
]


def load_data() -> dict[str, list[tuple[int, dict[str, str]]]]:
    by_arm: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for load in LOADS:
        with (ANALYSIS / f"peak_{load}x_ttft_breakdown.csv").open(newline="") as handle:
            ttft_rows = list(csv.DictReader(handle))
        with (ANALYSIS / f"peak_{load}x_tpot_summary.csv").open(newline="") as handle:
            tpot_rows = list(csv.DictReader(handle))
        tpot_by_arm = {row["arm"]: row for row in tpot_rows}
        for ttft_row in ttft_rows:
            arm = ttft_row["arm"]
            by_arm.setdefault(arm, []).append(
                (load, {**ttft_row, **tpot_by_arm[arm]})
            )
    return by_arm


def crossing_row(
    arm: str,
    rows: list[tuple[int, dict[str, str]]],
    metric: str,
    statistic: str,
    column: str,
    threshold: float,
) -> dict[str, str | float | int]:
    values = [(load, float(row[column])) for load, row in rows]
    first_index = next(
        (index for index, (_load, value) in enumerate(values) if value > threshold),
        None,
    )
    if first_index is None:
        return {
            "arm": arm,
            "metric": metric,
            "statistic": statistic,
            "threshold_ms": threshold,
            "first_exceed_load_x": "",
            "first_exceed_value_ms": "",
            "interpolated_crossing_load_x": "",
            "status": f"not exceeded through 10x ({values[-1][1]:.3f} ms at 10x)",
            "remains_exceeded_after_first": "",
        }

    load, value = values[first_index]
    interpolated: str | float = ""
    if first_index > 0:
        previous_load, previous_value = values[first_index - 1]
        if value != previous_value:
            interpolated = previous_load + (
                (threshold - previous_value) / (value - previous_value)
            ) * (load - previous_load)
    remains_exceeded = all(v > threshold for _x, v in values[first_index:])
    return {
        "arm": arm,
        "metric": metric,
        "statistic": statistic,
        "threshold_ms": threshold,
        "first_exceed_load_x": load,
        "first_exceed_value_ms": value,
        "interpolated_crossing_load_x": interpolated,
        "status": "exceeded",
        "remains_exceeded_after_first": str(remains_exceeded).lower(),
    }


def write_csv(rows: list[dict[str, str | float | int]], path: Path) -> None:
    fields = [
        "arm", "metric", "statistic", "threshold_ms",
        "first_exceed_load_x", "first_exceed_value_ms",
        "interpolated_crossing_load_x", "status",
        "remains_exceeded_after_first",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def display_crossing(row: dict[str, str | float | int]) -> str:
    if row["status"] != "exceeded":
        return "10xまで未超過"
    observed = f'{row["first_exceed_load_x"]}x'
    interpolated = row["interpolated_crossing_load_x"]
    if interpolated != "":
        observed += f'（補間 ≈ {float(interpolated):.2f}x）'
    if row["remains_exceeded_after_first"] == "false":
        observed += "*"
    return observed


def write_report(rows: list[dict[str, str | float | int]], path: Path) -> None:
    by_key = {
        (str(row["arm"]), str(row["metric"]), str(row["statistic"])): row
        for row in rows
    }
    arms = list(dict.fromkeys(str(row["arm"]) for row in rows))
    lines = [
        "# TTFT・TPOT SLA超過点（peak 2x–10x）",
        "",
        "閾値はTTFT 2,000 ms、TPOT 100 ms/token。`最初の観測超過倍率`を主結果とし、直前の負荷点との線形補間値を括弧内に示す。補間は負荷点間の近似であり、厳密な飽和点ではない。",
        "",
        "| 手法 | mean TTFT >2s | p95 TTFT >2s | mean TPOT >100ms | p95 TPOT >100ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for arm in arms:
        cells = [
            display_crossing(by_key[(arm, "TTFT", "mean")]),
            display_crossing(by_key[(arm, "TTFT", "p95")]),
            display_crossing(by_key[(arm, "TPOT", "mean")]),
            display_crossing(by_key[(arm, "TPOT", "p95")]),
        ]
        lines.append(f"| {arm} | " + " | ".join(cells) + " |")
    lines.extend([
        "",
        "\\* p95 TPOTは非単調。手法3は6xで初回超過後、8xで87.64 msまで下がり9xで再超過する。手法6も7xで初回超過後、8xで87.64 msまで下がり9xで再超過する。",
        "",
        "## 要点",
        "",
        "- mean TPOTは全手法で10xまで100 ms/tokenを超えない。",
        "- mean TTFTを10xまで2秒未満に保つのは手法4と5。10xでそれぞれ約1.908秒、1.907秒。",
        "- p95 TTFTは全手法で10xまでに2秒を超える。最初の超過が最も遅いのは手法4・5の7x。",
        "- p95 TPOTは手法4・5が3xで早くも100 ms/tokenを超える。PP2はTTFT tailを改善する一方、decode tailの悪化が早い。",
        "- PP1系のp95 TPOT初回超過は、手法3が6x、手法1・2・6が7x。",
        "",
        "判定は各負荷300リクエスト、seed 1の観測結果に基づく。",
    ])
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    data = load_data()
    rows = [
        crossing_row(arm, arm_rows, *metric)
        for arm, arm_rows in data.items()
        for metric in METRICS
    ]
    csv_path = ANALYSIS / "sla_crossings_ttft2s_tpot100ms.csv"
    report_path = ROOT / "report" / "sla_crossings_ttft2s_tpot100ms.md"
    write_csv(rows, csv_path)
    write_report(rows, report_path)
    print(f"Saved {csv_path}")
    print(f"Saved {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
