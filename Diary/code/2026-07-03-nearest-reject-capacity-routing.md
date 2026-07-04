# コード変更まとめ: `NEAREST_REJECT` キャパシティ考慮ルーティング

日付: 2026-07-03
ブランチ: `experiment/sim-hack`
関連: `Diary/output/2026-07-03-geographic-rtt-communication-breakdown.md`
（地理分散ワークロードでのTTFT分解実験。「queueingで待たせず、キャパ次第で
別サーバへ弾く設計にしたらRTTがどう効くか見たい」という発展形として実装）

## 何を追加したか(要約)

既存の`NEAREST`ルーティングポリシー(地理的に最も近いGPUへ常に送る)に対して、
**`NEAREST_REJECT`という新しいポリシーを追加**した。挙動:

1. まず最も近いGPUを見る(ここまでは`NEAREST`と同じ)。
2. そのGPUに空き実行スロットがなければ(`running_reqs >= max_num_seqs`)、
   「容量チェックの往復遅延」だけを課してreject扱いにし、**2番目に近いGPUへ
   確定的にリダイレクト**する(キューに並ばせない)。
3. 2番目のGPUは容量チェックをせず無条件に受理する(2段階目で確定、
   3番目以降へのカスケードはしない)。

既存コードの共通処理(スケジューラのバッチ処理・トレース生成・ASTRA-Sim連携)は
**一切変更していない**。全ての追加はgeographicワークロード生成とルーティング層
(`router.py`)に閉じている。

## 変更したファイル一覧

| ファイル | 変更内容 | diff規模 |
| --- | --- | ---: |
| `workloads/generators/geographic.py` | 各ユーザの「2番目に近いGPU」を計算・出力するよう拡張 | +41/-19行 |
| `serving/core/router.py` | `NEAREST_REJECT`ポリシー本体(容量判定・reject・リダイレクト・再挿入) | +109行 |
| `serving/core/request.py` | `Request`に`nearest_gpu_id`/`rerouted`/`reject_penalty_ns`属性を追加 | +5行 |
| `serving/core/scheduler.py` | 出力CSVに上記3列を追加 | +7/-1行 |
| `serving/__main__.py` | `--request-routing-policy`のchoicesと説明に`NEAREST_REJECT`を追加 | +8/-4行 |

---

## 1. `workloads/generators/geographic.py`

### 変更前
`_nearest_gpu(user_xy, gpus)`が「最も近いGPUのid・距離」のみを返していた。

### 変更後
`_two_nearest_gpus(user_xy, gpus)`に置き換え、`(nearest_id, nearest_dist,
second_nearest_id, second_nearest_dist)`の4値を返すようにした。

```python
def _two_nearest_gpus(user_xy, gpus):
    ux, uy = user_xy
    ranked = sorted(
        (math.sqrt((ux - gx) ** 2 + (uy - gy) ** 2), gpu_id)
        for gpu_id, (gx, gy) in enumerate(gpus)
    )
    if len(ranked) < 2:
        raise ValueError(...)  # GPUが1台しかない場合は明確に落とす
    (dist0, id0), (dist1, id1) = ranked[0], ranked[1]
    return id0, dist0, id1, dist1
```

`sorted((distance, gpu_id))`によるタイブレーク(同距離なら小さいgpu_idが勝つ)は、
旧`_nearest_gpu`の「厳密な`<`のみで更新」というルールと同じ結果になる
(distance, gpu_id)のタプル比較なので、gpu_id昇順のまま安定ソートされる。

出力への反映:
- workload JSONLの各リクエスト行に`second_nearest_gpu_id` /
  `second_nearest_distance_m`を追加(`distance_latency_ns_per_meter`は元々
  出力されていたのでそのまま流用)。
- users CSV(`--users-output`)にも同じ2列を追加。

**設計判断**: 2番目に近いGPUは、ユーザ・GPU座標が生成時に固定される以上
静的に決まる値なので、既存の「最近傍を1回だけ計算してworkloadに埋め込む」
という設計方針をそのまま踏襲した。ルーティング側(router.py)で毎回距離計算
をやり直す必要がない。

