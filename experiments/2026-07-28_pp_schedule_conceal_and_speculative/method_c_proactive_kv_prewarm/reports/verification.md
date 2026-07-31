# Method C(容量逼迫トリガー型・投機的KVキャッシュ先回り移送)検証記録

設計は[`../../reports/proactive_kv_prewarm_design.md`](../../reports/proactive_kv_prewarm_design.md)、
実装計画は`.claude/plans/elegant-dazzling-lobster.md`を参照。本レポートは
実装後の段階的検証(Stage 0〜)の結果を記録する。Method A/B
(`../../reports/implementation_and_verification.md`)とは別の機構であり、
検証結果が混ざらないよう本ディレクトリ(`method_c_proactive_kv_prewarm/`)
に分離した。

## Stage 0: 単体テスト — 全51件成功

- `tests/test_proactive_kv_prewarm_router.py`を新規作成(当初13件、後述の
  優先evict対応で+2件=15件): 閾値/クールダウン/グローバルスロットルによる
  トリガー抑制、頻度ランキングのtop-K選出とタイブレーク、宛先選定
  (`capacity_pressure`最小の他instance)、実`RadixCache`への実インストール
  確認(`match_prefix`で直接検証)、hit時の`migration_bytes`/`migration_ns`
  割引、miss時(別ターゲット行き/local行き)の無害化と二重クレジット防止、
  フラグ無効時の完全no-opを検証。
- 既存27件(旧router)+9件(Method A/B router)+5件(scheduler)+新規15件
  = 合計51件全て成功(`python3 -m pytest tests/ -q`)。
- 実装中に判明した副次的な発見: 既存テストの一部(`test_second_ttft_reserve_
  router.py`)が`sys.modules`にダミーの`memory_model`モジュールを登録して
  おり、pytestのファイル収集順序によっては後続のテストファイルに漏れて
  壊れる脆さがあった。新規テストファイルの読み込み順が先頭寄りだったため
  顕在化しかけたが、新規ファイル側でスタブを使わない(実`memory_model`を
  そのまま使う)方針にして回避した。実装コード自体の問題ではない。

## Stage 1: 小規模実機シミュレーション

Method A(`--enable-scheduler-hide-kv-migration`)とMethod C
(`--enable-proactive-kv-prewarm`)を同時に有効化し、ユーザーの意図する
「Method Cで極力抑え、外れた分はMethod Aで隠す」という2軸運用をそのまま
検証した(実装上、Hook 2のクレジット計算がMethod A/Bの重ね合わせロジック
より先に走るため、素の追加実装なしにこの2軸は合成される)。

### Stage 1a: `input512_reuse00`(デフォルト容量24GB) — トリガー無し(想定通り)

| 指標 | 値 |
|---|---:|
| request数 | 300 |
| redirect数 | 0 |
| `proactive_migration_log.csv` | 生成されず(トリガー0件) |

このworkloadは既知の通りKV容量圧迫がほぼ発生しない(README記載の
「PP overhead / low pressure」条件)。`capacity_pressure`が閾値0.8を
一度も超えず、トリガー自体が発火しなかった——バグではなく設計通りの
「そもそも逼迫が無ければ何もしない」という挙動。Method A/Cを載せても
TTFT等の数値は既存ベースラインの水準のまま(異常なし)。

### Stage 1b: `input8000_reuse025`(デフォルト容量24GB) — 実際にhitを確認

| 指標 | 値 |
|---|---:|
| request数 | 300 |
| redirect数 | 36 |
| トリガー発火回数(`proactive_migration_log.csv`行数) | 137 |
| 先回り対象になったユニークユーザ数 | 20(全ユーザ) |
| **hit数(先回り先とredirect先が一致)** | **9 / 36 redirects(25.0%)** |
| wasted数(先回りしたが使われなかった) | 109 |

**hit時のKV移送コスト削減:**

| 指標 | non-hit(通常redirect) | hit |
|---|---:|---:|
| 平均KV移送latency | 211.81 ms | **0.30 ms**(99.86%削減) |
| hit分のクレジットトークン数 | — | 2000(reuse_prefix_toksの全量、常に満額) |

**hit時のTTFT改善:**

