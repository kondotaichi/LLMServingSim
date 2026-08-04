# peak_5x @ 300 requests (seed1) — archived 2026-08-04

300-request run of the unbalanced (gaussian-hotspot, top PP-group ~67%) workload.

- **Arms 3,4,5,6 complete** (results/, plus figures/ and analysis/ breakdown+CDF).
- **Arms 1,2 (no_redirect / NEAREST_MIGRATE) omitted**: under the strong hotspot,
  the whole ~67% load piles on the 2 hotspot GPUs; these naive policies never
  spread it, so the sim entered a pathologically slow saturated-decode tail
  (27 min wall-clock still only at sim-time 12s, rate collapsing) and was killed.
- Headline (arm4 vs arm5, identical routing, 137 redirects both): carrying KV
  across the redirect cut mean TTFT 2354->1441 ms; the benefit is dominated by
  relieving the scheduler-queue feedback on the saturated PP instance.

The live results/figures/analysis were then regenerated at 100 requests so the
naive baselines (arms 1,2) converge and a full 6-arm reference comparison exists.