---

## 2. `serving/core/router.py`(本体)

### 2.1 `_GEO_FIELDS`にフィールド追加

```python
_GEO_FIELDS = (
    ...,
    'second_nearest_gpu_id', 'second_nearest_distance_m', 'distance_latency_ns_per_meter',
    ...,
)
```

これだけで`_extract_geo_fields(row)`が自動的にworkload行からこれらの値を
拾い、`req_data['geo']`に載せてくれる(既存の抽出ロジックは無変更)。

### 2.2 容量判定ヘルパー

```python
@staticmethod
def _has_capacity(sched):
    running_reqs = sum(len(b.requests) for b in sched.inflight)
    return running_reqs < sched.max_num_seqs
```

`scheduler.py`が各イテレーションで実際に使っている admission 判定
(`available_slots = max_num_seqs - running_reqs`、`scheduler.py:84-85`)と
同じ量を見ている。つまり「vLLM流の実行中スロット数が埋まっているか」を、
スケジューラ本体のロジックを一切変更せずルーティング層から覗き見て判定する
だけで、新しい容量モデルを持ち込んではいない。

### 2.3 reject + リダイレクト本体: `_maybe_reject_and_redirect`

```python
def _maybe_reject_and_redirect(self, req_data, current_time_ns):
    geo = req_data.get('geo')
    ...
    geo.setdefault('nearest_gpu_id', geo.get('gpu_id'))

    sched = self._find_scheduler(self.prefill_schedulers, req_data['assigned_instance_id'])
    if self._has_capacity(sched):
        return False  # 最近傍で受理、ペナルティなし

    # --- reject: 最近傍への容量チェック往復(伝搬遅延のみ) ---
    reject_penalty_ns = round(2 * nearest_distance_m * per_meter_ns)

    # --- 2番目に近いGPUへの本番アップリンク/ダウンリンク(通常通りフル) ---
    uplink_latency_ns = uplink_distance_ns + uplink_serialization_ns
    downlink_latency_ns = downlink_distance_ns + downlink_serialization_ns

    geo.update({
        'gpu_id': second_gpu_id, 'distance_m': second_distance_m,
        'uplink_*': ..., 'downlink_*': ...,
        'communication_latency_ns': reject_penalty_ns + uplink_latency_ns + downlink_latency_ns,
        'rerouted': 1, 'reject_penalty_ns': reject_penalty_ns,
    })
    req_data['assigned_instance_id'] = second_gpu_id
    req_data['arrival_time_ns'] = current_time_ns + reject_penalty_ns + uplink_latency_ns
    req_data['_reject_resolved'] = True
    return True
```

**モデル化の設計判断(要注意ポイント)**:
- reject判定そのものは「容量チェックの軽量プローブ」とみなし、
  **伝搬遅延のみの往復**(`2 × distance × distance_latency_ns_per_meter`)を
  課す。ペイロードのシリアライズ時間は含めない — 容量が埋まっているかどうかの
  判定に、プロンプト本文の送信は不要という想定。
- 実際に処理する2番目のGPUへは、通常のリクエストと同じ**フルのuplink+downlink**
  (距離由来の伝搬遅延 + ペイロードのシリアライズ時間)を課す。
- `geo`辞書を**その場でmutateする**ことで、後続の`Request.__init__`が読む
  `communication_latency_ns`等が「実際に使われた経路」を正しく反映するように
  した。`distance_m`/`gpu_id`は「最終的に処理したGPU」の値に上書きされるが、
  `nearest_gpu_id`に元の最近傍IDを退避してあるので、reject有無・元の割当は
  出力CSVから常に追跡できる。

### 2.4 `route_arrived_requests`への組み込み

```python
if self.routing_policy == "NEAREST_REJECT" and not req_data.get('_reject_resolved', False):
    if self._maybe_reject_and_redirect(req_data, current_time_ns):
        self._pending_requests.pop(self._pending_idx)
        self._insert_pending_sorted(req_data)
        continue
```