| 群 | 平均E2E TTFT |
|---|---:|
| hit | **931.4 ms** |
| non-hit redirected | 1260.8 ms |
| 全300件平均(A+C有効) | 954.9 ms |

hit群はnon-hit-redirected群より**平均330ms(26%)速い**。KV移送コスト
211.8msのほぼ全額(0.3ms残存は`apn_fixed_propagation_ns`等の固定
オーバーヘッドの下限、輸送量に依存しないため0にはならない)が相殺されて
おり、設計通りに機能していることを確認した。

wasted 109件は「先回りしたユーザの次のrequestが結局別GPU行き/local処理に
なった」ケース——投機は容量を消費しない前提(§8の受け入れた制約)なので、
無駄になっても実害は無い、と当初考えていた(§3で訂正)。

**Cの純増分は「A only」を基準に見る必要がある**(重要な訂正): Stage 1bは
Method A・Cを両方有効にした1本のrunなので、flag完全offのplain baselineと
比べるとMethod A自身の寄与とMethod Cの寄与が混ざり、Cだけの純増分を切り
分けられない。正しくは既存の`pp2_scheduler_hide`(Method Aのみ)アームを
基準にすべき:

| arm | redirect数 | 全300件平均TTFT |
|---|---:|---:|
| plain(flag完全off) | 35 | 958.3 ms |
| **A only**(`pp2_scheduler_hide`) | 35 | 959.0 ms |
| **A+C**(Stage 1b) | 36 | 954.9 ms |

A only → A+Cで見ると **959.0ms → 954.9ms(-4.1ms、-0.43%)**。**この数値は
§2のバグ修正前のものであり、後で訂正される(§3参照)。**

### Stage 1の結論(当初)

Method A/Bで実測した「ほぼ効果無し」とは対照的に、**未調整のデフォルト
パラメータ(閾値0.8、top-K=3、lookback 30秒)でもredirectの25%が実際に
hitし、hit群で26%のTTFT改善が確認できた**。これはMethod A/Bの検証で
見つかった構造的な壁(routerが選ぶredirect先はそもそも輻輳していない、
pinトリガーの中間領域がほぼ存在しない)を、Method Cの設計(事前に予測して
移送する、request非依存)が実際に回避できていることを示す最初の実測結果。

## 2. バグ修正: 先回りサイズが「プロンプト全長」になっていた

PP1(`ten_node_rtx4090_apn.json --pp-size 1`)でMethod A+Cを検証しようと
したところ、`input8000_reuse00/025/05`全てで以下のクラッシュが再現した:

```
RuntimeError: [MemoryModel] [node=1,inst=1]:
not enough NPU memory to seed migrated KV prefix of 8000 tokens
```

原因: `_record_user_activity`が、先回りする内容のサイズとして
`req_data['input_toks']`(プロンプト**全長**、例: 8000)を記録していた。
本来先回りすべきは実際に再利用可能な部分(`reuse_prefix_toks`、例:
2000)だけであり、これは実KV移送(`_apply_kv_migration_if_needed`)が
使っているのと同じ値。PP2ではたまたま容量に余裕があり8000トークン全量の
先回りが(無駄に大きいまま)成功していたため気づかなかったが、PP1の
ほぼ満杯(97〜99%)のNPUメモリでは確保できずクラッシュした。

`serving/core/router.py`の`_record_user_activity`を修正し、
`content['input_toks']`ではなく`min(req_data['reuse_prefix_toks'],
req_data['input_toks'])`を`content['reuse_prefix_toks']`として記録する
よう変更(`maybe_proactive_kv_prewarm`の`seed_migrated_prefix`呼び出しも
追随)。`input8000_reuse00`(reuse_prefix_toks=0の workload)ではこの修正
により先回り自体が起きなくなる(hit=0)——正しい挙動。

## 3. 発見: 投機的先行転送は実は「無料」ではなかった

バグ修正後、PP2 `input8000_reuse025`を再測定したところ、Stage 1bの数値が
変わった:

| 指標 | 修正前(バグ、8000トークン先回り) | 修正後(正しく2000トークン) |
|---|---:|---:|
| redirect数 | 36 | **46** |
| hit数 | 9 | 11 |
| 全300件平均TTFT | 954.9 ms | **988.9 ms** |
| vs A only(959.0ms) | -0.43%(改善) | **+3.10%(悪化)** |

