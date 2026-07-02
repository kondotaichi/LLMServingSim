# 地理分散推論シミュレーション Phase 1 実装記録

日付: 2026-07-02
ブランチ: `experiment/sim-hack`
仕様書: `~/Downloads/LLMServingSim 地理分散推論シミュレーション Phase 1 仕様書.pdf`（全32節）
承認済み実装計画: `~/.claude/plans/proud-sniffing-barto.md`

## 目的

3000人のユーザが1km四方のエリアに一様ランダム配置され、最も近いGPU（20台、
5列×4行グリッド配置）へLLM推論リクエストを送るシナリオをシミュレートし、
End-to-End TTFT（通信遅延＋キューイング＋Prefill）とそのボトルネックを
リクエスト単位・ユーザ単位・GPU単位で分析できるようにする。Phase 1は
数式ベースの解析的通信モデル（固定スループット＋距離比例遅延のみ、
ns-3連携なし）に限定。

## 実装した変更点

| ファイル | 内容 |
| --- | --- |
| `workloads/generators/geographic.py`（新規） | 元workloadを読み、ユーザ/GPU座標生成・最近傍GPU割当・通信時間計算・拡張JSONL/CSV/metadata出力 |
| `workloads/generators/__main__.py` | `geographic`サブコマンド登録 |
| `configs/cluster/geographic_20gpu.json`（新規） | 20個の独立single-GPU instance（TP=1, PP=1, `meta-llama/Llama-3.1-8B` on `RTXPRO6000`） |
| `serving/core/router.py` | `NEAREST`ルーティングポリシー追加（`assigned_instance_id`をそのまま使い再選択しない）、地理/通信フィールドの抽出・伝搬 |
| `serving/core/request.py` | `Request`に地理・通信・計測用フィールド一式を追加、`account_admission()`新設、`set_ttft`/`add_latency`拡張、`Batch.finish_time_ns`追加 |
| `serving/core/scheduler.py` | バッチ admission 時の待機時間計上、`add_done`でのバッチ完了時刻スタンプ＋prefill/decode時間積算、`save_output`/`print_result`拡張 |
| `serving/core/geo_report.py`（新規） | ユーザ単位・GPU単位集計とmetadata JSON出力（独立モジュール） |
| `serving/__main__.py` | 新規CLIフラグ5つ、ループ後処理からの`geo_report`呼び出し。**メインループ本体・呼び出し順序は無変更** |

既存の`serving/__main__.py`・`router.py`には、この作業と並行してユーザ自身が
`PROMPT`/`QUEUE`/`HYBRID`ルーティングポリシーを追加中だった（未コミット）。
それらは壊さず、その上に`NEAREST`を追加する形で実装した。

## 主要な設計判断（仕様の解釈が必要だった箇所）

計画ファイル（`proud-sniffing-barto.md`）に詳細あり。要約:

1. **User/GPU集計CSV用の全ロースター**: `--geographic-users-csv`/`--geographic-gpus-csv`
   という読み込み専用オプションを新設し、generatorが吐いた静的配置CSVから
   0件リクエストのユーザ/GPUも含めた全件をロースターとして使う。
2. **ratio列の分母**: `communication_ratio`/`queueing_ratio`/`prefill_ratio`は
   `E2E_TTFT`が分母（仕様19.1の式通り）。`decode_ratio`はTTFT範囲外のため
   `request_completion_latency_ns`を分母とした。
3. **Prefill/Decode状態判定**: バッチ実行時間をどちらに積算するかは
   `req.is_prefill()`ではなく`req.ttft == -1`で判定。フルprefixキャッシュ
   ヒット時の不整合を避け、`queueing_before_ttft_ns + prefill_service_ns == ttft`
   という不変条件が全ケースで厳密に成立するようにした（実際に検算済み）。
4. **集計コードの配置**: 仕様は`scheduler.py`への追加を示唆していたが、
   モジュール分離方針を優先し`geo_report.py`に独立させた。

## 実行コマンド

### 1. 地理workload生成

```bash
python -m workloads.generators geographic \
  --input workloads/sharegpt-llama-3.1-8b-300-sps10.jsonl \
  --user-frequency-file workloads/generated/geo_full_freq.csv \
  --output workloads/generated/geo_full_workload.jsonl \
  --users-output workloads/generated/geo_full_users.csv \
  --gpus-output workloads/generated/geo_full_gpus.csv \
  --metadata-output workloads/generated/geo_full_gen_metadata.json \
  --num-users 3000 --gpu-rows 4 --gpu-cols 5 --seed 42
```

