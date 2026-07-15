#!/usr/bin/env python3
"""Prepare deterministic 10000-token workloads from the 6000-token source."""

import json
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = EXPERIMENT_DIR.parents[1]
SOURCE = (
    REPO_ROOT
    / "workloads/generated/cell_apn/prompt6000_90s/"
    "sharegpt_300_prompt6000_reuse50_90s.jsonl"
)
TARGET_INPUT_TOKENS = 10000
BLOCK_SIZE = 16
VOCAB_SIZE = 128000
CONDITIONS = {
    "input10000_reuse00": 0.0,
    "input10000_reuse025": 0.25,
    "input10000_reuse05": 0.5,
}


def extend_token_ids(token_ids, request_id):
    extended = list(token_ids[:TARGET_INPUT_TOKENS])
    for position in range(len(extended), TARGET_INPUT_TOKENS):
        token_id = (request_id * 10007 + position * 7919 + 104729) % VOCAB_SIZE
        extended.append(token_id)
    return extended


def load_source():
    with SOURCE.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def main():
    rows = load_source()
    output_dir = EXPERIMENT_DIR / "workloads"
    output_dir.mkdir(parents=True, exist_ok=True)

    for condition, reuse_rate in CONDITIONS.items():
        reuse_tokens = int(TARGET_INPUT_TOKENS * reuse_rate)
        reuse_tokens = reuse_tokens // BLOCK_SIZE * BLOCK_SIZE
        output_path = output_dir / f"{condition}.jsonl"
        with output_path.open("w", encoding="utf-8") as output:
            for source_row in rows:
                row = dict(source_row)
                row["input_toks"] = TARGET_INPUT_TOKENS
                row["input_tok_ids"] = extend_token_ids(
                    source_row["input_tok_ids"], int(source_row["request_id"])
                )
                row["reuse_prefix_toks"] = reuse_tokens
                row["request_payload_bytes"] = 500 + TARGET_INPUT_TOKENS * 4
                output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                output.write("\n")
        print(
            f"Prepared {output_path}: requests={len(rows)}, "
            f"input_tokens={TARGET_INPUT_TOKENS}, reuse_prefix_toks={reuse_tokens}"
        )


if __name__ == "__main__":
    main()
