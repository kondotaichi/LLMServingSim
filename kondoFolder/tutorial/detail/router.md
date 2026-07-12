# `Router` 詳細解説

作成日: 2026-07-11  
対象クラス: `serving/core/router.py:40` の `Router`  
対象コマンド:

```bash
python -m serving \
  --cluster-config 'configs/cluster/single_node_single_instance.json' \
  --block-size 16 \
  --dataset 'workloads/example_trace.jsonl' \
  --output 'outputs/example_single_run.csv' \
  --log-interval 1.0
```

`Router` は workload の JSONL を読み込み、各 Request の到着時刻を管理し、
実行先の instance を選んで `Scheduler` へ渡す。agentic workload では、
1つの session に含まれる LLM 呼び出しの依存関係と tool 実行時間も管理する。

---

## 1. このフェーズの目的（一言で）

**「workload に書かれた Request を到着時刻まで保留し、その時点の routing policy
に従って、適切な Scheduler の待ち行列へ投入すること」**。

Router は Scheduler と役割が異なる。

| コンポーネント | 決めること |
|---|---|
| Router | どの instance に Request を送るか |
| Scheduler | その instance で、いつ・何 token を Batch に入れるか |

Router が Request を Scheduler に渡しても、すぐ実行されるとは限らない。
その Request はまず `Scheduler.request` に入り、`max_num_seqs`、token budget、
KV cache 容量を満たしたときに初めて Batch 化される。

---

## 2. Router の構築

`serving/__main__.py:523-530` で、全 Scheduler の構築後に1つだけ作られる。

```python
router = Router(
    num_instances,
    schedulers,
    num_req,
    request_routing_policy,
    gpu_backbone_bandwidth_gbps=args.gpu_backbone_bandwidth_gbps,
    gpu_backbone_distance_m=args.gpu_backbone_distance_m,
)
```

Scheduler は instance ごとに1つだが、Router はクラスタ全体に1つである。
そのため、全 instance の待ち行列と inflight 状態を比較して振り分けられる。

### 2.1 Prefill と Decode の Scheduler を分ける

```python
self.prefill_schedulers = [s for s in schedulers if s.pd_type != "decode"]
self.decode_schedulers = [s for s in schedulers if s.pd_type == "decode"]
```

- colocated instance (`pd_type=None`) は最初の Request を受け取る prefill 側に入る
- prefill instance (`pd_type="prefill"`) も prefill 側に入る
- decode instance (`pd_type="decode"`) は prefill 完了後の引き渡し先になる

通常の colocated 構成では `decode_schedulers` は空で、1つの Scheduler が
prefill と decode の両方を処理する。

### 2.2 Router が持つ主な状態

```python
self._pending_requests = []
self._pending_idx = 0

self._deferred_sessions = {}
self._request_to_session = {}
self._next_request_id = 0
```

| 状態 | 内容 |
|---|---|
| `_pending_requests` | JSONL からロード済みだが、まだ Scheduler に渡していない Request |
| `_pending_idx` | pending 配列のうち、次に確認する位置 |
| `_deferred_sessions` | agentic session のまだ解放していない後続 sub-request |
| `_request_to_session` | Request ID から session と sub-request 番号への逆引き |
| `_next_request_id` | flat / agentic を通して一意な Request ID を発行する counter |

`_pending_requests` の消費済み要素を毎回削除せず、`_pending_idx` を前へ進める。
途中で agentic の後続 Request が解放された場合は、未消費部分へ到着時刻順に挿入する。

---

## 3. `load_requests()` — workload を pending queue に読み込む

`serving/__main__.py:536-538`:

```python
router.load_requests(
    dataset,
    enable_prefix_caching=any_prefix_caching,
    is_init=is_init,
)
```

Router はこの時点では instance を選ばない。JSONL を内部形式へ変換して
`_pending_requests` に置き、到着時刻順に sort するだけである。

```python
path = f'../{path}'

with open(path) as f:
    for line in f:
        row = json.loads(line)
        if 'sub_requests' in row:
            self._load_agentic_session(row, enable_prefix_caching)
        else:
            self._load_flat_request(row, enable_prefix_caching)

self._pending_requests.sort(key=lambda r: r['arrival_time_ns'])
```

`serving/__main__.py` が既に cwd を `astra-sim/` へ変更しているため、dataset path
には `../` を付けてリポジトリルートへ戻る。

