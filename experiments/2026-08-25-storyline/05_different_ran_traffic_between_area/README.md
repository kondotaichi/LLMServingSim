# 05: Different RAN traffic between areas

## Purpose

This experiment adds a geographical RAN-load imbalance to the AI-demand
imbalance used in experiments 03 and 04. Tokyo retains 70% of AI requests and
uses a 23% Active TCP UE ratio. Kagoshima receives 30% of AI requests and uses
a 17% Active TCP UE ratio.

The comparison keeps the 900 total peak RRC-connected UEs, user placement,
requests, arrival times, model, eight GH200 GPUs, and network settings fixed.
Only the site-specific Active TCP ratios and the resulting AI-available VRAM
change.

## RAN VRAM model

```text
active_tcp_ue(gpu) = ceil(rrc_connected_ue(gpu) * site_active_tcp_ratio)
ran_vram_fraction(gpu) = 0.40 + 0.016 * active_tcp_ue(gpu)
ai_vram_gib(gpu) = 95.577 * (1 - ran_vram_fraction(gpu))
```

After integer rounding, Tokyo has 105 Active TCP UEs and Kagoshima has 79,
for 184 total. The previous uniform 20% setting has 183, so the experiment
approximately preserves total RAN load while changing its geographical
distribution.

| Site | Active TCP ratio | Mean RAN VRAM | Mean AI VRAM/GPU |
|---|---:|---:|---:|
| Tokyo | 23% | 82.0% | 17.20 GiB |
| Kagoshima | 17% | 71.6% | 27.14 GiB |

## Peak 5x comparison

- `local_only`: no global capacity sharing
- `redirect_cold`: global capacity sharing with prefix recomputation
- `kv_redirect`: global capacity sharing with KV-cache migration
- `pp_only`: PP=2 without global capacity sharing
- `proposed`: KV-cache migration with PP=2
- Offered load: Peak 5x only

The 20%/20% results from experiments 03 and 04 are the control. Reusing the
same workload isolates the effect of the different RAN traffic distribution.

## Reproduction

```bash
python3 experiments/2026-08-25-storyline/05_different_ran_traffic_between_area/scripts/prepare_experiment.py
experiments/2026-08-25-storyline/05_different_ran_traffic_between_area/scripts/run_peak5_5parallel.sh
```

The runner writes the same output structure as experiment 04: five result
directories, `analysis/peak_5x_summary.csv`, and four pairs of TTFT breakdown
and CDF figures under `figures/`.
