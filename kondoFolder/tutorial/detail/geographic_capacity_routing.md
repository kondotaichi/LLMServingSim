# Geographic Capacity Routing Policy 詳細解説

作成日: 2026-07-12  
対象ポリシー:

- `NEAREST_KV`
- `NEAREST_REJECT`
- `NEAREST_MIGRATE`
- `NEAREST_MIGRATE_KV`

対象実装:

- `workloads/generators/geographic.py`
- `serving/__main__.py`
- `serving/core/router.py`
- `serving/core/memory_model.py`
- `serving/core/request.py`
- `serving/core/scheduler.py`

本ドキュメントでは、既存の`NEAREST`を基準として、GPU capacity、UE再送、
GPU間forward、KV cache handoffを比較するために追加されたrouting policy群を、
workload生成からCSV出力までコード経路に沿って説明する。

---

## 1. 追加機能の目的

既存の`NEAREST`は、geographic workload generatorが事前に決めた最寄りGPUへ、
Requestを常に送る。

```text
User
  │
  ▼
Nearest GPU
  └─ 満杯でもそのままqueueで待つ
```

これは通信距離を最小にできる一方、特定GPUへRequestが集中すると長いqueue待ちが
発生する。

追加ポリシー群の目的は、次のtrade-offを比較することにある。

```text
最寄りGPUで待つ
    vs
通信コストを払って別GPUへ移し、queueを短くする
    vs
さらにKVを移してPrefill計算も減らす
```

4方式を整理すると次の通り。

| Policy | 最寄りが空いている場合 | 最寄りが満杯の場合 | Redirect先のKV |
|---|---|---|---|
| `NEAREST_KV` | 最寄りでlocal KV利用 | Redirectしないで待つ | 対象外 |
| `NEAREST_REJECT` | 最寄りでlocal KV利用 | UEへrejectし、2番目へ再送 | Cold |
| `NEAREST_MIGRATE` | 最寄りでlocal KV利用 | GPU backboneで2番目へforward | Cold |
| `NEAREST_MIGRATE_KV` | 最寄りでlocal KV利用 | RequestとKVを2番目へforward | KVを移送 |

---

## 2. 既存`NEAREST`との差分

既存`NEAREST`のinstance選択は単純なlookupである。

```python
target_instance_id = req_data['assigned_instance_id']

for idx, sched in enumerate(schedulers):
    if sched.instance_id == target_instance_id:
        return idx
```

次は行わない。

- runtime capacity確認
- queue長比較
- KV cache再利用のseed
- second-nearestへのredirect
- 通信時間の再計算

追加ポリシーは、最終的なinstance選択には同じ`_nearest_select()`を再利用するが、
その前段で`assigned_instance_id`、`arrival_time_ns`、通信metadataを更新する。

```text
NEAREST:
  assigned_instance_idをそのまま選択

追加Policy:
  capacityを確認
      │
      ├─ 空きあり → home GPUをそのまま選択
      │
      └─ 満杯     → second-nearestへmetadataを変更
                         │
                         ▼
                    _nearest_select()
```

---

## 3. 追加したCLI policy

`serving/__main__.py`の`--request-routing-policy`へ次を追加している。

```text
NEAREST_KV
NEAREST_REJECT
NEAREST_MIGRATE
NEAREST_MIGRATE_KV
```

```bash
python -m serving \
  --request-routing-policy NEAREST_MIGRATE_KV \
  ...
```

`NEAREST_MIGRATE`と`NEAREST_MIGRATE_KV`には、UE↔GPU access linkとは別に、
GPU間backboneの設定が必要。

```bash
--gpu-backbone-bandwidth-gbps 25 \
--gpu-backbone-distance-m 5000
```

| CLI | 意味 |
|---|---|
| `--gpu-backbone-bandwidth-gbps` | GPU A→GPU Bのoperator network帯域 |
| `--gpu-backbone-distance-m` | GPU A→GPU Bの物理距離 |

