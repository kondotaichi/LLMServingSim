#!/usr/bin/env python3
"""Measure and extrapolate the cost of an O(N) full-GPU routing scan."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from serving.core.memory_model import Device
from serving.core.router import Router


DEFAULT_SIZES = (10, 30, 100, 300, 1_000, 3_000, 10_000, 30_000, 100_000)


class _Memory:
    block_size = 16
    mem_for_kv = 12 * 1024**3
    npu_mem = 24 * 1024**3
    weight = 12 * 1024**3
    npu_used = weight
    enable_prefix_caching = True

    @staticmethod
    def get_kv(tokens):
        return int(tokens) * 131_072

    @classmethod
    def avail_size(cls, device):
        if device != Device.NPU:
            raise ValueError(f"Unexpected device: {device}")
        return cls.mem_for_kv


def _scheduler(instance_id, requests):
    return SimpleNamespace(
        instance_id=instance_id,
        request=[],
        inflight=[SimpleNamespace(requests=requests)] if requests else [],
        memory=_Memory(),
        max_num_seqs=128,
    )


def _measure_once(router, schedulers, request):
    start_ns = time.perf_counter_ns()
    best = min(
        schedulers,
        key=lambda scheduler: (
            router._capacity_snapshot(scheduler, request)['capacity_pressure'],
            scheduler.instance_id,
        ),
    )
    elapsed_ns = time.perf_counter_ns() - start_ns
    if best.instance_id != 0:
        raise RuntimeError(f"Unexpected tie-break result: {best.instance_id}")
    return elapsed_ns


def _linear_fit(xs, ys):
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / denominator
    intercept = y_mean - slope * x_mean
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    total = sum((y - y_mean) ** 2 for y in ys)
    r_squared = 1.0 - residual / total if total else 1.0
    return intercept, slope, r_squared


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent / 'results')
    parser.add_argument('--sizes', type=int, nargs='+', default=DEFAULT_SIZES)
    parser.add_argument('--target-candidate-evaluations', type=int, default=3_000_000)
    parser.add_argument('--min-repeats', type=int, default=7)
    parser.add_argument('--max-repeats', type=int, default=2_000)
    parser.add_argument('--warmups', type=int, default=3)
    parser.add_argument('--running-requests', type=int, nargs='+', default=(0, 32))
    parser.add_argument('--extrapolate', type=int, nargs='+', default=(10_000, 100_000, 1_000_000))
    return parser.parse_args()


def main():
    args = _parse_args()
    if any(size < 2 for size in args.sizes):
        raise ValueError('Every candidate-set size must be at least 2')

    args.output_dir.mkdir(parents=True, exist_ok=True)
    router = Router.__new__(Router)
    router._adaptive_reservations = {}
    request = {'index': 0, 'output_toks': 10_100}
    rows = []

    fits = []
    for running_requests in args.running_requests:
        active_requests = [
            SimpleNamespace(id=request_id, output=10_100)
            for request_id in range(running_requests)
        ]
        for size in args.sizes:
            schedulers = [
                _scheduler(instance_id, active_requests)
                for instance_id in range(size)
            ]
            router.prefill_schedulers = schedulers
            repeats = min(
                args.max_repeats,
                max(args.min_repeats, math.ceil(args.target_candidate_evaluations / size)),
            )
            for _ in range(args.warmups):
                _measure_once(router, schedulers, request)
            samples = [_measure_once(router, schedulers, request) for _ in range(repeats)]
            median_ns = statistics.median(samples)
            rows.append({
                'running_requests_per_gpu': running_requests,
                'candidate_gpus': size,
                'repeats': repeats,
                'median_scan_ns': median_ns,
                'mean_scan_ns': statistics.fmean(samples),
                'stdev_scan_ns': statistics.stdev(samples) if repeats > 1 else 0.0,
                'min_scan_ns': min(samples),
                'max_scan_ns': max(samples),
                'median_ns_per_gpu': median_ns / size,
            })
            print(
                f"running={running_requests:>3} N={size:>7,} repeats={repeats:>5,} "
                f"median={median_ns / 1e6:>10.3f} ms "
                f"({median_ns / size:>8.1f} ns/GPU)",
                flush=True,
            )

        fit_rows = [
            row for row in rows
            if row['running_requests_per_gpu'] == running_requests
            and row['candidate_gpus'] >= 100
        ]
        intercept_ns, slope_ns_per_gpu, r_squared = _linear_fit(
            [row['candidate_gpus'] for row in fit_rows],
            [row['median_scan_ns'] for row in fit_rows],
        )
        predictions = [
            {
                'candidate_gpus': size,
                'predicted_scan_ns': max(0.0, intercept_ns + slope_ns_per_gpu * size),
                'predicted_scan_ms': max(0.0, intercept_ns + slope_ns_per_gpu * size) / 1e6,
            }
            for size in args.extrapolate
        ]
        fits.append({
            'running_requests_per_gpu': running_requests,
            'model': 'scan_time_ns = intercept_ns + slope_ns_per_gpu * candidate_gpus',
            'fit_min_candidate_gpus': 100,
            'intercept_ns': intercept_ns,
            'slope_ns_per_gpu': slope_ns_per_gpu,
            'r_squared': r_squared,
            'predictions': predictions,
        })

    with (args.output_dir / 'measurements.csv').open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    with (args.output_dir / 'fit.json').open('w', encoding='utf-8') as file:
        json.dump({'fits': fits}, file, indent=2)
        file.write('\n')

    for fit in fits:
        print(
            f"running={fit['running_requests_per_gpu']} fit: "
            f"time_ns={fit['intercept_ns']:.1f}+{fit['slope_ns_per_gpu']:.1f}*N, "
            f"R^2={fit['r_squared']:.6f}"
        )
        for prediction in fit['predictions']:
            print(
                f"  extrapolated N={prediction['candidate_gpus']:,}: "
                f"{prediction['predicted_scan_ms']:.3f} ms"
            )


if __name__ == '__main__':
    main()
