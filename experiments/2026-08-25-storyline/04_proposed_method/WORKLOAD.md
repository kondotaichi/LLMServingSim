# Workload and experiment settings

## Scope

The proposed-method experiment uses exactly the same AI workload as
`03_geographical_offload_merit`. The PP=1 runs reference the 03 JSONL files
directly. The PP=2 files preserve every request field that affects workload
content or timing and change only the routing identifiers needed to map eight
physical GPUs to four two-stage pipeline instances.

The evaluated matrix is:

- Peak multipliers: 2x, 3x, 4x, and 5x
- Methods: KV redirect only, PP=2 only, and KV redirect + PP=2
- Requests per run: 600
- Total runs: 12

## Source workload

The workload is derived by
`03_geographical_offload_merit/scripts/prepare_experiment.py` from the Hongo
repeat-600 traces under
`experiments/2026-08-21-local-hongo-workload-gh200-test/workloads_cloud_aligned/`.
The PP=1 files used by both 03 and 04 are:

```text
03_geographical_offload_merit/workloads_ai_70_30/
  hongo_peak_2x_repeat600_seed1_ai70_30.jsonl
  hongo_peak_3x_repeat600_seed1_ai70_30.jsonl
  hongo_peak_4x_repeat600_seed1_ai70_30.jsonl
  hongo_peak_5x_repeat600_seed1_ai70_30.jsonl
```

All four files contain the same requests and token lengths. Only their request
arrival times differ according to the offered-load multiplier.

| Property | Value |
|---|---:|
| Requests | 600 |
| Sessions | 576 |
| One-request sessions | 558 |
| Two-request sessions | 12 |
| Three-request sessions | 6 |
| Mean input length | 3,860 tokens |
| Input range | 3,004--7,825 tokens |
| Mean output length | 279.59 tokens |
| Output range | 1--1,663 tokens |
| Mean `reuse_prefix_toks` | 1,922.08 tokens |
| Content/workload seed | 1 |
| Geographical split seed | 20260826 |

The request-send-time spans are:

| Load | Send-time span |
|---|---:|
| Peak 2x | 33.154 s |
| Peak 3x | 22.103 s |
| Peak 4x | 16.577 s |
| Peak 5x | 13.262 s |

## Geographical demand assignment

The 600 requests are assigned at session granularity so that every request in
a multi-turn session remains in the same home site.

| Site | Requests | Share | Physical GPUs |
|---|---:|---:|---|
| Tokyo | 420 | 70% | 0--3 |
| Kagoshima | 180 | 30% | 4--7 |

Within each site, requests are assigned round-robin across its four home GPUs.
The placement files are shared with 03:

```text
experiments/2026-08-21-local-hongo-workload-gh200-test/placements/
  hongo_users_8gpu.csv
  hongo_gh200_gpus.csv
```

The placement contains 27,000 registered users and eight GH200 GPUs, with four
GPUs at each site.

## RAN load and AI-available VRAM

The RAN-side assumptions are:

- Peak RRC-connected UEs: 900 across all 27,000 registered users
- Active TCP UE ratio: 20%
- Total VRAM per GH200: 95.577 GiB
- Per-GPU RRC load: proportional to the registered-user count assigned to that GPU
- Active TCP UEs: `ceil(per_gpu_rrc_connected * 0.20)`
- RAN VRAM fraction: `0.40 + 0.016 * active_tcp_ue`
- AI-available VRAM: `95.577 * (1 - ran_vram_fraction)` GiB

The resulting static peak assignment is:

| GPU | Registered users | Active TCP UEs | RAN VRAM | AI VRAM (GiB) |
|---:|---:|---:|---:|---:|
| 0 | 3,399 | 23 | 76.8% | 22.173864 |
| 1 | 3,300 | 22 | 75.2% | 23.703096 |
| 2 | 3,255 | 22 | 75.2% | 23.703096 |
| 3 | 3,454 | 24 | 78.4% | 20.644632 |
| 4 | 3,350 | 23 | 76.8% | 22.173864 |
| 5 | 3,451 | 24 | 78.4% | 20.644632 |
| 6 | 3,428 | 23 | 76.8% | 22.173864 |
| 7 | 3,363 | 23 | 76.8% | 22.173864 |

The source values are also recorded in
`03_geographical_offload_merit/configs/gpu_ran_vram_assignment.csv`.

## PP=1 and PP=2 mapping

PP=1 uses eight independent model instances, one per physical GPU.

PP=2 keeps the total hardware count at eight GPUs but forms four logical model
instances:

| PP instance | Physical GPUs | Site |
|---:|---|---|
| 0 | 0, 1 | Tokyo |
| 1 | 2, 3 | Tokyo |
| 2 | 4, 5 | Kagoshima |
| 3 | 6, 7 | Kagoshima |

This changes the system from eight PP=1 replicas to four PP=2 replicas. It does
not add GPUs. Each PP group uses the smaller AI-available VRAM value of its two
physical stages as a conservative per-stage capacity bound.

The PP=2 transformation preserves request order, request/session IDs,
send/arrival times, token IDs, token counts, prefix reuse, user coordinates,
and Tokyo/Kagoshima assignment. It changes only `assigned_instance_id` and
`second_nearest_gpu_id` to PP-group IDs and adds physical-ID bookkeeping fields.
The generated files are under `04_proposed_method/workloads_pp2/`.

## Network and simulator settings

All 12 runs use the same settings as 03:

| Setting | Value |
|---|---:|
| Model | `meta-llama/Llama-3.1-8B-Instruct` |
| Hardware profile | GH200 |
| Weight dtype | bfloat16 |
| KV-cache dtype | auto |
| Maximum sequences | 1,024 |
| Maximum batched tokens | 8,192 |
| KV block size | 16 tokens |
| Chunked prefill | enabled |
| Prefix caching | enabled |
| APN bandwidth | 10.7 Gbps |
| APN fixed propagation | 300.5 us |
| KV CPU-staging bandwidth | 297.8 GB/s |
| KV CPU-staging latency | 2.65 us |

Method-specific settings are:

| Method | Routing policy | PP |
|---|---|---:|
| KV redirect only | `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | 1 |
| PP=2 only | `NEAREST_KV` | 2 |
| KV redirect + PP=2 | `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | 2 |

For comparison, the 03 naive method uses
`NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE`, and the 03 local baseline uses
`NEAREST_KV` with PP=1.

## Reproduction

Prepare the PP=2 config and workloads, then run the 12 simulations with up to
six concurrent workers:

```bash
python3 experiments/2026-08-25-storyline/04_proposed_method/scripts/prepare_experiment.py
experiments/2026-08-25-storyline/04_proposed_method/scripts/run_peak_2x_to_5x_6parallel.sh
```

The completed outputs are under `04_proposed_method/results/`, with logs under
`04_proposed_method/logs/`.