未指定でMIGRATE系を使うと`RuntimeError`になる。

---

## 4. Geographic generatorへ追加した情報

元のgeneratorは各Userについて最寄りGPUだけを計算していた。Redirect先が必要に
なったため、全GPUを距離順にsortし、最寄りと2番目を返すように変更している。

```python
ranked = sorted(
    (distance, gpu_id)
    for gpu_id, gpu_xy in enumerate(gpus_xy)
)

(dist0, id0), (dist1, id1) = ranked[0], ranked[1]
```

同距離ならGPU IDが小さい方を優先する。

生成JSONLへ次を追加する。

```json
{
  "assigned_instance_id": 0,
  "gpu_id": 0,
  "distance_m": 1200.0,
  "second_nearest_gpu_id": 1,
  "second_nearest_distance_m": 1800.0,
  "distance_latency_ns_per_meter": 5.0
}
```

GPUが2台未満ならsecond-nearestを作れないため、generatorは`ValueError`にする。

---

## 5. Generator上のID前提

Generatorが出力するstatic GPU CSVでは、

```python
gpu_id == instance_id
```

としている。

```text
GPU location 0 → Instance 0
GPU location 1 → Instance 1
...
```

Routerも`second_nearest_gpu_id`をそのまま`assigned_instance_id`へ入れるため、
geographic workloadのGPU IDとcluster configのinstance IDが対応している必要がある。

このpolicy群は「1 geographic GPU location = 1 serving instance」という実験モデルを
前提にしている。

---

## 6. Generator上のaccess-link遅延

元Requestの`arrival_time_ns`はUE送信時刻として扱う。

```text
request_send_time_ns = source arrival_time_ns
```

Request payload:

```text
request_payload_bytes
= protocol_overhead_bytes
  + input_toks × bytes_per_input_token
```

Serialization時間:

```text
serialization_ns
= 8000 × payload_bytes / throughput_mbps
```

最寄りGPUまでのuplink:

```text
uplink_distance_ns
= distance_m × distance_latency_ns_per_meter

uplink_latency_ns
= uplink_distance_ns + uplink_serialization_ns
```

GPU到着時刻:

```text
gpu_arrival_time_ns
= request_send_time_ns + uplink_latency_ns
```

出力JSONLでは、既存Routerが読む`arrival_time_ns`をGPU到着時刻へ上書きする。

```json
{
  "request_send_time_ns": 1000000,
  "gpu_arrival_time_ns": 1800000,
  "arrival_time_ns": 1800000
}
```

したがってcapacity判定は、UEから最寄りGPUへのuplinkが完了した時点で行われる。

---

## 7. KV比較用generator option

再利用可能prefix長を各Requestへ付けるoptionを追加している。

```bash
--kv-reuse-prefix-toks 1024
```

実際に書く値はinput長を超えない。

```python
reuse_prefix_toks = min(configured_value, input_toks)
```

必要ならKV移送回線をRequest単位でoverrideできる。

```bash
--kv-migration-bandwidth-gbps 25 \
--kv-migration-distance-m 5000
```

未指定時、`NEAREST_MIGRATE_KV`はserving CLIの`--gpu-backbone-*`へfallbackする。

---

## 8. Capacity判定

3つのredirect policyに共通する判定:

```text
NEAREST_REJECT
NEAREST_MIGRATE
NEAREST_MIGRATE_KV
```

最寄りinstanceのinflight Request数を数える。

```python
running_reqs = sum(
    len(batch.requests)
    for batch in sched.inflight
)

has_capacity = running_reqs < sched.max_num_seqs
```

```text
running_reqs < max_num_seqs
  → 最寄りGPUに空きあり

running_reqs >= max_num_seqs
  → 最寄りGPUは満杯
```

### 具体例

```text
max_num_seqs = 4
inflight Request = 4

→ capacityなし
→ redirectを発生
```

---

## 9. Capacity判定に含まれないもの

このcapacityは完全なadmission feasibilityではない。次は見ていない。

