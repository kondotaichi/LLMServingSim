#!/usr/bin/env python3
"""Compress a workload JSONL's inter-arrival gaps to raise the offered
request rate, without touching token content, reuse, or GPU/user
placement. Used to probe how much capacity pressure (router queue,
redirects) appears at a given offered load before committing to a full
simulation sweep.
"""

import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument(
        "--rate-multiplier", type=float, required=True,
        help="Compress inter-arrival gaps by this factor (2.0 = twice the offered rate).",
    )
    args = ap.parse_args()

    rows = []
    with open(args.input) as f:
        for line in f:
            rows.append(json.loads(line))
    rows.sort(key=lambda r: r["arrival_time_ns"])

    t0 = rows[0]["arrival_time_ns"]
    for row in rows:
        offset = row["arrival_time_ns"] - t0
        new_offset = round(offset / args.rate_multiplier)
        row["arrival_time_ns"] = t0 + new_offset
        row["request_send_time_ns"] = row["arrival_time_ns"]

    with open(args.output, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    span_s = (rows[-1]["arrival_time_ns"] - rows[0]["arrival_time_ns"]) / 1e9
    rate = (len(rows) - 1) / span_s if span_s > 0 else float("nan")
    print(f"{args.output}: n={len(rows)} span={span_s:.1f}s avg_rate={rate:.2f} req/s")


if __name__ == "__main__":
    main()