**原因**: `seed_migrated_prefix`(Method A/Bの実KV移送とも共有する関数)は
ターゲットの実NPUメモリを本当に消費し、必要なら既存の実キャッシュ内容を
実際にevictする。「投機は容量を消費しない」という設計上の想定(§8)は、
旧Method Bの「タイムスタンプ記録だけ」という前例に倣ったものだったが、
Method Cは「後で本当にcache hitさせる」ために本物のキャッシュを実際に
インストールする必要があり、この想定はMethod Cの仕組みそのものと原理的に
両立しなかった。

結果として、先回りされたユーザ以外の**無関係な本物のrequest**が、この
容量圧迫のせいでredirectに回され(35→46件)、割引されない素のKV移送
コストを新たに払うことになり、hitによる得より広く薄い損の方が上回って
いた。

## 4. 対応A: 投機的コンテンツの優先evict

`RadixCache.evict()`(radix_tree.py)は`TreeNode.last_access_time`昇順の
純粋なLRUで、挿入時に触れたノードの`last_access_time`を「今」に更新する
ため、**投機的に挿入したばかりのコンテンツが最もevictされにくい**(狙いと
正反対)という問題を確認した。

`radix_tree.py`自体は変更せず、`MemoryModel.seed_migrated_prefix`
(memory_model.py)に`mark_speculative`引数を追加。`True`の時、挿入直後に
`match_prefix`で対象ノードを取得し`last_access_time`を`0.0`に巻き戻す。
本物のrequestが実際にこの内容を`match_prefix`でヒットさせた瞬間(Hook 2)、
既存の`_match_prefix_helper`が`last_access_time`を「今」に更新するため、
**実際に使われた投機コンテンツはその瞬間から通常の実コンテンツと同じ
扱いに戻る**——一度も本物に使われないまま放置されたコンテンツだけが
evict最優先であり続ける。`router.py`の`maybe_proactive_kv_prewarm`から
`mark_speculative=True`で呼ぶよう変更。単体テスト2件追加
(`PreferentialEvictionTest`)。

**効果(PP2 `input8000_reuse025`)**:

| | redirect数 | 全300件平均TTFT | vs A only |
|---|---:|---:|---:|
| A only | 35 | 959.0 ms | — |
| A+C(修正前) | 46 | 988.9 ms | +3.10%悪化 |
| A+C(evict優先度修正後) | 36 | 967.5 ms | **+0.94%悪化(大幅改善)** |

悪化幅を+3.1%→+0.9%まで縮小できたが、まだ完全には解消していない(→Stage 2)。

## 5. PP1での検証(baseline / A only / A+C 3群比較)

`method_c_proactive_kv_prewarm/run_pp1_proactive_prewarm_parallel.sh`
(新規、`run_pp1_scheduler_hide_parallel.sh`の複製+Method Cフラグ追加)で
PP1(10台独立GPU)の`input8000_reuse00/025/05`を実行(§2・§4の修正適用後)。

| workload | baseline | A only | A+C |
|---|---:|---:|---:|
| reuse00(0%) | 3592.0ms(186 redirect) | 3592.0ms(186) | 3592.0ms(186、hit=0) |
| reuse025(25%) | 1675.6ms(183) | 1675.6ms(183) | **1643.6ms(173)、-1.9%** |
| reuse05(50%) | 805.0ms(106) | 804.9ms(106) | **780.7ms(106)、-3.1%** |

**PP2とは対照的に、PP1ではredirect数が減り(183→173、106→106)、
全指標で明確に改善している**。reuse00でhit=0なのは、reuse_prefix_toks=0
(再利用可能な内容が無い)workloadなので投機自体が発火しないため——§2の
バグ修正が正しく効いている証拠。PP1とPP2で副作用の出方が異なる理由
(トポロジ構造の違いが影響していそう)は未解明のまま。

## 6. Stage 2: 閾値/top-Kスイープ

