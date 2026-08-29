# Distributed vs. clustered GH200 simulation

This experiment compares two simulated eight-GPU deployments with identical
model, performance profile, scheduler, KV capacity, and request workload.

- `distributed`: eight geographically distributed GH200 replicas using the
  existing 10.7-Gbps / 300.5-us backbone model.
- `clustered_cloud`: eight one-GPU nodes colocated at one site and connected by
  the measured Miyabi-G InfiniBand fabric.

The clustered ASTRA-Sim fabric uses the physical RDMA measurements from
`../2026-08-21-local-hongo-workload-gh200-test/gh200-info.txt`: 24.8 GB/s and
1.45 us. Direct GPU-to-GPU migration uses the measured NCCL-facing values:
23.0 GB/s and 16.8 us.

Both environments use Llama-3.1-8B-Instruct, the GH200 vLLM 0.26.0 profile,
8192 max batched tokens, 1024 max sequences, BF16 weights/KV, 71.3 GiB KV
capacity per GPU, prefix caching, chunked prefill, and Peak 1x-10x repeat600.

Run all 40 cases with eight concurrent simulations:

```bash
./experiments/2026-08-24-simulate-both-local-and-cloudlike/scripts/run_all_4parallel.sh
```