ユーザ頻度分布ファイルが無く`--allow-uniform-user-fallback`も指定しない場合は
明確なエラーで終了する（仕様10.5の必須制約、実装・動作確認済み）。

### 2. シミュレーション実行

```bash
python -m serving --cluster-config configs/cluster/geographic_20gpu.json \
  --dtype bfloat16 --block-size 16 \
  --dataset workloads/generated/geo_full_workload.jsonl \
  --request-routing-policy NEAREST \
  --output outputs/geo_full_requests.csv \
  --geographic-user-output outputs/geo_full_users.csv \
  --geographic-gpu-output outputs/geo_full_gpus.csv \
  --geographic-metadata-output outputs/geo_full_metadata.json \
  --geographic-users-csv workloads/generated/geo_full_users.csv \
  --geographic-gpus-csv workloads/generated/geo_full_gpus.csv \
  --log-level WARNING
```

`--dataset`はrepo root相対パスで指定する必要がある（`router.py`が内部で
`../`を前置する既存仕様のため、絶対パスは不可）。`servingsim_docker`
コンテナ内、`/app/LLMServingSim`から実行。

## 検証結果

### 小規模（2GPU・10ユーザ・10リクエスト、`workloads/example_trace.jsonl`ベース）
成功。出力CSVの不変条件を全件検算:
- `queueing_before_ttft_ns + prefill_service_ns == TTFT`
- `simulator_ttft_ns == TTFT`
- `e2e_ttft_ns == uplink_latency_ns + TTFT + downlink_latency_ns`
- `decode_queueing_ns + decode_active_ns == decode_after_ttft_ns`

いずれも10/10リクエストで成立。NEARESTルーティングも`assigned_gpu_id`と
実際の`gpu_id`が一致することを確認。ユーザCSVは0件送信ユーザも含め
10行全件出力、GPU CSVも2行（GPU 0/1）出力を確認。

### 後方互換性チェック
```bash
python -m serving --cluster-config configs/cluster/single_node_single_instance.json \
    --dtype bfloat16 --block-size 16 \
    --dataset workloads/example_trace.jsonl --output outputs/backcompat_check.csv \
    --num-req 10
```
無変更で成功。TTFT/TPOT/ITLの値もチュートリアル時（`Diary/output/
2026-07-02-single-instance-tutorial.md`）と完全一致。新規追加した
`End-to-End TTFT`/`Queueing Before TTFT`等のブロックは地理フィールドが
無いため自然に「No ... data available」と表示され、クラッシュしない。
出力CSVの新規列も`-1`/空文字で埋まり、既存12列は変更なし。

### 本番規模（20GPU・3000ユーザ・300リクエスト、ShareGPTベース）— 中断
`workloads/sharegpt-llama-3.1-8b-300-sps10.jsonl`（300リクエスト）で
generatorは0.58秒で完了したが、シミュレーション本体は実行開始から
34分47秒経過時点で`.et`バッチファイルの生成ペースが直近19分でわずか
+19ファイルまで低下（当初の観測ペース「7ファイル/秒」は序盤のバースト値で、
定常状態の実速度ではなかった）。総バッチ数が数千のオーダーになりそうで、
このペースでは完了に数時間かかる可能性があったため中断した
（CPU/メモリは正常に稼働しており、ハングではなく純粋に計算量が大きい）。

### 20GPU・3000ユーザ・10リクエスト（`example_trace.jsonl`ベース）— 完了・成功

Step 7の本来の目的（20GPUフル構成でのロースター処理・集計ロジックの
正しさ）は、300件の重いリクエストで負荷をかけなくても確認できるため、
同じ`geographic_20gpu.json`（20 instance構成）のまま入力を
`example_trace.jsonl`（10リクエスト）に差し替えて再実行した。

```bash
python -m serving --cluster-config configs/cluster/geographic_20gpu.json \
  --dtype bfloat16 --block-size 16 \
  --dataset workloads/generated/geo_small20_workload.jsonl \
  --request-routing-policy NEAREST \
  --output outputs/geo_small20_requests.csv \
  --geographic-user-output outputs/geo_small20_out_users.csv \
  --geographic-gpu-output outputs/geo_small20_out_gpus.csv \
  --geographic-metadata-output outputs/geo_small20_out_metadata.json \
  --geographic-users-csv workloads/generated/geo_small20_users.csv \
  --geographic-gpus-csv workloads/generated/geo_small20_gpus.csv \
  --log-level WARNING
```

