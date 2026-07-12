# `Scheduler` 詳細解説

作成日: 2026-07-11  
対象クラス: `serving/core/scheduler.py:18` の `Scheduler`  
対象コマンド:

```bash
python -m serving \
  --cluster-config 'configs/cluster/single_node_single_instance.json' \
  --block-size 16 \
  --dataset 'workloads/example_trace.jsonl' \
  --output 'outputs/example_single_run.csv' \
  --log-interval 1.0
```

`build_cluster_config()` がクラスタ設定を解決した後、`serving/__main__.py` は
instance ごとに `Scheduler` を1つ作る。以後、リクエストは Router から
Scheduler の待ち行列へ入り、メインループが `schedule()` を呼ぶたびに、
今回 ASTRA-Sim へ渡す `Batch` が1つ組み立てられる。

---

## 1. このフェーズの目的（一言で）

**「到着済みリクエストの中から、同時実行本数・トークン数・KV キャッシュ容量の
3つの制約をすべて満たす1回分のバッチを作ること」**。

Scheduler は単に待ち行列の先頭からリクエストを取り出すだけではない。

1. 今の時刻までに到着しているか
2. Pipeline Parallelism 上、次のバッチを流せるか
3. `max_num_seqs` の同時実行本数に空きがあるか
4. `max_num_batched_tokens` のトークン予算に収まるか
5. 追加する KV キャッシュが NPU メモリに収まるか
6. 収まらなければ、どの decode リクエストまたは prefix cache を退避するか

を順に判断する。

条件を満たすリクエストがなければ `None`、あれば `Batch` を返す。`Batch` が
返ったときだけ、後段の `trace_generator.generate_trace()` が実行トレースを作る。

---

## 2. Scheduler は instance ごとに1つ作られる

構築箇所は `serving/__main__.py:494-521`。

```python
schedulers = []
for instance_id, instance in enumerate(instances):
    ...
    inst_cfg = instance_runtime_configs[instance_id]

    schedulers.append(Scheduler(
        instance["model_name"], instance["node_id"], instance_id,
        inst_cfg["max_num_seqs"], inst_cfg["max_num_batched_tokens"],
        instance["num_npus"], instance["tp_size"], instance["pp_size"],
        instance["npu_mem"]["mem_size"], cpu_mem_size[instance["node_id"]],
        inst2npu_mapping[instance_id], instance["pd_type"],
        inst_cfg["fp"], inst_cfg["block_size"], num_req,
        inst_cfg["prioritize_prefill"], inst_cfg["enable_prefix_caching"],
        enable_prefix_sharing, prefix_pool, pool_device,
        inst_cfg["enable_chunked_prefill"],
        inst_cfg["long_prefill_token_threshold"],
        cxl_mem,
        ep_size=instance.get("ep_total", 1),
        kv_cache_dtype=inst_cfg["kv_cache_dtype"],
    ))
```

今回の `single_node_single_instance.json` は instance が1つなので、作られる
Scheduler も1つだけである。複数 instance のクラスタでは、各 Scheduler が
独立した待ち行列・inflight バッチ・メモリ使用量を持ち、Router がどの
Scheduler にリクエストを渡すか決める。

### 主な引数の役割

| 引数 | 意味 |
|---|---|
| `max_num_seqs` | inflight を含め、同時に実行中として扱えるリクエスト本数の上限 |
| `max_num_batched_tokens` | 1回のスケジューリングで処理できるトークン数の上限 |
| `pp_size` | 同時に保持できる inflight バッチ数の上限 |
| `block_size` | KV キャッシュを確保するトークンブロックの大きさ |
| `enable_chunked_prefill` | 長い prompt を複数回に分割して処理するか |
| `long_prefill_token_threshold` | chunked prefill で1リクエストに割り当てる1回分の上限 |
| `enable_prefix_caching` | RadixCache による prefix 再利用を有効にするか |
| `prefix_storage` | prefix cache の二次保存先（CPU/CXL、未指定なら無し） |
| `start_npu` | この instance が使用する先頭 NPU のグローバル番号 |
| `pd_type` | colocated / prefill / decode の役割 |

