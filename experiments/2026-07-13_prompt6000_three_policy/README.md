# Prompt 6000・3ルーティング方式比較実験

このフォルダは、2026-07-13に実施したPrompt 6000・300リクエスト・3方式比較の関連ファイルをまとめた入口です。

## 実験条件

- 10 GPU（RTX 4090、各24 GB）
- 300リクエスト
- 到着ウィンドウ60秒
- 入力6000 tokens
- Prefix再利用率約50%
- `max_num_seqs=128`
- `max_num_batched_tokens=2048`
- chunked prefill有効
- prefix caching有効

比較方式:

- `NEAREST_KV`: 最寄りGPUで待機し、ローカルKVを使用
- `NEAREST_MIGRATE`: 容量不足時に別GPUへ転送し、cold prefill
- `NEAREST_MIGRATE_KV`: 容量不足時に別GPUへ転送し、KVもhandoff

## フォルダ構成

```text
2026-07-13_prompt6000_three_policy/
├── README.md
├── results/
│   ├── NEAREST_KV/
│   ├── NEAREST_MIGRATE/
│   └── NEAREST_MIGRATE_KV/
├── reports/
├── figures/
├── analysis/
└── scripts/
```

## 最初に読むもの

まず[60秒版・90秒版のワークロード設定まとめ](../2026-07-14_prompt6000_90s_three_policy/reports/02_workload_configuration_summary.md)を参照する。

1. [3方式の詳細分析](reports/03_three_policy_deep_analysis.md)
2. [3方式の基本比較](reports/02_three_policy_comparison.md)
3. [次のワークロード感度実験計画](reports/04_workload_sensitivity_plan.md)
4. [Chakra変換・トレースI/O高速化レポート](reports/05_simulation_runtime_optimization.md)

補助レポート:

- [NEAREST_KVとNEAREST_MIGRATE_KVの2方式比較](reports/01_nearest_kv_vs_migrate_kv.md)

## 生データ

各方式のディレクトリには次の4ファイルがあります。

- `requests.csv`: リクエスト単位の結果
- `users.csv`: ユーザ単位集計
- `gpus.csv`: GPU単位集計
- `metadata.json`: 実験条件と出力情報

| 方式 | ディレクトリ |
|---|---|
| `NEAREST_KV` | [results/NEAREST_KV](results/NEAREST_KV/) |
| `NEAREST_MIGRATE` | [results/NEAREST_MIGRATE](results/NEAREST_MIGRATE/) |
| `NEAREST_MIGRATE_KV` | [results/NEAREST_MIGRATE_KV](results/NEAREST_MIGRATE_KV/) |

## 解析生成物

- [リクエスト単位の3方式対応表](analysis/paired_requests.csv)
- [主要指標の集計JSON](analysis/summary.json)
- [グラフ一覧](figures/)

## 再生成コマンド

リポジトリルートから実行します。

```bash
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-13_prompt6000_three_policy/scripts/plot_nearest_kv_comparison.py
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-13_prompt6000_three_policy/scripts/plot_three_policy_comparison.py
MPLCONFIGDIR=/tmp/llmservingsim-matplotlib python3 experiments/2026-07-13_prompt6000_three_policy/scripts/analyze_three_policy.py
```

スクリプトは自身の位置から実験フォルダを解決するため、カレントディレクトリに依存せず同じ場所へ結果を再生成します。

## 移動前の保存場所

整理前は次の場所に分散していました。

- `NEAREST_KV`: `outputs/cell_apn/prompt6000_v4/NEAREST_KV/`
- `NEAREST_MIGRATE`: `outputs/cell_apn/prompt6000_v4/NEAREST_MIGRATE/`
- `NEAREST_MIGRATE_KV`: `outputs/cell_apn/prompt6000_v3/NEAREST_MIGRATE_KV/`
- レポート: `kondoFolder/`
- グラフ: `outputs/image/`
- 集計結果: `outputs/analysis/prompt6000_three_policy/`

現在は本フォルダを正規の保存場所とします。
