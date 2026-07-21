# Mixed workloadでの学習モデル価値評価

## 目的

固定input長・固定prefix reuse率では単純なcapacity heuristicでも十分だったため、
同一run内でrequest特性が変化する条件を作り、offline TTFT学習モデルが
KV nearest migrationおよび非学習Multi-candidateより有用かを評価する。

## Workload matrix

6 workloadsを生成した。

| 平均request rate | Seeds | Requests | Duration |
|---:|---|---:|---:|
| 2.5 rps | 1, 2, 3 | 300 each | 120.0 s |
| 3.33 rps | 1, 2, 3 | 300 each | 90.09 s |

各workloadは以下を同一run内で混在させる。

| Axis | Values | Count per workload |
|---|---|---:|
| Input tokens | 512, 2000, 4000, 6000, 8000, 10000 | each 50 |
| Prefix reuse ratio | 0, 0.25, 0.5 | each 100 |
| Traffic phase | normal, burst | 240, 60 |

Reuse token数は16-token block境界へ切り下げる。Inputとreuseの割当はseedごとに
独立にshuffleするが、workload全体と各traffic phase内の周辺分布は完全に均等である。
したがってburst区間にも各input長が10件、各reuse率が20件含まれる。

## Burst design

Burstはrequest index `[75, 105)`と`[195, 225)`の2区間で、合計60 requestsとした。
各intervalはphase別の指数分布から生成する。

- Normal nominal rate: 平均rateの0.8667倍
- Burst nominal rate: Normalの3倍
- 全intervalを最後に一様scaleし、workload全体の実現平均rateを正確に2.5または3.33 rpsへ合わせる

SeedによるPoisson変動を残すため、実現burst rateには幅がある。

| Condition | Normal realized | Burst realized |
|---|---:|---:|
| mixed_rate2p5_seed1 | 2.17 rps | 6.51 rps |
| mixed_rate2p5_seed2 | 2.18 rps | 6.17 rps |
| mixed_rate2p5_seed3 | 2.19 rps | 5.69 rps |
| mixed_rate3p33_seed1 | 2.90 rps | 8.24 rps |
| mixed_rate3p33_seed2 | 2.89 rps | 8.69 rps |
| mixed_rate3p33_seed3 | 2.85 rps | 10.13 rps |

## Source and reproducibility

Source rowは既存の`input10000_reuse00.jsonl`からseedごとにshuffleして使う。
User/GPU assignment、地理情報、network throughput、output tokensは保持し、input token IDsのみ
指定長へtruncateする。Arrival、request ID、payload bytes、reuse tokensは新条件に合わせて再生成する。

生成スクリプト:

```bash
python3 experiments/2026-07-21_mixed_workload_model_value/scripts/prepare_workloads.py
```

Manifest:

- [Workload manifest](configs/workload_manifest.csv)

## Compared policies

| Label | CLI policy | Learned model | Behavior when Home is blocked |
|---|---|---|---|
| KV nearest migrate | `NEAREST_MIGRATE_KV` | No | Redirect to the second-nearest GPU with KV handoff |
| Multi-candidate, no model | `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | No | Immediately choose the admissible non-home GPU with minimum capacity pressure |
| Multi-candidate, learned | `NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE` | Yes | Compare predicted local/redirect TTFT and rank all admissible targets by predicted TTFT |

非学習版はformula artifactをロードせず、formula featureも必要としない。Homeがadmissibleならlocal、
Homeがblockedかつ候補があれば即redirectし、全候補がblockedなら容量解放ごとに再評価する。
学習版は同じMulti-candidate探索にoffline TTFT formulaによるlocal/redirect gateと候補順位を加える。

## Simulation matrix

6 workloads × 3 policies = 18 runsを最大3並列で実行する。

```bash
docker run --rm \
  -e MAX_PARALLEL=3 \
  -e SKIP_COMPLETED=1 \
  -v "$(pwd)":/app/LLMServingSim \
  -w /app/LLMServingSim \
  llmservingsim-sim:local \
  bash -lc 'experiments/2026-07-21_mixed_workload_model_value/run_three_policy.sh'
```

比較では全体とtraffic phase別にMean/p50/p95/p99、TTFT breakdown、redirect数、
input/reuse cell別のpaired delta、候補分散、formula regretを確認する。

## Results

- [Final three-policy analysis](reports/final_three_policy_analysis.md)
- [Rate-level performance](figures/rate_level_performance.png)
- [Seed consistency](figures/seed_consistency.png)
- [Learned-model value by input length](figures/learned_value_by_input.png)
