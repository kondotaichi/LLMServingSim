# KV転送の投機的実行: 設計・実装・検証レポート

## 1. 目的

Redirect(別GPUへの転送)+KVキャッシュ移送が発生した場合、現行の
シミュレータでは**KV転送が完了するまでリクエストがターゲットGPUの
schedulerに存在すら見えない**(`router.py`が`arrival_time_ns`を
KV転送時間ぶん先送りする)。つまりKV転送時間とターゲット側scheduler
queue待ち時間は常に直列(加算)でモデル化されている。

本タスクは、この直列処理を並列化する2つの手法を実装し、
`experiments/2026-07-22_pp2_five_workloads`(PP2、10 GPU→5台の
pipeline-parallel環境)で効果を検証する。

- **Method A(scheduler隠し)**: KV転送とターゲット側scheduler queue待ちを
  並列化する。決定論的で不確実性なし。
- **Method B(投機的KV転送 + scheduler隠し)**: redirect決定がまだ確定して
  いない「待機中」の間に、投機的にKV転送を開始する。

## 2. 経緯(このタスクに至るまでの調査)

1. `experiments/2026-07-25_speculative_test`で、redirect判断そのものの
   信頼性を実測検証した。18件のredirect判断について「home GPUで実際に
   待った場合のTTFT」を新規counterfactualモード
   (`--counterfactual-force-local-request-id`)で計測したところ、
   redirectは83.3%(15/18)で正しい判断だったが、その判断根拠である
   `oneshot_predicted_local_ttft_ns`が実測より常に10,000〜19,000ms
   過大評価されていることが判明した。原因は`route_upper_ms`
   (=`route_positive_ms + 固定の安全マージン残差`、約9,700〜10,300ms)
   が発生確率に関わらず常に`oneshot_max_local_wait_ns`(1秒)を超える
   ため、home非admissible判定と同時に**必ず即断即決でredirect**して
   しまうという構造的な問題だった。
2. TTFT予測モデルの回帰精度自体も改善した
   (`experiments/2026-07-16_ttft_component_regression`のroute_tail
   hyperparameter調整・hinge特徴量追加。TTFT MAE −4.8%、R² +12.1%)。
3. ここでユーザーから「KVキャッシュを事前に送っておけばKV転送時間を
   隠せるはず」という投機的実行のアイデアが出た。設計を検討する中で、
   「home非admissible検知」をトリガーにしても、上記1の理由により
   検知と決定確定が数学的に常に同時(待機ウィンドウがゼロ)であることが
   判明し、投機に意味を持たせるには先にhome側予測の再較正が必要
   (ユーザーの選択: 方針B-1)という結論に至った。
4. 上記を踏まえてプランモードで設計を確定
   (`.claude/plans/elegant-dazzling-lobster.md`)、実装・段階的検証
   (Stage 0〜4)を実施した。**本レポートはこの実装・検証の記録。**

## 3. 設計

### 3.1 Method A: scheduler隠し

`Request.waiting_since_ns = self.arrival`であり、`account_admission()`が
実際にbatchへ入った瞬間に`current - waiting_since_ns`を
`queueing_before_ttft_ns`へ加算する(scheduler.py既存ロジック)。したがって
**KV転送完了を待たずにrequestをscheduler queueへ見えるようにし、実際の
prefill計算開始だけをKV到着時刻でgateすれば、`queueing_before_ttft_ns`が
自動的に`max(KV転送待ち, scheduler待ち)`になる**——追加の算術なしに実現できる。

- `req_data['arrival_time_ns']`はKV転送完了を待たず、決定確定時点のまま
  据え置く(scheduler queueに即座に見える)。
- 新設の`kv_ready_time_ns`(=決定確定時刻+実効KV転送時間)を導入し、
  prefillのtoken schedulingだけをこの時刻でgateする(decode requestは
  対象外)。
- 何もフラグを立てなければ`kv_ready_time_ns == arrival`となり、既存挙動と
  完全に同一(no-op)。

### 3.2 B-1: home側予測の再較正(Method Bの前提)

`_maybe_capacity_dynamic_formula_route`のlocal側予測
(`local_wait`/`local_total`)は、redirect候補側が既に使っている
`prediction['ttft_ms']`ベースの点推定とは非対称に、常に膨張した
`route_upper_ms`ベースの安全マージン付き値を使っていた。これを
redirect候補側と対称な点推定に切り替えるオプトインフラグを追加した。