`--num-requests` が正なら、JSONL の読み込み行数がその値に達したところで止める。
agentic workload では1行が1 session なので、sub-request 数ではなく session 行数に
対する上限になる。

---

## 4. Flat Request のロード

通常の workload は1行が1 Request である。

```json
{
  "input_toks": 128,
  "output_toks": 64,
  "arrival_time_ns": 1000000
}
```

`_load_flat_request()` は、これを次の内部形式へ変換する。

```python
req_data = {
    'index': req_id,
    'input_toks': 128,
    'output_toks': 128 + 64,
    'arrival_time_ns': 1000000,
}
```

### 4.1 `output_toks` が足し算される理由

dataset の `output_toks` は「生成する token 数」だが、`Request.output` は
「入力＋出力を合わせた終了 token 位置」として使われる。そのためロード時に
`input_toks + output_toks` へ変換する。

上の例なら Scheduler に渡る値は次の通り。

```text
original_input = 128
output         = 192
生成token数     = 192 - 128 = 64
```

### 4.2 Prefix caching 用の token ID

prefix caching が有効なら、次の値も保持する。

```python
req_data['input_hash_ids'] = row.get('input_tok_ids', [])
req_data['output_hash_ids'] = row.get('output_tok_ids', [])
```

Scheduler の `MemoryModel.prefix_match()` は token 数だけでは prefix の一致を
判定できないため、token ID 列を RadixCache の key として使う。

### 4.3 Optional metadata

workload の種類に応じて、以下も `req_data` に付く。

- `assigned_instance_id`: geographic workload で事前計算した最寄り GPU
- `geo`: 距離、帯域、uplink/downlink latency など
- `failover`: cold / local KV / KV migration の指定
- `reuse_prefix_toks`: 再利用可能な KV prefix token 数

通常の `example_trace.jsonl` では、これらは存在しない。

---

## 5. Agentic Session のロード

agentic workload は1行が1 Request ではなく、依存関係を持つ1 session である。

```json
{
  "session_id": "session_0",
  "arrival_time_ns": 1000,
  "sub_requests": [
    {"input_toks": 100, "output_toks": 20, "tool_duration_ns": 5000},
    {"input_toks": 150, "output_toks": 30, "tool_duration_ns": 2000},
    {"input_toks": 200, "output_toks": 40, "tool_duration_ns": 0}
  ]
}
```

この3つを最初から同時に pending queue へ入れてはいけない。2番目は1番目の
LLM 呼び出しと tool 実行が終わるまで開始できないためである。

`_load_agentic_session()` は次のように分ける。

```text
_pending_requests:
  sub_request[0] のみ

_deferred_sessions["session_0"]:
  sub_requests 全体
  next_index = 1
  id_base = 最初に確保したRequest ID
```

session 内の全 sub-request 分の連続 ID を先に予約する。

```python
base_id = self._next_request_id
self._next_request_id += len(sub_reqs)
```

このため、他の flat Request と混在しても ID は重複しない。

---

## 6. リアルタイムルーティング

メインループは ASTRA-Sim から現在 cycle を受け取るたび、Scheduler を呼ぶ前に
`route_arrived_requests(current)` を実行する。

```python
while self._pending_idx < len(self._pending_requests):
    req_data = self._pending_requests[self._pending_idx]
    if req_data['arrival_time_ns'] > current_time_ns:
        break
    ...
```

pending queue は arrival 順なので、先頭の未消費 Request が未来ならそこで止めてよい。
到着済み Request は routing policy で instance を選び、対応 Scheduler の
`add_request()` へ渡す。

```text
JSONL
  │ load_requests()
  ▼
Router._pending_requests（arrival順）
  │ current >= arrival_time_ns
  │ route_arrived_requests()
  ▼
選択された Scheduler.request（arrival順）
  │ Scheduler.schedule()
  ▼
Batch / ASTRA-Sim
```

重要なのは、**起動時に全 Request の行き先を決めない**こと。`LOAD` や `QUEUE`
は Request が実際に到着した時点の Scheduler 状態を見られる。

---

## 7. Routing policy の選択

Router のコンストラクタは CLI の policy 名に応じ、`self._select_instance` を
対応する関数へ差し替える。

