# Hongo 1x–10x: PP=1 と PP=2 の比較

## 結論

PP=1 は PP=2 より推定電力量・電気料金が約8–14%低い一方、3x以上ではTTFTが大幅に悪化した。平均TTFT差は4xで約+68%、5xで約+72%、10xで約+25%である。特にp95は10xの全東京配置で PP=1 が6541 ms、PP=2が3452 msとなった。

PP=1高負荷時の主因は計算時間ではなく、capacity redirectに伴うrouter待機である。提案構成では平均router待機が4xで328 ms、7xで947 ms、10xで1235 msに達した。一方、PP=2では全負荷でredirectが0件のため、router待機も0 msだった。

したがって、今回の条件では次の選択になる。

- 電力量・料金を優先し、長いtail latencyを許容できる: PP=1
- 3x以上でTTFT、特にp95を優先する: PP=2
- 低負荷: 差は小さい。2xではPP=1の平均TTFTがPP=2より約1–2%短い

## 主要比較

| 負荷・構成 | PP=1 平均TTFT | PP=2 平均TTFT | PP=1 p95 | PP=2 p95 | PP=1 電力 | PP=2 電力 | PP=1 料金 | PP=2 料金 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1x 全東京 | 279.7 ms | 264.6 ms | 554 ms | 489 ms | 62.5 Wh | 70.1 Wh | 1.437円 | 1.613円 |
| 1x 東京+鹿児島 | 284.9 ms | 269.7 ms | 564 ms | 492 ms | 62.5 Wh | 70.2 Wh | 1.174円 | 1.335円 |
| 4x 全東京 | 885.3 ms | 525.3 ms | 2749 ms | 1018 ms | 42.1 Wh | 48.0 Wh | 0.967円 | 1.103円 |
| 4x 東京+鹿児島 | 891.2 ms | 530.6 ms | 2713 ms | 1028 ms | 42.2 Wh | 48.0 Wh | 0.791円 | 0.897円 |
| 7x 全東京 | 1852.2 ms | 1285.2 ms | 5396 ms | 2255 ms | 40.6 Wh | 45.3 Wh | 0.935円 | 1.042円 |
| 7x 東京+鹿児島 | 1856.9 ms | 1290.5 ms | 5374 ms | 2255 ms | 40.7 Wh | 45.3 Wh | 0.761円 | 0.847円 |
| 10x 全東京 | 2393.2 ms | 1913.5 ms | 6541 ms | 3452 ms | 40.9 Wh | 45.2 Wh | 0.942円 | 1.039円 |
| 10x 東京+鹿児島 | 2401.7 ms | 1918.8 ms | 6421 ms | 3462 ms | 40.7 Wh | 45.2 Wh | 0.759円 | 0.845円 |

PP=1のPP=2比は、10負荷平均で次のとおりだった。

- 平均TTFT: 全東京 +36.5%、東京+鹿児島baseline +36.2%、proposed +36.5%
- 電気料金: 全東京 −10.7%、東京+鹿児島baseline −11.1%、proposed −10.9%
- 電気料金差の範囲: PP=1がPP=2より8.2–14.3%低い

## TTFT breakdown

TTFTは重複を避け、次の加算可能な4要素に分解した。

1. access network / residual
2. router capacity wait
3. scheduler queueing
4. prefill service

東京+鹿児島 proposed の代表値は次のとおりである。

| 負荷 | PP | network/residual | router待機 | scheduler待ち | prefill | 平均TTFT |
|---:|---:|---:|---:|---:|---:|---:|
| 1x | 1 | 6.5 ms | 0.0 ms | 30.8 ms | 247.8 ms | 285.1 ms |
| 1x | 2 | 6.5 ms | 0.0 ms | 19.7 ms | 243.4 ms | 269.7 ms |
| 4x | 1 | 6.2 ms | 327.8 ms | 211.8 ms | 351.8 ms | 897.6 ms |
| 4x | 2 | 6.5 ms | 0.0 ms | 120.3 ms | 402.6 ms | 529.5 ms |
| 7x | 1 | 10.1 ms | 947.2 ms | 493.1 ms | 407.3 ms | 1857.7 ms |
| 7x | 2 | 6.5 ms | 0.0 ms | 770.1 ms | 513.5 ms | 1290.2 ms |
| 10x | 1 | 12.7 ms | 1234.8 ms | 724.6 ms | 422.8 ms | 2394.9 ms |
| 10x | 2 | 6.5 ms | 0.0 ms | 1385.8 ms | 525.9 ms | 1918.3 ms |

