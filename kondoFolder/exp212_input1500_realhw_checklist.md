# exp212 (input_toks=1500統一版) 実機再現チェックリスト

**作成日:** 2026-07-07
**前提文書:** `kondoFolder/exp212_real_hw_requirements.md`(本チェックリストはこれを補足するもの。
ハーネスの実装方法・4方式のロジック・出力スキーマなど基本事項はそちらを参照)
**このチェックリストの目的:** 前回の実機実験(`2026-07-06_exp212_realhw_official_report.md`)で、
順位がシミュレーションと逆転する(方法Aが最良、方法Cが最悪)という結果になった。
その原因調査で見つかった問題点を踏まえ、**同じ失敗を繰り返さないための実行前・実行後
チェック項目**をまとめる。

---

## 0. 今回追加で転送するファイル

| ファイル | 用途 |
| --- | --- |
| `workloads/generated/geo_2gpu100_kv_workload_1to2_n50_input1500.jsonl` | プロンプト長(input_toks)を全件1500トークンに統一した新ワークロード |
| `results/exp212-input1500-nearest-{kv,reject,migrate,migrate-kv}.csv` | シミュレーション側の目標値(比較の基準) |
| `outputs/exp212_input1500/ttft_breakdown.png`<br>`outputs/exp212_input1500/ttft_cdf.png` | シミュレーション側の可視化(見た目の参考) |
| `Diary/output/2026-07-07-exp212-input1500-uniform-length-report.md` | 本実験の背景・結果の説明 |

このワークロードは元の`geo_2gpu100_kv_workload_1to2_n50.jsonl`と**到着時刻・GPU割当・
ユーザ座標などは完全に同じ**で、`input_toks`だけを1500に揃え、それに伴う
`reuse_prefix_toks`(→1024固定)・`request_payload_bytes`等の派生値だけを再計算したもの。

---

## 1. 実行前チェック(設定ミスを防ぐ)

- [ ] **`--max-model-len`をこのワークロード用に再計算したか。**
      今回のワークロードは`input_toks=1500`固定、`output_toks`最大784なので、
      最大合計長は`1500+784=2284`。`--max-model-len 2432`程度(以前の4352や8192を
      そのまま流用しない)。前回の実験は8192のままだったため、KVキャッシュブロック数が
      不必要に圧迫されていた可能性がある。
- [ ] **起動ログの`# GPU blocks`を確認し、`ブロック数 × 16 ≥ 24 × 2284`程度の余裕が
      あるか確認したか。** 無ければ実行中にpreemptionが起き、キュー滞留の原因が
      `max_num_seqs`なのかメモリなのか区別できなくなる。
- [ ] **`--arrival-mode`を`workload`にしたか(`simultaneous`にしていないか)。**
      前回は`simultaneous`(全50件を文字通り同時到着扱い)にしていたが、シミュレーション
      側の実際の到着分布は**0.055秒〜6.257秒に分散**しており、これは異なる設定である。
      `workload`モード(ワークロードの`arrival_time_ns`をそのまま使う)に揃えること。
- [ ] **4方式すべてに共通のウォームアップフェーズを追加したか。**
      前回の`harness.py`は`NEAREST_KV`だけ`maybe_prime_all_nearest_kv()`で本番前に
      実際のプライミングリクエストを送っており、結果的に`NEAREST_KV`だけがCUDA
      Graph/JITの温まった状態で計測されていた(他の3方式はサーバ再起動直後の
      コールドスタートがそのまま計測に乗っていた)。**4方式すべてで、本番の50件を
      送る前に、両GPUへ同じ本数のダミーリクエストを送って結果を捨てる**、という
      処理を追加すること。
- [ ] エンジン起動フラグが4方式・GPU0/GPU1で完全に同一か(`--dtype`, `--seed`,
      `--kv-cache-dtype`, `--gpu-memory-utilization`, `--max-num-seqs 24`,
      `--max-num-batched-tokens 2048`)。
- [ ] 各方式の実行前にvLLMサーバを再起動しているか(APCキャッシュの汚染防止)。

---

## 2. 実行後チェック(結果の妥当性判定)

以下は「シミュレーションと厳密に同じ値になっているか」ではなく、**同じメカニズムで
動いているかを判定するための整合性チェック**。値が合わなくても、下記の関係が
崩れていなければ「実機特有の絶対値のズレ」として許容できる。逆に下記が崩れていれば、
ハーネスかvLLM設定に問題が残っている。

