# KV転送の投機的実行(Method A/B)実装と検証記録

設計方針は`.claude/plans/elegant-dazzling-lobster.md`を参照。本レポートは実装後の
段階的検証(Stage 0〜4)の結果を記録する。

## 実装した2手法

- **Method A(scheduler隠し)**: KV転送とターゲット側scheduler queue待ちを並列化する。
  決定論的で不確実性なし。既存のPP2 baseline policy(pressureヒューリスティック)
  の上にそのまま乗る。`--enable-scheduler-hide-kv-migration`。
- **Method B(投機的KV転送 + scheduler隠し)**: 決定がまだ確定していない
  「待機中」の間に投機的にKV転送を始める。前提として、home側の待機時間予測を
  再較正し(`--enable-formula-local-wait-point-estimate`)、実際に「待つ」局面を
  作る必要がある(現行のpressure/formula両policyとも、安全マージンにより
  redirect判断が常に即断即決になってしまうため)。加えてpolicyを
  `NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE`に切り替える必要がある。
  `--enable-speculative-kv-migration`(`--enable-scheduler-hide-kv-migration`との
  併用必須)。

## Stage 0: 単体テスト — 全36件成功

- `tests/test_second_ttft_reserve_router.py`にrouter.pyレベルのテストを追加
  (9件): B-1再較正フラグの効果、Method Aのarrival/kv_ready_time_ns分離、
  Method Bのpin/commit/waste会計。
- `tests/test_scheduler_hide_kv_migration.py`を新規作成(5件): scheduler.pyの
  batch_reqフィルタが、KV未到着requestを`self.request`から誤って削除しない
  ことを直接確認(最もリスクが高いと事前に指摘していた箇所)。
- 既存27件 + 新規9件 = 36件全て成功(`python3 -m pytest tests/ -q`)。

## Stage 1: Method Aのみ、実機シミュレーションでの検証

`input8000_reuse025`(300リクエスト、PP2 5台構成)でbaseline(pressure
policy、フラグ無し)とMethod A(同policy + `--enable-scheduler-hide-kv-migration`)
を比較した。

### 確認できた不変条件

- 非redirect分265件中257件(96.9%)はbaselineとTTFTが完全一致。
- redirect分35件全てで`queueing_before_ttft_ns >= kv_migration_effective_latency_ns`
  が成立(admissionはkv_ready_time_ns以降にしか起きないという設計通り)。

### 集計結果: 予想に反し、明確な改善は見られなかった

| 指標 | baseline | Method A | 差分 |
|---|---:|---:|---:|
| 平均TTFT | 958.3 ms | 959.0 ms | +0.07%(ほぼ同じ) |
| p95 TTFT | 1385.1 ms | 1453.2 ms | **+4.9%(悪化)** |
| redirect件数 | 35 | 35 | 同じ |

### 解釈: 単体のメカニズムは正しいが、混雑した共有資源での相殺効果

非redirect分265件中8件でMethod AがbaselineとTTFTが食い違っていた。これは
バグではなく、離散イベントシミュレーション特有の正当な副作用と考えられる。
Method Aでredirectされたrequestがターゲットのschedulerへ早く入るようになった
結果、そのGPUのtoken budget/slotを他のrequestとより早いタイミングで奪い合う
ことになり、一部のrequest(redirectされていないものも含む)の待ち時間が
変化した。つまり「1件のrequestの critical path を短くする」ことが、
混雑した共有資源の奪い合いを通じて「別のrequestを遅くする」形で部分的に
相殺されている可能性が高い。

`input8000_reuse025`は比較的高負荷(capacity-pressure寄り)な条件であり、
この1条件だけでMethod Aの効果を判断するのは早計。低負荷条件では素直に
改善する可能性がある。個別requestレベルのメカニズム自体は不変条件通り
正しく動作していることを確認済みなので、Stage 2/3へ進み、最終的には
Stage 4(10 workload全件)の集計結果で総合的に判断する方針とする。

## Stage 2: 再較正のみ — 狙い通り「待機」が発生することを確認

同じ`input8000_reuse025`(300リクエスト)で、policyを
`NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE`に切り替え、
`--enable-formula-local-wait-point-estimate`のみ追加した(Method Aの
scheduler隠しフラグは付けていない)。

| 指標 | 値 |
|---|---:|
| `router_capacity_retry_count > 0`のrequest数 | **7 / 300**(再較正前は常に0/300) |
| 最大retry_count | 39,403(数万回のtickにわたり実際に待機) |
| `oneshot_decision_reason = elapsed_local_wait_exceeds_limit`(待った末にdeadline超過でredirect) | 2件 |
| `oneshot_decision_reason = home_became_admissible`(待った末にhomeが空いて redirect回避) | 1件 |
| `oneshot_decision_reason = predicted_local_wait_exceeds_limit`(再較正後も予測が閾値超で即断) | 48件 |
| `oneshot_decision_reason = home_admissible`(即座にhomeで処理) | 249件 |

