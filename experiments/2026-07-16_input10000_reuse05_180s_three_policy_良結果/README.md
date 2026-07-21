# Input 10000・Prefix reuse 50%・180秒・3方式比較

90秒版でRouter queueが支配的だったため、同じ300 requestの到着間隔だけを2倍へ拡大した負荷感度実験です。

## 実験条件

- 10 GPU（RTX 4090、各24 GB）
- 300 requests
- 到着span 179.209912秒
- Input 10,000 tokens
- Prefix reuse 4,992 tokens（49.92%）
- `max_num_seqs=128`
- `max_num_batched_tokens=2048`
- Chunked prefill有効
- Prefix caching有効
- APN帯域10.7 Gbit/s

比較方式:

- `NEAREST_KV`: 最寄りGPUで待機
- `NEAREST_MIGRATE`: 第2近傍GPUへredirectし、cold prefill
- `NEAREST_MIGRATE_KV`: 第2近傍GPUへredirectし、KVもhandoff

## 入口

- [詳細分析レポート](reports/01_three_policy_analysis.md)
- [主要指標](analysis/summary.csv)
- [90秒版との比較](analysis/comparison_with_90s.csv)
- [7系列breakdown](analysis/seven_series_breakdown.csv)
- [Routing flow](analysis/routing_flows.md)
- [Phase1学習済みformulate modelの評価](reports/06_formula_phase1_model_analysis.md)

比較元:

- [Input/reuse 90秒sweep](../2026-07-14_input_reuse_90s_sweep/reports/06_input10000_reuse05_analysis.md)

## 再生成

```bash
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-16_input10000_reuse05_180s_three_policy/scripts/analyze_three_policy.py
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-16_input10000_reuse05_180s_three_policy/scripts/compare_with_90s.py
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-16_input10000_reuse05_180s_three_policy_良結果/scripts/analyze_formula_phase1_model.py
```

Simulationの再実行:

```bash
docker exec llmservingsim_sim_local bash -lc 'cd /app/LLMServingSim && MAX_PARALLEL=3 experiments/2026-07-16_input10000_reuse05_180s_three_policy/run_three_policy.sh'
```
