# Azure self-hosted vLLM Level 1 results

計測日: 2026-08-19

## 結論

MacからAzure Korea CentralのvLLMへHongo Peak 1x〜10xをopen-loop replayした。
Azure構成はH100 NVL 94 GBを1枚使うvLLM replica 8個、Nginx `least_conn` router、
モデルは`meta-llama/Llama-3.1-8B`である。

Peak 1x〜10xの各600件を完走した。各倍率で599件のTTFTを取得できた。
残る同一request 1件はHTTP 200だったが生成textが空でTTFTを定義できず、1回再送しても再現した。
通信・HTTP failureによる欠測ではない。

Azureは、比較対象としたローカル12 GPU（RTX 4090、提案手法arm 4、PP=2 + KV migrate）
シミュレーションより、全倍率でmean TTFTが短かった。Peak 1xではmean TTFTが37.5%、
mean TPOTが77.0%短い。
一方、Azureの後半300件はPeak 7x以上でTTFT増加が大きく、継続負荷ではqueue蓄積が示唆される。

## 比較条件

- Workload正本: `experiments/2026-08-01_hongo_workload/workloads/`
- 600件化: 元300件の到着間隔を保って2周期を連結
- 実vLLM用の2周期目: input token列を1 token巡回し、token数を保ったままprefix cache hitを防止
- Azure: H100 NVL x8、8 independent replica、TP=1、Nginx `least_conn`
- Local: RTX 4090 x12のシミュレーション、提案手法arm 4 `redirect_kv_pp2`、PP=2 + KV migrate
- 全倍率: 同一時期に実行した元の300-request sweepを使用し、Azureも先頭300件に限定
- Azure TTFT/TPOTはMacのmonotonic clockで測定し、SSH tunnelを含むE2E値

これはGPU性能を正規化した比較ではなく、クラウド集中構成とローカル12 GPU構成のsystem比較である。
またLocal値は実機計測ではなくLLMServingSimの出力である。

## Azure対Local

| Peak | 比較件数 | Azure TTFT mean (ms) | Local TTFT mean (ms) | TTFT差 | Azure TPOT mean (ms) | Local TPOT mean (ms) | TPOT差 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1x | 299 / 300 | 164.9 | 263.7 | -37.5% | 6.7 | 29.3 | -77.0% |
| 2x | 299 / 300 | 183.7 | 312.2 | -41.1% | 8.8 | 43.0 | -79.6% |
| 3x | 299 / 300 | 229.0 | 399.8 | -42.7% | 12.1 | 54.6 | -77.8% |
| 4x | 299 / 300 | 264.6 | 510.4 | -48.2% | 16.7 | 60.5 | -72.4% |
| 5x | 299 / 300 | 296.2 | 726.3 | -59.2% | 26.4 | 63.6 | -58.5% |
| 6x | 299 / 300 | 306.7 | 982.0 | -68.8% | 28.6 | 64.0 | -55.3% |
| 7x | 299 / 300 | 369.7 | 1263.4 | -70.7% | 43.5 | 64.0 | -32.0% |
| 8x | 299 / 300 | 427.6 | 1523.0 | -71.9% | 48.1 | 64.1 | -25.0% |
| 9x | 299 / 300 | 710.4 | 1730.8 | -59.0% | 59.3 | 63.7 | -6.9% |
| 10x | 299 / 300 | 723.2 | 1907.7 | -62.1% | 69.5 | 63.8 | +8.9% |

高負荷ではAzureのTPOTがLocal simulationを上回るが、TTFTは依然短い。
LocalではqueueingがTTFTを大きく押し上げる一方、Azureの8 replicaは少なくとも先頭300件では
より多くのrequestを低いTTFTで処理している。

## Azure内の継続負荷

| Peak | 前半 TTFT mean (ms) | 後半 TTFT mean (ms) | 後半の変化 |
|---:|---:|---:|---:|
| 1x | 164.9 | 178.2 | +8.1% |
| 2x | 183.7 | 194.4 | +5.8% |
| 3x | 229.0 | 210.8 | -7.9% |
| 4x | 264.6 | 255.0 | -3.6% |
| 5x | 296.2 | 314.5 | +6.2% |
| 6x | 306.7 | 358.5 | +16.9% |
| 7x | 369.7 | 624.0 | +68.8% |
| 8x | 427.6 | 851.3 | +99.1% |
| 9x | 710.4 | 873.8 | +23.0% |
| 10x | 723.2 | 1237.8 | +71.1% |

1x〜5xは前半と後半のmean TTFT差が±10%以内である。6xで増加が始まり、7x以上では
明確な悪化が見える。離散点からは、このAzure構成の安定上限はPeak 5x〜7xの間にある可能性が高い。
確定には6x近辺の長時間・複数seed計測とserver-side queue telemetryが必要である。

## Failureと再送

Primary送信後に失敗requestだけを1回再送した。全倍率で同じrequest ID 211が再送後も
`missing first token`となった。HTTP statusは200であり、短い生成が空のdecoded textになったため、
Level 1の「最初の非空content」を満たさない。成功率は各倍率99.83%（599/600）である。

再送attemptを含むraw logでは同じrequest IDが複数行になり、`attempt`が大きい行を最終結果とする。

## Artifacts

- 集計CSV: `analysis/azure_vs_local_proposed_pp2.csv`
- workload manifest: `workloads/manifests/hongo_peak_1to10_repeat600.csv`
- replay runner: `scripts/replay_remote_vllm.py`
- workload生成: `scripts/prepare_repeat600_workloads.py`
- 比較集計: `scripts/compare_remote_local.py`
- raw results: `results/remote/peak_{1..10}x_repeat600_v3_20260819/`（Git対象外）

誤った2,000件workloadで行ったpilotと、token namespaceが実vLLMで無効だったv1/v2 runは、
Primary結果には使用していない。