**狙い通り、以前は0件だった「実際に待機してから判断が変わる」ケースが3件
(elapsed超過2件+home回復1件)発生した。** 残り48件は再較正後も点推定
そのものが閾値を超えているため即断即決のままだが、これはhome側が実際に
長時間混雑している(=正しい判断である可能性が高い)ケースであり、
以前のように「安全マージンのせいで無条件に即断」していたのとは異なる。
再較正メカニズムは設計通りに機能していることを確認した。

redirect件数は50件(pressureヒューリスティックの35件とは policy が異なる
ため直接比較はできない)。平均TTFT 993.2ms、p95 1480.5msで、Stage 1の
pressure policy baseline(958.3ms/1385.1ms)と近い水準。

## Stage 3: Method B(投機的KV転送) — pinが1件も発生しない

3フラグ全部(`--enable-formula-local-wait-point-estimate
--enable-scheduler-hide-kv-migration --enable-speculative-kv-migration`)を
有効にして`input8000_reuse025`(300リクエスト)を実行したところ、
**投機的pinが1件も発生しなかった**(Stage 2と平均/p95 TTFTが完全一致)。

原因を切り分けるため、以下を追加で試した(すべて`input8000_reuse025`
300件、pin発生数は全て0):

| 試行 | 変更点 | pin数 | 備考 |
|---|---|---:|---|
| Stage 3 | デフォルト設定 | 0 | retry_count>0が7件あるにも関わらず |
| Stage 3b | `--oneshot-redirect-margin-ns 2000000000`(margin 10倍) | 0 | Stage 3と数値が完全一致(効果なし) |
| Stage 3c | `--oneshot-max-local-wait-ns 10000000000`(10秒に拡大) | 0 | `predicted_local_wait_exceeds_limit`が48→50件に増加 |
| Stage 3d | `input6000_reuse05`(150件、低負荷) | 0 | そもそもredirectが0件 |

### 原因: pinのトリガー条件(`awaiting_predicted_local`分岐)が、この
workload群ではほぼ到達不能

pinは`margin_wins=False かつ deadline_exceeded=False`(=「redirectが
明確に優位でもなく、home側の予測待機時間もまだ許容範囲内」という
"際どい"状態)でのみ発生する設計にしている。実測データを見ると、
home側の点推定(`local_wait`)は**ほぼ二値的**(home即座に空いているか、
さもなければ極端に大きい待機予測になるか)で、その中間の"際どい"領域に
落ちるケースがほとんど存在しない。Stage 3cで許容上限を1秒→10秒に
広げても`predicted_local_wait_exceeds_limit`(=deadline_exceeded=True)の
件数がむしろ増えたことからも、点推定自体が既に10秒を超えるケースが
多いことが分かる。`--oneshot-redirect-margin-ns`を10倍にしても結果が
一切変わらなかったことは、margin側が律速していないことも示している
(=`awaiting_candidate_capacity`分岐(候補が1つも無い)経由で待機している
requestが大半で、`awaiting_predicted_local`分岐には到達していない)。

これはitem 16(TTFT回帰モデル改善)で確認した「route回帰の予測値が
中間領域を持たず両極端に圧縮されている」という既知の性質と整合する。

### 現状の結論

**Method Bは設計通りに実装されているが、pinのトリガー条件を満たす
"際どい"状況がテストした4パターンいずれでも発生しなかったため、
Method Aに対する追加効果を実測できていない。** 単体テスト(Stage 0)では
pin/commit/waste会計ロジック自体は正しく動くことを確認済みなので、
バグではなく「この設計のpinトリガーが、テストした workload 群では
起きにくい」という経験的事実である。

対応方針の選択肢:
1. このままStage 4(10 workload全件)を実行し、他のworkloadでpinが
   発生するか確認する(workload次第では発生する可能性が残っている)。
2. pinのトリガー範囲を`awaiting_candidate_capacity`分岐(候補が1つも
   無い場合)にも広げる。実際にretry_count>0の大半はこちらの分岐を
   経由していると考えられる。ただし設計時点で「モデルのTTFT予測に
   基づかないヒューリスティックな暫定ターゲットで投機の質が悪い」と
   判断しスコープ外にした経緯がある。
3. Method Bはこの形では効果が測定できないとして、Method Aの効果
   検証(Stage 4)に注力する。
