# Full-GPU routing scan scaling

This experiment measures the wall-clock cost of the router's capacity snapshot and
minimum-pressure selection across every candidate GPU. The default run measures both
idle GPUs and GPUs with 32 active requests. It deliberately measures the
intended O(N) scan and excludes the formula policy's global-feature recomputation,
which is a separate O(N^2) implementation issue.

Run from the repository root:

```bash
python3 experiments/2026-07-23_router_full_scan_scaling/benchmark_full_scan.py
```

Outputs:

- `results/measurements.csv`: repeated timing summary for each candidate-set size
- `results/fit.json`: linear fit and extrapolated scan times

The benchmark uses `time.perf_counter_ns()`. Results are host-specific wall-clock
measurements and are not automatically added to simulated request latency.
