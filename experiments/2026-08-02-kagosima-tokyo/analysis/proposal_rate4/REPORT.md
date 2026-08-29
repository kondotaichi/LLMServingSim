# Kagoshima–Tokyo experiment: rate-4 comparison

## Scope

The main comparison uses the three completed `results/proposal_rate4` arms. Each arm
contains the same 300-request, seed-1 workload for Llama 3.1 8B, 12 RTX 4090 GPUs,
PP=2, chunked prefill, prefix caching, and the same capacity-aware routing policy.
`kg_proposed` additionally enables proactive KV prewarming. Older `heavy`, probe,
PP=1, and archived runs use different configurations or arrival processes and are
therefore not pooled into this comparison.

## Results

| Arm | Mean TTFT | p50 | p95 | p99 | Change vs All Tokyo |
|---|---:|---:|---:|---:|---:|
| All Tokyo | 949.4 ms | 870.9 ms | 1351.1 ms | 1685.2 ms | baseline |
| Tokyo + Kagoshima | 955.0 ms | 875.2 ms | 1351.1 ms | 1686.2 ms | +0.6% |
| Tokyo + Kagoshima + prewarm | 939.9 ms | 876.8 ms | 1295.4 ms | 1568.3 ms | -1.0% |

Moving half of the nominal GPU placement to Kagoshima adds only 5.7 ms to mean TTFT
in the baseline. Proactive prewarming reduces mean TTFT by 15.1 ms relative to the
Kagoshima baseline and improves p95/p99 by 55.7/117.9 ms. The median is 1.7 ms worse,
so the benefit is concentrated in the tail rather than being a uniform per-request
speedup. Only 46 of 300 paired requests are faster than the Kagoshima baseline, while
a small number of large improvements dominate the mean and tail.

Mean attribution is dominated by prefill service (861–868 ms). Queueing contributes
71–80 ms and communication 19–26 ms. The Kagoshima baseline raises communication by
5.1 ms versus All Tokyo. Prewarming more than offsets this by lowering mean queueing
by 8.3 ms and prefill attribution by 6.8 ms versus the Kagoshima baseline.

The component columns are wall-clock attributions and are not always mutually
exclusive. In particular, KV migration hidden behind scheduler work appears in both
the prefill attribution and elapsed time accounting. Consequently, their mean sum is
11–18 ms above observed E2E TTFT, although the median residual is zero. The breakdown
figure shows the actual E2E value as a black diamond and must not be read as a strict
additive decomposition.

The proposed arm issued 23 redirects (26 in each baseline) and recorded seven
proactive-prewarm hits. With only one seed and 300 requests, the roughly 1% mean
improvement should be treated as suggestive; multiple seeds are needed for an error
bar or significance claim.

## Energy estimate

There is no measured power trace. The estimate integrates every recorded GPU
utilization window using a linear model:

`power = 50 W + utilization × (450 W - 50 W)`

| Arm | Recorded utilization | Estimated GPU energy | Electricity cost assumption |
|---|---:|---:|---:|
| All Tokyo | 47.78% | 77.65 Wh | ¥1.786 |
| Tokyo + Kagoshima | 47.80% | 77.66 Wh | ¥1.719 |
| Tokyo + Kagoshima + prewarm | 47.85% | 77.84 Wh | ¥1.723 |

The workload energy is effectively unchanged: +0.02% for the Kagoshima baseline and
+0.25% for prewarming relative to All Tokyo. Prewarming therefore buys lower tail
latency at a modeled cost of about 0.18 Wh (+0.23%) versus the Kagoshima baseline.
The monetary saving (~3.7%) comes from applying ¥14.7/kWh to six Kagoshima GPUs and
¥23.0/kWh to six Tokyo GPUs, not from using less energy.

Important limitation: GPU IDs 6–11 report 0% utilization in all three PP=2 runs,
while IDs 0–5 report about 93–99%. This may reflect how pipeline-stage activity is
recorded rather than physical idleness. The estimate follows the recorded data and
charges 50 W idle power to those six GPUs. If the second PP stage actually mirrors the
first stage, total GPU energy would be roughly 141 Wh instead of 78 Wh. Server CPU,
memory, networking, cooling/PUE, and KV-transfer energy are also excluded. Therefore
these figures are comparative GPU-only estimates, not facility energy measurements.
