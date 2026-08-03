# peak_3x 五手法比較 結果

ワークロードの人口・面積・GPU台数・コンテンツ根拠は [`design.md`](./design.md) を参照。
本ファイルはその設計に基づいて実行した `peak_3x` 負荷レベルでの結果を記録する。

> **注意(2026-08-02)**: 以下の結果はキャンパスセグメントが「20 requests/active-user/day」
> だった旧ワークロードで実行したものである。design.md §5の更新(キャンパスを
> 40/dayに変更)に伴い`workloads/hongo_peak_3x_seed1[_pp2].jsonl`を再生成し、
> 5アームを新ワークロードで再実行する。旧結果一式(`results/peak_3x_seed1_*/`、
> ログ、`peak_3x_ttft_breakdown.{png,csv}`)は削除せず
> [`../archive/peak_3x_campus20perday/`](../archive/peak_3x_campus20perday/)
> へ退避済み(キャンパス20/day版として参照可能)。以下の数値・図は旧版のもので、
> 新ワークロードでの再実行完了後に本文を置き換える。

## 使用ワークロード・設定

- データセット: `workloads/hongo_peak_3x_seed1.jsonl`(PP1用) /
  `workloads/hongo_peak_3x_seed1_pp2.jsonl`(PP2用、`scripts/transform_pp2.py`で
  物理GPU24台→論理12グループに変換)
- クラスタ設定: `configs/rtx4090_hongo.json`(PP1、24 flat node) /
  `configs/rtx4090_hongo_pp2.json`(PP2、12 node、`--pp-size 2`で24物理GPUへ展開)
- リクエスト数: 2000件/アーム(design.mdのコンテンツプールをそのまま使用)
- 共通APN設定: `--gpu-backbone-bandwidth-gbps 10.7 --apn-fixed-propagation-ns 300500
  --kv-staging-bandwidth-gbytes-per-s 33.8 --kv-staging-latency-ns 102.9`
  (既存クラスタ構成の値を踏襲、本郷固有のチューニングはしていない)

## 5アームの定義