---

## 3. コンストラクタで作られる状態

`Scheduler.__init__()` は設定値を保存し、3つのリクエスト状態リストと
`MemoryModel` を初期化する。

```python
self.request = []
self.inflight = []
self.done = []
self.batch_ids = -1

self.memory = MemoryModel(...)
```

### 3.1 3つのリスト

```text
request  ──schedule()──>  inflight  ──add_done()──>  request または done
 待機中                     ASTRA-Sim実行中             未完了なら再待機 / 完了
```

- `request`: Scheduler に到着した待機中リクエスト。`arrival`, `id` 順に並ぶ
- `inflight`: Batch 化され、ASTRA-Sim の完了を待っているリクエスト
- `done`: 必要な output token をすべて生成し終えたリクエスト

Router は `Scheduler.add_request()` を呼び、`bisect.insort()` で `request` に
挿入する。agentic session の次サブリクエストのように、実行途中で新しい
リクエストが追加されても到着時刻順が崩れない。

### 3.2 `max_num_batched_tokens` の補正

```python
self.max_num_batched_tokens = min(
    max_num_batched_tokens,
    self.config['max_position_embeddings'],
)
```

CLI の指定値をそのまま使うのではなく、モデルの最大コンテキスト長を上限にする。
例えば CLI でそれ以上を指定しても、Scheduler 内部では
`max_position_embeddings` までに縮められる。

### 3.3 `MemoryModel` の初期化

`MemoryModel` は最初にモデル重みの1 GPU あたりのサイズを計算し、NPU 使用量を
その重みサイズで初期化する。

```python
self.weight = self.get_weight()
self.npu_used = self.weight
self.cpu_used = 0

if self.weight > self.npu_mem:
    raise RuntimeError(...)
```

つまり、Scheduler が使える NPU メモリはクラスタ設定の `npu_mem.mem_size`
全部ではなく、**モデル重みを引いた残り**である。重みだけで容量を超える場合は、
シミュレーションループに入る前にエラーになる。

prefix caching が有効なら、この残容量を capacity とする NPU 上の
`RadixCache` もここで作られる。KV 要素のバイト数は通常はモデル精度、
`--kv-cache-dtype fp8` のときは1 byte で計算される。

---

## 4. `schedule()` は2つの実装への入口

`serving/core/scheduler.py:55-59`:

```python
def schedule(self, current, sys, batch_id=-1):
    if self.enable_prefix_caching:
        return self.schedule_with_prefix(current, sys, batch_id)
    else:
        return self.schedule_base(current, sys, batch_id)
```

| 設定 | 呼ばれる関数 | KV 管理方式 |
|---|---|---|
| prefix caching 有効（既定） | `schedule_with_prefix()` | prefix match、RadixCache の lock / eviction、必要なら CPU/CXL から load |
| prefix caching 無効 | `schedule_base()` | リクエスト単位で KV を NPU↔CPU に退避・再読込 |

両者のバッチ選択の骨格は同じで、prefix cache の照合・ロック・退避処理だけが
大きく異なる。

---

## 5. 最初の入口判定

以下は `schedule_base()` と `schedule_with_prefix()` に共通する。

### 5.1 新規バッチを作るのは先頭 NPU だけ

```python
if sys == self.start_npu:
    # 新規バッチを作る
else:
    # batch_id で既存 inflight バッチを探す
```

1つの instance が複数 NPU を使っていても、同じバッチを各 NPU が勝手に
作ってはいけない。`start_npu` だけが新規 `Batch` を作り、他の NPU は
`batch_id` が一致する既存 Batch を受け取る。