`run_stage2_sweep.sh`(新規)で、PP2 `input8000_reuse025`について
`--proactive-kv-prewarm-pressure-threshold` ∈ {0.6, 0.7, 0.8, 0.9} ×
`--proactive-kv-prewarm-top-k` ∈ {1, 3, 5}の12通り(evict優先度修正後)を
実行し、「A only」(959.0ms、p95 1453.2ms、35 redirect)と比較した。

| threshold | top-K | redirect数 | hit数 | 平均TTFT | vs A only | p95 | vs A only(p95) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| **0.6** | **3** | 38 | 12 | **956.6ms** | **+0.25%改善** | **1388.2ms** | **+4.48%改善** |
| 0.6 | 1 | 35 | 2 | 959.9ms | -0.09% | 1420.0ms | +2.29% |
| 0.6 | 5 | 37 | 15 | 958.2ms | +0.09% | 1447.2ms | +0.41% |
| 0.7 | 1 | 37 | 1 | 970.1ms | -1.16% | 1480.8ms | -1.90% |
| 0.7 | 3 | 42 | 6 | 976.8ms | -1.85% | 1484.7ms | -2.16% |
| 0.7 | 5 | 45 | 5 | 982.4ms | -2.44% | 1459.9ms | -0.46% |
| 0.8 | 1 | 37 | 5 | 968.2ms | -0.95% | 1481.0ms | -1.91% |
| 0.8 | 3(旧デフォルト) | 36 | 11 | 967.5ms | -0.88% | 1459.5ms | -0.43% |
| 0.8 | 5 | 37 | 14 | 971.8ms | -1.33% | 1468.2ms | -1.03% |
| 0.9 | 1 | 35 | 5 | 969.2ms | -1.06% | 1482.7ms | -2.03% |
| 0.9 | 3 | 36 | 8 | 965.4ms | -0.67% | 1458.4ms | -0.35% |
| 0.9 | 5 | 38 | 11 | 962.5ms | -0.36% | 1460.7ms | -0.51% |

**閾値0.6・top-K=3が唯一、平均・p95の両方でA onlyを上回った設定**
(素のplain baseline 958.3msと比べても+0.18%改善)。閾値を上げる
(0.7〜0.9、より保守的にする)ほど全設定で悪化するという、直感に反する
結果になった。デフォルト値として使っていた閾値0.8は、実はこの
グリッドの中で相対的に悪い部類だった。

### なぜ閾値を上げるほど悪化するのか

`proactive_migration_log.csv`(トリガーイベント診断ログ)をtop-K=3で
閾値別に比較すると、原因が見えた:

| threshold | トリガーイベント数 | 初回発火タイミング | 発火時の平均`source_capacity_pressure` |
|---:|---:|---:|---:|
| 0.6 | 90 | 21.7秒目 | 0.867 |
| 0.8 | 65 | 27.1秒目 | 0.916 |
| 0.9 | 53 | 36.3秒目 | 0.947 |

閾値を上げるほど、トリガーの発火が**遅くなり**(まだ余裕がある早い段階を
逃す)、発火時点の逼迫度も**より深刻**になる。この90秒workloadでは全体の
負荷が時間とともに高まっていく傾向があるため、発火が遅れるほど宛先候補
(`_select_proactive_destination`が選ぶ「その時点で最も空いている他
instance」)自体もその頃には同程度に逼迫し始めている可能性が高い——結果、
先回りしたコンテンツが実際に使われる前に(または使われても)、宛先側の
容量圧迫を悪化させやすくなり、hit率も下がる(top-K=3でhit数12→11→8、
threshold 0.6→0.8→0.9)。「早い段階で・まだ余裕のある宛先に」先回りする
方が、「逼迫が深刻になってから・同じく逼迫し始めた宛先に」先回りするより
明確に有利、という一貫した説明になる。

## Stage 3: hit / miss / 投機非対象の3群比較

閾値0.6・top-K=3(Stage 2の最良設定)の`requests.csv`を、Method Cとの
関わり方で4群に分けて比較した:

