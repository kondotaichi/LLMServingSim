#!/usr/bin/env python3
"""Build 600-request Hongo Peak 1x-10x replay workloads."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
from pathlib import Path


EXP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EXP_ROOT.parents[1]
SOURCE_ROOT = REPO_ROOT / "experiments/2026-08-01_hongo_workload"
SOURCE_WORKLOADS = SOURCE_ROOT / "workloads"
REPEAT_SCRIPT = SOURCE_ROOT / "scripts/build_peak5_repeat600.py"
OUTPUT_DIR = EXP_ROOT / "workloads/hongo_repeat600"
MANIFEST_PATH = EXP_ROOT / "workloads/manifests/hongo_peak_1to10_repeat600.csv"


def load_repeat_module():
    spec = importlib.util.spec_from_file_location("hongo_repeat600", REPEAT_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load repeat helper: {REPEAT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_path(level: int) -> Path:
    name = "busy_hour" if level == 1 else f"peak_{level}x"
    return SOURCE_WORKLOADS / f"hongo_{name}_seed1.jsonl"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_vllm_safe(rows: list[dict]) -> list[dict]:
    """Avoid cross-cycle prefix hits without leaving the tokenizer vocabulary."""
    half = len(rows) // 2
    for index, copied in enumerate(rows[half:]):
        source = rows[index]
        tokens = list(source["input_tok_ids"])
        copied["input_tok_ids"] = tokens[1:] + tokens[:1] if len(tokens) > 1 else tokens
        if "output_tok_ids" in source:
            copied["output_tok_ids"] = list(source["output_tok_ids"])
    return rows


def main() -> None:
    repeat600 = load_repeat_module()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    manifest_rows = []

    for level in range(1, 11):
        source = source_path(level)
        output = OUTPUT_DIR / f"hongo_peak_{level}x_repeat600_seed1.jsonl"
        rows = make_vllm_safe(repeat600.repeat(repeat600.load(source)))
        repeat600.write(output, rows)
        manifest_rows.append(
            {
                "filename": output.name,
                "requests": len(rows),
                "size_bytes": output.stat().st_size,
                "sha256": file_sha256(output),
                "source": str(source.relative_to(REPO_ROOT)),
                "construction": "repeat300_twice_second_cycle_input_rotated",
            }
        )

    with MANIFEST_PATH.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=manifest_rows[0].keys())
        writer.writeheader()
        writer.writerows(manifest_rows)
    print(f"wrote {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