`Batch.fired` には既にそのバッチを発行した NPU 番号が記録され、同じ NPU へ
二重発行するのを防ぐ。

### 5.2 未到着なら何もしない

```python
if len(self.request) != 0 and self.request[0].arrival > current:
    return None
```

`request` は到着時刻順なので、先頭が未来なら他のリクエストもまだ到着していない。

### 5.3 Pipeline Parallelism の inflight 上限

```python
if len(self.inflight) >= self.pp_size:
    return None
```

`pp_size=1` の今回の構成では、前の Batch が終わるまで次の Batch は作れない。
`pp_size>1` なら、その数まで Batch を pipeline 上で同時に inflight にできる。

---

## 6. `max_num_seqs` は「新規バッチの本数」ではなく「実行中総数」の上限

```python
batch_req = [req for req in self.request if req.arrival <= current]

running_reqs = sum(len(b.requests) for b in self.inflight)
available_slots = max(0, int(self.max_num_seqs) - running_reqs)
batch_len = min(len(batch_req), available_slots)
```

ここが `max_num_seqs` の意味を決める重要箇所である。

例えば `max_num_seqs=128` で、既存の inflight Batch に100リクエストが入って
いれば、新しい Batch に入れられるのは最大28リクエストである。各 Batch に
128本ずつ入れられるわけではない。

```text
max_num_seqs = 128
inflight 内の request = 100
今回使える slot = 128 - 100 = 28
```

空きが0なら、到着済みリクエストが存在していても `None` を返す。

---

## 7. Prefill と Decode の並べ方

### 7.1 chunked prefill が有効な場合（既定）

```python
prefills = [req for req in batch_req if req.is_prefill()]
decodes = [req for req in batch_req if not req.is_prefill()]
batch_req = decodes + prefills
```

decode を先、prefill を後ろに並べる。decode は各リクエスト1 token で進むため、
先に token budget を確保して inter-token latency の悪化を抑える。その残りを
prefill chunk に配る。

### 7.2 chunked prefill が無効で `prioritize_prefill=True` の場合

到着済み候補に prefill が1本でもあれば、候補を prefill だけに絞る。
chunked prefill 有効時は decode-first の規則が使われるため、この分岐には入らない。

### 7.3 Prefill / Decode の判定

```python
def is_prefill(self):
    return self.num_computed_tokens < self.original_input
```

`Request` は boolean の phase を別に持たず、「入力トークンをまだ計算し終えて
いないか」で判定する。`num_computed_tokens` が `original_input` に達した後は、
同じ Request が decode として1 token ずつ再スケジュールされる。

---

## 8. STEP 1: token budget の割り当て

Scheduler は `scheduled_tokens` という辞書を作る。

```python
scheduled_tokens = {
    request_id: 今回このRequestで計算するtoken数,
}
```

### 8.1 chunked prefill 有効時

まず decode に1 token ずつ割り当て、残った予算を prefill に使う。

```python
token_budget = self.max_num_batched_tokens

# Decode
scheduled_tokens[req.id] = 1
token_budget -= 1

# Prefill
remaining = req.original_input - req.num_computed_tokens
remaining = min(remaining, long_prefill_token_threshold)  # 0なら制限なし
chunk = min(remaining, token_budget)
scheduled_tokens[req.id] = chunk
token_budget -= chunk
```

例として `max_num_batched_tokens=2048`、decode が3本、未計算 prompt が
3000 token の prefill が1本なら、decode に3 token を確保し、prefill の
今回の chunk は最大2045 token になる。

```text
2048 - 3 decode tokens = 2045 prefill tokens
```

`long_prefill_token_threshold=512` なら、同じ状況でも prefill には512 token
だけ割り当て、残り予算を後続 prefill に回せる。

### 8.2 chunked prefill 無効時

prefill は入力全体、decode は1 token として合計する。上限を超える間、
候補リストの末尾リクエストを丸ごと外す。

