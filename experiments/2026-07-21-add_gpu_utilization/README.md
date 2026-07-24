# Prompt 6000 / 90s: five-policy GPU utilization experiment

This experiment repeats the workload from
`2026-07-14_prompt6000_90s_three_policy_良結果` and compares five routing
policies while recording batch-busy GPU utilization in one-second windows.

## Workload and simulator settings

- Dataset: `sharegpt_300_prompt6000_reuse50_90s.jsonl`
- Requests: 300
- Input tokens: 6000
- Prefix reuse: approximately 50%
- Arrival window: 90 seconds
- Cluster: 10 RTX 4090 instances
- `max_num_seqs`: 128
- `max_num_batched_tokens`: 2048
- Chunked prefill and prefix caching enabled
- GPU utilization window: 1 second

## Policies

| Label | Policy | Description |
|---|---|---|
| A | `NEAREST_KV` | Wait at the nearest GPU and keep local KV |
| B | `NEAREST_MIGRATE` | Redirect without KV handoff; cold prefill |
| C | `NEAREST_MIGRATE_KV` | Redirect with KV handoff |
| D | `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | Multi-candidate without learned model |
| E | `NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE` | Multi-candidate with learned model |

## Utilization definition

For each GPU and time window, utilization is the union of real-batch execution
intervals divided by the window duration. Request service times are not summed,
so batched requests are not double-counted. Overlapping pipeline intervals are
merged and DP synchronization-only dummy batches are excluded. This is a
simulated batch-busy fraction, not an SM hardware-counter measurement.

## Outputs

Each policy writes `requests.csv`, `gpus.csv`, and
`gpu_utilization_timeseries.csv` under `results/<POLICY>/`. The analyzer writes
summary CSVs under `analysis/`, figures under `figures/`, and `report.md`.
The learned Multi-candidate policy additionally writes
`routing_candidates.csv`, with one row per admissible candidate including all
formula features, component predictions, raw and clipped Scheduler estimates,
capacity-pressure rank, and model-TTFT rank.

## Run

Use the simulator container from the repository root:

```bash
docker run --rm \
  -e MAX_PARALLEL=3 \
  -e SKIP_COMPLETED=1 \
  -v "$(pwd)":/app/LLMServingSim \
  -w /app/LLMServingSim \
  llmservingsim-sim:local \
  bash -lc 'experiments/2026-07-21-add_gpu_utilization/run_five_policy.sh'
```

Then generate the analysis:

```bash
docker run --rm \
  -v "$(pwd)":/app/LLMServingSim \
  -w /app/LLMServingSim \
  llmservingsim-sim:local \
  bash -lc 'MPLCONFIGDIR=/tmp/matplotlib python3 experiments/2026-07-21-add_gpu_utilization/scripts/analyze_utilization.py'
```

Generate candidate-prediction diagnostics after the learned policy reruns:

```bash
docker run --rm \
  -v "$(pwd)":/app/LLMServingSim \
  -w /app/LLMServingSim \
  llmservingsim-sim:local \
  bash -lc 'MPLCONFIGDIR=/tmp/matplotlib python3 experiments/2026-07-21-add_gpu_utilization/scripts/analyze_candidate_predictions.py'
```

Run one-decision counterfactual replay for every non-selected admissible
candidate. The manifest contains 161 candidates; 18 baseline-selected rows are
reused and 143 additional simulations are run:

```bash
docker run --rm \
  -e MAX_PARALLEL=3 \
  -e SKIP_COMPLETED=1 \
  -v "$(pwd)":/app/LLMServingSim \
  -w /app/LLMServingSim \
  llmservingsim-sim:local \
  bash -lc 'experiments/2026-07-21-add_gpu_utilization/run_counterfactual_candidates.sh'
```

For an incremental first pass, set `REQUEST_LIMIT=3` to run only the first
three redirect decisions. Completed outputs remain reusable when the limit is
later removed:

```bash
docker run --rm \
  -e MAX_PARALLEL=3 \
  -e SKIP_COMPLETED=1 \
  -e REQUEST_LIMIT=3 \
  -v "$(pwd)":/app/LLMServingSim \
  -w /app/LLMServingSim \
  llmservingsim-sim:local \
  bash -lc 'experiments/2026-07-21-add_gpu_utilization/run_counterfactual_candidates.sh'
```

Available outputs can be collected and used to fit a provisional residual
model before the full sweep completes:

```bash
python3 experiments/2026-07-21-add_gpu_utilization/scripts/analyze_counterfactual_candidates.py --allow-partial
python3 experiments/2026-07-21-add_gpu_utilization/scripts/train_partial_candidate_model.py
```

The provisional model is diagnostic only. Candidate-ranking evaluation needs
multiple labeled candidates from several independent redirect decisions.

After all counterfactual runs complete, join predictions with actual TTFT and
evaluate model versus capacity-pressure ranking:

```bash
docker run --rm \
  -v "$(pwd)":/app/LLMServingSim \
  -w /app/LLMServingSim \
  llmservingsim-sim:local \
  bash -lc 'MPLCONFIGDIR=/tmp/matplotlib python3 experiments/2026-07-21-add_gpu_utilization/scripts/analyze_counterfactual_candidates.py'
```

Each replay follows the baseline learned policy up to the target decision,
forces only that request to the specified candidate, and then returns to the
baseline policy. This is a one-decision intervention, so earlier state is
identical while later state is allowed to reflect the forced placement.
