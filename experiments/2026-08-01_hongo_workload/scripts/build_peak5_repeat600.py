#!/usr/bin/env python3
"""Repeat the original Peak 5x trace to test twice the observation length."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKLOADS = ROOT / "workloads"
SOURCES = {
    "pp1": WORKLOADS / "hongo_peak_5x_seed1.jsonl",
    "pp2": WORKLOADS / "hongo_peak_5x_seed1_pp2.jsonl",
}
OUTPUTS = {
    "pp1": WORKLOADS / "hongo_peak_5x_repeat600_seed1.jsonl",
    "pp2": WORKLOADS / "hongo_peak_5x_repeat600_seed1_pp2.jsonl",
}
TOKEN_NAMESPACE_OFFSET = 1_000_000


def load(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len(rows) != 300:
        raise ValueError(f"{path} has {len(rows)} rows; expected 300")
    return rows


def shifted_tokens(tokens: list[int]) -> list[int]:
    return [int(token) + TOKEN_NAMESPACE_OFFSET for token in tokens]


def repeat(rows: list[dict]) -> list[dict]:
    rows = sorted(rows, key=lambda row: int(row["request_send_time_ns"]))
    first_send = int(rows[0]["request_send_time_ns"])
    last_send = int(rows[-1]["request_send_time_ns"])
    mean_interval = round((last_send - first_send) / (len(rows) - 1))
    period = last_send - first_send + mean_interval
    max_request_id = max(int(row["request_id"]) for row in rows)
    numeric_sessions = [
        int(row["session_id"])
        for row in rows
        if isinstance(row.get("session_id"), int)
    ]
    session_offset = max(numeric_sessions, default=0) + 1

    output = [dict(row) for row in rows]
    for row in rows:
        copied = dict(row)
        copied["request_id"] = int(row["request_id"]) + max_request_id + 1
        if isinstance(row.get("session_id"), int):
            copied["session_id"] = int(row["session_id"]) + session_offset
        elif "session_id" in row:
            copied["session_id"] = f'{row["session_id"]}_repeat'
        copied["request_send_time_ns"] = int(row["request_send_time_ns"]) + period
        copied["arrival_time_ns"] = int(row["arrival_time_ns"]) + period
        if "input_tok_ids" in row:
            copied["input_tok_ids"] = shifted_tokens(row["input_tok_ids"])
        if "output_tok_ids" in row:
            copied["output_tok_ids"] = shifted_tokens(row["output_tok_ids"])
        copied["repeat_cycle"] = 2
        output.append(copied)

    for row in output[: len(rows)]:
        row["repeat_cycle"] = 1
    return output


def write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    span = (
        int(rows[-1]["request_send_time_ns"])
        - int(rows[0]["request_send_time_ns"])
    ) / 1e9
    rate = (len(rows) - 1) / span
    print(f"wrote {path}: requests={len(rows)}, span={span:.6f}s, rate={rate:.3f} rps")


def main() -> None:
    pp1 = load(SOURCES["pp1"])
    pp2 = load(SOURCES["pp2"])
    pp1_identity = [(row["request_id"], row.get("session_id")) for row in pp1]
    pp2_identity = [(row["request_id"], row.get("session_id")) for row in pp2]
    if pp1_identity != pp2_identity:
        raise ValueError("PP1 and PP2 source request order differs")

    write(OUTPUTS["pp1"], repeat(pp1))
    write(OUTPUTS["pp2"], repeat(pp2))


if __name__ == "__main__":
    main()