このため、入力全体が `max_num_batched_tokens` より長い prefill は1本も Batch に
入れられず、`schedule_base()` は警告して `None` を返す。長い prompt を扱う
場合に chunked prefill が必要になる理由がここにある。

### 8.3 prefix caching 有効時の違い

新しい prefill の最初のスケジュール時に `memory.prefix_match(req)` を呼ぶ。
cache hit 分は `req.num_computed_tokens` に反映済みになるため、token budget に
数えるのは残りの実計算 token だけである。

例えば 1000 token の prompt のうち400 token が cache hit なら、計算対象は
600 token になる。cache hit token をもう一度 `scheduled_tokens` から引くことは
しない。

---

## 9. STEP 1.5: prefix cache のロック

このステップは `schedule_with_prefix()` だけに存在する。

```python
if req.is_prefill() and req.npu_last_node is not None and not req._prefix_locked:
    self.memory.lock_prefix(req, Device.NPU)
    req._prefix_locked = True
```

今回利用する prefix を eviction 対象から守るため、Batch が確定する前に
RadixCache の該当ノードをロックする。

後続のメモリ判定でその Request が Batch から外れた場合は、ロックを解除して
prefix 情報を消す。メモリ不足で Batch 自体を作れない場合にも rollback する。
この対称的な後始末がないと、使われなかった prefix が永続的に eviction 不可に
なってしまう。

---

## 10. STEP 2: KV キャッシュ容量に収まる本数を探す

> KVサイズの式、block単位の追加容量、候補本数の探索、prefix caching有無による
> 判定差の詳細は
> [`kv_cache_capacity.md`](./kv_cache_capacity.md)を参照。

`MemoryModel.get_block_kv()` は、各 Request が今回新たに必要とする KV block の
合計バイト数を返す。

```text
blocks_after  = ceil((computed_tokens + scheduled_tokens) / block_size)
blocks_before = ceil(computed_tokens / block_size)
new_blocks    = blocks_after - blocks_before
```

`block_size=16` なので、例えば計算済み16 token の Request を次に1 token
decode すると、新しい16-token block が1個必要になる。一方、計算済み17 token
から18 token へ進むだけなら、既に同じ block 内なので追加 block は0個である。

Scheduler は候補数を末尾から減らしながら、収容できる最大の prefix を探す。

### prefix caching 無効時

```python
if self.memory.is_avail(kv_size + load_size, Device.NPU):
    temp_len = i
```

新規 KV と、CPU へ退避済み Request を戻すための `load_size` の両方が、現在の
空き容量に収まるかを見る。

### prefix caching 有効時

```python
total_useable_size = (
    self.memory.avail_size(Device.NPU)
    + self.memory.evictable_size(Device.NPU)
)
```

現在の空きに加え、ロックされていない prefix cache を追い出せば確保できる容量も
利用可能として数える。

---

## 11. STEP 3: メモリ不足時の eviction

候補を減らしても1本も入らない `temp_len == 0` の間、既存 decode Request を
後ろから preempt する。

### 11.1 prefix caching 無効時

```python
req_to_evict.evict = True
self.memory.free(evicted_kv_size, Device.NPU)
self.memory.allocate(evicted_kv_size * self.num_npus, Device.CPU)
```

対象 Request の KV を NPU から解放し、CPU 使用量へ移す。NPU 側の KV サイズは
per-rank だが CPU 側はクラスタ全体のバイト数で追跡するため、CPU へ移すときは
`num_npus` 倍する。

この Request が後の Batch に再採用されたときは逆に NPU へ load し、CPU 側を
解放する。転送量は `Batch.evict` / `Batch.load` に入り、後段のトレース生成で
メモリ転送として扱われる。

### 11.2 prefix caching 有効時

個別 Request の KV バイト列を直接 CPU 使用量へ付け替えるのではなく、
RadixCache の eviction を使う。