- [ ] **`NEAREST_KV`のQueueが極端に小さくなっていないか。**
      GPU1には33件が割り当てられ`max_num_seqs=24`を超えるので、Queueは
      **必然的に大きくなるはず**(シミュレーションでは平均2278.64ms)。前回は
      54ms程度しか出ておらず、これが最大の異常だった。今回もQueueが数十ms程度で
      あれば、並行実行(concurrency)が実際には起きていない疑いが強い。
- [ ] **`NEAREST_REJECT`/`NEAREST_MIGRATE`/`NEAREST_MIGRATE_KV`で、リダイレクトされて
      いない(約40件の)リクエストのCompute/Queueが、`NEAREST_KV`のそれと大きく
      systematicにズレていないか。** 前回はリダイレクト有無に関係なく3方式全体で
      Compute/Queueが`NEAREST_KV`の2倍以上になっており、ウォームアップの有無という
      別要因が疑われた。
- [ ] **GPU0:GPU1の最終割当が概ね25:25前後になっているか**(シミュレーションでは
      B1/B2=25:25、C=26:24)。大きくズレる場合、容量チェックのタイミングや
      `max_num_seqs`の扱いが想定と異なっている可能性がある。
- [ ] **リダイレクト件数が8〜9件/50件程度になっているか。**
- [ ] **`NEAREST_MIGRATE_KV`のCompute(cold部分を除いた実効prefill)が、
      `NEAREST_KV`のComputeと近い値になっているか。** シミュレーションでは両者とも
      65ms前後(`reuse_prefix_toks=1024`により、実質1500-1024=476トークン程度の
      差分計算のみで済むため)。大きく異なる場合、priming(予熱)が実際に効いて
      いない、またはpriming完了前に本番リクエストを送ってしまっている可能性がある。
- [ ] **内訳チャートを作る際、`communication_latency_ns`から`migration_latency_ns`を
      引いてから「RTT」バケットに使っているか。**(`kondoFolder/exp212_real_hw_requirements.md`
      7.1節参照)。引かずにそのまま使うと、方式Cの「RTT」が見かけ上150ms超に
      膨らみ、シミュレーション側の内訳(RTTは常に数ms程度)と一致しなくなる。
- [ ] vLLMのログ・`/metrics`にpreemption/swapイベントが出ていないか(出ていれば
      メモリが真のボトルネックになっている証拠)。

---

## 3. 目標値(シミュレーション側の結果、比較の基準)

`Diary/output/2026-07-07-exp212-input1500-uniform-length-report.md`より。

| 手法 | GPU0:GPU1 | rerouted | Mean E2E TTFT | Queue | KV transfer | Compute | RTT |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A `NEAREST_KV` | 17:33 | 0/50 | 2347.71ms | 2278.64ms | 0.00ms | 65.45ms | 5.31ms |
| B1 `NEAREST_REJECT` | 25:25 | 8/50 | 868.24ms | 602.95ms | 0.00ms | 259.68ms | 5.31ms |
| B2 `NEAREST_MIGRATE` | 25:25 | 8/50 | 867.52ms | 603.16ms | 0.00ms | 259.68ms | 5.32ms |
| C `NEAREST_MIGRATE_KV` | 26:24 | 9/50 | 531.94ms | 268.51ms | 193.28ms | 64.93ms | 5.32ms |

**厳密に同じ値になる必要はない。** 確認したいのは主に次の3点:

1. 順位が`C < B1 ≈ B2 < A`(Mean・P99 TTFTとも)になっているか。
2. `NEAREST_KV`のTTFTがQueue支配的(大部分がQueue)になっているか。
3. `NEAREST_MIGRATE_KV`が、KV transferという追加コストを払ってもなお、
   Queue・Computeの削減効果でトータルは最良になっているか。

この3点が崩れている場合は、2章のチェック項目に戻って原因を切り分けること。

---

## 4. 再実行後にやること

- [ ] 2章のチェックを踏まえて`Diary/output`に結果と考察を追記(前回同様の形式)。
- [ ] 1章の項目のうちどれを直したか・直さなかったかを明記すること
      (直せなかった項目があれば、それが順位不一致の原因候補として残ることを
      明記する)。
