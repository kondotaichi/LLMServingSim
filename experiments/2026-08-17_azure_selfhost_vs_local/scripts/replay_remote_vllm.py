#!/usr/bin/env python3
"""Open-loop replay of a Hongo workload against a remote vLLM endpoint."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import aiohttp


REQUIRED_FIELDS = (
    "request_id",
    "session_id",
    "user_id",
    "input_tok_ids",
    "input_toks",
    "output_toks",
    "request_send_time_ns",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a Hongo JSONL workload against a remote vLLM server."
    )
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--max-client-concurrency", type=int, default=512)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--first-token-timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=1800.0)
    parser.add_argument("--warmup-requests", type=int, default=1)
    parser.add_argument("--failure-retries", type=int, default=1)
    parser.add_argument("--api-key-env", default="HONGO_API_KEY")
    parser.add_argument("--launch-lag-p99-threshold-ms", type=float)
    parser.add_argument("--server-gpu-model", default="NVIDIA H100 NVL 94GB")
    parser.add_argument("--server-gpu-count", type=int, default=8)
    parser.add_argument("--server-vm-sku", default="Standard_NC40ads_H100_v5")
    parser.add_argument("--server-replica-count", type=int, default=8)
    parser.add_argument("--tensor-parallel-size", type=int, default=1)
    parser.add_argument("--router-policy", default="least_conn")
    parser.add_argument("--server-environment", default="azure")
    parser.add_argument("--server-vllm-version", default="0.19.0")
    parser.add_argument(
        "--server-container-digest",
        default="sha256:7a0f0fdd2771464b6976625c2b2d5dd46f566aa00fbc53eceab86ef50883da90",
    )
    parser.add_argument(
        "--server-model-revision",
        default="d04e592bb4f6aa9cfee91e2e20afa771667e1d4b",
    )
    args = parser.parse_args()
    if args.max_requests is not None and args.max_requests <= 0:
        parser.error("--max-requests must be positive")
    if args.max_client_concurrency <= 0:
        parser.error("--max-client-concurrency must be positive")
    if args.warmup_requests < 0:
        parser.error("--warmup-requests cannot be negative")
    if args.failure_retries < 0:
        parser.error("--failure-retries cannot be negative")
    return args


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_workload(path: Path, manifest: Path, limit: int | None) -> tuple[list[dict], str]:
    if not path.is_file():
        raise SystemExit(f"Workload does not exist: {path}")
    with manifest.open(newline="", encoding="utf-8") as stream:
        entries = {entry["filename"]: entry for entry in csv.DictReader(stream)}
    entry = entries.get(path.name)
    if entry is None:
        raise SystemExit(f"Workload is not listed in manifest: {path.name}")
    actual_hash = sha256(path)
    if actual_hash != entry["sha256"]:
        raise SystemExit(f"SHA-256 mismatch for workload: {path}")
    if path.stat().st_size != int(entry["size_bytes"]):
        raise SystemExit(f"Size mismatch for workload: {path}")

    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = [field for field in REQUIRED_FIELDS if field not in row]
            if missing:
                raise SystemExit(f"Missing fields at line {line_number}: {', '.join(missing)}")
            if len(row["input_tok_ids"]) != int(row["input_toks"]):
                raise SystemExit(f"input token count mismatch at line {line_number}")
            rows.append(row)
    if len(rows) != int(entry["requests"]):
        raise SystemExit(f"Request count mismatch for workload: {path}")
    if [row["request_id"] for row in rows] != list(range(len(rows))):
        raise SystemExit("request_id must be contiguous and ordered")
    send_times = [int(row["request_send_time_ns"]) for row in rows]
    if send_times != sorted(send_times):
        raise SystemExit("request_send_time_ns must be monotonic")
    return rows[:limit] if limit is not None else rows, actual_hash


def sanitized_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SystemExit("--endpoint must be an http(s) URL")
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{parsed.hostname}{port}", parsed.path.rstrip("/"), "", ""))


def api_url(endpoint: str, resource: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1"):
        return f"{endpoint}/{resource}"
    return f"{endpoint}/v1/{resource}"


def git_metadata() -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], check=True, capture_output=True, text=True
            ).stdout
        )
        return {"commit": commit, "dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def distribution(values: list[int]) -> dict:
    floats = [float(value) for value in values]
    return {
        "mean": sum(floats) / len(floats) if floats else None,
        "p50": percentile(floats, 50),
        "p95": percentile(floats, 95),
        "p99": percentile(floats, 99),
        "max": max(floats) if floats else None,
    }


def short_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    return message[:300]


async def parse_stream(
    response: aiohttp.ClientResponse,
    first_token_timeout: float,
    clock_start_ns: int,
) -> tuple[int | None, int, int | None, int | None, str | None]:
    first_token_ns = None
    actual_input_tokens = None
    actual_output_tokens = None
    finish_reason = None
    buffer = b""
    iterator = response.content.iter_chunked(64 * 1024).__aiter__()
    first_deadline = time.perf_counter() + first_token_timeout

    while True:
        try:
            if first_token_ns is None:
                remaining = first_deadline - time.perf_counter()
                if remaining <= 0:
                    raise asyncio.TimeoutError("first token timeout")
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            else:
                chunk = await iterator.__anext__()
        except StopAsyncIteration:
            break
        buffer += chunk.replace(b"\r\n", b"\n")
        while b"\n\n" in buffer:
            block, buffer = buffer.split(b"\n\n", 1)
            data_lines = [line[5:].lstrip() for line in block.split(b"\n") if line.startswith(b"data:")]
            if not data_lines:
                continue
            payload = b"\n".join(data_lines).decode("utf-8", errors="replace")
            if payload.strip() == "[DONE]":
                continue
            event = json.loads(payload)
            usage = event.get("usage")
            if usage:
                actual_input_tokens = usage.get("prompt_tokens", actual_input_tokens)
                actual_output_tokens = usage.get("completion_tokens", actual_output_tokens)
            choices = event.get("choices") or []
            for choice in choices:
                text = choice.get("text")
                if text and first_token_ns is None:
                    first_token_ns = time.perf_counter_ns() - clock_start_ns
                if choice.get("finish_reason") is not None:
                    finish_reason = choice["finish_reason"]
    return first_token_ns, time.perf_counter_ns() - clock_start_ns, actual_input_tokens, actual_output_tokens, finish_reason


async def issue_request(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    row: dict,
    scheduled_send_ns: int,
    run_start_ns: int,
    args: argparse.Namespace,
    result_stream,
    warmup: bool = False,
    attempt: int = 1,
) -> dict:
    delay = (run_start_ns + scheduled_send_ns - time.perf_counter_ns()) / 1e9
    if delay > 0:
        await asyncio.sleep(delay)

    async with semaphore:
        actual_send_ns = time.perf_counter_ns() - run_start_ns
        requested_output_tokens = 1 if warmup else int(row["output_toks"])
        request_id = f"{args.run_id or 'run'}-{'warmup' if warmup else row['request_id']}-a{attempt}"
        payload = {
            "model": args.model,
            "prompt": row["input_tok_ids"],
            "temperature": 0,
            "top_p": 1,
            "max_tokens": requested_output_tokens,
            "ignore_eos": True,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        result = {
            "run_id": args.run_id,
            "request_id": row["request_id"],
            "attempt": attempt,
            "session_id": row["session_id"],
            "user_id": row["user_id"],
            "assigned_instance_id": row.get("assigned_instance_id"),
            "communication_latency_ns": row.get("communication_latency_ns"),
            "scheduled_send_ns": scheduled_send_ns,
            "actual_send_ns": actual_send_ns,
            "launch_lag_ns": actual_send_ns - scheduled_send_ns,
            "first_token_ns": None,
            "completion_ns": None,
            "e2e_ttft_ns": None,
            "completion_latency_ns": None,
            "tpot_ns": None,
            "requested_input_tokens": int(row["input_toks"]),
            "actual_input_tokens": None,
            "requested_output_tokens": requested_output_tokens,
            "actual_output_tokens": None,
            "http_status": None,
            "finish_reason": None,
            "success": False,
            "error_type": None,
            "error_message": None,
            "replica_id": None,
            "server_request_id": None,
        }
        try:
            async with session.post(
                api_url(args.endpoint, "completions"),
                json=payload,
                headers={"X-Request-ID": request_id},
            ) as response:
                result["http_status"] = response.status
                result["replica_id"] = response.headers.get("X-VLLM-Backend")
                result["server_request_id"] = response.headers.get("X-Request-ID")
                if response.status != 200:
                    await response.read()
                    raise RuntimeError(f"HTTP {response.status}")
                first_ns, end_ns, input_tokens, output_tokens, finish_reason = await parse_stream(
                    response, args.first_token_timeout, run_start_ns
                )
                result["first_token_ns"] = first_ns
                result["completion_ns"] = end_ns
                result["actual_input_tokens"] = input_tokens
                result["actual_output_tokens"] = output_tokens
                result["finish_reason"] = finish_reason
                if first_ns is not None:
                    result["e2e_ttft_ns"] = first_ns - actual_send_ns
                    result["completion_latency_ns"] = end_ns - actual_send_ns
                if first_ns is not None and output_tokens is not None:
                    result["tpot_ns"] = (end_ns - first_ns) / max(output_tokens - 1, 1)
                problems = []
                if first_ns is None:
                    problems.append("missing first token")
                if input_tokens is None or output_tokens is None:
                    problems.append("missing usage")
                elif input_tokens != int(row["input_toks"]):
                    problems.append("input token mismatch")
                elif output_tokens != requested_output_tokens:
                    problems.append("output token mismatch")
                if problems:
                    raise RuntimeError("; ".join(problems))
                result["success"] = True
        except asyncio.TimeoutError as exc:
            result["completion_ns"] = time.perf_counter_ns() - run_start_ns
            result["error_type"] = "timeout"
            result["error_message"] = short_error(exc)
        except Exception as exc:
            result["completion_ns"] = time.perf_counter_ns() - run_start_ns
            result["error_type"] = type(exc).__name__
            result["error_message"] = short_error(exc)

        if not warmup:
            result_stream.write(json.dumps(result, separators=(",", ":")) + "\n")
            result_stream.flush()
        return result


async def preflight(session: aiohttp.ClientSession, args: argparse.Namespace) -> None:
    async with session.get(api_url(args.endpoint, "models")) as response:
        if response.status != 200:
            await response.read()
            raise RuntimeError(f"Model preflight failed with HTTP {response.status}")
        payload = await response.json()
    model_ids = {item.get("id") for item in payload.get("data", [])}
    if args.model not in model_ids:
        raise RuntimeError(f"Model is not served by endpoint: {args.model}")


def build_summary(results: list[dict], elapsed_ns: int, threshold_ms: float | None) -> dict:
    successful = [result for result in results if result["success"]]
    failures = [result for result in results if not result["success"]]
    ttft = [result["e2e_ttft_ns"] for result in successful]
    tpot = [result["tpot_ns"] for result in successful]
    completion = [result["completion_latency_ns"] for result in successful]
    launch_lag = [result["launch_lag_ns"] for result in results]
    output_mismatches = sum(
        result["actual_output_tokens"] is not None
        and result["actual_output_tokens"] != result["requested_output_tokens"]
        for result in results
    )
    input_mismatches = sum(
        result["actual_input_tokens"] is not None
        and result["actual_input_tokens"] != result["requested_input_tokens"]
        for result in results
    )
    lag_p99 = percentile([float(value) for value in launch_lag], 99)
    invalid_reasons = []
    if len(results) == 0:
        invalid_reasons.append("no requests were sent")
    if failures:
        invalid_reasons.append(f"{len(failures)} requests failed after retries")
    if output_mismatches:
        invalid_reasons.append(f"{output_mismatches} output token mismatches")
    if input_mismatches:
        invalid_reasons.append(f"{input_mismatches} input token mismatches")
    if threshold_ms is not None and lag_p99 is not None and lag_p99 > threshold_ms * 1e6:
        invalid_reasons.append("launch lag p99 exceeded threshold")
    elapsed_s = elapsed_ns / 1e9
    return {
        "requested_requests": len(results),
        "sent_requests": len(results),
        "completed_requests": len(results),
        "successful_requests": len(successful),
        "failed_requests": len(failures),
        "elapsed_ns": elapsed_ns,
        "e2e_ttft_ns": distribution(ttft),
        "tpot_ns": distribution(tpot),
        "completion_latency_ns": distribution(completion),
        "launch_lag_ns": distribution(launch_lag),
        "request_throughput_per_s": len(successful) / elapsed_s if elapsed_s else None,
        "prompt_tokens_per_s": sum(result["actual_input_tokens"] or 0 for result in successful) / elapsed_s
        if elapsed_s
        else None,
        "generation_tokens_per_s": sum(result["actual_output_tokens"] or 0 for result in successful) / elapsed_s
        if elapsed_s
        else None,
        "http_status_counts": dict(Counter(str(result["http_status"]) for result in results)),
        "error_type_counts": dict(Counter(result["error_type"] for result in failures)),
        "replica_counts": dict(Counter(result["replica_id"] for result in results)),
        "input_token_mismatches": input_mismatches,
        "output_token_mismatches": output_mismatches,
        "accepted": not invalid_reasons,
        "acceptance_reasons": invalid_reasons,
    }


async def run(args: argparse.Namespace) -> int:
    api_key = os.environ.get(args.api_key_env)
    # Local tunnels and trusted HPC ingress may intentionally have no API key.
    args.endpoint = sanitized_endpoint(args.endpoint)
    rows, workload_hash = load_workload(args.workload, args.manifest, args.max_requests)
    if not rows:
        raise SystemExit("Workload selection is empty")

    args.run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir / args.run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise SystemExit(f"Run directory already exists: {run_dir}")

    metadata = {
        "run_id": args.run_id,
        "status": "running",
        "start_utc": utc_now(),
        "end_utc": None,
        "git": git_metadata(),
        "workload": {
            "path": str(args.workload),
            "sha256": workload_hash,
            "selected_requests": len(rows),
        },
        "endpoint": args.endpoint,
        "model": args.model,
        "generation": {
            "temperature": 0,
            "top_p": 1,
            "ignore_eos": True,
            "stream": True,
        },
        "client": {
            "max_concurrency": args.max_client_concurrency,
            "connect_timeout_s": args.connect_timeout,
            "first_token_timeout_s": args.first_token_timeout,
            "request_timeout_s": args.request_timeout,
            "warmup_requests": args.warmup_requests,
            "failure_retries": args.failure_retries,
            "launch_lag_p99_threshold_ms": args.launch_lag_p99_threshold_ms,
            "python": sys.version,
            "aiohttp": aiohttp.__version__,
            "platform": platform.platform(),
        },
        "server": {
            "environment": args.server_environment,
            "vm_sku": args.server_vm_sku,
            "gpu_model": args.server_gpu_model,
            "gpu_count": args.server_gpu_count,
            "replica_count": args.server_replica_count,
            "tensor_parallel_size_per_replica": args.tensor_parallel_size,
            "router_policy": args.router_policy,
            "vllm_version": args.server_vllm_version,
            "container_digest": args.server_container_digest,
            "model_revision": args.server_model_revision,
        },
        "api_key_env": args.api_key_env,
    }
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    log_path = run_dir / "run.log"

    def log(message: str) -> None:
        line = f"{utc_now()} {message}"
        print(line)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    log(f"run={args.run_id} status=starting requests={len(rows)}")

    timeout = aiohttp.ClientTimeout(total=args.request_timeout, connect=args.connect_timeout)
    connector = aiohttp.TCPConnector(limit=args.max_client_concurrency)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    semaphore = asyncio.Semaphore(args.max_client_concurrency)
    results = []
    status = "complete"
    try:
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:
            await preflight(session, args)
            log("preflight=models_ok")
            with (run_dir / "requests.jsonl").open("a", encoding="utf-8", buffering=1) as result_stream:
                for index in range(min(args.warmup_requests, len(rows))):
                    warmup_start = time.perf_counter_ns()
                    warmup_result = await issue_request(
                        session,
                        semaphore,
                        rows[index],
                        0,
                        warmup_start,
                        args,
                        result_stream,
                        warmup=True,
                    )
                    if not warmup_result["success"]:
                        raise RuntimeError(
                            f"Warm-up request failed: {warmup_result['error_type']} "
                            f"{warmup_result['error_message']}"
                        )
                log(f"warmup=ok requests={min(args.warmup_requests, len(rows))}")

                run_start_ns = time.perf_counter_ns()
                first_send_ns = int(rows[0]["request_send_time_ns"])
                tasks = [
                    asyncio.create_task(
                        issue_request(
                            session,
                            semaphore,
                            row,
                            int(row["request_send_time_ns"]) - first_send_ns,
                            run_start_ns,
                            args,
                            result_stream,
                        )
                    )
                    for row in rows
                ]
                last_progress = time.monotonic()
                for completed, future in enumerate(asyncio.as_completed(tasks), start=1):
                    results.append(await future)
                    now = time.monotonic()
                    if completed == len(tasks) or completed % 100 == 0 or now - last_progress >= 10:
                        successful = sum(result["success"] for result in results)
                        log(f"progress={completed}/{len(tasks)} successful={successful}")
                        last_progress = now
                results_by_id = {result["request_id"]: result for result in results}
                rows_by_id = {row["request_id"]: row for row in rows}
                for attempt in range(2, args.failure_retries + 2):
                    failed_ids = [
                        request_id
                        for request_id, result in results_by_id.items()
                        if not result["success"]
                    ]
                    if not failed_ids:
                        break
                    log(f"retry_attempt={attempt} requests={len(failed_ids)}")
                    retry_start_ns = time.perf_counter_ns()
                    retry_tasks = [
                        asyncio.create_task(
                            issue_request(
                                session,
                                semaphore,
                                rows_by_id[request_id],
                                0,
                                retry_start_ns,
                                args,
                                result_stream,
                                attempt=attempt,
                            )
                        )
                        for request_id in failed_ids
                    ]
                    retry_results = await asyncio.gather(*retry_tasks)
                    for retry_result in retry_results:
                        results_by_id[retry_result["request_id"]] = retry_result
                    successful_retries = sum(result["success"] for result in retry_results)
                    log(
                        f"retry_attempt={attempt} successful={successful_retries}/"
                        f"{len(retry_results)}"
                    )
                results = [results_by_id[row["request_id"]] for row in rows]
                elapsed_ns = time.perf_counter_ns() - run_start_ns
    except (KeyboardInterrupt, asyncio.CancelledError):
        status = "interrupted"
        raise
    except Exception:
        status = "failed"
        raise
    finally:
        metadata["status"] = status
        metadata["end_utc"] = utc_now()
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    summary = build_summary(results, elapsed_ns, args.launch_lag_p99_threshold_ms)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    log(
        f"Run {args.run_id}: {summary['successful_requests']}/{summary['requested_requests']} successful; "
        f"accepted={summary['accepted']}"
    )
    print(f"Artifacts: {run_dir}")
    return 0 if summary["accepted"] else 2


def main() -> None:
    args = parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