| Policy | 選択方法 |
|---|---|
| `RR` | instance を順番に巡回 |
| `RAND` | seed 付き乱数で選択 |
| `LOAD` | queue と running を capacity で正規化して最小を選択 |
| `PROMPT` | prompt 長に合う token capacity の instance を優先 |
| `QUEUE` | queue pressure が最小の instance を選択 |
| `HYBRID` | prompt 長との適合度と queue pressure の和を最小化 |
| `CUSTOM` | 未実装。呼ぶと `NotImplementedError` |
| `NEAREST` | workload の `assigned_instance_id` を使用 |
| `NEAREST_KV` | NEAREST に local KV 再利用を追加 |
| `NEAREST_REJECT` | 最寄りが満杯なら2番目へ user-side resend |
| `NEAREST_MIGRATE` | 最寄りが満杯なら GPU backbone 経由で2番目へ転送 |
| `NEAREST_MIGRATE_KV` | GPU backbone 転送時に KV prefix も移送 |

### 7.1 `RR`

prefill 用と decode 用に別々の counter を持つ。

```text
prefill instance: 0 → 1 → 2 → 0 → ...
decode instance : 3 → 4 → 3 → ...
```

PD 分離構成でも、prefill の巡回回数が decode の巡回順へ影響しない。

### 7.2 `LOAD`

```python
waiting = len(sched.request)
running = sum(len(b.requests) for b in sched.inflight)
raw_score = waiting * 4 + running
score = raw_score / sched.max_num_seqs
```

待機中 Request に4倍の重みを付け、実行中 Request を加えた値を instance capacity
で割る。異なる `max_num_seqs` を持つ heterogeneous instance 同士も比較できる。

同点時は毎回 instance 0 に偏らないよう、前回選択位置の次から巡回して比較する。

### 7.3 `QUEUE`

`waiting * 4 + running` が最小の instance を選ぶ。`LOAD` と違い、
`max_num_seqs` による正規化はしない。

### 7.4 `PROMPT`

prompt 全体が `max_num_batched_tokens` に収まる instance の中から、最も小さい
capacity を持つものを優先する。収まる instance が無ければ、最大 capacity を選ぶ。

これは短い prompt を小さい token-budget instance、長い prompt を大きい
instance へ寄せるための policy である。

### 7.5 `HYBRID`

prompt length と token capacity の差を正規化した `prompt_score` と、クラスタ内の
最大 queue に対して正規化した `queue_score` を足す。

```python
score = prompt_score + queue_score
```

prompt に適した instance でも混雑していれば避け、空いていても prompt に対して
capacity が不適切なら不利になる。

### 7.6 `NEAREST`

geographic workload generator が事前に書いた `assigned_instance_id` と一致する
Scheduler を選ぶ。Router 自身は座標から距離を再計算しない。

`assigned_instance_id` が無い、または有効な Scheduler ID と一致しない場合は
明示的な `RuntimeError` になる。

---

## 8. Scheduler への投入

選択結果は `prefill_schedulers` 配列上の index なので、Scheduler を取り出す。

```python
instance_id = self._select_instance(
    self.prefill_schedulers,
    "prefill",
    req_data,
)
sched = self.prefill_schedulers[instance_id]
```

prefix caching の有無に応じて token ID を含む引数を作り、`add_request()` を呼ぶ。

```python
sched.add_request([
    req_data['index'],
    sched.model,
    req_data['input_toks'],
    req_data['output_toks'],
    req_data['arrival_time_ns'],
    sched.instance_id,
    req_data.get('input_hash_ids', []),
    req_data.get('output_hash_ids', []),
], is_init=self._is_init, geo=geo, failover=failover)
```

Scheduler 側はこの配列から `Request` を作り、`bisect.insort()` で
`Scheduler.request` に arrival/id 順で挿入する。

投入後に `_pending_idx` と routed 数を1増やす。`route_arrived_requests()` の
戻り値は、その呼び出しで新たに Scheduler へ渡した Request 数である。

---

## 9. Agentic Session の後続 Request 解放

Request が完了すると、メインループは colocated/decode instance に対して
次を呼ぶ。

```python
router.notify_request_completed(req.id, current)
```

flat Request なら `_request_to_session` に登録がないので何もしない。
agentic sub-request なら session を引き、完了した sub-request の
`tool_duration_ns` を加える。

