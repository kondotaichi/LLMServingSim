"""Geographic distributed-inference simulation (Phase 1): per-user and
per-GPU aggregation, plus run metadata output.

Kept independent from ``scheduler.py`` per the Phase 1 spec's modularity
guidance -- this module only reads ``Scheduler.done`` (finished ``Request``
objects) and the static placement CSVs written by
``python -m workloads.generators geographic``. It has no effect on, and is
never called from, the main simulation loop; ``serving/__main__.py`` invokes
it once after the loop exits, only when the relevant ``--geographic-*``
output flags are set.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess

import numpy as np


# ---------------------------------------------------------------------------
# Static roster loading (generator outputs)
# ---------------------------------------------------------------------------

def load_static_users(path):
    """Read the generator's users CSV. Returns {user_id: dict} or None."""
    if path is None:
        return None
    users = {}
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            uid = int(row['user_id'])
            users[uid] = {
                'user_id': uid,
                'user_x_m': float(row['user_x_m']),
                'user_y_m': float(row['user_y_m']),
                'request_weight': row['request_weight'],
                'request_probability': row['request_probability'],
                'assigned_gpu_id': int(row['assigned_gpu_id']),
                'distance_to_gpu_m': float(row['distance_to_gpu_m']),
            }
    return users


def load_static_gpus(path):
    """Read the generator's GPUs CSV. Returns {gpu_id: dict} or None."""
    if path is None:
        return None
    gpus = {}
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            gid = int(row['gpu_id'])
            gpus[gid] = {
                'gpu_id': gid,
                'instance_id': int(row['instance_id']),
                'gpu_x_m': float(row['gpu_x_m']),
                'gpu_y_m': float(row['gpu_y_m']),
            }
    return gpus


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _percentiles_ms(values_ns):
    if not values_ns:
        return None, None, None, None
    arr = np.array(values_ns, dtype=float) / 1_000_000.0
    return (float(np.mean(arr)), float(np.percentile(arr, 50)),
            float(np.percentile(arr, 95)), float(np.percentile(arr, 99)))


def _mean_ms(values_ns):
    if not values_ns:
        return None
    return float(np.mean(values_ns)) / 1_000_000.0


def _interval_union_ns(intervals, start_ns, end_ns):
    """Return covered time after clipping and merging wall-clock intervals."""
    clipped = sorted(
        (max(start_ns, int(start)), min(end_ns, int(end)))
        for start, end in intervals if end > start and end > start_ns and start < end_ns
    )
    if not clipped:
        return 0
    total = 0
    merged_start, merged_end = clipped[0]
    for start, end in clipped[1:]:
        if start <= merged_end:
            merged_end = max(merged_end, end)
        else:
            total += merged_end - merged_start
            merged_start, merged_end = start, end
    return total + merged_end - merged_start


def _workload_observation_window(schedulers):
    completed = [req for sched in schedulers for req in sched.done]
    starts = [req.request_send_time_ns for req in completed
              if req.request_send_time_ns is not None and req.request_send_time_ns >= 0]
    ends = [req.request_end_time_ns for req in completed
            if req.request_end_time_ns is not None and req.request_end_time_ns >= 0]
    start_ns = min(starts) if starts else 0
    end_ns = max(ends) if ends else start_ns
    return start_ns, end_ns