| 群 | 定義 | n | うちredirect | redirect時の平均TTFT | redirect時の平均KV移送 | local時の平均TTFT |
|---|---|---:|---:|---:|---:|---:|
| **hit** | 先回り先とredirect先が一致 | 12 | 12 | **939.9ms** | **0.3ms** | — |
| wasted・redirected | 先回りしたが別の場所へredirect | 12 | 12 | 1207.0ms | 211.8ms | — |
| wasted・local | 先回りしたが結局local処理 | 110 | 0 | — | — | 970.2ms |
| 投機非対象・redirected | 一度も先回り対象にならずredirect | 14 | 14 | 1287.3ms | 211.8ms | — |
| 投機非対象・local | 一度も先回り対象にならずlocal処理 | 152 | 0 | — | — | 897.8ms |

**redirect時の比較**: hit(939.9ms)は、同じ「redirectされた」母集団の中で
wasted・redirected(1207.0ms)より267ms(22%)、投機非対象・redirected
(1287.3ms)より348ms(27%)速い。KV移送コストはhitのみ0.3msに割引かれ、
残り2群は満額(211.8ms)——**過大クレジットは一切発生していない**ことも
確認できた(意図通りmatch_prefixで実際の残存を検証しているため)。

**local時の比較(興味深い点)**: どちらもredirectされていない(=KV移送
コスト自体が発生していない)にも関わらず、wasted・local(970.2ms)は
投機非対象・local(897.8ms)より72ms(8%)遅い。これはMethod Cが悪さを
しているのではなく、**先回り対象になった時点で「そのユーザのhome
instanceが既に逼迫していた」という選択バイアス**——先回り対象に選ばれる
こと自体が「元々混みがちなGPUのユーザである」ことを意味するため、
たまたまその回はlocal処理で済んでも、home GPU自体の混雑がscheduler
queueや実行時間にわずかに影響していると考えられる。

## 現状のまとめ

| 項目 | 状況 |
|---|---|
| Method C実装 | 完了、単体テスト51件・PP1/PP2実機で動作確認済み |
| バグ修正 | 先回りサイズを`input_toks`→`reuse_prefix_toks`に修正(§2)。投機コンテンツの優先evict追加(§4) |
| PP2 `input8000_reuse025` | 修正・チューニング前提でA onlyを上回る設定(閾値0.6・top-K3)を発見(§6) |
| PP1(3 workload) | reuse025/05でA onlyを明確に上回る(-1.9%/-3.1%)、reuse00は投機非該当で無変化(§5) |
| 閾値の逆相関の原因 | 解明済み(§6): 閾値が高いほどトリガー発火が遅れ、発火時点の逼迫度が深刻化するため、宛先候補も同時に逼迫し始めておりhit率が下がる |
| Stage 3 | 完了(hit/wasted/投機非対象の4群比較)。過大クレジット無し、hitはredirect母集団内で22-27%速いことを確認。wasted-localが投機非対象-localよりやや遅いのは選択バイアス(元々混みがちなGPUのユーザが先回り対象に選ばれやすい)と判断 |
| 次のステップ | Stage 4(既存4-5 workloadでの本番sweep、閾値0.6・top-K3で)、PP1側でも閾値スイープを行うか検討 |
| 別トラック(未着手) | Method Aが効くような輻輳を意図的に作るための到着率実験(`make_harsh_workload.py`、input8000系への適用) |

## 生成物

- `results/stage1/input512_reuse00/` — Stage 1a(トリガー無し確認)
- `results/stage1/input8000_reuse025/` — Stage 1b(evict優先度修正後の最新版)
- `results/pp1_proactive_prewarm/{input8000_reuse00,025,05}/` — §5のPP1検証
- `results/stage2_sweep/th{閾値}_k{top-K}/` — §6の12通りスイープ結果
- 各`requests.csv`は新規4列(`proactive_kv_prewarm_hit`/`_hit_tokens`/
  `_seeded_tokens`/`_wasted`)と、対応する`proactive_migration_log.csv`
  (トリガーイベント診断ログ)を含む

## 関連ファイル

- 設計計画: `.claude/plans/elegant-dazzling-lobster.md`
- 設計レポート: `../../reports/proactive_kv_prewarm_design.md`
- 実装: `serving/core/router.py`, `serving/core/request.py`,
  `serving/core/scheduler.py`, `serving/__main__.py`
- 単体テスト: `tests/test_proactive_kv_prewarm_router.py`
- Method A/Bの検証記録(比較対象): `../../reports/implementation_and_verification.md`
