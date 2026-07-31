# 容量逼迫トリガー型・投機的KVキャッシュ先回り移送("Method C")設計レポート

## 1. 背景: なぜMethod A/Bとは別に新しい機構が必要か

`implementation_and_verification.md`(Method A: scheduler隠し)・`branch_predictor_analogy_analysis.md`(旧Method B: request自身の際どい判断待ちの間の投機)を実装・検証した結果、どちらもほぼ効果が無いことが実測で判明した。

- **Method A**: routerがredirect先として選ぶGPUは「空きがある」と判断された候補なので、そもそも隠すべき輻輳がほとんど無い。実測(`input8000_reuse025`/`reuse05`、PP1トポロジ): redirectされた183件中0件、106件中7件だけがscheduler待ちの実質的な変化を示した。残りは`queueing_before_ttft_ns ≈ 転送時間 + 約58ms`という完全な足し算になっており、この58msはiteration境界による不可避な最小遅延(実コード確認済み、vLLM同様「1 stepごとに1回だけ待ち行列を再評価する」ため)であって、他requestとの競合による輻輳ではなかった。
- **旧Method B**: pinのトリガー条件(`margin_wins`も`deadline_exceeded`も成立しない「際どい」状態)が、home側TTFT点推定が実測上ほぼ二値的(即座に空いているか、明確に閾値超過か)なため、検証したのべ2000件超のrequestで一度も発火しなかった。

これらの検証中にユーザーから、当初意図していた「投機的実行」が旧Method Bの設計とは異なることが明らかになった。ユーザーの意図は:

> **request自身の判断ではなく、ワークロード全体の傾向(GPU容量逼迫)から予測し、requestが到着する前に、そのユーザが再利用するであろうKVキャッシュを候補GPUへ先回りで移送しておく**

という仕組みであり、これを新規に設計したものが本レポートのMethod C。

## 2. 設計方針(セッション内で確定した3点)

1. **トリガー**: GPU側の容量逼迫。到着時刻そのものの予測はしない——ユーザーの判断で「ノイズを予測することになる」として明示的に却下した(実測: `input8000_reuse025`の同一ユーザ連続request間隔は平均5.5秒・中央値3.7秒・範囲0.05〜30秒とPoisson的でタイミング予測に使える構造が無い)。
2. **移送対象の選び方**: 到着時刻ではなく、そのGPUを最近よく使っているユーザの**頻度ランキング**上位K名。全ユーザを移送するのは「意味がない」として却下済み。外れコストがほぼ無料という前提のもとでは、Kを増やしてヒット率を稼ぐ方が合理的という認識で一致。
3. **ワークロード**: 新規生成は不要。既存の`experiments/2026-07-22_pp2_five_workloads/workloads/*.jsonl`(全て20ユーザ×平均15回のrequest、同一user_idは常に同じhome GPUに固定)がそのまま使える構造だと確認済み。KV移送コスト(212〜423ms)より到着間隔(中央値3.7秒)の方が十分に長く、予測→移送を間に合わせる時間的余裕がある。

## 3. Method Cの全体像

Method A/Bとは独立した、**request非依存のバックグラウンド処理**として動作する:

```
1. 各prefill instanceの容量逼迫(capacity_pressure)を周期的にスキャン
2. 閾値超過時、そのinstanceを「home」とするユーザのうち、
   直近の到着頻度が高い上位K名を選ぶ
3. 各ユーザについて、直近に見た自分のプロンプト内容(input_hash_ids)を、
   圧迫の低い候補instanceへ先回りインストール(実際のrequestを介さない)
4. 後で本物のrequestが到着し、通常のredirectロジックが独立に
   同じ候補へmigrate_kvすることを決めたら、
   先回り済みキャッシュを検証しつつ課金トークン数を割り引く(hit)。
   別の場所に決まった/local処理になったら無駄になる(miss、無料)
```

旧Method Bとの決定的な違い: **Method Aが無効でも効果が出る設計**になっている。旧Method Bは`kv_ready_time_ns`(Method Aの仕組み)経由でしか効果を発揮できなかったが、Method Cは`migration_bytes`自体を割り引くため、KV転送とscheduler queueが直列のままの旧来モデルでもそのまま効く。`scheduler.py`のスケジューリングループ変更(Method Aで必要だった繊細な`batch_req`フィルタ)も不要。

## 4. ユーザ属性の持ち方: radix_tree.pyには手を入れない

当初「GPU側に滞留しているキャッシュのうちどれがどのユーザのものか」を`radix_tree.py`のノードに直接属性付けする案を検討したが、以下の理由で見送った:

- `RadixCache`の`TreeNode`はトークンハッシュでdedupされ、複数リクエスト(≠複数ユーザとは限らない)間で共有・分割(`_split_node`)される。ノードにスカラーの`user_id`を持たせると、1ノードが複数ユーザに属する場合や分割時の按分が必要になり、correctness-criticalな既存コードに複雑さを持ち込むことになる。

