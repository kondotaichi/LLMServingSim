#!/usr/bin/env python3
"""Sweep single-request TTFT over prompt length and max_num_batched_tokens.

This is a lightweight profiler-backed estimator for the simple case of one
single-GPU instance and one request at a time. It reuses the production
Scheduler and trace generator, but does not launch ASTRA-Sim or Chakra.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from serving.core.logger import configure_logger  # noqa: E402
from serving.core.scheduler import Scheduler  # noqa: E402
from serving.core.trace_generator import generate_trace  # noqa: E402


DTYPE_TO_BITS = {
    "float16": 16,
    "bfloat16": 16,
    "float32": 32,
    "fp8": 8,
    "int8": 8,
}

DEFAULT_PLACEMENT = {
    "default": {
        "weights": "LOCAL",
        "kv_loc": "LOCAL",
        "kv_evict_loc": "REMOTE:0",
    },
    "block": [],
    "layer": {},
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate single-request TTFT for a grid of prompt lengths and "
            "max_num_batched_tokens values without launching ASTRA-Sim."
        )
    )
    parser.add_argument("--hardware", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--dtype", default="bfloat16", choices=sorted(DTYPE_TO_BITS))
    parser.add_argument("--kv-cache-dtype", default="auto", choices=["auto", "fp8"])
    parser.add_argument(
        "--max-num-batched-tokens",
        required=True,
        type=int,
        nargs="+",
        help="One or more max_num_batched_tokens values to sweep.",
    )
    parser.add_argument(
        "--prompt-tokens",
        required=True,
        type=int,
        nargs="+",
        help="One or more input prompt lengths to sweep.",
    )
    parser.add_argument(
        "--output-tokens",
        type=int,
        default=32,
        help="Generated tokens per request. Default: 32.",
    )
    parser.add_argument(
        "--num-repeats",
        type=int,
        default=30,
        help=(
            "Number of output rows per condition. The deterministic condition "
            "is computed once and repeated this many times. Default: 30."
        ),
    )
    parser.add_argument("--max-num-seqs", type=int, default=1)
    parser.add_argument("--block-size", type=int, default=16)
    parser.add_argument("--npu-mem-gb", type=float, default=96)
    parser.add_argument("--cpu-mem-gb", type=float, default=512)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--keep-inputs",
        action="store_true",
        help="Keep generated text traces under astra-sim/inputs/runs/ttft_budget_prompt_sweep.",
    )
    return parser.parse_args()


def _trace_path(inputs_root: Path, hardware: str, model: str, batch_id: int) -> Path:
    return (
        inputs_root
        / "trace"
        / hardware
        / model
        / f"instance0_batch{batch_id}.txt"
    )


def _trace_comp_time_ns(path: Path) -> int:
    total = 0
    with path.open() as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                total += int(parts[1])
            except ValueError:
                continue
    return total


def _make_scheduler(args: argparse.Namespace, max_num_batched_tokens: int) -> Scheduler:
    return Scheduler(
        args.model,
        0,  # node_id
        0,  # instance_id
        args.max_num_seqs,
        max_num_batched_tokens,
        1,  # num_npus
        1,  # tp_size
        1,  # pp_size
        args.npu_mem_gb,
        args.cpu_mem_gb,
        0,  # start_npu
        None,  # pd_type
        DTYPE_TO_BITS[args.dtype],
        args.block_size,
        1,  # req_num
        False,  # prioritize_prefill
        False,  # enable_prefix_caching
        False,  # enable_prefix_sharing
        None,  # prefix_pool
        None,  # prefix_storage
        True,  # enable_chunked_prefill
        0,  # long_prefill_token_threshold
        0,  # cxl_mem
        ep_size=1,
        kv_cache_dtype=args.kv_cache_dtype,
    )


def _estimate_ttft(
    args: argparse.Namespace,
    max_num_batched_tokens: int,
    prompt_tokens: int,
    inputs_root: Path,
) -> dict:
    scheduler = _make_scheduler(args, max_num_batched_tokens)
    output_tokens_total = prompt_tokens + args.output_tokens
    scheduler.add_request([
        0,
        args.model,
        prompt_tokens,
        output_tokens_total,
        0,
        0,
    ])

    current_ns = 0
    chunk_latencies = []
    chunk_tokens = []

    while True:
        batch = scheduler.schedule(current_ns, 0, -1)
        if batch is None:
            raise RuntimeError(
                "Scheduler returned no batch for "
                f"max_num_batched_tokens={max_num_batched_tokens}, "
                f"prompt_tokens={prompt_tokens}"
            )

        generate_trace(
            batch,
            args.hardware,
            1,  # tp_size
            1,  # pp_size
            1,  # local_ep
            1,  # ep_total
            None,  # pd_type
            0,  # node_id
            0,  # instance_id
            max_num_batched_tokens,
            args.max_num_seqs,
            DEFAULT_PLACEMENT,
            False,  # block_mode_on
            "BALANCED",
            False,  # enable_prefix_caching
            False,  # enable_attn_offloading
            None,  # power_model
            None,  # pim_model
            False,  # enable_sub_batch_interleaving
            DTYPE_TO_BITS[args.dtype],
            dtype=args.dtype,
            kv_cache_dtype=args.kv_cache_dtype,
            tp_dim=None,
            ep_dim=None,
            enable_block_copy=True,
            inputs_root=str(inputs_root),
        )

        latency_ns = _trace_comp_time_ns(
            _trace_path(inputs_root, args.hardware, args.model, batch.batch_id)
        )
        current_ns += latency_ns
        chunk_latencies.append(latency_ns)
        chunk_tokens.append(batch.total_len)

        _, _, finished = scheduler.add_done(batch.batch_id + 1, 0, current_ns)
        if scheduler.done or finished:
            break
        if scheduler.request and scheduler.request[0].ttft >= 0:
            break

        reqs = scheduler.request + [req for inflight in scheduler.inflight for req in inflight.requests]
        if reqs and reqs[0].ttft >= 0:
            break

    req = scheduler.done[0] if scheduler.done else scheduler.request[0]
    ttft_ns = req.ttft
    if ttft_ns < 0:
        # After prefill completion the request is returned to the waiting queue
        # for decode, so it normally lives in scheduler.request at this point.
        candidates = scheduler.request + [req for b in scheduler.inflight for req in b.requests]
        for candidate in candidates:
            if candidate.id == 0:
                req = candidate
                ttft_ns = candidate.ttft
                break
    if ttft_ns < 0:
        raise RuntimeError(
            "Failed to compute TTFT for "
            f"max_num_batched_tokens={max_num_batched_tokens}, "
            f"prompt_tokens={prompt_tokens}"
        )

    return {
        "max_num_batched_tokens": max_num_batched_tokens,
        "prompt_tokens": prompt_tokens,
        "output_tokens": args.output_tokens,
        "ttft_ns": int(ttft_ns),
        "ttft_ms": int(ttft_ns) / 1_000_000.0,
        "num_prefill_chunks": len(chunk_latencies),
        "chunk_tokens": ";".join(str(v) for v in chunk_tokens),
        "chunk_latencies_ns": ";".join(str(v) for v in chunk_latencies),
    }


def main() -> None:
    args = _parse_args()
    if args.num_repeats <= 0:
        raise ValueError("--num-repeats must be positive")

    configure_logger(level="WARNING")

    astra_sim = REPO_ROOT / "astra-sim"
    inputs_root = astra_sim / "inputs" / "runs" / "ttft_budget_prompt_sweep"
    if inputs_root.exists():
        shutil.rmtree(inputs_root)
    inputs_root.mkdir(parents=True, exist_ok=True)

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = REPO_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    original_cwd = Path.cwd()
    rows = []
    try:
        os.chdir(astra_sim)
        for max_num_batched_tokens in args.max_num_batched_tokens:
            for prompt_tokens in args.prompt_tokens:
                result = _estimate_ttft(
                    args,
                    max_num_batched_tokens,
                    prompt_tokens,
                    inputs_root,
                )
                for repeat in range(args.num_repeats):
                    rows.append({
                        "repeat": repeat,
                        **result,
                    })
    finally:
        os.chdir(original_cwd)
        if not args.keep_inputs and inputs_root.exists():
            shutil.rmtree(inputs_root)

    fieldnames = [
        "max_num_batched_tokens",
        "prompt_tokens",
        "output_tokens",
        "repeat",
        "ttft_ns",
        "ttft_ms",
        "num_prefill_chunks",
        "chunk_tokens",
        "chunk_latencies_ns",
    ]
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