数十秒で完了し、以下を全て確認:
- リクエストCSV: 10件全てで4つの不変条件が成立
- NEARESTルーティング: 10リクエストが20 GPU中7つの異なるGPUへ正しく分散
  （ユーザの最近傍GPUと実際のルーティング先`gpu_id`が一致）
- **ユーザCSV: 正確に3000行**（0件送信ユーザ2990行＋送信ユーザ10行）。
  0件ユーザは`request_count=0`かつ遅延指標が全て空欄（null相当）で出力
- **GPU CSV: 正確に20行**、`request_count`の合計が10（総リクエスト数と一致）

これによりStep 7の受け入れ条件（仕様29節の18〜19番: 「ユーザ単位で
mean/p50/p95/p99を集計できる」「GPU単位で負荷と遅延を集計できる」）を
20GPU/3000ユーザという目標スケールで実証できた。300件の本番規模
ShareGPTワークロードでの長時間実行は、時間に余裕があるときに改めて
バックグラウンドで流す運用とする。

## 次のTODO
- [ ] 時間に余裕があれば300リクエスト本番規模を改めてバックグラウンドで実行し、
      本記録に結果を追記（Step 7自体は完了済みなので必須ではない）

---

## 改造ポイントの詳細（コードレベル）

既存コアの処理フロー（`Diary/codeReading/2026-07-02-serving-main-walkthrough.md`参照）
自体は一切変更していない。全ての追加はその既存フローの中の決まった箇所への
「計装（instrumentation）」と、新規モジュールの追加に限定した。

### 1. `workloads/generators/geographic.py`（新規、約330行）

`sharegpt.py`/`burstgpt.py`と同じ`register_args(p)` / `run(args) -> int`規約に
従う新規generator。主要な関数:

- `_generate_users(num_users, width, height, rng)` — `rng.uniform(0,W)/(0,H)`で
  3000ユーザ座標を一様分布生成。
- `_generate_gpus(rows, cols, width, height)` — グリッド式
  `x_g=(c+0.5)*W/C, y_g=(r+0.5)*H/R`で20 GPU座標を生成。`gpu_id = r*cols + c`
  （下段→上段、各行左→右）。
- `_nearest_gpu(user_xy, gpus)` — GPU ID昇順にスキャンし、厳密な`<`比較のみで
  更新することで「同距離なら最小ID」を自然に実現。
- `_load_user_weights(path, num_users)` — 頻度分布CSVの検証（範囲・重複・
  非負・合計>0・不正行エラー）込みの読み込み。
- `select_user(row)`（`run`内のローカル関数） — 優先順位「元行の`user_id`
  → 頻度ファイルの重み付きサンプリング → `--allow-uniform-user-fallback`
  時のみ一様分布 → それ以外はエラー」を実装。
- `_serialization_ns(payload_bytes, mbps)` — `8000 * bytes / mbps`
  （`T_ns = 8*bytes*1e9/(mbps*1e6)`を約分した式）。仕様書の実例数値
  （`request_payload_bytes=2548, mbps=100` → `203840ns`）で検算済み。
- `run(args)`本体 — 元workloadを1行ずつ読み、`arrival_time_ns`は
  **`request_send_time_ns`としてそのまま複製**（絶対に再生成しない）。
  Uplink/Downlink時間を計算後、`gpu_arrival_time_ns = request_send_time_ns +
  uplink_latency_ns`を計算し、既存Routerがそのまま読める`arrival_time_ns`
  フィールドを**上書き**する（＝GPU到着時刻の意味に変わる）。
  出力はns単位を`round()`して整数化（浮動小数点のままだと仕様書の実例と
  桁がずれるため）。

### 2. `serving/core/router.py`

- ファイル先頭に`_GEO_FIELDS`タプル（地理・通信フィールド名一覧）と
  `_extract_geo_fields(row)`ヘルパーを追加。地理フィールドが1つも無い行
  （＝旧形式workload）では`None`を返すようにして、「旧形式かどうか」を
  安価に判定できるようにした。
- `Router.__init__`のポリシーディスパッチ（`if/elif`チェーン）に
  `elif self.routing_policy == "NEAREST": self._select_instance =
  self._nearest_select`を追加（ユーザが並行して追加していた
  `PROMPT`/`QUEUE`/`HYBRID`はそのまま維持）。
- 新規`_nearest_select(self, schedulers, role, req_data=None)`:
  `req_data['assigned_instance_id']`を読み、渡された`schedulers`リストを
  線形探索して`sched.instance_id`が一致するインデックスを返す。
  `assigned_instance_id`が無い・一致するインスタンスが無い場合は
  `RuntimeError`で明確に落とす（仕様22.5の「不正なinstance IDの検証」）。
