#!/usr/bin/env python3
"""Validate transferred Hongo workloads before an AWS benchmark run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


STABLE_FIELDS = (
    "request_id",
    "session_id",
    "user_id",
    "input_toks",
    "output_toks",
    "input_tok_ids",
    "output_tok_ids",
    "reuse_prefix_toks",
    "assigned_instance_id",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--source-from-manifest", action="store_true")
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    if args.source_from_manifest == (args.directory is not None):
        parser.error("select exactly one of --directory or --source-from-manifest")
    if args.source_from_manifest and args.repo_root is None:
        parser.error("--source-from-manifest requires --repo-root")
    return args


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main() -> None:
    args = parse_args()
    with args.manifest.open(newline="", encoding="utf-8") as stream:
        entries = list(csv.DictReader(stream))

    baseline: list[dict] | None = None
    for entry in entries:
        if args.source_from_manifest:
            path = args.repo_root / entry["source_path"]
        else:
            path = args.directory / entry["filename"]
        if not path.is_file():
            raise SystemExit(f"Missing workload: {path}")
        if path.stat().st_size != int(entry["size_bytes"]):
            raise SystemExit(f"Size mismatch: {path}")
        actual_hash = sha256(path)
        if actual_hash != entry["sha256"]:
            raise SystemExit(f"SHA-256 mismatch: {path}")

        rows = load_rows(path)
        request_ids = [row["request_id"] for row in rows]
        sessions = {row["session_id"] for row in rows}
        users = {row["user_id"] for row in rows}
        send_times = [row["request_send_time_ns"] for row in rows]
        if len(rows) != int(entry["requests"]):
            raise SystemExit(f"Request count mismatch: {path}")
        if len(sessions) != int(entry["unique_sessions"]) or len(users) != len(sessions):
            raise SystemExit(f"Session/user count mismatch: {path}")
        if request_ids != list(range(len(rows))):
            raise SystemExit(f"request_id is not contiguous and ordered: {path}")
        if send_times != sorted(send_times):
            raise SystemExit(f"Send times are not monotonic: {path}")

        stable = [{field: row[field] for field in STABLE_FIELDS} for row in rows]
        if baseline is None:
            baseline = stable
        elif stable != baseline:
            raise SystemExit(f"Request content differs across load levels: {path}")
        print(f"OK {entry['load']}: {len(rows)} requests, sha256={actual_hash}")


if __name__ == "__main__":
    main()
