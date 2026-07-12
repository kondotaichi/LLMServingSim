# 10 GPU実験用WildChat地域負荷比率ワークロード

## 目的

このワークロードは、ShareGPT由来のリクエスト形状と、WildChatで観測された地域別・
時間帯別の相対負荷を組み合わせたものである。独立したRTX 4090インスタンス10基と、
20ユーザを対象とする。

## 入力

- リクエスト内容およびトークンID:
  `workloads/sharegpt-llama-3.1-8b-300-sps10.jsonl`
- 地域別負荷比率:
  `/Users/taichikondo/wildchat-analysis/results/figures/state/10min/us/01_top_regions_time_requests.csv`
- 比率として使用する列: `avg_requests_per_active_user`
- 曜日区分: `weekday`（平日）
- 乱数seed: `42`
- 日内負荷の圧縮後時間: `60`秒

WildChatの値は相対的なサンプリング重みとしてのみ使用し、絶対リクエストレートの
決定には使用しない。初期ワークロードには、入力ファイルに含まれる重複のない
ShareGPTリクエスト300件をそのまま収録する。

## 配置

地域をアルファベット順に並べ、GPUインスタンスへ1対1で対応付ける。

| インスタンス | 地域 | ユーザID |
| ---: | --- | --- |
| 0 | Arizona | 0, 1 |
| 1 | California | 2, 3 |
| 2 | Florida | 4, 5 |
| 3 | Illinois | 6, 7 |
| 4 | Michigan | 8, 9 |
| 5 | New York | 10, 11 |
| 6 | Ohio | 12, 13 |
| 7 | Pennsylvania | 14, 15 |
| 8 | Texas | 16, 17 |
| 9 | Virginia | 18, 19 |

各リクエストについて、WildChatの比率に応じて
`(region, local_10min_slot)`の組をサンプリングする。到着時刻は、選択された10分枠の
中から一様分布で決定する。各地域内では2ユーザを交互に割り当てる。生成される
タイムスタンプは、各地域のローカル時刻における平日24時間分の負荷比率を維持したまま、
1分（60秒）へ線形圧縮する。州間でUTC時刻を同期したトレースではない点に注意する。

## 生成ワークロード

`workloads/generated/wildchat_regional/sharegpt_300_weekday_ratio_1min.jsonl`

次のコマンドで再生成できる。

```bash
python -m workloads.generators regional-ratio \
  --input workloads/sharegpt-llama-3.1-8b-300-sps10.jsonl \
  --ratios /Users/taichikondo/wildchat-analysis/results/figures/state/10min/us/01_top_regions_time_requests.csv \
  --day-type weekday \
  --users-per-region 2 \
  --duration-seconds 60 \
  --seed 42 \
  --output workloads/generated/wildchat_regional/sharegpt_300_weekday_ratio_1min.jsonl
```

## 重要な制約と次の拡張工程

このファイルで確定するのは、リクエスト内容、ユーザID、地域別GPU割り当て、到着時刻で
ある。`NEAREST_REJECT`、`NEAREST_MIGRATE`、`NEAREST_MIGRATE_KV`を実行する前に、
州およびGPUの固定座標、第2近傍GPUのフィールド、APN通信パラメータ、
`reuse_prefix_toks`を付加する必要がある。これらの値は、この段階では根拠なく仮定して
いない。

各ShareGPT行を1回ずつ使用するため、初期ワークロードでは、同一行の反復による不自然な
Prefix cache hitを回避できる。より大規模な実験では、この300行を繰り返し利用せず、
先に重複のないShareGPTリクエストを追加生成すること。