- `len(sched.request)`のwaiting queue
- `max_num_batched_tokens`の残り
- NPU KV cache空き容量
- prefix cache eviction可能量
- `pp_size`に対するinflight Batch数
- second-nearest GPUのcapacity

つまり、

```text
最寄りGPUのrunning slotが空いているか
```

だけを判定する軽量な近似である。

waiting queueが非常に長くてもinflightが`max_num_seqs`未満なら最寄りへ送る。
反対に、token/KV制約で実際にはすぐBatchへ入れなくても、running slotだけ空いていれば
accept扱いになる。

---

## 10. `NEAREST_KV`

`NEAREST_KV`はredirect policyではない。

```text
User
  │
  ▼
Nearest GPU
  ├─ Redirectなし
  └─ reuse_prefix_toks分のKVがlocalに存在する仮定
```

`reuse_prefix_toks > 0`なら、Routerが`local_kv` metadataを作る。

```python
req_data['failover'] = {
    'failover_mode': 'local_kv',
    'failed_instance_id': target_instance_id,
    'target_instance_id': target_instance_id,
    'reuse_prefix_toks': reuse_prefix_toks,
}
```

既存のstatic failover処理を再利用し、target Schedulerのprefix cacheへseedする。

```text
KV transfer latency = 0
```

これは「実Tensorが既にlocalにある」という実験上の仮定をmetadataとして再現したもの。

---

## 11. `NEAREST_REJECT`

最寄りGPUにrunning slotがなければ、UEへcapacity rejectionを返し、UEが2番目のGPUへ
Requestを送り直すモデル。

```text
             capacity probe
User ──────> GPU A (nearest, full)
  ^             │
  └── reject ───┘
  │
  └────────────> GPU B (second-nearest)
```

### 11.1 Reject penalty

Capacity probeはpayload serializationなしの軽量probeと仮定する。

```text
reject_penalty_ns
= 2 × nearest_distance_m
  × distance_latency_ns_per_meter
```

最寄りGPUとの往復伝搬時間だけを課金する。

### 11.2 2番目への再送

```text
second_uplink_distance_ns
= second_distance_m × per_meter_ns

second_uplink_serialization_ns
= 8000 × request_payload_bytes / access_mbps

second_uplink_ns
= distance + serialization
```

Downlinkも2番目GPUの距離で再計算する。

### 11.3 到着時刻の延期

```python
req_data['arrival_time_ns'] = (
    current_time_ns
    + reject_penalty_ns
    + second_uplink_latency_ns
)
```

このRequestはすぐSchedulerへ渡さず、Router pending queueへ戻す。

### 11.4 KV

RedirectされたRequestのKVは移動しない。

```text
GPU AにあったKV
  → GPU Bでは利用不可
  → GPU Bでcold Prefill
```

---

## 12. `NEAREST_MIGRATE`

最寄りGPUが満杯なら、UEへ戻さず、GPU Aがoperator backboneでGPU BへRequestを
forwardする。

```text
User ──────> GPU A (nearest, full)
                │
                │ GPU backbone
                ▼
              GPU B (second-nearest)
                │
                └── first token ──> User
```

UE→GPU Aのuplinkは既に完了しているため再課金しない。新たな待ち時間はGPU A→GPU B
のbackbone hop。

### 12.1 Request forward時間

```text
backbone_mbps
= gpu_backbone_bandwidth_gbps × 1000

migration_distance_ns
= gpu_backbone_distance_m × per_meter_ns

migration_serialization_ns
= 8000 × request_payload_bytes / backbone_mbps

migration_latency_ns
= distance + serialization
```

### 12.2 到着時刻

```python
req_data['arrival_time_ns'] = (
    current_time_ns + migration_latency_ns
)
```

### 12.3 Downlink

最初のtokenはGPU BからUserへ返るため、downlinkをsecond-nearest距離で再計算する。

### 12.4 KV

Requestだけをforwardし、KVは移動しない。

```text
GPU Bではcold Prefill
```

