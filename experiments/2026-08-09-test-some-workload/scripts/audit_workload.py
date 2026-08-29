#!/usr/bin/env python3
"""Audit whether a JSONL workload contains useful user-return signal."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def summarize(rows: list[dict]) -> dict:
    user_times: dict[str, list[int]] = defaultdict(list)
    session_counts = Counter()
    reusable = 0
    for index, row in enumerate(rows):
        user = row.get("user_id", row.get("session_id", f"row:{index}"))
        user_times[str(user)].append(
            int(row.get("request_send_time_ns", row.get("arrival_time_ns", 0)))
        )
        session_counts[str(row.get("session_id", f"row:{index}"))] += 1
        reusable += int(row.get("reuse_prefix_toks", 0) or 0) > 0

    revisit_delays = []
    for times in user_times.values():
        times.sort()
        revisit_delays.extend((b - a) / 1e9 for a, b in zip(times, times[1:]))
    user_histogram = Counter(len(times) for times in user_times.values())
    all_times = [time for times in user_times.values() for time in times]
    request_count = len(rows)
    return {
        "requests": request_count,
        "users": len(user_times),
        "sessions": len(session_counts),
        "return_visits": sum(max(0, len(times) - 1) for times in user_times.values()),
        "return_visit_rate": (
            sum(max(0, len(times) - 1) for times in user_times.values()) / request_count
            if request_count else 0.0
        ),
        "duration_s": (max(all_times) - min(all_times)) / 1e9 if all_times else 0.0,
        "requests_per_user_histogram": dict(sorted(user_histogram.items())),
        "revisit_delay_p50_s": percentile(revisit_delays, 0.50),
        "revisit_delay_p95_s": percentile(revisit_delays, 0.95),
        "reusable_prefix_request_rate": reusable / request_count if request_count else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workload", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--split-fraction", type=float, default=0.5,
        help="Chronological observation/evaluation split fraction.",
    )
    args = parser.parse_args()
    if not 0.0 < args.split_fraction < 1.0:
        raise ValueError("--split-fraction must be between 0 and 1")
    rows = [json.loads(line) for line in args.workload.read_text().splitlines() if line.strip()]
    rows.sort(key=lambda row: int(row.get("request_send_time_ns", row.get("arrival_time_ns", 0))))
    split = max(1, min(len(rows) - 1, round(len(rows) * args.split_fraction))) if len(rows) > 1 else len(rows)
    report = {
        "workload": str(args.workload),
        "full": summarize(rows),
        "observation": summarize(rows[:split]),
        "evaluation": summarize(rows[split:]),
    }
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()