| # | アーム名 | request-routing-policy | PP | Method C |
|---|---|---|---|---|
| 1 | no_redirect | `NEAREST_KV` | なし | — |
| 2 | redirect_no_kv | `NEAREST_MIGRATE` | なし | — |
| 3 | redirect_kv_nopp | `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | なし | — |
| 4 | redirect_kv_pp2 | 同上 | `--pp-size 2` | — |
| 5 | redirect_kv_pp2_c | 同上 | `--pp-size 2` | `--enable-proactive-kv-prewarm --proactive-kv-prewarm-pressure-threshold 0.6 --proactive-kv-prewarm-top-k 3`(Stage 2スイープで確認済みの本番設定) |

## 結果

図: [`../figures/peak_3x_ttft_breakdown.png`](../figures/peak_3x_ttft_breakdown.png)
CSV: [`../analysis/peak_3x_ttft_breakdown.csv`](../analysis/peak_3x_ttft_breakdown.csv)
生成スクリプト: [`../scripts/plot_ttft_breakdown.py`](../scripts/plot_ttft_breakdown.py)

| # | アーム | 平均TTFT | 対arm1 | p95 | p99 | max | redirect数 |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | no_redirect | 912 ms | — | 4150 ms | 12494 ms | 88708 ms | 0 |
| 2 | redirect_no_kv | 542 ms | -40.6% | 1652 ms | 4693 ms | 42231 ms | 226 |
| 3 | redirect_kv_nopp | 301 ms | -67.0% | 768 ms | 1518 ms | 2725 ms | 131 |
| 4 | redirect_kv_pp2 | 234 ms | -74.4% | 533 ms | 819 ms | 1641 ms | 14 |
| 5 | redirect_kv_pp2_c | 233 ms | -74.5% | 526 ms | 815 ms | 1641 ms | 14 |

TTFT breakdown内訳(平均、ms)。「PP transfer (est.)」は`prefill_service_ns`から
分離した推定値(下記「PP段間通信の扱い」参照)。

| # | Router queue | Scheduler queue | KV transfer | PP transfer (est.) | Compute/prefill | RTT/other |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 658.0 | 31.2 | 0.0 | 0.0 | 220.9 | 1.5 |
| 2 | 221.5 | 46.7 | 0.0 | 0.0 | 272.3 | 1.6 |
| 3 | 0.1 | 37.8 | 20.1 | 0.0 | 241.2 | 1.5 |
| 4 | 0.0 | 21.0 | 2.2 | 2.3 | 206.5 | 1.5 |
| 5 | 0.0 | 20.9 | 2.0 | 2.3 | 205.9 | 1.5 |

## 読み方

- **①→②(リダイレクト導入)**: 最寄り固定だとRouter queue(空きGPU待ち)が658msまで
  膨張。リダイレクトを許すだけで222msまで縮む。ただしKVを運ばないため宛先で
  プレフィルを再計算する分、Compute/prefillは220→272msへ増加
- **②→③(KV移送を追加)**: Router queueがほぼゼロになり、代わりに軽いKV transfer
  (20ms)が乗る。プレフィル再計算が不要になりCompute/prefillも272→241msに減少
- **③→④(PP2を追加)**: redirect数が131→14件に激減(1論理グループの実効容量が
  倍になり詰まりにくくなるため)。Compute/prefillも206.5msまで下がる
- **④→⑤(Method C)**: 差はほぼ誤差(234→233ms)。redirectが既に14件と少なく、
  先読みで救えるケースがほぼ残っていないため、この負荷レベルでは効果が飽和

## PP段間通信の扱い(訂正の経緯)

初回分析では「PP2化でCompute/prefillが下がったのは、シミュレータがPPの段間
通信コストをモデル化していないからではないか」と疑ったが、これは誤りだった。
`--no-cleanup-inputs`付きの小規模runで実際の`.et`(Chakraトレース)を直接
デコードして確認した結果、`layers_per_group = num_layers // pp_size`で
層が2ステージに分割され、境界に`COMM_SEND_NODE`/`COMM_RECV_NODE`
(16,777,216バイトの実転送)が挿入されていることを確認した
(`astra-sim/extern/graph_frontend/chakra/src/converter/llm_converter.py`)。
`network.yml`のintra-PP-group帯域(16GB/s、20µs)から計算される実コストは
1バッチあたり約1ms程度で、PP2による詰まり解消効果(数十ms)に対して小さく、
正味ではCompute/prefillが下がって見える——というのが正しい説明。

`requests.csv`にはPP転送時間を分離する列がないため、上表の
「PP transfer (est.)」は`plot_ttft_breakdown.py`内で
`input_tokens × hidden_size × fp_bytes × (pp_size-1) ÷ bandwidth + latency×(pp_size-1)`
から事後的に推定した近似値であり、ASTRA-Simが実際に計算した値そのものではない
(バッチ内の輻輳などは反映されない)。正確な値が必要な場合は
`scheduler.py`/`request.py`への新規計装とアーム4・5の再実行が必要
(詳細はメモリ`ttft_breakdown_include_pp_transfer.md`)。

## 既知の限界

- seed1のみ(design.md §7の通り、段階的にseed数を増やす予定)
- `peak_2x`/`peak_2p5x`は同5アームのうちarm3(redirect_kv_nopp)のみ完走データが
  残っている(`results/peak_2x_seed1_3_redirect_kv_nopp/`、
  `results/peak_2p5x_seed1_3_redirect_kv_nopp/`)。Docker Desktopが計3回
  クラッシュしたため、`peak_3x`のみ5アーム完全版を優先して完走させた
- Docker Desktopクラッシュの根本原因は`astra-sim/inputs/runs/`
  (bind mount配下)への大量小ファイル生成・削除がvirtiofsを詰まらせることと
  特定し、`--inputs-root /tmp/astra_runs/...`(コンテナローカル、非同期)へ
  切り替えて解消済み。以後の本郷runは全てこの設定を使うこと