### 3.3 Method B: 投機的先行転送(pin方式)

- **Pinのトリガー**: `margin_wins=False かつ deadline_exceeded=False`
  (=「redirectが明確に優位でもなく、home側の予測待機時間もまだ許容範囲内」
  という際どい状態、`awaiting_predicted_local`分岐)でのみpinする。
  候補が1つも無い場合の`awaiting_candidate_capacity`分岐は対象外とした
  (モデルのTTFT予測に基づかないヒューリスティックな暫定ターゲットで
  投機の質が悪いと判断)。
- **Pinは一度きり**: 一度pinしたら以後の再評価で上書きしない。決定
  ロジック自体は毎回そのまま再計算される。pinは最終コミット時に一度だけ
  参照するサイドチャネル。
- **進捗管理**: pin後にターゲットが変わらない前提なので、
  `elapsed_speculative_ns = decision_time_ns - pin_time_ns`を最終
  コミット時に1回計算するだけでよい(毎tick積み上げるループ不要)。
- **容量への影響は無視**: pinは`req_data`上のタイムスタンプ記録のみ。
  他requestの容量判定に影響させない(この設計の既知の限界、7節参照)。

## 4. 実装

### 4.1 変更ファイル

| ファイル | 変更内容 |
|---|---|
| `serving/core/request.py` | `kv_ready_time_ns`, `kv_migration_effective_latency_ns`, `kv_migration_speculative_hidden_ns`, `speculative_kv_*`(5列)を追加 |
| `serving/core/scheduler.py` | `schedule_base`/`schedule_with_prefix`双方に、KV未到着のprefill requestを`batch_req`から除外するフィルタを追加。新CSV列8個を`save_output`のheader/rowに追加 |
| `serving/core/router.py` | `enable_scheduler_hide_kv_migration`/`enable_formula_local_wait_point_estimate`/`enable_speculative_kv_migration`の3フラグ追加。`_apply_kv_migration_if_needed`(Method A適用箇所)、`_maybe_capacity_dynamic_formula_route`のlocal_wait計算(B-1)とpin/commit/waste会計(Method B)を変更 |
| `serving/__main__.py` | 上記3フラグに対応するCLIオプション(`--enable-scheduler-hide-kv-migration`等)を追加 |

デフォルトは全て無効(既存挙動を完全に保持)。

### 4.2 scheduler.pyのフィルタ挿入位置(最重要リスクへの対処)

`schedule_base`/`schedule_with_prefix`は、`batch_req`に残った全requestを
無条件に`self.request`から削除する処理(STEP3/4)を持つ。単純に「token
scheduling loopの中だけ」でgateすると、KV未到着requestが
`scheduled_tokens`エントリを持たないまま`batch_req`に居座り、STEP3/4で
誤って`self.request`から削除・「schedule済み」扱いされてしまうバグを
生む。これを避けるため、**`batch_req`が`self.request`から最初に構築された
直後、STEP1〜4のいずれの処理よりも前**にフィルタを1行挿入した:

```python
batch_req = [
    req for req in batch_req
    if not (req.is_prefill() and current < req.kv_ready_time_ns)
]
```

decode requestは`is_prefill()==False`なので影響を受けず、外側の
`self.request[0].arrival > current`ゲートにも触れない。

## 5. 検証(Stage 0〜3)

### Stage 0: 単体テスト — 全36件成功

- `tests/test_second_ttft_reserve_router.py`に9件追加: B-1再較正フラグの
  効果、Method Aのarrival/kv_ready_time_ns分離、Method Bのpin/commit/waste
  会計を、既存の`FakeMemory`/`FakeRequest`ハーネスで直接検証。
- `tests/test_scheduler_hide_kv_migration.py`を新規作成、5件: 4.2節の
  フィルタが実際に`self.request`を破損しないことを、`Scheduler`を
  `__init__`バイパスで直接構築して確認(最もリスクが高いと事前に指摘して
  いた箇所)。
- 既存27件+新規9件(router)+新規5件(scheduler、別ファイル集計)=
  合計36件全て成功。

### Stage 1: Method Aのみ、実機シミュレーション(input8000_reuse025、300件)

