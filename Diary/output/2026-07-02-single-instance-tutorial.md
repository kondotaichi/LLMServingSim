# 2026-07-02: シングルインスタンス チュートリアル実行記録

## 目的
LLMServingSim 2.0 の最小構成（1ノード・1インスタンス・1GPU）でのシミュレーションを
実際に一通り動かし、環境構築の流れと出力の読み方を確認する。

## 環境
- 実行場所: `servingsim_docker` コンテナ（イメージ: `astrasim/tutorial-micro2024`）
  - `scripts/docker-sim.sh` で起動。`-v $REPO_ROOT:/app/LLMServingSim` でリポジトリ全体を
    バインドマウントしているため、コンテナ内で書き込んだ出力ファイルはホスト側にもそのまま残る。
- 事前準備で必要だったもの:
  - ASTRA-Sim 本体のビルド（`scripts/compile.sh` の `build/astra_analytical/build.sh` 相当）
    → 今回は既にビルド済みだった。
  - Chakra converter のインストール（初回は未インストールで失敗した）:
    ```bash
    pip3 install ./astra-sim/extern/graph_frontend/chakra
    ```

## 使用したクラスタ構成
`configs/cluster/single_node_single_instance.json`

| 項目 | 値 |
| --- | --- |
| モデル | `meta-llama/Llama-3.1-8B` |
| ハードウェア | `RTXPRO6000`（プロファイル済みvariantは `bf16` のみ） |
| NPU数 | 1（TP=1） |
| NPUメモリ | 96GB, 帯域1597GB/s |
| CPUメモリ | 512GB, 帯域256GB/s |
| ノード間リンク | 16GB/s, latency 20000ns |

## 実行コマンド

```bash
python -m serving --cluster-config 'configs/cluster/single_node_single_instance.json' \
    --dtype bfloat16 --block-size 16 \
    --dataset 'workloads/example_trace.jsonl' --output 'outputs/example_single_run.csv' \
    --num-req 10
```

### ハマったポイント
1. **`chakra` モジュール未インストール** — `graph_generator.py` が
   `python -m chakra.src.converter.converter` をサブプロセス起動する際に
   `ModuleNotFoundError: No module named 'chakra'`。上記の `pip3 install` で解消。
2. **`--dtype float16` を指定してエラー** —
   ```
   FileNotFoundError: Profile variant folder not found:
   ../profiler/perf/RTXPRO6000/meta-llama/Llama-3.1-8B/fp16
   ```
   `Llama-3.1-8B` は `bf16` variant でしかプロファイルされていないため。
   `--dtype bfloat16` に変更して解決。プロファイルされていない dtype を指定すると
   即座に variant フォルダが見つからずエラーになる（自動フォールバックはしない）。

## 使用データセット
`workloads/example_trace.jsonl`（10リクエストのサンプルトレース、`--num-req 10` で全件使用）

## 結果サマリ

### シミュレータが出力したスループット指標
| 指標 | 値 |
| --- | --- |
| Total simulation time (wall) | 0h 0m 18.030s |
| Total requests | 10 |
| Total clocks (ns) | 1,665,077,255 |
| Total latency (s) | 1.665 |
| Total input tokens | 120 |
| Total generated tokens | 591 |
| Request throughput (req/s) | 6.01 |
| Avg prompt throughput (tok/s) | 72.07 |
| Avg generation throughput (tok/s) | 354.94 |
| Total token throughput (tok/s) | 427.01 |

### 出力CSV (`outputs/example_single_run.csv`) から算出したリクエスト単位の統計
（コンソール出力はRichのライブ表示が端末キャプチャで一部潰れて読めなかったため、CSVから再集計した）

| 指標 | 値 |
| --- | --- |
| 平均 latency | 663.2 ms |
| 平均 queuing_delay | 4.4 ms |
| 平均 TTFT (Time to First Token) | 15.7 ms |
| 平均 TPOT (Time per Output Token) | 11.15 ms |
| 総入力トークン数 | 120 |
| 総出力トークン数 | 591 |

## 考察
- 1リクエストあたりの平均出力トークン数は約59トークン（591/10）に対し、平均latencyは
  663ms。TTFT（15.7ms）+ TPOT×出力トークン数（11.15ms × 59 ≈ 658ms）でほぼ説明でき、
  デコード側のレイテンシが支配的というシングルバッチ・低同時実行数のワークロードとしては
  妥当な内訳。
- `queuing_delay` の平均は4.4msと小さく、`max-num-seqs`(既定128) に対してリクエスト数が
  10件しかないため、ほぼキューイングなしで即座にバッチに乗っている（NPUメモリ使用率も
  実行ログで15.6%程度と余裕あり）。
- Prefix Cache Hit ratio は 0.00% — `example_trace.jsonl` の10件が共通プレフィックスを
  持たない独立リクエストであるため、prefix caching の効果を見るには別データセット
  （例: `workloads/sharegpt-*.jsonl` のような共通システムプロンプトを持つもの）が必要。
- 今回はTP=1・単一インスタンスの最小構成のため、ASTRA-Simの通信シミュレーション
  （ALLREDUCE/ALLTOALL）は実質発生していない。マルチGPU/マルチインスタンス構成
  （`single_node_multi_instance.json` 等）で動かすと通信オーバーヘッドの効果が見えるはず。

## 次にやってみたいこと
- [ ] `single_node_multi_instance.json` でマルチインスタンス構成を試す
- [ ] `workloads/sharegpt-*.jsonl` で prefix caching の効果を確認する
- [ ] `python -m bench run` → `python -m bench validate` で実vLLMとの精度比較を行う