```python
release_time_ns = completion_time_ns + tool_duration_ns
```

次の sub-request をこの時刻で作り、pending queue の未消費部分へ二分探索で挿入する。

### 9.1 時間軸の具体例

```text
session arrival                 = 1,000 ns
sub_request[0] completion       = 20,000 ns
sub_request[0].tool_duration_ns = 5,000 ns

sub_request[1] release/arrival  = 25,000 ns
```

tool 実行中の5,000 ns は Scheduler の queue に入らない。Router の deferred/pending
状態で待ち、時刻25,000 ns に達して初めて instance が選ばれる。

最後の sub-request が完了すると `_deferred_sessions` から session を削除する。

### 9.2 シミュレーションの早期終了を防ぐ

tool 実行中は全 Scheduler が空になることがある。しかし
`router.has_deferred_sessions()` が `True` の間、メインループは simulation 完了と
判定しない。

全 instance が idle で次の Request が未来なら、`get_next_pending_arrival()` で
次の arrival を取り、`current` をそこまで進める。これにより tool call 中に
空の iteration を回し続ける busy loop を避ける。

---

## 10. Prefill / Decode 分離構成での引き渡し

prefill instance の `Scheduler.add_done()` は、prefill が完了した Request を
`finished_reqs` として返す。メインループはそれを Router へ渡す。

```python
router.transfer_prefill_request(finished_reqs)
```

Router は decode 用 policy で `decode_schedulers` から行き先を選び、
`add_decode(req)` を呼ぶ。

```python
for req in requests:
    req_data = {'input_toks': req.original_input}
    instance_id = self._select_instance(
        self.decode_schedulers,
        "decode",
        req_data,
    )
    self.decode_schedulers[instance_id].add_decode(req)
```

新しい Request を作り直すのではなく、prefill 済みの同じ Request オブジェクトを
decode Scheduler へ移す。`add_decode()` は KV cache を decode instance 側へ
確保し、prefix caching 有効時は prefix match と cache 更新も行う。

---

## 11. Geographic redirect policy

> `NEAREST_KV` / `NEAREST_REJECT` / `NEAREST_MIGRATE` /
> `NEAREST_MIGRATE_KV`について、追加したgenerator field、capacity判定、遅延式、
> KV handoff、fair comparison、CSV列まで含む詳細は
> [`geographic_capacity_routing.md`](./geographic_capacity_routing.md)を参照。

以下は通常の `RR` / `LOAD` では通らない、geographic workload 専用の分岐である。

### 11.1 Capacity 判定

```python
running_reqs = sum(len(b.requests) for b in sched.inflight)
return running_reqs < sched.max_num_seqs
```

Scheduler の admission と同じく inflight Request 数を見る。ただし待ち行列の長さは
含めない。最寄り instance に running slot が無いときだけ redirect する。

### 11.2 `NEAREST_REJECT`

最寄り GPU が満杯なら、user が capacity rejection を受け取って2番目に近い GPU へ
request を再送するモデルである。

```text
User → nearest GPU → rejection → User → second-nearest GPU
```

最寄り GPU との往復 propagation を `reject_penalty_ns` として加え、2番目の GPU
までの uplink を再計算する。`arrival_time_ns` を未来へ変更し、pending queue に
挿し直すため、同じ iteration では Scheduler に投入されない。

redirect は1 Request につき最大1回で、2番目も満杯だった場合に3番目へ連鎖させない。

### 11.3 `NEAREST_MIGRATE`

user が再送するのではなく、最寄り GPU が operator の GPU backbone を使って
2番目の GPU へ request を転送する。

```text
User → nearest GPU ──GPU backbone──> second-nearest GPU → User
```

元の user→nearest uplink は既に支払い済みなので、新たに加える待ち時間は
GPU backbone の距離 latency と request serialization である。

この policy には `--gpu-backbone-bandwidth-gbps` と
`--gpu-backbone-distance-m` が必須。転送後の最初の token は2番目の GPU から
user へ返るため、downlink 距離も2番目の GPU 基準へ更新する。

### 11.4 `NEAREST_MIGRATE_KV`

request 転送に加え、`reuse_prefix_toks` で指定した KV prefix も宛先へ移す。
Router は通常の failover と同じ metadata を付け、後段の
`_apply_kv_migration_if_needed()` で宛先 Scheduler の prefix cache を seed する。