```python
evict_size = max(0, kv_size - self.memory.avail_size(Device.NPU))
self.memory.evict_prefix_cache(evict_size, Device.NPU)
```

二次 prefix storage が設定されていれば、NPU から追い出した prefix metadata は
CPU または CXL 側 cache へ保存される。再利用時には NPU cache hit より storage
cache hit が長い分を `prefix_load_size` として数える。

退避可能な decode Request も prefix cache も無く、必要容量を作れない場合は
`None` を返す。

---

## 12. STEP 4: 待ち行列から削除し、メモリを確保する

最終的に Batch へ入る Request を `self.request` から削除する。

prefix caching 無効時は、今回増える KV block と退避 KV の再読込分を NPU に
allocate する。prefix caching 有効時は RadixCache の event が使用量を更新し、
二次ストレージから読むサイズや evicted Request の reload サイズを集計する。

この時点まで `self.request` から削除しないのが重要である。token budget や
メモリ容量で候補から落ちた Request は、待ち行列に残って次回の
`schedule()` を待てる。

---

## 13. STEP 5: `Batch` を構築する

最終候補を attention lookup に必要な形へ集計する。

| Batch の値 | 内容 |
|---|---|
| `total_len` | 今回計算する token 数の合計 |
| `num_prefill` | prefill Request 数 |
| `num_decode` | decode Request 数 |
| `prefill_q_list` | 各 prefill の今回の chunk 長 |
| `prefill_k_list` | 各 prefill で既に計算済みの KV 長 |
| `decode_k_list` | 各 decode Request の現在の KV 長 |
| `kv_size` | 今回追加確保する KV サイズ |
| `evict` | NPU から退避する量 |
| `load` | CPU/CXL などから読み戻す量 |

```python
batch = Batch(
    self.get_batch_id(), self.model,
    total_len, kv_len, q_list, k_list,
    num_prefill, num_decode,
    prefill_q_list, prefill_k_list, decode_k_list,
    current, kv_size, evict_size, load_size,
)
batch.fired.append(sys)
batch.requests.extend(batch_req)
self.inflight.append(batch)
batch.scheduled_tokens = scheduled_tokens
return batch
```

この `Batch` の形状情報を `trace_generator` が使い、dense layer は
`total_len`、attention は prefill/decode の Q/K 長をキーに profile CSV の
latency を検索する。

---

## 14. 具体例: 1回のスケジューリング

次の状態を考える。

```text
max_num_seqs           = 4
max_num_batched_tokens = 8
inflight request数     = 1

到着済み待ち行列:
  Request 10: decode                 → 1 token
  Request 11: prefill 残り 6 tokens
  Request 12: prefill 残り 5 tokens
  Request 13: decode                 → 1 token
```

1. inflight が1本なので `available_slots = 4 - 1 = 3`
2. 先頭3本だけが候補: Request 10, 11, 12
3. chunked prefill により decode-first: 10, 11, 12
4. Request 10 に1 token。残り budget は7
5. Request 11 に6 tokens。残り budget は1
6. Request 12 に1 token。残り budget は0
7. KV block 容量を検査し、収まれば3本で Batch を作る

結果:

```python
scheduled_tokens = {10: 1, 11: 6, 12: 1}
num_decode = 1
num_prefill = 2
total_len = 8
```

Request 13 は `max_num_seqs` の空きに入らなかったため待ち行列に残る。
もし KV 容量が Request 12 まで収容できなければ、最終 Batch はさらに短くなり、
Request 12 も次回へ持ち越される。

---

## 15. ASTRA-Sim 完了後の `add_done()`

Scheduler の役割は Batch を作って終わりではない。ASTRA-Sim が Batch の実行完了を
返すと、メインループが `Scheduler.add_done(id, sys, current)` を呼ぶ。

### 15.1 全 NPU の完了を待つ

