#!/usr/bin/env python3
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = (
    REPO_ROOT
    / "experiments/2026-08-21-local-hongo-workload-gh200-test"
)
OUTPUT_ROOT = REPO_ROOT / "experiments/2026-08-24-simulate-both-local-and-cloudlike/workloads_no_ran_pp2"


def load(path):
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for level in range(1, 11):
        filename = f"hongo_peak_{level}x_repeat600_seed1.jsonl"
        pp2_path = SOURCE_ROOT / "workloads" / filename.replace(".jsonl", "_pp2.jsonl")
        cloud_path = SOURCE_ROOT / "workloads_cloud_aligned" / filename
        pp2_rows = load(pp2_path)
        cloud_rows = load(cloud_path)
        if len(pp2_rows) != 600 or len(cloud_rows) != 600:
            raise ValueError(f"{filename}: expected 600 rows")

        output = []
        for index, (pp2, cloud) in enumerate(zip(pp2_rows, cloud_rows)):
            for field in (
                "request_id", "session_id", "user_id", "input_toks",
                "output_toks", "request_send_time_ns", "reuse_prefix_toks",
                "repeat_cycle",
            ):
                if pp2.get(field) != cloud.get(field):
                    raise ValueError(f"{filename} row {index}: {field} differs")
            row = dict(pp2)
            row["input_tok_ids"] = cloud["input_tok_ids"]
            row["output_tok_ids"] = cloud["output_tok_ids"]
            row["arrival_time_ns"] = int(row["request_send_time_ns"])
            for field in (
                "uplink_distance_latency_ns",
                "uplink_serialization_latency_ns",
                "uplink_latency_ns",
                "downlink_distance_latency_ns",
                "downlink_serialization_latency_ns",
                "downlink_latency_ns",
                "communication_latency_ns",
            ):
                row[field] = 0
            output.append(row)

        output_path = OUTPUT_ROOT / filename
        with output_path.open("w") as handle:
            for row in output:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(output_path)


if __name__ == "__main__":
    main()