- `_load_flat_request`: `assigned_instance_id`があれば`req_data`に格納、
  `_extract_geo_fields(row)`の結果を`req_data['geo']`に格納。
- `route_arrived_requests`: `sched.add_request(...)`呼び出し2箇所
  （prefix caching有効/無効の分岐）に`geo=req_data.get('geo')`を追加。

### 3. `serving/core/request.py`

**`_argmax_label(pairs)`**（モジュールレベル関数）: `[(label, value), ...]`
を受け取り、厳密な`>`比較のみで更新するため「同値なら先に来たラベルが勝つ」
という決定的なタイブレークになる。TTFTボトルネックは
`[("queueing", q), ("prefill", pf), ("communication", comm)]`の順で渡すことで
仕様19.1の同値優先順位（queueing > prefill > communication）をそのまま表現。

**`Request.__init__`**: 末尾に`geo: dict | None = None`を追加（既存の
位置引数シグネチャは不変なので、`Request(*(req), is_init=is_init)`という
既存呼び出しは無傷）。`geo`辞書から地理・通信フィールドを個別属性として
展開し（`self.user_id`, `self.gpu_x_m`, `self.uplink_latency_ns`など）、
`self.request_send_time_ns`は`geo`に無ければ`None`のままにして「これが
旧形式workloadかどうか」の判定に使う。加えて計測用の内部状態を初期化:
`self.waiting_since_ns = self.arrival`（GPU到着した瞬間から「待機中」として
タイマーが動き出す）、`queueing_before_ttft_ns`等のアキュムレータは`0`、
`ttft_bottleneck`等の分析結果は`None`/`-1`で「未確定」を表す。

**`account_admission(self, current)`**（新規メソッド）:
```python
def account_admission(self, current):
    if self.waiting_since_ns is not None:
        delta = current - self.waiting_since_ns
        if self.ttft == -1:
            self.queueing_before_ttft_ns += delta
        else:
            self.decode_queueing_ns += delta
        self.waiting_since_ns = None
```
「バッチに admit されるたびに、直前の待機区間を確定して積算し、
`waiting_since_ns`を`None`にして"today active"状態にする」という単純な
状態機械。`self.ttft == -1`（まだTTFT未確定）かどうかで
`queueing_before_ttft_ns`（TTFT前の待機）と`decode_queueing_ns`
（TTFT後、decode中の待機）のどちらに積むかを切り替える。

**`set_ttft`拡張**: 既存の2行（`self.ttft = ...`, `self.recent_end = ...`）は
そのまま維持（ITL[0]の起点であるため順序を変えると既存指標が壊れる）。
その後ろに`first_token_ready_time_ns`/`first_token_received_time_ns`
（`+downlink_latency_ns`）/`e2e_ttft_ns`の計算と、`_argmax_label`による
`ttft_bottleneck`判定、3つのratio列の計算を追加。

**`add_latency`拡張**: 既存のlatency/tpot計算はそのまま。末尾に
`decode_after_ttft_ns = decode_queueing_ns + decode_active_ns`、
`request_completion_latency_ns`、`total_latency_bottleneck`
（queueing > prefill > decode > communicationの優先順位）、`decode_ratio`
を追加。

**`Batch.__init__`**: コンストラクタの引数は一切変更せず（`__main__.py`に
位置引数を手書きしたダミーBatch生成箇所があり、引数を増やすと壊れるため）、
本体に`self.finish_time_ns = -1`を1行追加するだけに留めた。

### 4. `serving/core/scheduler.py`

**`schedule_base`/`schedule_with_prefix`の両方**、バッチを構成する
`for req in batch_req:`ループの先頭（既存の`if req.is_prefill(): ...`より前）に
2行追加:
```python
req.account_admission(current)
if req.first_schedule_time_ns == -1:
    req.first_schedule_time_ns = current
```
注意点: 既存コードは`set_que_delay`をprefillリクエストのみに呼んでいた
（decodeリクエストには呼ばれていない）。`account_admission`は**両方の
リクエストに対して無条件で**呼ぶ必要がある（decode再開時の待機も
計測するため）ので、`if req.is_prefill():`の分岐に入る前、ループの
一番最初に置いた。

