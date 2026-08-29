#!/usr/bin/env python3
"""Build a session-stratified 600-request Peak 5x workload."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ROOT / "workloads/full"
SOURCE_PP1 = WORKLOADS / "hongo_peak_5x_seed1.jsonl"
SOURCE_PP2 = WORKLOADS / "hongo_peak_5x_seed1_pp2.jsonl"
OUTPUT_PP1 = WORKLOADS / "hongo_peak_5x_600_seed1.jsonl"
OUTPUT_PP2 = WORKLOADS / "hongo_peak_5x_600_seed1_pp2.jsonl"
TARGET_COUNTS = {1: 221, 2: 49, 3: 33, 4: 15, 5: 12, 6: 4, 7: 3, 8: 1, 9: 1}
TIME_SCALE = 600 / 2000


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def select_sessions(rows: list[dict]) -> set[int]:
    session_rows: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        session_rows[int(row["session_id"])].append(row)

    sessions_by_size: dict[int, list[int]] = defaultdict(list)
    for session_id, requests in session_rows.items():
        sessions_by_size[len(requests)].append(session_id)

    rng = random.Random(1)
    selected: set[int] = set()
    for size, count in TARGET_COUNTS.items():
        candidates = sessions_by_size[size]
        rng.shuffle(candidates)
        if len(candidates) < count:
            raise ValueError(f"Only {len(candidates)} sessions of size {size}, need {count}")
        selected.update(candidates[:count])
    return selected


def write(rows: list[dict], selected: set[int], destination: Path) -> None:
    chosen = [row for row in rows if int(row["session_id"]) in selected]
    if len(chosen) != 600:
        raise ValueError(f"Selected {len(chosen)} requests, expected 600")

    first_send_ns = min(int(row["request_send_time_ns"]) for row in chosen)
    with destination.open("w", encoding="utf-8") as handle:
        for request_id, row in enumerate(chosen):
            new_row = dict(row)
            old_send_ns = int(row["request_send_time_ns"])
            uplink_ns = int(row["arrival_time_ns"]) - old_send_ns
            send_ns = round((old_send_ns - first_send_ns) * TIME_SCALE)
            new_row["request_id"] = request_id
            new_row["request_send_time_ns"] = send_ns
            new_row["arrival_time_ns"] = send_ns + uplink_ns
            handle.write(json.dumps(new_row, separators=(",", ":")) + "\n")

    session_count = len({row["session_id"] for row in chosen})
    revisits = len(chosen) - session_count
    span_s = (
        max(int(row["request_send_time_ns"]) for row in chosen) - first_send_ns
    ) * TIME_SCALE / 1e9
    print(
        f"wrote {destination}: requests={len(chosen)}, sessions={session_count}, "
        f"revisits={revisits} ({revisits / len(chosen):.2%}), span={span_s:.6f}s"
    )


def main() -> None:
    pp1_rows = load(SOURCE_PP1)
    pp2_rows = load(SOURCE_PP2)
    pp1_identity = [(row["request_id"], row["session_id"]) for row in pp1_rows]
    pp2_identity = [(row["request_id"], row["session_id"]) for row in pp2_rows]
    if pp1_identity != pp2_identity:
        raise ValueError("PP=1 and PP=2 sources do not have matching request order")

    selected = select_sessions(pp1_rows)
    write(pp1_rows, selected, OUTPUT_PP1)
    write(pp2_rows, selected, OUTPUT_PP2)


if __name__ == "__main__":
    main()
