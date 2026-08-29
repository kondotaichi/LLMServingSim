#!/usr/bin/env python3
"""Build 300-request Hongo workloads with a 45% return-visit rate."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "workloads/full"
OUTPUT = ROOT / "workloads/revisit45_300"
LEVELS = (2, 5, 10)
TARGET_REQUESTS = 300
TARGET_USERS = 165
TIME_SCALE = TARGET_REQUESTS / 2000
SEED = 1


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def choose_sessions(rows: list[dict]) -> set[str]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row["session_id"])].append(row)

    candidates = list(groups.items())
    random.Random(SEED).shuffle(candidates)
    reachable: dict[tuple[int, int], tuple[tuple[int, int], str]] = {(0, 0): ((-1, -1), "")}
    for session_id, requests in candidates:
        size = len(requests)
        for users, total in sorted(reachable, reverse=True):
            next_state = users + 1, total + size
            if next_state[0] > TARGET_USERS or next_state[1] > TARGET_REQUESTS:
                continue
            reachable.setdefault(next_state, ((users, total), session_id))
        if (TARGET_USERS, TARGET_REQUESTS) in reachable:
            break

    target = (TARGET_USERS, TARGET_REQUESTS)
    if target not in reachable:
        raise RuntimeError("Could not construct the requested session-preserving sample")
    selected = set()
    state = target
    while state != (0, 0):
        previous, session_id = reachable[state]
        selected.add(session_id)
        state = previous
    return selected


def rescale(rows: list[dict], level: int) -> list[dict]:
    rows.sort(key=lambda row: int(row["request_send_time_ns"]))
    base = int(rows[0]["request_send_time_ns"])
    for row in rows:
        old_send = int(row["request_send_time_ns"])
        uplink = int(row.get("uplink_latency_ns", 0))
        send = base + round((old_send - base) * TIME_SCALE * 2 / level)
        row["request_send_time_ns"] = send
        row["arrival_time_ns"] = send + uplink
    return rows


def main() -> None:
    base_rows = load(FULL / "hongo_peak_2x_seed1.jsonl")
    selected = choose_sessions(base_rows)
    OUTPUT.mkdir(parents=True, exist_ok=True)

    source = FULL / "hongo_peak_2x_seed1.jsonl"
    for level in LEVELS:
        rows = [
            row for row in load(source)
            if str(row["session_id"]) in selected
        ]
        if len(rows) != TARGET_REQUESTS:
            raise RuntimeError(f"{source} produced {len(rows)} rows, expected {TARGET_REQUESTS}")
        rows = rescale(rows, level)
        destination = OUTPUT / f"hongo_peak_{level}x_revisit45_seed1.jsonl"
        with destination.open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(f"{destination}: {len(rows)} requests, {len(selected)} sessions")


if __name__ == "__main__":
    main()