| 指標 | ベースライン | Method A | 差分 |
|---|---:|---:|---:|
| 平均TTFT | 958.3 ms | 959.0 ms | +0.07%(ほぼ同じ) |
| p95 TTFT | 1385.1 ms | 1453.2 ms | **+4.9%(悪化)** |

個別requestの不変条件(`queueing_before_ttft_ns >=
kv_migration_effective_latency_ns`)は35件全redirectで成立、非redirect
265件中257件(96.9%)はTTFT完全一致。残り8件の食い違いは、redirectされた
requestがターゲットのschedulerへ早く入るようになった結果、token
budget/slotを他requestと早いタイミングで奪い合うようになったことに
よる、離散イベントシミュレーション特有の正当な副作用と判断した(6節で
再考察)。

### Stage 2: 再較正のみ — 狙い通り「待機」が発生

policyを`NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE`に切り替え、
`--enable-formula-local-wait-point-estimate`のみ追加(同workload、300件)。

| 指標 | 値 |
|---|---:|
| `router_capacity_retry_count > 0`のrequest数 | **7 / 300**(再較正前は常に0/300) |
| 最大retry_count | 39,403 |
| `elapsed_local_wait_exceeds_limit`(待った末にdeadline超過でredirect) | 2件 |
| `home_became_admissible`(待った末にhomeが空いてredirect回避) | 1件 |

以前は0件だった「実際に待機してから判断が変わる」ケースが3件発生し、
再較正メカニズムが設計通り機能することを確認した。

### Stage 3: Method B — pinが1件も発生せず

3フラグ全部を有効にして同workloadを実行したが、**投機的pinが1件も
発生しなかった**(TTFTがStage 2と完全一致)。margin 10倍、max_wait
10秒に拡大、低負荷workload(input6000_reuse05)への切替、いずれを試しても
pin数は0のままだった。

原因は、home側の点推定が実測上ほぼ二値的(即座に空いているか、極端に
大きい待機予測になるか)で、pinのトリガー条件である「際どい」中間領域に
落ちるケースがほとんど存在しないため。retry_count>0の7件は
`awaiting_candidate_capacity`分岐(候補が1つも無い)経由で待機していたと
考えられ、pin対象の`awaiting_predicted_local`分岐には到達していなかった。
これはitem 16(TTFT回帰モデル改善)で確認した「route回帰の予測値が
中間領域を持たず両極端に圧縮されている」という既知の性質と整合する。

単体テストではpin/commit/waste会計ロジック自体は正しく動くことを確認
済みであり、バグではなく「この設計のpinトリガーが、テストした
workload群では起きにくい」という経験的事実である。

詳細は`experiments/2026-07-22_pp2_five_workloads/reports/
speculative_kv_transfer_verification.md`に記録。

## 6. Stage 1の結果に対する考察: なぜMethod Aは単純に「勝つ」とは限らないか

Stage 1でMethod Aの平均TTFTがほぼ変わらず、p95がむしろ悪化したことは、
一見「実装が失敗した」ように見えるが、以下の理由から**正しい実装が
示す、正直な結果**だと考えている。

1. **単体のrequestに対する効果は保証通り**: 不変条件
   (`queueing_before_ttft_ns >= kv_migration_effective_latency_ns`)は
   例外なく成立しており、KV転送とscheduler queueが並列化されている
   こと自体は確認できている。
2. **共有資源での相殺効果**: redirectされたrequestがターゲットの
   schedulerへ早く入れるようになった結果、そのGPUのtoken budget/slotを
   他のrequest(redirectされていないものも含む)とより早いタイミングで
   奪い合うようになる。「1件のcritical pathを縮める」ことが、混雑した
   共有資源の奪い合いを通じて「別のrequestを遅くする」形で部分的に
   相殺されている可能性が高い。
3. **input8000_reuse025はcapacity-pressure寄りの高負荷条件**であり、
   この1条件だけでMethod Aの効果を判断するのは早計。低負荷条件では
   素直に改善する可能性が高い。

## 7. 今回のスコープで明示的に受け入れる制約

- Method Bの投機は容量・メモリ競合を一切モデル化しない(複数requestが
  同じ混雑candidateへ同時に投機しても競合しない、という楽観世界)。
