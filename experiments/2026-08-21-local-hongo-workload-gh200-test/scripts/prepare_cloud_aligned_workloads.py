#!/usr/bin/env python3
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
EXP_ROOT = REPO_ROOT / "experiments/2026-08-21-local-hongo-workload-gh200-test"
CLOUD_ROOT = (
    REPO_ROOT
    / "experiments/2026-08-17_azure_selfhost_vs_local/workloads/hongo_repeat600"
)
OUTPUT_ROOT = EXP_ROOT / "workloads_cloud_aligned"


def load_jsonl(path):
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    invariant_fields = (
        "request_id",
        "session_id",
        "user_id",
        "input_toks",
        "output_toks",
        "arrival_time_ns",
        "request_send_time_ns",
        "reuse_prefix_toks",
        "repeat_cycle",
    )
    token_fields = ("input_tok_ids", "output_tok_ids")

    for level in range(1, 11):
        filename = f"hongo_peak_{level}x_repeat600_seed1.jsonl"
        geographic_rows = load_jsonl(EXP_ROOT / "workloads" / filename)
        cloud_rows = load_jsonl(CLOUD_ROOT / filename)
        if len(geographic_rows) != 600 or len(cloud_rows) != 600:
            raise ValueError(f"{filename}: expected 600 rows")

        output_rows = []
        for index, (geographic, cloud) in enumerate(zip(geographic_rows, cloud_rows)):
            for field in invariant_fields:
                if geographic.get(field) != cloud.get(field):
                    raise ValueError(
                        f"{filename} row {index}: {field} differs between inputs"
                    )
            merged = dict(geographic)
            for field in token_fields:
                merged[field] = cloud[field]
            output_rows.append(merged)

        output_path = OUTPUT_ROOT / filename
        with output_path.open("w") as handle:
            for row in output_rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(output_path)


if __name__ == "__main__":
    main()