def aggregate_users(schedulers, users_static):
    """Build one row per user (spec section 25).

    ``users_static``: {user_id: dict} from load_static_users(), or None (in
    which case only users that actually sent a routed request appear, since
    the full 3000-user roster including zero-request senders is only known
    from the generator's static output).
    """
    by_user = {}
    for sched in schedulers:
        for req in sched.done:
            if req.user_id is None:
                continue
            by_user.setdefault(req.user_id, []).append(req)

    user_ids = set(users_static.keys()) if users_static is not None else set(by_user.keys())
    user_ids |= set(by_user.keys())

    rows = []
    for uid in sorted(user_ids):
        static = (users_static or {}).get(uid, {})
        reqs = by_user.get(uid, [])
        row = {
            'user_id': uid,
            'user_x_m': static.get('user_x_m', ''),
            'user_y_m': static.get('user_y_m', ''),
            'request_weight': static.get('request_weight', ''),
            'request_probability': static.get('request_probability', ''),
            'assigned_gpu_id': static.get('assigned_gpu_id', ''),
            'distance_to_gpu_m': static.get('distance_to_gpu_m', ''),
            'request_count': len(reqs),
        }
        if not reqs:
            row.update({
                'mean_e2e_ttft_ms': None, 'p50_e2e_ttft_ms': None,
                'p95_e2e_ttft_ms': None, 'p99_e2e_ttft_ms': None,
                'mean_communication_ms': None, 'mean_queueing_before_ttft_ms': None,
                'mean_prefill_service_ms': None, 'mean_decode_after_ttft_ms': None,
                'mean_request_completion_latency_ms': None,
                'dominant_ttft_bottleneck': None,
                'communication_bottleneck_count': 0,
                'queueing_bottleneck_count': 0,
                'prefill_bottleneck_count': 0,
            })
        else:
            e2e = [r.e2e_ttft_ns for r in reqs if r.e2e_ttft_ns >= 0]
            mean, p50, p95, p99 = _percentiles_ms(e2e)
            bottlenecks = [r.ttft_bottleneck for r in reqs if r.ttft_bottleneck is not None]
            counts = {'communication': 0, 'queueing': 0, 'prefill': 0}
            for b in bottlenecks:
                counts[b] = counts.get(b, 0) + 1
            dominant = max(counts, key=counts.get) if bottlenecks else None
            row.update({
                'mean_e2e_ttft_ms': mean, 'p50_e2e_ttft_ms': p50,
                'p95_e2e_ttft_ms': p95, 'p99_e2e_ttft_ms': p99,
                'mean_communication_ms': _mean_ms([r.communication_latency_ns for r in reqs]),
                'mean_queueing_before_ttft_ms': _mean_ms([r.queueing_before_ttft_ns for r in reqs]),
                'mean_prefill_service_ms': _mean_ms([r.prefill_service_ns for r in reqs]),
                'mean_decode_after_ttft_ms': _mean_ms([r.decode_after_ttft_ns for r in reqs]),
                'mean_request_completion_latency_ms': _mean_ms(
                    [r.request_completion_latency_ns for r in reqs if r.request_completion_latency_ns >= 0]),
                'dominant_ttft_bottleneck': dominant,
                'communication_bottleneck_count': counts.get('communication', 0),
                'queueing_bottleneck_count': counts.get('queueing', 0),
                'prefill_bottleneck_count': counts.get('prefill', 0),
            })
        rows.append(row)
    return rows


