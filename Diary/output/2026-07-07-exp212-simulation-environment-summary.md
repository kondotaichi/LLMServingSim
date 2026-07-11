# exp212 シミュレーション実験環境まとめ

日付: 2026-07-07

実機再現(`kondoFolder/exp212_real_hw_requirements.md`)の作業中、シミュレーション側の
前提が実機側に正確に伝わっていない疑いが出てきたため、`exp212`のシミュレーション環境
そのものを一次情報として整理する。

対象ワークロード: `workloads/generated/geo_2gpu100_kv_workload_1to2_n50.jsonl`
対象結果: `results/exp212-1to2-nearest-{kv,reject,migrate,migrate-kv}.csv`

---

## 1. ワークロード

### 1.1 リクエスト件数・GPU割当比率

| 項目 | 値 |
| --- | --- |
| 総リクエスト数 | 50 |
| 最近傍GPU割当(リダイレクト前) | GPU0: 17件 / GPU1: 33件(狙いは1:2) |
| 実現比率 | 約1:1.94(小サンプルのため厳密な1:2からはずれる) |

割当はユーザの座標(10km四方、GPU0・GPU1からの実距離)から決まる。地域ラベルを
直接使わず、`home(i) = argmin_j distance(i, j)` で最近傍GPUを再計算している。

### 1.2 リクエストごとのトークンサイズ

| 項目 | 値 |
| --- | --- |
| input_toks(プロンプト長) | 最小268 / 最大3452 / 平均973.76 |
| output_toks(出力長) | 最小519 / 最大784 / 平均690.3 |
| 合計長(input+output)の最大 | 4197 |

トークン長はShareGPT由来の実データセットから採取したものをそのまま使用している
(人工的な分布ではない)。

### 1.3 到着タイミング

全50件が同時刻ではなく、**0.055秒〜6.257秒の間に分散して到着**する(いわゆる
バースト到着ではあるが、厳密な「t=0に全件同時」ではない)。到着間隔は
ワークロード生成時のポアソン的な到着モデルに従う。

**注意:** 実機側の再現実験では`--arrival-mode simultaneous`を選択し、全50件を
文字通り同時刻扱いにしていた。これはシミュレーションの実際の到着分布
(0.055〜6.257秒に分散)とは異なる設定であり、再現性の観点では
`--arrival-mode workload`(ワークロードの`arrival_time_ns`をそのまま使う)に
揃えるべきである。

### 1.4 KV再利用量(`reuse_prefix_toks`)

各リクエストについて `reuse_prefix_toks = min(1024, input_toks)` が付与されている
(生成時に`--kv-reuse-prefix-toks 1024`を指定)。つまり:

- `input_toks ≤ 1024`のリクエストは、プロンプト全体が再利用可能とみなされる
- `input_toks > 1024`のリクエストは、先頭1024トークン分だけが再利用可能とみなされる

この値は「方法A(`NEAREST_KV`)のローカルKV再利用」と「方法C
(`NEAREST_MIGRATE_KV`)のGPU間KV移送」の両方で、再利用/移送するトークン数として
使われる。

---

## 2. vLLM相当のエンジンパラメータ(シミュレータ側)

シミュレータ(`python -m serving`)は実際のvLLMを起動せず、プロファイル済みの
実測latencyテーブルを引いてTTFT/TPOTを計算する。そのため以下は「vLLMサーバの
起動フラグ」ではなく、**シミュレータのCLI引数・クラスタ設定**である。