---

## 13. `NEAREST_MIGRATE_KV`

Request forwardまでは`NEAREST_MIGRATE`と同じ。さらに再利用可能なKV prefixをGPU Bへ
移送する。

```text
User ──────> GPU A (full)
                │
                ├─ Request payload
                └─ reusable KV prefix
                         │
                         ▼
                       GPU B
```

Redirect時、Routerは既存static failoverと同じ形のmetadataを付ける。

```python
req_data['failover'] = {
    'failover_mode': 'migrate_kv',
    'failed_instance_id': source_instance_id,
    'target_instance_id': target_instance_id,
    'reuse_prefix_toks': reuse_prefix_toks,
    'kv_migration_bandwidth_gbps': ...,
    'kv_migration_distance_m': ...,
    'distance_latency_ns_per_meter': ...,
}
```

この設計により、新しいpolicy専用にKV cache処理を二重実装せず、既存
`_apply_kv_migration_if_needed()`を再利用している。

---

## 14. KV handoffの実体

Simulatorは実際のK/V Tensorを保持していない。KV handoffは次の3つで表現する。

1. 移送token数をblock境界へ合わせる
2. target RadixCacheへprefix metadataをseed
3. KV bytesから移送時間を計算してarrivalを遅らせる

### 14.1 Prefix seed

```python
migrated_tokens = sched.memory.seed_migrated_prefix(
    input_hash_ids,
    requested_prefix,
)
```

`block_size > 1`なら、prefix長はblock境界へ切り下げる。

```text
requested = 1000 tokens
block_size = 16

migrated = floor(1000 / 16) × 16
         = 992 tokens
```

target NPUに空きがなければ、evictable prefixを先に追い出す。それでも入らなければ
`RuntimeError`。

### 14.2 KV bytes

```python
migration_bytes = (
    sched.memory.get_kv(migrated_tokens)
    * sched.num_npus
)
```

`get_kv()`はper-rank KV bytesを返すため、cluster全体の移送量へ戻す目的で
`num_npus`倍する。

### 14.3 KV migration時間

```text
kv_serialization_ns
= 8000 × migration_bytes / bandwidth_mbps

kv_distance_ns
= migration_distance_m × per_meter_ns

kv_migration_ns
= distance + serialization
```

### 14.4 Request arrivalへの反映

Request payloadのbackbone forward後、さらにKV migration時間を加える。

```python
req_data['arrival_time_ns'] += kv_migration_ns
```

コード上ではRequest forward後にRouter pendingへ一度戻り、再到着時のinstance選択後、
KV migration分だけSchedulerへ渡すarrivalを未来にする。

```text
GPU A到着
  │
  │ Request forward
  ▼
GPU B routing時刻
  │
  │ KV migration
  ▼
GPU B Schedulerで実行可能になる時刻
```

---

## 15. Prefix caching要件

`local_kv`と`migrate_kv`はtarget RadixCacheへprefixをseedするため、次が必須。

```bash
--enable-prefix-caching
```

既定では有効だが、明示的に、

```bash
--no-enable-prefix-caching
```

を付けて`NEAREST_KV`やKV handoffを実行すると`RuntimeError`になる。

`NEAREST_REJECT`と`NEAREST_MIGRATE`も、home GPUでfair local-KV baselineを利用する
設定ではprefix cachingが必要。再利用を使わない`reuse_prefix_toks=0`ならseedは
発生しない。

---

## 16. Redirect後のpending queue再挿入

Redirectに通信時間があるため、同じsimulation時刻で即座にGPU Bへ投入してはいけない。

```python
self._pending_requests.pop(self._pending_idx)
self._insert_pending_sorted(req_data)
continue
```

`_insert_pending_sorted()`は未消費部分へarrival順で二分探索挿入する。

```text
現在時刻: 100

Redirect Requestの新arrival: 150
他Requestのarrival:          120, 140, 180

再挿入後:
  120, 140, 150, 180
```