**採用した方針**: `radix_tree.py`には一切手を入れず、**Router側に軽量な履歴テーブル**を持つ。各ユーザの「直近に見た自分のプロンプト内容」を、既存の`Request.user_id`/`Request.input_hash_ids`からRouter側に記録するだけ。「内容が実際には(容量逼迫で)evictされて古いかもしれない」という近似は許容する——これは旧Method Bで既に受け入れている「投機は容量競合をモデル化しない」という簡略化と同じ性質。この近似による実害は、**payoff時に`RadixCache.match_prefix`で実際の残存状態を検証してから課金する**ことで抑える(過大クレジットしない)。

## 5. 新規データ構造

`Router`に追加:

```python
self._instance_user_history = {}      # instance_id -> {user_id -> deque[arrival_ns]}
self._user_last_seen_content = {}     # user_id -> {input_hash_ids, input_toks, home_instance_id, seen_at_ns}
self._proactive_last_trigger_ns = {}  # instance_id -> ns (per-instance cooldown)
self._proactive_last_scan_ns = None   # global throttle
self._proactive_migrations = {}       # user_id -> {target_instance_id, source_instance_id, seeded_tokens, seeded_at_ns}
self.proactive_migration_log = []     # 1 row per trigger event(診断用)
```

`Scheduler`/`MemoryModel`への変更は不要——既存の`seed_migrated_prefix`(destinationへのインストール)・`match_prefix`(残存確認)をそのまま再利用する。

`request.py`に追加(既存のMethod A/Bブロックと同じパターン):

```python
self.proactive_kv_prewarm_hit = geo.get('proactive_kv_prewarm_hit', '')
self.proactive_kv_prewarm_hit_tokens = geo.get('proactive_kv_prewarm_hit_tokens', 0)
self.proactive_kv_prewarm_seeded_tokens = geo.get('proactive_kv_prewarm_seeded_tokens', 0)
self.proactive_kv_prewarm_wasted = geo.get('proactive_kv_prewarm_wasted', '')
```

## 6. トリガー・フロー

新メソッド`Router.maybe_proactive_kv_prewarm(current_time_ns)`。呼び出し箇所は`serving/__main__.py`のメインループ、既存の`router.route_arrived_requests(current)`直後——毎iteration呼ばれ、requestの到着有無に関係なく実行される、コードベース内で唯一のrequest非依存フック地点(実コード確認済み)。

```python
if dataset is not None:
    router.route_arrived_requests(current)
    router.maybe_proactive_kv_prewarm(current)
```

処理内容:

1. `enable_proactive_kv_prewarm`が偽なら即return(既存挙動を完全温存)
2. グローバルスロットル(`proactive_kv_prewarm_eval_interval_ns`、デフォルト200ms)でスキップ——このメインループはNPUマイクロイベントごとに回るため頻度が高すぎる
3. 各prefill schedulerについて:
   - `_capacity_snapshot(sched, dummy_req_data)`で`capacity_pressure`を取得(既存の容量逼迫計算をrequest非依存のダミー入力で再利用)
   - 閾値(`proactive_kv_prewarm_pressure_threshold`、デフォルト0.8)未満ならスキップ
   - per-instanceクールダウン(`proactive_kv_prewarm_cooldown_ns`、デフォルト2秒)チェック
   - `_instance_user_history`からlookback窓(`proactive_kv_prewarm_lookback_ns`、デフォルト30秒)内の到着頻度で上位K名(`proactive_kv_prewarm_top_k`、デフォルト3)を選出
   - 1ユーザにつき同時に未解決の先回りは1件までとする
   - 宛先選定: 自分以外の全schedulerで`capacity_pressure`最小のものを選ぶ(新規`_select_proactive_destination`)
   - `target.memory.seed_migrated_prefix(...)`を呼び、先回りインストール。ブックキーピングと診断ログに記録(実コストは課金しない)

**履歴の記録**: 新規`_record_user_activity(req_data)`を、既存の`_record_initial_capacity_context`呼び出し直後に追加。同じ冪等ガードパターン(初回到着時に1回だけ記録)を使う——`route_arrived_requests`のループは同一requestに対し複数回re-runされ得るため。

## 7. Payoffフロー(2フック構成)

**Hook 1(解決)**: 新規`_resolve_proactive_kv_prewarm(req_data, sched)`。最終的なターゲットschedulerが確定した直後、既存の`_apply_kv_migration_if_needed`呼び出しの直前に、**全request**(migrate_kv以外も含む)で呼ぶ。先回り先と実際の宛先が一致すればトークン数をreq_dataへ引き継ぎ、不一致・local処理ならその場でwasted扱い。

**Hook 2(課金適用)**: 既存の`_apply_kv_migration_if_needed`のmigrate_kv分岐内、`migrated_tokens = sched.memory.seed_migrated_prefix(...)`の直後に挿入:

