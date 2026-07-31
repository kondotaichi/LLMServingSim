"""Map a flat workload onto regional and temporal load ratios from a CSV."""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path


def register_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True, help="Source flat JSONL workload.")
    parser.add_argument("--ratios", required=True, help="Regional 10-minute ratio CSV.")
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    parser.add_argument("--day-type", default="weekday")
    parser.add_argument("--users-per-region", type=int, default=2)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=86400.0,
        help="Compress the 24-hour regional timeline into this duration.",
    )
    parser.add_argument("--seed", type=int, default=42)


def run(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    ratio_rows = []
    with Path(args.ratios).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["day_type"] != args.day_type:
                continue
            ratio_rows.append({
                "region": row["region_label"],
                "slot": int(row["local_10min_slot"]),
                "weight": float(row["avg_requests_per_active_user"]),
            })
    if not ratio_rows:
        raise ValueError(f"No ratio rows found for day_type={args.day_type!r}")

    regions = sorted({row["region"] for row in ratio_rows})
    region_id = {region: index for index, region in enumerate(regions)}
    weighted_slots = [(row, row["weight"]) for row in ratio_rows]
    choices = [item[0] for item in weighted_slots]
    weights = [item[1] for item in weighted_slots]

    source_rows = []
    with Path(args.input).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                source_rows.append(json.loads(line))

    output_rows = []
    user_cursor = defaultdict(int)
    # SPEC: 2026-07-28_pp_schedule_conceal_and_speculative -- when the
    # source carries a session_id (sharegpt.py's multi-turn mode), every
    # turn of the same conversation must land on the same (region,
    # user_id): a session is one real user, and its turns are the only
    # place genuine repeated KV content comes from (see
    # proactive_kv_prewarm investigation report). The region/user_id is
    # picked once, on the session's first turn, and reused verbatim for
    # its later turns -- without this, KV reuse has no real repeated
    # content to key off even if the token content itself repeats.
    # Sources without session_id (e.g. --fix-len, or older flat inputs)
    # keep the original per-row-random assignment untouched.
    # Real users can't send turn N+1 before turn N's reply exists, so a
    # session's rows (already in chronological turn order in source_rows --
    # sharegpt.py's _stream_turns only ever advances a session forward)
    # must get strictly increasing arrival_time_ns. Independent per-row
    # sampling (below) has no notion of this and can draw turn 2 an
    # earlier slot than turn 1; MIN_TURN_GAP_NS clamps it forward instead.
    MIN_TURN_GAP_NS = 500_000_000
    session_assignment: dict[int, tuple[dict, int, int]] = {}
    session_last_arrival: dict[int, int] = {}
    for request_id, source in enumerate(source_rows):
        session_id = source.get("session_id")
        if session_id is not None and session_id in session_assignment:
            selected, rid, user_id = session_assignment[session_id]
        else:
            selected = rng.choices(choices, weights=weights, k=1)[0]
            rid = region_id[selected["region"]]
            local_user = user_cursor[rid] % args.users_per_region
            user_cursor[rid] += 1
            user_id = rid * args.users_per_region + local_user
            if session_id is not None:
                session_assignment[session_id] = (selected, rid, user_id)
        day_offset_ns = (
            selected["slot"] * 600 * 1_000_000_000
            + rng.randrange(600 * 1_000_000_000)
        )
        arrival_time_ns = int(day_offset_ns * args.duration_seconds / 86400.0)
        if session_id is not None and session_id in session_last_arrival:
            arrival_time_ns = max(
                arrival_time_ns, session_last_arrival[session_id] + MIN_TURN_GAP_NS
            )
        if session_id is not None:
            session_last_arrival[session_id] = arrival_time_ns
        row = dict(source)
        row.update({
            "request_id": request_id,
            "user_id": user_id,
            "region": selected["region"],
            "assigned_instance_id": rid,
            "gpu_id": rid,
            "local_10min_slot": selected["slot"],
            "arrival_time_ns": arrival_time_ns,
        })
        output_rows.append(row)

    output_rows.sort(key=lambda row: (row["arrival_time_ns"], row["request_id"]))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"Wrote {len(output_rows)} requests across {len(regions)} regions -> {output_path}")
    return 0