**`add_done`**: 全NPUの完了報告が揃った直後（既存の`"Batch #%d is done"`
ログの直後）に2行追加:
```python
batch.finish_time_ns = finish
batch_dur = finish - batch.batch_time
```
続く`for req in batch.requests:`ループの先頭（既存の
`is_prefill_req = req.is_prefill()`の直後）に、このバッチのdurationを
どちらの積算先に加えるかを決める4行を追加:
```python
if req.ttft == -1:
    req.prefill_service_ns += batch_dur
else:
    req.decode_active_ns += batch_dur
```
`req.is_prefill()`ではなく`req.ttft == -1`で判定している点が唯一
既存コードの慣習から外れた判断（フルprefixキャッシュヒット時に
`is_prefill()`が先に`False`になるケースで、`queueing_before_ttft_ns +
prefill_service_ns == ttft`という不変条件を保つため）。

「完了せずpoolへ戻る」分岐（既存の`pool.append(req)`の直前）に1行追加:
```python
req.waiting_since_ns = finish
```
これでChunked Prefillのchunk間待機・decode再待機も次の`account_admission`
呼び出し時に正しく積算される。

**`add_request`**: シグネチャに`geo=None`を追加し
`Request(*(req), is_init=is_init, geo=geo)`へ転送するだけ。

**`save_output`**: 既存12列はそのまま、その後ろに地理・通信・時刻・
TTFT内訳・Decode・ボトルネックの列を約35列追加（仕様24節に準拠）。
旧形式workload（`geo=None`）の場合は`req.user_id is None`等を判定して
空文字/`-1`を書き込む。

**`print_result`**: 既存の`_render(title, values)`ヘルパーはそのまま
再利用し、`"End-to-End TTFT"` / `"Queueing Before TTFT"` /
`"Prefill Service"` / `"Decode After TTFT"`の4ブロックを追加。
`req.e2e_ttft_ns >= 0`でフィルタしているため、旧形式workloadでは
自然に「No ... data available」と表示される（`_render`の既存の
空リストガードがそのまま効く）。

### 5. `serving/core/geo_report.py`（新規、独立モジュール）

`scheduler.py`本体を肥大化させないよう、集計・出力ロジックはここに分離
（仕様30/31節の「地理シミュレーション固有処理は独立モジュールへ分離」
という指示に従った）。

- `load_static_users(path)` / `load_static_gpus(path)` — generatorが吐いた
  静的CSVを`{id: dict}`に読み込む。パス未指定なら`None`。
- `aggregate_users(schedulers, users_static)` — 全schedulerの`self.done`を
  `user_id`でグルーピングし、mean/p50/p95/p99・ボトルネック件数を算出。
  `users_static`があれば0件送信ユーザも`request_count=0`・null埋めで
  必ず出力される。
- `aggregate_gpus(schedulers, gpus_static)` — instance単位で同様の集計。
- `write_metadata(path, args, cluster, ...)` — 仕様27節の全項目に加え、
  `network_model`等の固定明記フィールド、TTFT/Prefill/Decode/Bottleneckの
  定義文、`git rev-parse HEAD`によるcommit hashを書き出す。

### 6. `serving/__main__.py`

- `--request-routing-policy`の`choices`に`'NEAREST'`を追加（ユーザが
  並行追加していた`PROMPT`/`QUEUE`/`HYBRID`はそのまま維持）。
- 新規CLIフラグ5つ: `--geographic-user-output` / `--geographic-gpu-output` /
  `--geographic-metadata-output`（書き込み先、全て未指定ならこの節の処理は
  完全にスキップされ既存コマンドに影響ゼロ）、`--geographic-users-csv` /
  `--geographic-gpus-csv`（generatorの静的CSVを読む任意入力）。
- **メインループ本体（`while True:`の中身）は1行も変更していない。**
  `current`/`finish`は既存の`schedule()`/`add_done()`呼び出し経由で
  そのまま計装コードに届くため、ループ側の変更は不要だった。
- ループを抜けた後、既存の`schedulers[i].save_output(...)`呼び出しの
  直後に、地理系フラグが1つでも指定されていれば`geo_report`の関数を
  呼んでCSV/JSON書き出しを行うブロックを追加。

### 7. `configs/cluster/geographic_20gpu.json`（新規）

`configs/cluster/single_node_multi_instance.json`のパターンを20instance分
そのまま複製。各instanceは`num_npus=1, tp_size=1, pp_size=1, pd_type=null`、
モデル・ハードウェアは既にプロファイル済みの`meta-llama/Llama-3.1-8B` /
`RTXPRO6000`を再利用（`config_builder.py`側は無変更で20instanceにスケール
することを事前に調査で確認済み）。