```python
proactive_credit_tokens = 0
if self.enable_proactive_kv_prewarm:
    seeded = int(req_data.get('_proactive_kv_prewarm_seeded_tokens', 0))
    if seeded > 0:
        match = sched.memory.npu_prefix_cache.match_prefix(input_hash_ids[:migrated_tokens])
        proactive_credit_tokens = min(match.hit_length, seeded, migrated_tokens)

billable_tokens = max(0, migrated_tokens - proactive_credit_tokens)
migration_bytes = sched.memory.get_kv(billable_tokens) * sched.num_npus
```

`match_prefix`で実際の残存状態を検証してから課金するため、先回りキャッシュがその後evictされていた場合の過大クレジットは起きない。これより下にあるMethod A(`kv_ready_time_ns`)・旧Method B(`elapsed_speculative_ns`)のロジックは無変更のまま、割り引かれた`migration_ns`の上にそのまま適用される——**3手法は競合せず合成できる**設計。

## 8. 新規CLIフラグ(全てデフォルト無効、opt-in)

| フラグ | デフォルト | 用途 |
|---|---:|---|
| `--enable-proactive-kv-prewarm` | False | マスタースイッチ |
| `--proactive-kv-prewarm-pressure-threshold` | 0.8 | トリガー閾値 |
| `--proactive-kv-prewarm-top-k` | 3 | 先回り対象ユーザ数 |
| `--proactive-kv-prewarm-lookback-ns` | 30秒 | 頻度ランキング窓・内容の鮮度上限 |
| `--proactive-kv-prewarm-cooldown-ns` | 2秒 | instance単位の再トリガー抑制 |
| `--proactive-kv-prewarm-eval-interval-ns` | 200ms | グローバルスキャン間隔 |
| `--proactive-kv-prewarm-output` | None | 診断用CSV出力パス(任意) |

旧Method Bと異なり、Method A/Bとのクロスフラグ検証は不要(§3の通り独立して効くため)。数値デフォルトは実測(到着間隔中央値3.7秒、90秒トレース)から逆算した初期値であり、Stage 2のスイープで調整する前提。

## 9. 決定済みの論点

- **トリガー指標は`capacity_pressure`のみ**(`slot_pressure`は見ない): 実測した全redirectケースが例外なく`redirect_capacity_reason=npu_memory`(seq数上限は128中26〜29程度で余裕があった)であり、v1はシンプルに保つ。
- **先回り転送は無料**: 旧Method Bと同じ前提(投機は容量・帯域競合をモデル化しない)。まず上限効果を測り、有望なら輻輳競合モデル付きのv2を検討する。
- **「instance Iの常連ユーザ」はhome instance(redirect前の`assigned_instance_id`)基準**: そのユーザの通常トラフィックがそのinstanceの逼迫を引き起こしている、という前提に対応する自然な定義。

## 10. 段階的検証計画

- **Stage 0(単体テスト)**: `tests/test_proactive_kv_prewarm_router.py`新設。payoffが実際の`match_prefix`挙動に依存するため、`FakeMemory`ではなく実`RadixCache`/最小限の実`MemoryModel`相当を使う。閾値/クールダウン/スロットル、頻度ランキングのtop-K選出、宛先選定、`seed_migrated_prefix`の実インストール、hit時の`migration_bytes`割引、miss時の二重クレジット防止、フラグ無効時の完全no-opを検証。
- **Stage 1(小規模実機)**: `input512_reuse00`で、デフォルトcluster config(24GB)と、並行検証中の容量を絞った`five_node_rtx4090_apn_kvtight20.json`(20GB)の両方で、そもそも閾値を超える逼迫が発生するかを確認。
- **Stage 2(閾値/top-Kスイープ)**: pressure閾値{0.6,0.7,0.8,0.9}×top-K{1,3,5}でトリガー回数とhit率を確認。
- **Stage 3(hit/miss比較)**: hit / miss / 投機未対象の3群でTTFTを比較。
- **Stage 4(本番sweep)**: 既存4-5 workloadで、Method A単体・Method A+Cの両方を検証し、「Method Aが無くても効く」という設計上の主張を確認する。

## 11. 今回のスコープで明示的に受け入れる制約

- 先回り転送は無料(帯域・容量競合をモデル化しない)——測定値は上限として報告する
- 宛先選定は`capacity_pressure`のみ(formula系policyのTTFT予測は使わない)
- 頻度ランキングは単純な窓内カウント(減衰や到着時刻予測は無し——ユーザーの明示判断通り)
- 1ユーザにつき同時に未解決の先回りは1件まで
- エージェント的セッション(`sub_requests`)は対象外、v1は`user_id`のあるflatワークロードのみ

## 関連ファイル

- 設計計画: `.claude/plans/elegant-dazzling-lobster.md`
- 実装対象: `serving/core/router.py`, `serving/core/request.py`, `serving/core/scheduler.py`, `serving/__main__.py`
- 再利用(無変更): `serving/core/memory_model.py`(`seed_migrated_prefix`), `serving/core/radix_tree.py`(`match_prefix`)
- Method A/Bの検証記録: `reports/implementation_and_verification.md`, `reports/branch_predictor_analogy_analysis.md`
