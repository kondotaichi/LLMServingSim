# 鹿児島・東京配置実験: rate-4 比較

## 比較対象

主比較には `results/proposal_rate4` の3条件を用いる。いずれも Llama 3.1 8B、
RTX 4090 12基、PP=2、300要求、seed=1、同じ rate-4 負荷および同じ
capacity-aware routing である。`kg_proposed` のみ proactive KV prewarming を有効に
している。古い heavy、probe、PP=1、archive の結果は構成または到着過程が異なるため、
この比較には混ぜない。

## TTFT

| 条件 | 平均 | p50 | p95 | p99 | All Tokyo比 |
|---|---:|---:|---:|---:|---:|
| All Tokyo | 949.4 ms | 870.9 ms | 1351.1 ms | 1685.2 ms | 基準 |
| Tokyo + Kagoshima | 955.0 ms | 875.2 ms | 1351.1 ms | 1686.2 ms | +0.6% |
| Tokyo + Kagoshima + prewarm | 939.9 ms | 876.8 ms | 1295.4 ms | 1568.3 ms | -1.0% |

東京のGPUの半分を鹿児島へ移したベースラインでは、平均TTFTは5.7 ms増加した。
通信時間の平均が All Tokyo より5.1 ms増えたことと整合する。一方、prewarmは
Tokyo + Kagoshima baselineに対し平均TTFTを15.1 ms、p95を55.7 ms、p99を
117.9 ms短縮した。p50は1.7 ms悪化しているため、改善は全要求に一様ではなく、
主にtail latencyの削減である。

要求IDで対応付けると、prewarm条件がKagoshima baselineより速い要求は46/300、
遅い要求は50/300、同値は204/300であった。少数の大きな改善が平均値と上位分位を
押し下げている。prewarm hitは7件であり、単一seed・300要求の結果なので、約1%の
平均改善を一般化するには複数seedでの再実験が必要である。

## TTFT内訳

| 条件 | 通信 | Queueing | Prefill service | 観測E2E TTFT |
|---|---:|---:|---:|---:|
| All Tokyo | 21.0 ms | 80.1 ms | 866.6 ms | 949.4 ms |
| Tokyo + Kagoshima | 26.1 ms | 79.5 ms | 867.8 ms | 955.0 ms |
| Tokyo + Kagoshima + prewarm | 19.1 ms | 71.2 ms | 861.0 ms | 939.9 ms |

支配項は全条件でprefill serviceである。prewarmはKagoshima baselineに対し、
通信を7.0 ms、queueingを8.3 ms、prefill attributionを6.8 ms減らしている。
ただし各列は要求が経験したwall-clock attributionで、相互排他的ではない。
特にスケジューラ処理と重なるKV migrationは重複計上され得るため、積み上げ棒の合計は
実測E2E TTFTより11–18 ms大きい。黒い菱形が実際の平均E2E TTFTである。

## GPU電力量の推定

電力トレースは存在しないため、各GPUの記録利用率に対し
`P = 50 W + utilization * (450 W - 50 W)` を積分した。

| 条件 | 記録利用率 | 推定GPU電力量 | 想定電気料金 |
|---|---:|---:|---:|
| All Tokyo | 47.78% | 77.65 Wh | 1.786円 |
| Tokyo + Kagoshima | 47.80% | 77.66 Wh | 1.719円 |
| Tokyo + Kagoshima + prewarm | 47.85% | 77.84 Wh | 1.723円 |

処理に必要な推定GPU電力量はほぼ同じである。prewarmの追加分はKagoshima baseline比
0.18 Wh、0.23%に留まる。一方、想定料金は東京23.0円/kWh、鹿児島14.7円/kWhを
適用したため、分散配置はAll Tokyoより約3.7%安い。この差は省エネではなく立地別単価差
による。

重要な制約として、PP=2の3実験すべてでGPU 6–11の利用率が0%と記録され、GPU 0–5は
約93–99%である。これは後段pipeline stageの物理的アイドル状態ではなく、計測方法の
制約である可能性が高い。本推定は0%側にも50 Wのidle powerを課しているが、後段が前段と
同程度に動作していたなら総GPU電力量は約78 Whではなく約141 Whになる。CPU、メモリ、
ネットワーク、冷却/PUE、KV転送の電力も含まないため、絶対値ではなく条件間比較として
扱うべきである。