これにより、通信中のRequestが、先に到着する別Requestを追い越さない。

---

## 17. Redirectは1回だけ

Redirect時に内部flagを立てる。

```python
req_data['_reject_resolved'] = True
```

次にpending queueから取り出されたとき、capacity判定を繰り返さない。

```text
GPU Aが満杯
  → GPU Bへredirect

GPU Bも満杯
  → GPU Cへ再redirectしない
  → GPU BのScheduler queueへ入る
```

したがって本実装は最大1回のredirectで、third-nearest以降へのcascading policyではない。

また、second-nearestのcapacityは事前に確認せず、無条件にacceptする。

---

## 18. Fair-KV baseline

4方式を比較するとき、「redirect policy」と「最初からwarm cacheか」を混同しないため、
現在のコードではhome GPU上のKV前提を揃えている。

### Home GPUに留まる場合

次の4方式は、`reuse_prefix_toks > 0`ならlocal KVをseedする。

```text
NEAREST_KV
NEAREST_REJECT
NEAREST_MIGRATE
NEAREST_MIGRATE_KV
```

### Redirectした場合

| Policy | Redirect後のprefix |
|---|---|
| `NEAREST_REJECT` | 移動しないためcold |
| `NEAREST_MIGRATE` | 移動しないためcold |
| `NEAREST_MIGRATE_KV` | KVを移送して再利用 |

`_reject_resolved`がfalse、つまりhome GPUにいる場合だけ、REJECT/MIGRATEにも
`local_kv`を付ける。

```python
if policy in ("NEAREST_REJECT", "NEAREST_MIGRATE") \
        and not req_data.get('_reject_resolved', False):
    attach_local_kv()
```

この設計により、比較する差を次に限定する。

```text
Redirectするか
UE resendかGPU forwardか
Redirect時にKVが追随するか
```

---

## 19. 各Policyの時間構成

概念的なE2E TTFTは次のように分解できる。

### `NEAREST_KV`

```text
original uplink
+ nearest queue
+ cached-prefix後のPrefill
+ nearest downlink
```

### `NEAREST_REJECT`

```text
original uplink to GPU A
+ reject round trip
+ UE→GPU B uplink
+ GPU B queue
+ cold Prefill
+ GPU B downlink
```

### `NEAREST_MIGRATE`

```text
original uplink to GPU A
+ GPU A→GPU B Request forward
+ GPU B queue
+ cold Prefill
+ GPU B downlink
```

### `NEAREST_MIGRATE_KV`

```text
original uplink to GPU A
+ GPU A→GPU B Request forward
+ GPU A→GPU B KV migration
+ GPU B queue
+ remaining Prefill
+ GPU B downlink
```

`NEAREST_MIGRATE_KV`はKV transferという追加コストを払う代わりに、targetで再計算する
Prefill token数を減らす。

---

## 20. 通信モデルの性質

UE access linkとGPU backboneは解析式で時間を計算し、ASTRA-Sim network graphへは
投入しない。

```text
UE↔GPU latency
  → geographic generator / Routerで事前・動的計算

GPU A→GPU B Request/KV migration
  → Routerで解析式計算

Model内部ALLREDUCE/ALLTOALL
  → ASTRA-Simでsimulation
```

したがってRequest redirect回線について、次はモデル化していない。

- 複数migration間のnetwork contention
- access linkのqueueing
- packet loss
- jitter
- bandwidth sharing
- retransmission
- User mobility

指定帯域と距離から決まる固定的な解析時間である。

---

## 21. Requestへ追加した状態

Redirect結果を保持するため、`Request`へ次を追加している。

| Field | 意味 |
|---|---|
| `nearest_gpu_id` | 元の最寄りGPU |
| `rerouted` | Redirectされたか。0/1 |
| `reject_penalty_ns` | UE reject方式の追加往復時間 |
| `migration_latency_ns` | Request payloadのGPU backbone forward時間 |

KVについては既存failover fieldを利用する。