- Pinは`awaiting_predicted_local`分岐のみ対象、`awaiting_candidate_capacity`
  分岐は対象外(Stage 3の結果、これが実質的にMethod Bを無効化している)。
- `_maybe_capacity_oneshot_route`にも同種の`route_upper_ms`膨張パターンが
  あるが、今回は対象外(Method Bが必要とする再評価ループが無いため)。

## 8. Stage 4: 全workloadでの効果検証(確定版)

10 workloadのうちinput10000系(reuse00/025/05)は、300リクエストの
完走に数時間〜数十時間かかることが判明した(オリジナルのPP1/PP2
baseline実行も5日以上前からRUNNINGのまま完走していない)。ユーザーの
判断でinput10000系はスキップし、`input2000_reuse025` /
`input6000_reuse05` / `input8000_reuse00` / `input8000_reuse025` /
`input8000_reuse05`の5 workloadに絞って検証した
(`mixed_rate3p33_seed1`は後日)。

`input8000_reuse00`の素のPP2 baseline(`results/pp2/input8000_reuse00/`)は
最終確認時点でプロセス自体が存在せず、ログも出力CSVも無いまま
statusファイルの更新が7/23 07:30(オリジナル実験時点)で止まっていた
——つまり実質的に「実行されなかった」stale状態であり、これ以上待っても
完走しない。ユーザーの判断で、この1 workloadについては**PP1 baseline
とMethod A/Bの3アームのみ**(素のPP2との比較は無し)で確定させ、
残り4 workloadは当初通り4アームフルで確定させた。

以下は`experiments/2026-07-22_pp2_five_workloads/scripts/analyze_pp_comparison.py`
(`ANALYSIS_OUTPUT_ROOT`で本ディレクトリへ出力)で生成した最終集計。

### 8.1 Method Bのpinは5 workload全てで1件も発火しなかった

Stage 3(小規模)で見つかった「pinトリガーがほぼ到達不能」という結論を、
5 workload全件(input8000_reuse00含む)で`speculative_kv_pin_time_ns`等を
直接確認して最終的に再検証した。

| ワークロード | pinあり | 経過時間の充当あり | 無駄になった投機 | リクエスト数 |
|---|---:|---:|---:|---:|
| input2000_reuse025 | 0 | 0 | 0 | 300 |
| input6000_reuse05 | 0 | 0 | 0 | 300 |
| input8000_reuse00 | 0 | 0 | 0 | 300 |
| input8000_reuse025 | 0 | 0 | 0 | 300 |
| input8000_reuse05 | 0 | 0 | 0 | 300 |

5 workload・1500 request全てでpin=0。**つまり「PP2 + speculative」アームと
「PP2 + scheduler-hide」アームの間のTTFT差分は、投機的先行転送そのものの
効果ではなく、Method Bが同時に切り替えているpolicy(pressure→formula)と
B-1の home側再較正が引き起こす redirect対象集合の変化でしかない。**
この2アームを「投機の有無」として直接比較するのは妥当ではない
(Method Bはこのworkload群では未検証のまま、という結論は最終的に確定)。

### 8.2 Method A(scheduler隠し)の機構確認: redirectされたrequestの内訳

`analysis/input8000_redirect_ttft_breakdown.csv`(redirected-onlyコホート)で、
KV転送セグメントがscheduler queueへ完全に吸収されることを確認できた。

| ワークロード | 指標 | PP2（変更なし） | PP2 + scheduler-hide |
|---|---|---:|---:|
| 8000/reuse25%（redirect 35件） | KV転送 | 211.8 ms | **0 ms**（scheduler queue 91.1→289.3 ms） |
| | TTFT合計 | 1210.2 ms | 1197.4 ms（**-1.1%**） |
| 8000/reuse50%（redirect 12件） | KV転送 | 423.3 ms | **0 ms**（scheduler queue 28.3→451.6 ms） |
| | TTFT合計 | 1036.6 ms | 1036.6 ms（**差分ゼロ、ビット単位で同一**） |

KV転送コストは例外なくscheduler queueへ完全に吸収されており(単体テスト・
Stage 1で確認した不変条件通り)、機構自体は設計通り動作している。ただし
reuse50%ではTotalが1ビットも変わらなかった。これは、この12件では隠す
以前から「scheduler queue待ち(423 ms超)」が「KV転送時間(423 ms)」を
上回っており、**クリティカルパスの律速要因が既にscheduler側にあったため、
隠す余地(スラック)がそもそも無かった**ため。Method Aの効き目は
「KV転送時間 > 元々のscheduler待ち時間」の場合に限られる、という条件が
実データで裏付けられた。

