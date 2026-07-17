# Prompt 6000・90秒・3ルーティング方式比較実験

300リクエストの到着ウィンドウを60秒から90秒へ伸ばし、queue負荷を緩和した比較実験です。

## 実験条件

- 10 GPU（RTX 4090、各24 GB）
- 300リクエスト
- 到着ウィンドウ90秒
- 入力6000 tokens
- Prefix再利用率約50%
- `max_num_seqs=128`
- `max_num_batched_tokens=2048`
- chunked prefill有効
- prefix caching有効

比較方式:

- `NEAREST_KV`: 最寄りGPUで待機
- `NEAREST_MIGRATE`: 容量不足時に別GPUへ転送し、cold prefill
- `NEAREST_MIGRATE_KV`: 容量不足時に別GPUへ転送し、KVもhandoff

## フォルダ構成

```text
2026-07-14_prompt6000_90s_three_policy/
├── README.md
├── results/          # 3方式の生CSVとmetadata
├── reports/          # 日本語分析レポート
├── figures/          # 比較グラフ
├── analysis/         # paired CSVと集計JSON
└── scripts/          # 再生成スクリプト
```

## 入口

- [60秒版・90秒版のワークロード設定まとめ](reports/02_workload_configuration_summary.md)
- [詳細分析レポート](reports/01_three_policy_analysis.md)
- [容量ベースredirectと最寄りGPU待機の比較](reports/03_redirect_vs_wait_local_analysis.md)
- [Redirect先集中とScheduler queueへの影響](reports/04_redirect_concentration_analysis.md)
- [現行redirect routingの課題と適応型routing設計案](reports/05_adaptive_routing_design.md)
- [主要指標の集計](analysis/summary.json)
- [60秒版との比較集計](analysis/comparison_with_60s.json)
- [リクエスト単位の対応表](analysis/paired_requests.csv)
- [グラフ一覧](figures/)

比較元の60秒版:

- [2026-07-13 Prompt 6000・60秒実験](../2026-07-13_prompt6000_three_policy/README.md)

## 再生成コマンド

リポジトリルートから実行します。

```bash
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_prompt6000_90s_three_policy/scripts/plot_three_policy_comparison.py
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_prompt6000_90s_three_policy/scripts/analyze_three_policy.py
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_prompt6000_90s_three_policy/scripts/compare_with_60s.py
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_prompt6000_90s_three_policy/scripts/analyze_redirect_counterfactual.py
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-14_prompt6000_90s_three_policy/scripts/analyze_redirect_concentration.py
```

スクリプトは自身の位置から実験フォルダを解決するため、生成先は常にこのフォルダ内になります。

## 元の保存場所

整理前の結果は `outputs/cell_apn/prompt6000_90s_v1/` にありました。現在は `results/` を正規の保存場所とします。