| Field | 意味 |
|---|---|
| `failover_mode` | `local_kv` / `migrate_kv`など |
| `failed_instance_id` | Source/home instance |
| `failover_target_instance_id` | KV seed先instance |
| `reuse_prefix_toks` | 要求した再利用prefix長 |
| `kv_migration_tokens` | block丸め後に実際にseedしたtoken数 |
| `kv_migration_bytes` | 移送KV bytes |
| `kv_migration_latency_ns` | KV移送時間 |
| `kv_migration_distance_latency_ns` | KV移送の距離成分 |
| `kv_migration_serialization_latency_ns` | KV移送のserialization成分 |

---

## 22. CSVへ追加した列

Per-request output CSVへ次を追加している。

```text
nearest_gpu_id
rerouted
reject_penalty_ns
migration_latency_ns
```

既存KV migration列と合わせることで、

```text
Request redirect時間
KV migration時間
Queue時間
Prefill時間
Downlink時間
```

を分けて比較できる。

特に、

```text
migration_latency_ns
  → Request payloadのGPU forward

kv_migration_latency_ns
  → KV cache payloadのGPU forward
```

は別の値である。

---

## 23. Routing実行順序

`route_arrived_requests()`内の順序:

```text
1. arrival_time <= currentか確認
2. Capacity redirect判定
3. Redirectならarrivalを延期してpendingへ再挿入
4. Static/dynamic failover targetがあればtargetを選択
5. なければassigned_instance_idで選択
6. Fair local-KV metadataを付与
7. KV seed/migrationを適用
8. Scheduler.add_request()
```

`NEAREST_MIGRATE_KV`では、Step 2でdynamic `migrate_kv` metadataを作り、Step 7の
既存処理へ接続する。

---

## 24. Workload生成例

元になるflat workloadへgeographic情報を付ける。

```bash
python -m workloads.generators geographic \
  --input workloads/source.jsonl \
  --output workloads/geographic.jsonl \
  --users-output outputs/geographic_users.csv \
  --gpus-output outputs/geographic_gpus.csv \
  --metadata-output outputs/geographic_meta.json \
  --num-users 3000 \
  --gpu-rows 1 \
  --gpu-cols 2 \
  --area-width-m 10000 \
  --area-height-m 10000 \
  --network-throughput-mbps 100 \
  --distance-latency-ns-per-meter 5 \
  --kv-reuse-prefix-toks 1024 \
  --allow-uniform-user-fallback
```

入力はflat JSONL限定。`sub_requests`を持つagentic sessionは現行generatorの対象外で、
`RuntimeError`になる。

---

## 25. Serving実行例

### `NEAREST_KV`

```bash
python -m serving \
  --cluster-config configs/cluster/<two-instance-config>.json \
  --dataset workloads/geographic.jsonl \
  --request-routing-policy NEAREST_KV \
  --enable-prefix-caching \
  --output results/nearest-kv.csv
```

### `NEAREST_REJECT`

```bash
python -m serving \
  --cluster-config configs/cluster/<two-instance-config>.json \
  --dataset workloads/geographic.jsonl \
  --request-routing-policy NEAREST_REJECT \
  --enable-prefix-caching \
  --output results/nearest-reject.csv
```

### `NEAREST_MIGRATE`

```bash
python -m serving \
  --cluster-config configs/cluster/<two-instance-config>.json \
  --dataset workloads/geographic.jsonl \
  --request-routing-policy NEAREST_MIGRATE \
  --gpu-backbone-bandwidth-gbps 25 \
  --gpu-backbone-distance-m 5000 \
  --enable-prefix-caching \
  --output results/nearest-migrate.csv
```

### `NEAREST_MIGRATE_KV`

```bash
python -m serving \
  --cluster-config configs/cluster/<two-instance-config>.json \
  --dataset workloads/geographic.jsonl \
  --request-routing-policy NEAREST_MIGRATE_KV \
  --gpu-backbone-bandwidth-gbps 25 \
  --gpu-backbone-distance-m 5000 \
  --enable-prefix-caching \
  --output results/nearest-migrate-kv.csv
```

