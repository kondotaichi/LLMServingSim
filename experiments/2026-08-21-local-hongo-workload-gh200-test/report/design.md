# Hongo GH200 eight-GPU experiment

## Objective

Repeat the Hongo RTX 4090 geographic workload experiment with eight geographically
distributed GH200 nodes. Request content, send times, network assumptions, scheduler
limits, and the four routing methods follow `experiments/2026-08-01_hongo_workload`.

## Hardware and network

- Eight independent geographic nodes, one GH200 per node
- No InfiniBand, NVLink, or shared memory between nodes
- Llama 3.1 8B, BF16 weights, automatic KV-cache dtype
- Profile: `profiler/perf/GH200/meta-llama/Llama-3.1-8B/bf16/tp1`
- Usable HBM capacity: 95.577 GiB per GH200
- HBM bandwidth: 3850 GB/s
- KV staging bandwidth: 297.8 GB/s, conservatively limited by measured D2H
- KV staging fixed latency: 2650 ns per hop; the simulator charges source and
  destination staging separately
- ASTRA-Sim link: 16 GB/s, 20000 ns, inherited from the RTX 4090 experiment
- Geographic GPU backbone: 10.7 Gbps
- Fixed one-way APN propagation: 300500 ns

The CPU memory capacity remains 128 GiB to match the earlier experiment. It is not
used as a persistent second-tier prefix cache in these runs. Cross-node KV migration
uses the independently measured staging CLI parameters above.

## Placement

The eight GH200 nodes use a uniform 4-by-2 grid over the same 748.3 m square Hongo
campus. User coordinates are unchanged. Workload preparation recalculates the nearest
and second-nearest GPU and the distance-dependent access latency.

For PP2, horizontally adjacent GPUs are paired: `(0,1)`, `(2,3)`, `(4,5)`, and
`(6,7)`. The two stages remain geographically separated and communicate over the same
link assumptions as the earlier experiment.

## Methods

| Number | Label | Routing policy | Logical instances | PP |
|---|---|---|---:|---:|
| 1 | naive | `NEAREST_KV` | 8 | 1 |
| 2 | redirect_no_kv | `NEAREST_MIGRATE` | 8 | 1 |
| 3 | redirect_kv_no_pp | `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | 8 | 1 |
| 4 | redirect_kv_pp2 | `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | 4 | 2 |

## Workloads and scheduler

- Peak 1x through Peak 10x, seed 1
- Exactly 600 requests at every load level
- Peak 1x uses the earlier `busy_hour` trace, matching the previous repeat600 method
- Each 300-request source trace is repeated once using the original mean interarrival
  period; repeated token IDs and session IDs use a separate namespace
- Request content and send-time distribution are unchanged by the eight-GPU remap
- `max_num_seqs=128`, `max_num_batched_tokens=2048`, `block_size=16`
- Prefix caching remains at the simulator default (enabled)

This produces 10 load levels times 4 methods, for 40 simulations.

## Reproduction

Prepare and validate all derived inputs:

```bash
python3 experiments/2026-08-21-local-hongo-workload-gh200-test/scripts/prepare_experiment.py
```

Run a smoke test with ten requests per method at Peak 1x:

```bash
NUM_REQS=10 experiments/2026-08-21-local-hongo-workload-gh200-test/scripts/run_peak_worker.sh 1
```

Run all 40 simulations sequentially:

```bash
experiments/2026-08-21-local-hongo-workload-gh200-test/scripts/run_all.sh
```

Run the commands above inside the simulator container started with
`scripts/docker-sim.sh`. The host Python environment is not authoritative for this
experiment because its protobuf runtime may not match the checked-in Chakra protobuf
code.

Use `MAX_PARALLEL_LEVELS` only when the host has enough CPU and memory for multiple
ASTRA-Sim processes. Every run has an isolated `inputs-root`.