| 項目 | 値 | 指定方法 |
| --- | --- | --- |
| ハードウェアプロファイル | RTX4090 | `configs/cluster/single_node_multi_instance_rtx4090.json`の`hardware`フィールド |
| モデル | `meta-llama/Llama-3.1-8B` | クラスタ設定の`model_name` |
| dtype | bfloat16(既定値) | 明示指定なし(`--dtype`省略時のデフォルト) |
| `max_num_seqs` | **24** | `--max-num-seqs 24` |
| `max_num_batched_tokens` | **2048**(既定値) | 明示指定なし(`serving/__main__.py`のデフォルト) |
| `kv_cache_dtype` | auto(既定値) | 明示指定なし |
| prefix caching | 方式による(下記) | `--enable-prefix-caching` / `--no-enable-prefix-caching` |
| GPU間バックボーン帯域 | 1Gbps | `--gpu-backbone-bandwidth-gbps 1`(MIGRATE系のみ) |
| GPU間バックボーン距離 | 5000m(5km) | `--gpu-backbone-distance-m 5000`(MIGRATE系のみ) |

### 方式ごとのCLI差分

| 方式 | 追加フラグ |
| --- | --- |
| `NEAREST_KV` | なし(prefix caching有効のまま) |
| `NEAREST_REJECT` | `--no-enable-prefix-caching` |
| `NEAREST_MIGRATE` | `--gpu-backbone-bandwidth-gbps 1 --gpu-backbone-distance-m 5000 --no-enable-prefix-caching` |
| `NEAREST_MIGRATE_KV` | `--gpu-backbone-bandwidth-gbps 1 --gpu-backbone-distance-m 5000` |

`NEAREST_REJECT`/`NEAREST_MIGRATE`で`--no-enable-prefix-caching`を付けているのは、
KVを引き継がない(cold prefillにする)という実験設計上の意図であると同時に、
シミュレータのprefix cache会計処理にあった既知のバグを避けるための実務的な
回避策でもある。

---

## 3. ユーザ(UE)↔GPU間のRTTモデル

### 3.1 モデルの種類

`propagation_only`モデル(距離比例の伝搬遅延 + ペイロードサイズに基づく
シリアライゼーション時間のみ。ネットワークの輻輳・ジッタ・パケットロスは
考慮しない)。

### 3.2 パラメータ

| 項目 | 値 |
| --- | --- |
| UE↔GPU間スループット | 10Mbps(`network_throughput_mbps=10.0`) |
| 距離あたり遅延 | 5ns/m(`distance_latency_ns_per_meter=5.0`) |
| UE-GPU間距離(最近傍) | 417m〜4974m(リクエストごとに異なる) |
| UE-2番目近傍GPU間距離 | 3281m〜8368m |

### 3.3 計算式

```
uplink_latency_ns   = round(distance_m * 5) + round(8000 * request_payload_bytes / throughput_mbps)
downlink_latency_ns = round(distance_m * 5) + round(8000 * first_token_payload_bytes / throughput_mbps)
communication_latency_ns = uplink_latency_ns + downlink_latency_ns (+ リダイレクト/KV移送分)
```

実際の値としては、通常のリクエストでこの合計は**3.6ms程度**(4手法いずれも
ほぼ同じ)であり、TTFT全体(数百〜数千ms)に対しては無視できるほど小さい。
これは`2026-07-04`〜`07-05`の一連の実験で繰り返し確認してきた知見でもある。

### 3.4 GPU間バックボーン(リダイレクト・KV移送時のみ)

UE↔GPUのアクセス回線とは別に、GPU間の直接転送(`NEAREST_MIGRATE`/
`NEAREST_MIGRATE_KV`でリダイレクトが発生した場合)には次のパラメータを使う。

| 項目 | 値 |
| --- | --- |
| 帯域 | 1Gbps |
| 距離 | 5000m(5km。実機のAPNは約6kmで、この値は未実測のプレースホルダ) |
| 距離あたり遅延 | UE側と同じ5ns/m |

`NEAREST_MIGRATE_KV`ではこの回線を使って、`reuse_prefix_toks`分のKVキャッシュ
(計算済みバイト数 = トークン数 × KVキャッシュのバイト/トークン)を追加で
転送する時間も課される。

---

## 4. キュー待ちの発生メカニズム

### 4.1 キューが貯まる条件