### 8.3 KV転送セグメントの「消失」は必ずしも「無料での隠蔽」を意味しない: request単位の検証

8.2節の集計値だけを見ると「KV転送コストがscheduler queueへ吸収され、
実害が無い」ように読めるが、この解釈には注意が必要——`breakdown()`の
表示ロジック(`exposed_transfer = max(0, effective_transfer -
scheduler_queue)`)は、`queueing_before_ttft_ns >=
kv_migration_effective_latency_ns`という不変条件(schedulerへのgateが
`kv_ready_time_ns`未満のprefillを常に除外する設計そのものから来る)により
**構造的にほぼ常に0になる**。つまりグラフ上でKV transferバーが消えるのは
「転送コストが実際に無料で隠れた」ことの証明ではなく、単に「scheduler
queueという別バケットに計上先を変えた」という表示上の付け替えに過ぎない。

`input8000_reuse025`のredirect 35件をrequest単位でPP2(plain)と
scheduler-hideで突き合わせたところ、以下の内訳になった:

| 内訳 | 件数 | scheduler queue増加量 | TTFT差分 |
|---|---:|---|---|
| 完全に中立(重ね合わせの恩恵ゼロ) | 30/35 | ちょうど転送時間ぶん(+211.8 ms) | **0 ms(1ビットも変化なし)** |
| 大幅改善(輻輳の連鎖効果) | 3/35 | 転送時間より少ない、または減少 | -130〜-262 ms |
| **悪化**(重ね合わせ失敗) | 2/35 | **転送時間を超えて増加**(+298, +317 ms) | **+19 ms, +134 ms** |

大半(30/35)は「ターゲットGPUが元々空いていた」ため隠す相手(輻輳)が
存在せず、恩恵ゼロ(直列処理と壁時計上まったく同じ)。2件
(request 292, 294)は`arrival_time_ns`を早めたこと自体がターゲットGPUの
FCFSキュー内での投入順序を変え、直列処理より**悪化**した。3件は逆に
順序変化が有利に働き大幅改善。**保証されているのは不変条件による下限
(直列より短くはならない)だけで、実際の得失は輻輳状況とFCFS順序変化に
完全に依存する**、というのが最終的な結論。8.2節の「集計では中立」という
結果は、この「無関係(30件)・改善(3件)・悪化(2件)」が相殺した結果であり、
「Method Aが安定して機能している」ことの証拠ではない。

### 8.4 全300件集計(PP2基準、Method A/Bの純増分)

| ワークロード | scheduler-hideの対PP2改善率（平均/p95/p99） | speculativeの対PP2改善率（平均/p95/p99） | redirect件数（PP2→A→B） |
|---|---|---|---|
| 2000/reuse25% | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% | 0→0→0(redirectなし) |
| 6000/reuse50% | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% | 0→0→0(redirectなし) |
| 8000/reuse25% | -0.1% / -4.9% / +3.9% | -3.6% / -6.9% / -14.9% | 35→35→50 |
| 8000/reuse05% | 0.0% / 0.0% / 0.0% | +0.1% / -1.1% / +2.8% | 12→12→10 |
| 8000/reuse00%(PP2基準無し、参考: **PP1基準**) | +30.5% / +8.9% / +11.2% | +24.9% / +1.5% / +6.7% | PP1: 186 → A: 109 → B: 110 |

2000/6000はPP2側でredirectが元々ゼロのため、Method A/Bの出番自体が無い
(想定通りの結果)。redirectを伴う8000系(reuse25%/50%)を見ると、
8.2節でredirectされたrequestだけなら改善が見えたMethod Aも、**全300件
平均に薄まるとほぼ中立(±0.1%、tailも±5%程度)**になる。8.3節の通り、
これは「効いていない」のではなく「無関係・改善・悪化が相殺している」
結果である。speculativeアームの差分(-3.6%〜+2.8%)は8.1節の通りpin効果
ではなくpolicy変更由来。

