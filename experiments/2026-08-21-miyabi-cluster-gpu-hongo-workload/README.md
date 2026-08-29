# Miyabi 8-node GPU Hongo workload experiment

This experiment replays the same accepted workload and client settings used by the
Azure v3 measurements, from this Mac to a vLLM-compatible endpoint hosted on Miyabi.
It measures client-observed TTFT, TPOT, completion latency, throughput, failures, and
launch lag. Workload files remain in the Azure experiment directory so there is only
one canonical copy.

## Fixed comparison contract

- Model: `meta-llama/Llama-3.1-8B`
- Workload: Hongo Peak 1x-10x repeat600, seed 1, controlled output length
- Requests per full run: 600
- Arrival: open loop using `request_send_time_ns`
- Generation: temperature 0, top-p 1, streaming, EOS ignored
- Client concurrency: 512
- Launch-lag rejection threshold: p99 greater than 100 ms
- Client location: this Mac; the measured TTFT therefore includes the Mac-to-Miyabi path
- Miyabi allocation: 8 nodes with 1 GPU per node (8 GPUs total)
- Initial serving topology: 8 replicas, each using TP=1

The workload manifest and hashes are at
`../2026-08-17_azure_selfhost_vs_local/workloads/manifests/hongo_peak_1to10_repeat600.csv`.
The replay implementation is the exact runner used for the accepted Azure v3 runs.

## One-time setup

Use Python 3.9 or later and install the only non-standard client dependency:

```bash
python3 -m venv .venv-miyabi-client
source .venv-miyabi-client/bin/activate
python3 -m pip install aiohttp==3.13.3
```

Create the ignored local configuration and fill in all `REPLACE_ME` values:

```bash
cp experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/config.env.example \
  experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/config.env
```

`MIYABI_ENDPOINT` may be the public/router URL or a local SSH tunnel such as
`http://127.0.0.1:18080/v1`. Record the tunnel or ingress topology separately because
it affects the interpretation of E2E TTFT. `MIYABI_REPLICA_COUNT` and
`MIYABI_TENSOR_PARALLEL_SIZE` must describe the deployed serving topology, not merely
the allocation size.

## Preflight and execution

First confirm that the endpoint returns the configured model. Never print the real API
key or commit `config.env`.

```bash
source experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/config.env
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${MIYABI_API_KEY}" \
  "${MIYABI_ENDPOINT%/}/models"
```

Run a 10-request smoke test at Peak 1x, inspect its `summary.json`, then run the full
load sweep in ascending order. Do not start a higher load if the smoke test has token
mismatches, failures, or excessive launch lag.

```bash
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 1 smoke
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 1 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 2 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 3 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 4 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 5 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 6 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 7 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 8 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 9 full
experiments/2026-08-21-miyabi-cluster-gpu-hongo-workload/run_replay.sh 10 full
```

Each run writes `metadata.json`, request-level `requests.jsonl`, `summary.json`, and
`run.log` below the ignored `results/<run-id>/` directory. A run is accepted only when
`summary.json::accepted` is true. Preserve server-side vLLM/router logs and GPU telemetry
using the same run ID and UTC interval shown in `metadata.json`.

## Values still required from the Miyabi deployment

Before measurement, fill in and verify:

- reachable endpoint or SSH tunnel procedure;
- GPU model and actual node/GPU allocation;
- serving topology (replicas, TP size, and router policy);
- vLLM version, container/image identity, model revision, dtype, prefix caching, and
  chunked-prefill settings;
- whether the router returns `X-VLLM-Backend` and `X-Request-ID` headers;
- server telemetry/log collection commands and clock synchronization status.

The replay runner records the Miyabi vLLM version, container digest, and model revision
from `config.env`; placeholder values are rejected before a run starts.