シミュレータのスケジューラ(`scheduler.py`)はvLLM V1スタイルの継続的
バッチングを模しており、**同時に処理できるリクエスト本数(`max_num_seqs`)が
唯一のハード上限**になる(プロンプト長やトークン予算とは独立)。

> あるGPUに割り当てられたリクエストの**総数**が`max_num_seqs`を超えない限り、
> 到着タイミングをどれだけずらしてもキューは発生しない。

今回の設定では`max_num_seqs=24`に対し、GPU1には33件が割り当てられている
(GPU0は17件で上限未満)。そのため:

- **方法A(`NEAREST_KV`、リダイレクト無し)**: GPU1の33件のうち、常に
  どこかで9件分が上限超過となり、順番待ちが発生する。到着が0.055〜6.257秒に
  分散していても、サービス時間(プロンプト長に応じ数百ms〜)の方が到着間隔より
  長いため、キューは解消されずに蓄積し続ける。結果として平均Queueは
  **2004.54ms**と、TTFTの大部分を占める。
- **方法B1/B2(`NEAREST_REJECT`/`NEAREST_MIGRATE`)**: キャパ超過を検知した
  時点でGPU0側へリダイレクトし、割当を17:33→26:24に均す。GPU0側は24を
  超えないため、リダイレクトされた分もキュー無しで処理できる。平均Queueは
  **451ms程度**まで下がる。
- **方法C(`NEAREST_MIGRATE_KV`)**: B1/B2と同じリダイレクト判定に加え、
  KV引き継ぎでprefillも短縮するため、平均Queueは**268ms程度**まで
  さらに下がる。

### 4.2 「キャパシティ」の定義

`running_reqs`(現在バッチに含まれ実行中のリクエスト数)が`max_num_seqs`未満で
あれば「空きあり」、そうでなければ「キャパ超過」と判定する。これは
`--max-num-batched-tokens`(トークン予算)やKVキャッシュ容量とは別の、
**純粋にリクエスト本数だけを見る制約**である。

### 4.3 リダイレクト判定のロジック(`NEAREST_REJECT`/`NEAREST_MIGRATE`/`NEAREST_MIGRATE_KV`共通)

1. 最近傍GPUに空きがあれば、そのままそこで処理する。
2. 空きが無ければ、2番目に近いGPUへリダイレクトする(最大1回まで)。
3. リダイレクト先ではキャパシティチェックを行わず、無条件に受理する
   (2台構成のため、リダイレクト先はキャパに余裕がある前提)。
4. 一度処理が始まったリクエストはリダイレクトされない。

`NEAREST_REJECT`(UE差し戻し)と`NEAREST_MIGRATE`(GPU間転送)は、この判定
ロジック自体は同一で、リダイレクトに伴う追加コストのモデル化だけが異なる
(UE往復 vs GPUバックボーン転送)。ただしどちらも数msオーダーでTTFTへの影響は
ほぼ無い(3.3節参照)。

---

## 5. 参考: 結果サマリ(再掲)

| 手法 | GPU0:GPU1割当 | rerouted | Mean E2E TTFT | Queue | KV transfer | Compute | RTT |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A `NEAREST_KV` | 17:33 | 0/50 | 2075.48ms | 2004.54ms | 0.00ms | 67.32ms | 3.62ms |
| B1 `NEAREST_REJECT` | 26:24 | 9/50 | 613.66ms | 450.92ms | 0.00ms | 156.67ms | 3.63ms |
| B2 `NEAREST_MIGRATE` | 26:24 | 9/50 | 613.66ms | 451.56ms | 0.00ms | 156.67ms | 3.63ms |
| C `NEAREST_MIGRATE_KV` | 26:24 | 9/50 | 495.11ms | 267.95ms | 155.36ms | 67.16ms | 3.63ms |

詳細は`Diary/output/2026-07-05-exp212-kv-cache-1to2-routing-report.md`を参照。