各 NPU の完了通知を `batch.end` に記録し、instance の先頭・末尾 NPU が完了する
まで Request 状態を更新しない。複数 NPU の同じ Batch を途中で二重更新しないため
の barrier である。

### 15.2 Prefill の更新

```python
req.num_computed_tokens += chunk_len
req.chunk_len = 0
```

入力全体に達していなければ待ち行列へ戻す。達した場合は prefix cache を更新し、
`is_init=False`、TTFT を記録する。colocated 構成では、prefill の最後を lm_head に
通した時点で最初の output token が生成されたものとして数える。

PD 分離構成の prefill instance では、完了 Request を `end_reqs` として返し、
Router が decode instance へ引き渡す。

### 15.3 Decode の更新

decode は `num_computed_tokens` を1増やし、ITL を記録する。必要な output token 数に
達したら KV cache を終了済み cache として処理し、latency を確定して `done` へ移す。
未完了なら再び `request` へ戻り、次の1 token を待つ。

```text
Batch完了
├── Request完了   → done
├── Prefill途中   → request（次chunk待ち）
├── Decode途中    → request（次token待ち）
└── PD Prefill完了 → Router経由でdecode instanceへ
```

---

## 16. メインループとの接続

> BatchがChakra graphへ変換され、ASTRA-Sim内部でCOMP/MEM/COMM nodeとして
> 実行されて完了cycleが戻るまでの詳細は
> [`astra_sim_execution.md`](./astra_sim_execution.md)を参照。

Scheduler を中心に1イテレーションを見ると、次の流れになる。

```text
ASTRA-Simから前Batchの完了通知
        │
        ▼
Scheduler.add_done()
  └─ Requestを done または request に戻す
        │
        ▼
Router.route_arrived_requests(current)
  └─ 新着Requestを Scheduler.add_request() へ渡す
        │
        ▼
Scheduler.schedule(current, sys, batch_id)
        │
        ├─ None  ──> ASTRA-Simへ "pass"
        │
        └─ Batch ──> generate_trace()
                       └─ generate_graph()
                            └─ ASTRA-Simへ .et workload path
```

Scheduler は latency 自体を計算しない。どの Request を何 token ずつ実行するかと、
必要な KV メモリ移動量を決める。実行時間は、その結果を受けた
`trace_generator` と ASTRA-Sim が計算する。

---

## 17. まとめ

このフェーズを一言でまとめると、

> **待ち行列から「今実行できる Request」を選び、token budget と KV memory の
> 両方に収まるまで候補を絞って、ASTRA-Sim が実行できる `Batch` に変換する処理**

特に重要なのは次の4点。

1. `max_num_seqs` は inflight を含む実行中 Request 総数のハード上限
2. chunked prefill では decode に1 token ずつ先に配り、残り budget を prefill に使う
3. KV は `block_size` 単位で追加確保され、足りなければ decode または prefix cache を退避する
4. prefix caching 有効時は cache hit 分を計算 token から除き、RadixCache の lock / eviction / reload を管理する

作られた `Batch` は `trace_generator` に渡される。ASTRA-Sim の完了後は
`add_done()` が Request の token 進捗と TTFT/ITL/latency を更新し、未完了なら
再び待ち行列へ戻す。この循環が、すべての Request が完了するまで繰り返される。

## 関連ファイル

- `serving/core/scheduler.py` — 本解説の対象
- `serving/core/memory_model.py` — 重み・KV cache・RadixCache の容量管理
- `serving/core/request.py` — `Request` / `Batch` の状態定義
- `serving/core/router.py` — Scheduler への Request 投入と完了後の引き渡し
- `serving/core/trace_generator.py` — Batch から実行トレースを生成
- `serving/__main__.py` — Scheduler の構築とメインループからの呼び出し
- `kondoFolder/tutorial/simulation_callgraph.md` — このフェーズを含む全体コールグラフ
- `kondoFolder/tutorial/detail/build_cluster_config.md` — 直前フェーズの詳細解説