def aggregate_gpus(schedulers, gpus_static):
    """Build one row per GPU/instance (spec section 26)."""
    by_instance = {sched.instance_id: sched for sched in schedulers}
    observation_start_ns, observation_end_ns = _workload_observation_window(schedulers)
    observation_time_ns = max(0, observation_end_ns - observation_start_ns)
    gpu_ids = set(gpus_static.keys()) if gpus_static is not None else set(by_instance.keys())
    gpu_ids |= set(by_instance.keys())

    rows = []
    for gid in sorted(gpu_ids):
        static = (gpus_static or {}).get(gid, {})
        sched = by_instance.get(gid)
        reqs = list(sched.done) if sched is not None else []
        e2e = [r.e2e_ttft_ns for r in reqs if r.e2e_ttft_ns >= 0]
        mean, p50, p95, p99 = _percentiles_ms(e2e)
        assigned_user_count = len({r.user_id for r in reqs if r.user_id is not None})
        intervals = sched.batch_busy_intervals_ns if sched is not None else []
        busy_time_ns = _interval_union_ns(
            intervals, observation_start_ns, observation_end_ns
        )
        idle_time_ns = max(0, observation_time_ns - busy_time_ns)
        utilization_pct = (
            busy_time_ns / observation_time_ns * 100.0
            if observation_time_ns > 0 else 0.0
        )
        rows.append({
            'gpu_id': gid,
            'instance_id': static.get('instance_id', gid),
            'gpu_x_m': static.get('gpu_x_m', ''),
            'gpu_y_m': static.get('gpu_y_m', ''),
            'assigned_user_count': assigned_user_count,
            'request_count': len(reqs),
            'mean_e2e_ttft_ms': mean, 'p50_e2e_ttft_ms': p50,
            'p95_e2e_ttft_ms': p95, 'p99_e2e_ttft_ms': p99,
            'mean_queueing_before_ttft_ms': _mean_ms([r.queueing_before_ttft_ns for r in reqs]),
            'mean_prefill_service_ms': _mean_ms([r.prefill_service_ns for r in reqs]),
            # Phase 1 does not track a running peak-concurrency time series;
            # approximate with the final done-request count as a lower bound.
            'max_waiting_requests': len(sched.request) if sched is not None else 0,
            'max_running_requests': len(reqs),
            'prompt_tokens_processed': sum(r.input for r in reqs),
            'output_tokens_generated': sum(r.output - r.input for r in reqs),
            'observation_start_ns': observation_start_ns,
            'observation_end_ns': observation_end_ns,
            'observation_time_ns': observation_time_ns,
            'busy_time_ns': busy_time_ns,
            'idle_time_ns': idle_time_ns,
            'utilization_pct': utilization_pct,
            'completed_batch_count': len(intervals),
        })
    return rows


def aggregate_gpu_utilization_timeseries(schedulers, gpus_static, window_ns):
    """Build fixed-window batch-busy utilization rows for each GPU/instance."""
    if window_ns <= 0:
        raise ValueError("GPU utilization window must be positive")
    by_instance = {sched.instance_id: sched for sched in schedulers}
    gpu_ids = set(gpus_static.keys()) if gpus_static is not None else set(by_instance.keys())
    gpu_ids |= set(by_instance.keys())
    observation_start_ns, observation_end_ns = _workload_observation_window(schedulers)
    rows = []
    window_index = 0
    start_ns = observation_start_ns
    while start_ns < observation_end_ns:
        end_ns = min(start_ns + window_ns, observation_end_ns)
        duration_ns = end_ns - start_ns
        for gid in sorted(gpu_ids):
            static = (gpus_static or {}).get(gid, {})
            sched = by_instance.get(static.get('instance_id', gid))
            intervals = sched.batch_busy_intervals_ns if sched is not None else []
            busy_time_ns = _interval_union_ns(intervals, start_ns, end_ns)
            rows.append({
                'window_index': window_index,
                'window_start_ns': start_ns,
                'window_end_ns': end_ns,
                'window_duration_ns': duration_ns,
                'time_since_start_s': (start_ns - observation_start_ns) / 1_000_000_000.0,
                'gpu_id': gid,
                'instance_id': static.get('instance_id', gid),
                'busy_time_ns': busy_time_ns,
                'idle_time_ns': duration_ns - busy_time_ns,
                'utilization_pct': busy_time_ns / duration_ns * 100.0,
            })
        window_index += 1
        start_ns = end_ns
    return rows


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _write_csv(path, rows, fieldnames):
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


_USER_FIELDS = [
    'user_id', 'user_x_m', 'user_y_m', 'request_weight', 'request_probability',
    'assigned_gpu_id', 'distance_to_gpu_m', 'request_count',
    'mean_e2e_ttft_ms', 'p50_e2e_ttft_ms', 'p95_e2e_ttft_ms', 'p99_e2e_ttft_ms',
    'mean_communication_ms', 'mean_queueing_before_ttft_ms', 'mean_prefill_service_ms',
    'mean_decode_after_ttft_ms', 'mean_request_completion_latency_ms',
    'dominant_ttft_bottleneck', 'communication_bottleneck_count',
    'queueing_bottleneck_count', 'prefill_bottleneck_count',
]

