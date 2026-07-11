# 2026-07-07: 検証2-1-2 KV再利用率50%版

日付: 2026-07-07

## 目的

`2026-07-07-exp212-kv20pct-realistic-cache-report.md`で「前ターン分20%」の
現実的な設定を試した結果、B1/B2とCの差がほぼ拮抗した(12.6ms差)。KV再利用率が
上がるとKV移送コストも比例して増えるはずなので、**再利用率を50%に上げた場合**を
確認する。

## ワークロード

`geo_2gpu100_kv_workload_1to2_n50.jsonl`の`reuse_prefix_toks`を
`round(0.50 * input_toks)`で再計算(全リクエスト共通で50%)。

出力: `workloads/generated/geo_2gpu100_kv_workload_1to2_n50_kv50pct.jsonl`

## 実行コマンド・出力

前回(kv20pct)と同一構成、`DATASET`のみ`_kv50pct.jsonl`に変更。
出力: `results/exp212-kv50pct-nearest-{kv,reject,migrate,migrate-kv}.csv`

## 図

- `outputs/exp212_kv50pct/ttft_breakdown.png`
- `outputs/exp212_kv50pct/ttft_cdf.png`

![TTFT breakdown](../../outputs/exp212_kv50pct/ttft_breakdown.png)

![TTFT CDF](../../outputs/exp212_kv50pct/ttft_cdf.png)

## 結果

| 手法 | GPU0:GPU1 | rerouted | Mean E2E TTFT | Queue | KV transfer | Compute | RTT |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A `NEAREST_KV` | 17:33 | 0/50 | 2144.72ms | 2063.85ms | 0.00ms | 77.24ms | 3.62ms |
| B1 `NEAREST_REJECT` | 26:24 | 9/50 | **438.30ms** | 347.78ms | 0.00ms | 85.29ms | 3.63ms |
| B2 `NEAREST_MIGRATE` | 26:24 | 9/50 | **438.30ms** | 348.43ms | 0.00ms | 85.29ms | 3.63ms |
| C `NEAREST_MIGRATE_KV` | 26:24 | 9/50 | 475.43ms | 300.02ms | 92.61ms | 77.82ms | 3.63ms |

## KV再利用率と結果の関係(まとめ)

| KV再利用率 | KV transfer(平均) | B1/B2 Mean TTFT | C Mean TTFT | 差(B1/B2が速い分) |
| --- | ---: | ---: | ---: | ---: |
| 絶対上限1024トークン(実質最大87%) | 155.36ms | 427.14ms | 495.11ms | 68.0ms |
| 50%(今回) | 92.61ms | 438.30ms | 475.43ms | 37.1ms |
| 20% | 36.24ms | 538.65ms | 551.27ms | 12.6ms |

KV再利用率が上がるほど、移送すべきKVバイト数が増えて`NEAREST_MIGRATE_KV`の
KV転送コストが増加し(20%→50%→上限1024トークンの順で36ms→93ms→155ms)、
それに伴いB1/B2に対する不利さ(差)も12.6ms→37.1ms→68.0msと単調に拡大している。

一方でCompute削減効果(cold vs 再利用)は再利用率が上がるほど大きくなるが
(20%: 127→124ms、50%: 85→78ms程度)、KV転送コストの増加ペースの方が
Compute削減効果の増加ペースを上回るため、**再利用率が上がるほどCは
相対的に不利になる**という傾向が明確になった。

## 結論

- 今回の帯域・距離設定(1Gbps・5km)では、**KV再利用率が高いほど、
  KV移送(方式C)は相対的に不利になる**。これは直感(「たくさんキャッシュ
  できているなら運ぶ価値も上がるはず」)とは逆の結果に見えるが、実際には
  「運ぶ量が増える→転送コストが線形に増える」のに対し、「浮く計算時間」は
  頭打ちになりやすい(継続的バッチングの性質上、部分的な計算量削減は
  個々のリクエストの所要時間に反映されにくい、2026-07-07の別の議論参照)
  ため、この非対称性がKV再利用率が上がるほど拡大する。
- したがって「KV移送が有利になる条件」を探すなら、再利用率を上げる方向では
  なく、**GPU間バックボーンの帯域を上げる・距離を縮める**方向を試す方が
  筋が良い可能性が高い。