`input8000_reuse00`のみPP2基準が無いためPP1基準で参考掲載。この
workloadはPP1で186件(62%)もの高いredirect率を持つ高負荷条件で、
Method A/Bともに大きなmean改善(+30.5%/+24.9%)を示しているが、これは
**PP1→PP2のトポロジ変更(10 replicas→5 groups)の効果とMethod A/Bの効果が
混ざった値**であり、Method A/B単体の純増分としては解釈できない
(PP2 baselineが無いため切り分け不能)。

### 8.5 生成物

以下は全て本ディレクトリ(`experiments/2026-07-28_pp_schedule_conceal_and_speculative/`)
直下に生成した。Method A/Bのシミュレーション結果(`requests.csv`等、
`results/pp2_scheduler_hide/`・`results/pp2_spec_scheduler_hide/`)も本
ディレクトリへ移設済み(README参照)。PP1/PP2ベースラインの結果は引き続き
`experiments/2026-07-22_pp2_five_workloads/results/`側にあり、
`analyze_pp_comparison.py`は`_results_dir()`で本ディレクトリ→
`pp2_five_workloads`の順にフォールバック探索する。出力先(figures/analysis/
reports)は`ANALYSIS_OUTPUT_ROOT`環境変数でこちらに向けている
(`experiments/2026-07-22_pp2_five_workloads/figures`等オリジナルの
2-arm(PP1/PP2)結果は上書きせず、コミット済み状態のまま維持)。

- [`figures/ttft_breakdown.png`](../figures/ttft_breakdown.png) ——
  4アーム×8workload(揃っている分)の内訳棒グラフ。scheduler-hide/speculative
  アームでKV transferセグメントが消えている様子が視覚的に確認できる
  (ただし8.3節の通り、これは「無料で隠れた」ことの証明ではない)。
- 同ディレクトリ`figures/ttft_cdf.png` / `ttft_percentiles.png` /
  `latency_throughput_tradeoff.png` / `logical_instance_utilization.png` /
  `input8000_redirect_ttft.png` / `input8000_redirect_ttft_breakdown.png`
- `../analysis/comparison_summary.csv`, `ttft_breakdown.csv`,
  `paired_improvements_vs_pp2.csv`, `input8000_redirect_ttft_breakdown.csv`
- [`interim_pp1_vs_pp2.md`](interim_pp1_vs_pp2.md)(自動生成、missing一覧付き。
  ファイル名は開発初期の名残りで実際は4アーム分の内容)

再生成コマンド:

```bash
cd /Users/taichikondo/LLMServingSim/experiments/2026-07-22_pp2_five_workloads
ANALYSIS_OUTPUT_ROOT=/Users/taichikondo/LLMServingSim/experiments/2026-07-28_pp_schedule_conceal_and_speculative \
  python3 scripts/analyze_pp_comparison.py
```

## 9. 現状のまとめ(確定)

| 項目 | 状況 |
|---|---|
| Method A実装 | 完了、単体テスト・不変条件とも成立確認済み |
| B-1(home側再較正)実装 | 完了、狙い通り「待機」が発生することを確認 |
| Method B実装 | 完了(ロジック自体は単体テストで正しいと確認)だが、pinトリガーが5 workload・1500 request全てで1件も発生せず、追加効果を実測できなかった |
| Method Aの効果 | 個別requestレベルでは「直列より短くはならない」という下限のみ保証。実際は無関係(恩恵ゼロ)が大半、一部は輻輳の連鎖効果で大幅改善、一部は逆に投入順序変化で直列より悪化(8.3節)。全300件平均では±0.1%程度とほぼ中立で、これは相殺の結果であり安定した改善ではない |
| Method Bの効果 | pin=0のため実質未測定。観測された差分は全てpolicy切替+home側再較正由来で、投機的先行転送そのものの効果ではない。`awaiting_candidate_capacity`分岐までpin対象を広げるかは今後の検討課題(`branch_predictor_analogy_analysis.md`参照) |
| Stage 4のスコープ | input2000_reuse025 / input6000_reuse05 / input8000_reuse025 / input8000_reuse05は4アームフルで確定。input8000_reuse00は素のPP2 baselineがstale(未実行のまま放置)のため3アーム(PP1/A/B)のみで確定。input10000系・mixed_rate3p33_seed1は対象外(後日/スキップ) |