reject時は`arrival_time_ns`を「reject往復+2番目のGPUへのアップリンクが
完了する時刻」まで遅らせ、既存の`_insert_pending_sorted`(agentic session の
遅延sub-request挿入で元々使っていたヘルパー、**無変更で再利用**)でpending
キューに戻す。次回`route_arrived_requests`が呼ばれたとき、新しい到着時刻に
達していれば`_reject_resolved=True`なので今度は素通りで2番目のGPUへ実際に
`add_request`される。

**なぜこの設計にしたか**: 単純に「その場で2番目のGPUへadd_requestする」
のではなく、いったんpendingキューに戻して時間を進めてから改めて処理する
ことで、reject+リダイレクトにかかる往復時間ぶん、実際にリクエストが
GPU側のキューに現れる時刻が正しく遅れる(＝その分だけ他の後続リクエストとの
競合状況も現実的にシミュレートされる)。メインループの「pendingが残っていれば
次の到着時刻まで時間を進める」既存ロジック(`__main__.py`)もagentic session
と共通のフックを見ているため、ここも無変更で動く。

### 2.5 ポリシー一覧への追加

```python
elif self.routing_policy == "NEAREST_REJECT":
    self._select_instance = self._nearest_select
```

`_select_instance`自体は既存の`_nearest_select`をそのまま使う
(reject/リダイレクトの判断は`_select_instance`が呼ばれる**前**に
`route_arrived_requests`側で済ませているため、`_select_instance`に届く
頃には`req_data['assigned_instance_id']`が既に最終決定済みの値になっている)。

---

## 3. `serving/core/request.py`

既存のgeographic関連属性ブロックの末尾に3行追加しただけ:

```python
self.nearest_gpu_id = geo.get('nearest_gpu_id', geo.get('gpu_id'))
self.rerouted = geo.get('rerouted', 0)
self.reject_penalty_ns = geo.get('reject_penalty_ns', 0)
```

`NEAREST_REJECT`以外のポリシー・非geographicワークロードでは`geo`にこれらの
キーが存在しないため、それぞれ`gpu_id`(nearest_gpu_id)/`0`/`0`にフォール
バックする。既存の呼び出し側・既存ワークロードへの影響はゼロ。

---

## 4. `serving/core/scheduler.py`(`save_output`)

既存の35列超のgeographic出力の末尾に3列追加:

```python
'nearest_gpu_id', 'rerouted', 'reject_penalty_ns'
```

他ポリシーでは`rerouted=0`/`reject_penalty_ns=0`/`nearest_gpu_id`=元の
`gpu_id`と同じ値になり、実質無害な列として出力される。

---

## 5. `serving/__main__.py`

`--request-routing-policy`の`choices`に`'NEAREST_REJECT'`を追加し、
helpテキストに説明を追記しただけ。

---

## 動作確認

`workloads/example_trace.jsonl`ベースの10リクエスト・2GPU構成、
`--max-num-seqs 1`(わざと1スロットに絞って即reject条件を作る)で
スモークテスト済み。出力CSVで以下を確認:

- `nearest_gpu_id`と実際に処理された`gpu_id`(=`instance id`)が異なる行が
  複数存在し(`rerouted=1`)、`reject_penalty_ns`が距離に応じた妥当な値
  (数千ns、往復伝搬遅延のみ)になっていること。
- reject後の`arrival_time_ns`が正しく`reject_penalty_ns + 2番目のGPUへの
  uplink_latency_ns`だけ遅れていること(単体テストで直接
  `Router._maybe_reject_and_redirect`を叩いて検算済み)。
- エラー・クラッシュなしで最後まで完走し、`print_result`のTTFT/E2E-TTFT等の
  集計も正常に出力されること。

## 未実施

- 100リクエスト規模での`NEAREST` vs `NEAREST_REJECT`本番比較実行
  (コマンドは`Diary/output/`側の会話に用意済み、次のタスク)。
- `geo_report.py`(per-user/per-GPU集計)への`rerouted`集計列の追加は
  見送った(生の per-request CSV で十分分析できるため、スコープを絞った)。