PP=2も高負荷ではscheduler queueingが増えるが、PP=1ではそれにrouter capacity waitが加わる。この二重の待機がPP=1のtail latencyを悪化させている。

## 地理分散と料金

東京+鹿児島配置は、同じPP内の全東京配置に対して次の料金削減を示した。

- PP=1 baseline: 平均18.8%削減
- PP=1 proposed: 平均18.7%削減
- PP=2 baseline: 平均18.5%削減
- PP=2 proposed: 平均18.5%削減

平均TTFTの地理分散ペナルティは、PP=1 baselineで平均+0.64%、PP=2 baselineで平均+0.90%だった。鹿児島配置による料金削減は、PPの選択にかかわらず比較的安定している。

## Redirectとprewarm

PP=1では2xからredirectが発生し、3x以上では概ね74–101件だった。PP=2は全負荷で0件だった。

PP=1 proposed のprewarm hitは4x以降で1–5件に留まり、wasted prewarmは30–41件程度発生した。東京+鹿児島baseline比では平均TTFTが改善する負荷もあるが、最大改善は10xの−0.28%であり、多くの負荷ではわずかに悪化した。p95は4xで−4.21%、5xで−3.73%改善した一方、9xでは+5.90%悪化しており、一貫した優位性は確認できない。

## 電力推計上の注意

電力モデルはGPU利用率0%を50 W、100%を450 Wとする線形モデルである。単価は東京23.0円/kWh、鹿児島14.7円/kWhとした。

PP=1は12 GPUの記録利用率を直接積分した。PP=2は既知の利用率記録制約により後段GPUが0%となるため、6論理インスタンスの利用率を各2段へ複製して12 GPU分を推計した。したがって、PP=1とPP=2の絶対的な電力量差は、両pipeline stageの利用率が同程度というPP=2側の仮定に依存する。地理配置間の相対料金差は比較的頑健だが、PP間の約8–14%差は実機計測で検証する必要がある。

### 料金計算への入力と式

料金計算が直接読む入力は、各ランの `gpu_utilization_timeseries.csv` である。主に次の列を使用する。

- `gpu_id`: GPUまたはPP=2の論理インスタンスID
- `window_duration_ns`: 集計窓の長さ。通常は1秒
- `busy_time_ns`: その窓でGPUが処理中だった時間
- `idle_time_ns`: `window_duration_ns - busy_time_ns`
- `utilization_pct`: `busy_time_ns / window_duration_ns × 100`

各GPU・各時間窓の電力量は次式で求める。

```text
energy_J
  = 50 W × window_duration_ns / 1e9
  + (450 W - 50 W) × busy_time_ns / 1e9
```

これは、アイドル時でも50 Wを消費し、busy時間には利用率に応じて最大450 Wまで線形に増えるモデルである。したがって料金は時間に比例するが、比例係数となる平均電力がbusy率で変わる。

全GPU・全時間窓のエネルギーを地域別に合計し、次式で料金へ変換する。

```text
energy_kWh = energy_J / 3.6e6
cost_yen = energy_kWh × regional_rate_yen_per_kWh
```

地域単価は東京23.0円/kWh、鹿児島14.7円/kWhである。全東京構成は全GPUに東京単価を適用する。東京+鹿児島構成では、PP=1はGPU ID 0–5を東京、6–11を鹿児島とする。PP=2は記録される論理ID 0–2を東京、3–5を鹿児島とし、それぞれの利用率を2 pipeline stageへ複製する。

ここでいう時間はシミュレーション上のサービス時間であり、Docker上でシミュレータを実行するのに要した実時間ではない。また、この計算にCPU、ネットワーク機器、ストレージ、冷却設備の電力やPUEは含まれない。

## 生成物

- `summary.csv`: 60ランの統合集計
- `ttft_breakdown_{1,4,7,10}x.png`: 代表負荷のTTFT breakdown
- `power_cost_comparison.png`: 全構成の電力量・料金比較
- `{arm}_ttft_cost.png`: 構成別のTTFT・料金曲線

再生成コマンド:

```bash
python3 experiments/2026-08-02-kagosima-tokyo/scripts/compare_pp1_pp2_hongo.py
```