KV migration latency は次の和である。

```text
distance latency
+ migrated KV bytes / migration bandwidth
```

KV の移送には prefix caching が必須。無効なら `RuntimeError` になる。

---

## 12. Static failover / KV migration

workload 行に `failover_mode` がある場合は、routing policy とは別に指定された
target instance を優先する。

| Mode | 挙動 |
|---|---|
| `cold` | target instance へ送るが、KV は再利用しない |
| `local_kv` | target に既にある prefix を latency 0 で利用する |
| `migrate_kv` | source から target へ prefix を移し、転送 latency を加える |

`local_kv` / `migrate_kv` は `MemoryModel.seed_migrated_prefix()` を呼び、宛先の
RadixCache に prefix token ID を挿入する。実テンソルを持つ simulator ではないため、
「KV migration」は cache metadata の追加と転送バイト・時間の課金として表現される。

`migrate_kv` では migration 完了分だけ `arrival_time_ns` を後ろへずらす。
communication latency、migration bytes、distance/serialization の内訳は
Request に引き継がれ、最終 output CSV に記録される。

---

## 13. 今回の single-instance 構成で実際に起きること

`single_node_single_instance.json` では Scheduler が1つしかないため、`RR`、
`RAND`、`LOAD` のどれを選んでも行き先は instance 0 になる。

それでも Router は必要である。

1. workload を読み、`output_toks` を内部表現へ変換する
2. arrival time まで Request を pending に保持する
3. 到着した Request を instance 0 の Scheduler へ渡す
4. agentic session なら後続 sub-request の依存関係を管理する
5. simulation の終了条件に pending/deferred 状態を提供する

今回の最小構成では「負荷分散器」よりも「arrival event と依存関係の管理器」としての
役割が中心になる。

---

## 14. メインループとの接続

Router を中心にした一連の流れは次の通り。

```text
起動時
  Router.load_requests(dataset)
    ├─ flat request      → pending
    └─ agentic session   → 先頭だけpending、残りdeferred

各iteration
  ASTRA-Simから current cycle を取得
    │
    ▼
  Router.route_arrived_requests(current)
    ├─ 未到着 → pendingに残す
    └─ 到着済 → policyでinstance選択 → Scheduler.add_request()
    │
    ▼
  Scheduler.add_done()
    ├─ 通常完了 → Router.notify_request_completed()
    │               └─ agenticの次sub-requestを解放
    └─ PD prefill完了 → Router.transfer_prefill_request()
                         └─ decode Schedulerへ移す
```

Router は ASTRA-Sim と直接通信しない。Router が扱うのは Python 側の Request と
時刻・instance 割り当てであり、Batch の実行時間は Scheduler、trace generator、
ASTRA-Sim の後段が扱う。

---

## 15. まとめ

このフェーズを一言でまとめると、

> **JSONL の Request を到着時刻と依存関係に従って解放し、その瞬間の routing
> policy で実行先 Scheduler を選ぶ、クラスタ全体の受付・振り分け処理**

特に重要なのは次の4点。

1. `load_requests()` は Request を直ちに割り当てず、arrival 順の pending queue に置く
2. `route_arrived_requests(current)` が実際の到着時点の負荷を見て instance を選ぶ
3. agentic session は先頭だけを解放し、後続は「前段完了＋tool duration」まで保留する
4. PD 分離構成では prefill 完了後の同じ Request を decode Scheduler へ引き渡す

通常 workload では arrival と load balancing、agentic workload では依存 chain、
geographic workload では距離・capacity redirect・KV migration まで、すべて
「Scheduler へ入る前の Request の動き」を Router が担当する。

## 関連ファイル

- `serving/core/router.py` — 本解説の対象
- `serving/core/scheduler.py` — Router が選んだ Request の Batch 化
- `serving/core/request.py` — Request の内部状態と geographic/failover metadata
- `serving/core/memory_model.py` — migrated/local KV prefix の seed
- `serving/__main__.py` — Router の構築、到着 routing、完了通知、終了条件
- `workloads/generators/` — flat/agentic/geographic workload の生成
- `kondoFolder/tutorial/simulation_callgraph.md` — このフェーズを含む全体コールグラフ
- `kondoFolder/tutorial/detail/scheduler.md` — Router の次段にある Scheduler の詳細解説
