# Five-server PP workload set

This experiment keeps the original ten geographically distributed RTX 4090
GPUs and pairs them into five two-stage pipeline-parallel servers. Physical
GPU coordinates, users, arrivals, tokens, and prefix reuse are preserved from
the source workloads.

The fixed pairing is:

| Logical PP server | Physical GPUs | Stages |
|---:|---|---|
| 0 | 0, 1 | 0, 1 |
| 1 | 2, 3 | 0, 1 |
| 2 | 4, 5 | 0, 1 |
| 3 | 6, 7 | 0, 1 |
| 4 | 8, 9 | 0, 1 |

| Workload | Input tokens | Prefix reuse | Purpose |
|---|---:|---:|---|
| `input512_reuse00` | 512 | 0% | PP overhead / low pressure |
| `input2000_reuse025` | 2000 | 25% | Moderate prompt |
| `input6000_reuse05` | 6000 | 50% | Existing 90-second main condition |
| `input8000_reuse00` | 8000 | 0% | Long prompt without prefix reuse |
| `input8000_reuse025` | 8000 | 25% | Long prompt with moderate prefix reuse |
| `input8000_reuse05` | 8000 | 50% | Long prompt with high prefix reuse |
| `input10000_reuse00` | 10000 | 0% | KV-capacity pressure |
| `input10000_reuse025` | 10000 | 25% | KV-capacity pressure with moderate prefix reuse |
| `input10000_reuse05` | 10000 | 50% | KV-capacity pressure with high prefix reuse |
| `mixed_rate3p33_seed1` | 512--10000 | 0--50% | Normal + burst mixed traffic |

`assigned_instance_id` addresses the logical PP server (0 through 4).
The original physical home is retained as `physical_assigned_instance_id`,
while `gpu_id` and all user/GPU coordinates remain unchanged. Regenerate the
JSONL and placement files from repository-local sources:

```bash
python3 experiments/2026-07-22_pp2_five_workloads/scripts/prepare_workloads.py
```

The fixed APN simulation should use
`NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE`. The PP=2 arm adds
`--pp-size 2`; the PP=1 arm omits the option or uses `--pp-size 1`.

Run all five PP=2 workloads concurrently:

```bash
./experiments/2026-07-22_pp2_five_workloads/run_pp2_parallel.sh
```

On macOS, the script automatically dispatches into the existing
`llmservingsim_sim_local` container because the ASTRA-Sim executable is a
Linux binary. If the container has not been created yet, run
`./scripts/docker-sim.sh` once, exit its shell, and rerun the command above.

The default is five concurrent simulations. Set `MAX_PARALLEL=1` through
`5` to reduce concurrency. Completed 300-request outputs are skipped by
default; use `SKIP_COMPLETED=0` to rerun them.

Run the remaining five PP=1 baseline workloads concurrently:

```bash
./experiments/2026-07-22_pp2_five_workloads/run_pp1_parallel.sh
```

The PP=1 baseline uses the original ten-instance workloads (`assigned_instance_id`
0 through 9) and `ten_node_rtx4090_apn.json`. It must not use the PP2-remapped
workloads, whose routing IDs address five logical PP groups.

Generate the comparison tables, TTFT breakdown, CDF, trade-off, utilization,
and interim report from all currently completed PP1/PP2 pairs:

```bash
python3 experiments/2026-07-22_pp2_five_workloads/scripts/analyze_pp_comparison.py
```