---

## 26. 実装上の制約と解釈上の注意

### 26.1 Capacityはrunning slotだけ

Queue、token budget、KV空きまで含む完全なcapacityではない。

### 26.2 Second-nearestは無条件accept

2番目が満杯でもthird-nearestへ回さない。Redirect後は通常Scheduler queueで待つ。

### 26.3 Redirectは1回だけ

`_reject_resolved`によりcascading redirectを防ぐ。

### 26.4 GPU IDとinstance IDの一致が必要

GeneratorのGPU番号をRouterがinstance IDとして使用する。

### 26.5 KVはmetadata model

実Tensorをnetworkへ流すのではなく、bytesから時間を課金し、target RadixCacheへ
prefix metadataをseedする。

### 26.6 Prefix cachingが必要

Local/migrated KVを使う方式ではRadixCacheが必要。

### 26.7 Redirect networkにcontentionはない

固定帯域・距離の解析式で、同時transferによる帯域競合は反映しない。

### 26.8 Geographic generatorはflat workloadのみ

Agentic sessionとの組み合わせは現行実装の対象外。

---

## 27. 何を追加したかのファイル別まとめ

### `workloads/generators/geographic.py`

- 最寄りだけでなくsecond-nearest GPUも計算
- second-nearest ID/距離をJSONLへ保存
- `reuse_prefix_toks`生成option
- KV migration bandwidth/distance override
- static users CSVへsecond-nearest情報を追加

### `serving/__main__.py`

- 4つのrouting policyをCLI choicesへ追加
- GPU backbone bandwidth/distance CLIを追加
- Router constructorへbackbone設定を渡す

### `serving/core/router.py`

- 最寄りScheduler検索helper
- running-slot capacity判定
- UE reject/resend処理
- GPU backbone request forward処理
- Dynamic KV handoff metadata生成
- Redirect後のarrival延期とpending再挿入
- 1回だけredirectするflag
- Fair local-KV baseline
- 既存static failover経路との統合

### `serving/core/request.py`

- 元の最寄りGPU
- reroute flag
- reject penalty
- Request migration latency

をRequest状態へ追加。

### `serving/core/scheduler.py`

- Redirect/KV migration関連fieldをper-request CSVへ追加

---

## 28. まとめ

追加したpolicy群は、最寄りGPU固定routingを次の比較実験へ拡張する。

```text
NEAREST_KV
  → 最寄りで待つ、local KVを使う

NEAREST_REJECT
  → 満杯ならUEへ返し、2番目へ再送、KVなし

NEAREST_MIGRATE
  → 満杯ならGPU backboneで2番目へforward、KVなし

NEAREST_MIGRATE_KV
  → Requestに加えてKVも2番目へ移し、Prefill再計算を削減
```

実装の中心は、

```text
runtime capacity判定
+ communication timeによるarrival延期
+ pending queueへの時系列再挿入
+ 既存KV failover処理の再利用
+ 結果CSVの内訳追加
```

である。

これにより、「近いGPUで長く待つ方がよいか」「遠いGPUへ移る通信コストを払う方が
よいか」「KVまで移す追加コストがPrefill削減に見合うか」を同じSimulator上で比較
できる。

## 関連ファイル

- `workloads/generators/geographic.py` — geographic workloadとsecond-nearest生成
- `serving/__main__.py` — policy/CLI定義
- `serving/core/router.py` — policy本体
- `serving/core/memory_model.py` — local/migrated prefix seedとKV bytes計算
- `serving/core/request.py` — geographic/redirect/KV状態
- `serving/core/scheduler.py` — 結果CSV出力
- `kondoFolder/tutorial/detail/router.md` — Router全体の詳細
- `kondoFolder/tutorial/detail/kv_cache_capacity.md` — KV容量計算
- `kondoFolder/simulator_overview_and_updates.md` — 実験全体の定義と結果概要