_GPU_FIELDS = [
    'gpu_id', 'instance_id', 'gpu_x_m', 'gpu_y_m', 'assigned_user_count', 'request_count',
    'mean_e2e_ttft_ms', 'p50_e2e_ttft_ms', 'p95_e2e_ttft_ms', 'p99_e2e_ttft_ms',
    'mean_queueing_before_ttft_ms', 'mean_prefill_service_ms',
    'max_waiting_requests', 'max_running_requests',
    'prompt_tokens_processed', 'output_tokens_generated',
    'observation_start_ns', 'observation_end_ns', 'observation_time_ns',
    'busy_time_ns', 'idle_time_ns', 'utilization_pct',
    'completed_batch_count',
]

_GPU_UTILIZATION_TIMESERIES_FIELDS = [
    'window_index', 'window_start_ns', 'window_end_ns', 'window_duration_ns',
    'time_since_start_s', 'gpu_id', 'instance_id', 'busy_time_ns',
    'idle_time_ns', 'utilization_pct',
]


def write_user_csv(path, rows):
    _write_csv(path, rows, _USER_FIELDS)


def write_gpu_csv(path, rows):
    _write_csv(path, rows, _GPU_FIELDS)


def write_gpu_utilization_timeseries_csv(path, rows):
    _write_csv(path, rows, _GPU_UTILIZATION_TIMESERIES_FIELDS)


def write_metadata(path, args, cluster, num_users_static, num_gpus_static):
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=os.path.dirname(__file__), text=True
        ).strip()
    except Exception:
        git_hash = None

    instances = cluster.get("instances", [])
    model_name = instances[0]["model_name"] if instances else None
    hardware = instances[0]["hardware"] if instances else None

    metadata = {
        "num_users": num_users_static,
        "num_gpus": num_gpus_static,
        "num_instances": cluster.get("num_instances"),
        "model_name": model_name,
        "gpu_hardware": hardware,
        "dataset": args.dataset,
        "cluster_config": args.cluster_config,
        "request_routing_policy": args.request_routing_policy,
        "max_num_seqs": args.max_num_seqs,
        "max_num_batched_tokens": args.max_num_batched_tokens,
        "output_files": {
            "requests": args.output,
            "users": args.geographic_user_output,
            "gpus": args.geographic_gpu_output,
            "gpu_utilization_timeseries": args.gpu_utilization_timeseries_output,
        },
        "git_commit_hash": git_hash,
        "network_model": "fixed_throughput_distance_proportional",
        "network_contention": False,
        "network_queueing": False,
        "network_jitter": False,
        "packet_loss": False,
        "user_mobility": False,
        "routing_policy": "nearest" if args.request_routing_policy == "NEAREST" else args.request_routing_policy.lower(),
        "ttft_definition": (
            "simulator_ttft_ns = first_token_ready_time_ns - gpu_arrival_time_ns (== req.arrival); "
            "e2e_ttft_ns = uplink_latency_ns + simulator_ttft_ns + downlink_latency_ns"
        ),
        "prefill_service_definition": (
            "sum of wall-clock batch durations (ASTRA-Sim-reported finish_time_ns - batch start) over every "
            "batch a request participated in while its TTFT was not yet stamped; a batch's full duration is "
            "attributed to every request in it (not divided), representing each request's own wall-clock "
            "experience rather than an exclusive GPU-time allocation"
        ),
        "gpu_utilization_definition": (
            "union of completed real-batch wall-clock intervals divided by the global workload observation "
            "window from the earliest request send to the latest request completion; overlapping pipeline "
            "batch intervals are merged, and dummy DP synchronization batches are excluded"
        ),
        "gpu_utilization_window_ns": args.gpu_utilization_window_ns,
        "decode_definition": (
            "decode_after_ttft_ns = decode_queueing_ns + decode_active_ns, measured from first-token-ready to "
            "request completion; never added into e2e_ttft_ns"
        ),
        "bottleneck_definition": (
            "ttft_bottleneck = argmax(communication_latency_ns, queueing_before_ttft_ns, prefill_service_ns), "
            "ties broken queueing > prefill > communication; total_latency_bottleneck additionally compares "
            "decode_after_ttft_ns, ties broken queueing > prefill > decode > communication"
        ),
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
