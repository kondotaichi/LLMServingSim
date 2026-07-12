# LLMServingSimから見たASTRA-Sim内部実行

作成日: 2026-07-11  
対象範囲:

- `serving/core/trace_generator.py`
- `serving/core/graph_generator.py`
- `astra-sim/extern/graph_frontend/chakra/src/converter/llm_converter.py`
- `astra-sim/astra-sim/workload/Workload.cc`
- `astra-sim/astra-sim/system/Sys.cc`
- `astra-sim/astra-sim/network_frontend/analytical/congestion_unaware/main.cc`
- `serving/core/controller.py`

本ドキュメントはASTRA-Sim全体のアルゴリズム解説ではない。LLMServingSimの
Schedulerが作ったBatchの実行時間が、どのようにASTRA-Simで計算され、Pythonへ
返るかを理解するために必要な範囲だけを追う。

---

## 1. このフェーズの目的

Python側のSchedulerは、Batchへ入れるRequestとtoken数を決めるが、Batchの
実行時間そのものは計算しない。

```text
Scheduler
  └─ 今回処理するRequest/tokenを決定
          │
          ▼
Trace Generator
  └─ layerごとのcompute/memory/communicationへ展開
          │
          ▼
Chakra Converter
  └─ 依存関係付きgraphへ変換
          │
          ▼
ASTRA-Sim
  └─ graphをイベント実行し、完了cycleを計算
          │
          ▼
Controller
  └─ 完了cycleをPythonへ戻す
```

この境界を理解すると、次の区別が明確になる。

```text
Batch待ち時間
  → Python Schedulerが決める

Batch実行時間
  → profile latencyとASTRA-Simのmemory/network simulationが決める
```

---

## 2. SchedulerのBatchはASTRA-Simへ直接渡されない

`Batch`はPythonオブジェクトであり、ASTRA-SimのC++コードはこれを直接読まない。

```text
Python Batch
├── requests
├── total_len
├── num_prefill / num_decode
├── prefill_q_list / prefill_k_list
├── decode_k_list
├── kv_size
├── evict
└── load
```

これらを`trace_generator.generate_trace()`が、layer単位のテキストtraceへ変換する。

```text
Layername  comp_time  input_loc  input_size  weight_loc  weight_size
           output_loc output_size comm_type comm_size misc
```

例えば、

```text
qkv_proj_0  12000  LOCAL  ...  LOCAL ... LOCAL ... NONE       0 ...
o_proj_0     9000  LOCAL  ...  LOCAL ... LOCAL ... ALLREDUCE  ...
```

のように、1 layerのcompute時間、tensor location、通信種別と通信量を記述する。

---

## 3. `comp_time`はどこから来るか

通常のNPU計算layerの`comp_time`は、profiler CSVから検索したlatencyである。

```text
profiler/perf/.../*.csv
    │
    ▼
trace_generatorのlookup
    │ microseconds → nanoseconds
    ▼
traceのcomp_time
```

Batch形状に応じて検索keyが変わる。

| Layer category | 主な検索key |
|---|---|
| dense | `total_len` |
| per-sequence | Request数 |
| attention | prefill chunk/KV、decode本数/KV |
| MoE | local token数、activated expert数 |

重要なのは、ASTRA-SimがGEMMなどの演算量から通常layerのlatencyを再計算している
わけではない点である。本構成では、profilerで測ったlatencyをCOMP nodeの実行時間
として再生する。

---

## 4. テキストtraceからChakra graphへ変換する

`graph_generator.generate_graph()`は、Chakra converterをsubprocessで呼ぶ。

```python
python -m chakra.src.converter.converter LLM \
  --input <trace.txt> \
  --output <workload>/llm \
  --num-npus <N> \
  --npu-offset <offset>
```

converterはNPUごとの`.et`ファイルを生成する。

```text
<workload path>/llm.0.et
<workload path>/llm.1.et
...
```

ASTRA-Simの`Workload`コンストラクタは、base pathへ`.<sys_id>.et`を付けて、
自分のNPU用graphを開く。

```cpp
string workload_filename =
    et_filename + "." + to_string(sys->id) + ".et";

this->et_feeder = new ETFeeder(workload_filename);
```

---

## 5. Chakra graphのnode種類

LLM converterは主に次のnodeを作る。

| Node | 意味 |
|---|---|
| `MEM_LOAD_NODE` | CPU/CXL/Storageなどからtensorを読み込む |
| `COMP_NODE` | layer計算を実行する |
| `MEM_STORE_NODE` | 計算結果を指定memoryへ書き出す |
| `COMM_COLL_NODE` | ALLREDUCE/ALLTOALLなどを実行する |
| `COMM_SEND_NODE` / `COMM_RECV_NODE` | point-to-point転送 |
| `PIM_COMP_NODE` | PIM memory上の計算 |

単にnodeを並べるだけではなく、親子依存関係を付ける。

```text
MEM_LOAD
    │
    ▼
qkv COMP
    │
    ▼
attention COMP
    │
    ▼
o_proj COMP
    │
    ▼
ALLREDUCE
    │
    ▼
次のlayer
```

converterでは、通信があるlayerについてCOMP nodeを親としてCOMM nodeを作る。

```python
comm_coll_node = self.get_comm_coll_node(...)
self.add_parent(comm_coll_node, comp_node)
```

この依存により、o_projのALLREDUCEがo_proj計算より先に始まることはない。

---

## 6. ASTRA-Simは依存が解けたnodeだけを発行する

各NPUの`Workload`は`ETFeeder`から、親nodeがすべて完了したnodeだけを取得する。

```cpp
node = et_feeder->getNextIssuableNode();

while (node != nullptr) {
    if (hw_resource->is_available(node)) {
        issue(node);
    } else {
        // 資源が空くまで戻す
        push_back_queue.push(node);
    }
    node = et_feeder->getNextIssuableNode();
}
```

発行可能でもcompute/communication/memory資源が使用中なら、nodeをissuable queueへ
戻す。これにより、graph依存とhardware resource availabilityの両方を満たすnode
だけが実行される。

```text
実行可能node
= 依存関係が解決済み
  かつ
  必要なhardware resourceが空いている
```

---

## 7. Node typeごとのdispatch

`Workload::issue()`はnode typeを見て処理を分ける。

```text
MEM_LOAD / MEM_STORE / PIM_COMP
    → issue_mem()

COMP
    → issue_comp()

COMM_COLL / SEND / RECV
    → issue_comm()
```

このdispatchが、Chakra graphとASTRA-Sim内部モデルの接続点である。

---

## 8. COMP nodeの実行

通常の設定では`issue_comp()`から`issue_replay()`へ進む。

```cpp
uint64_t runtime = node->runtime();
sys->register_event(
    this,
    EventType::General,
    wlhd,
    runtime
);
```

本リポジトリのconverterはtraceの`comp_time`をChakra nodeのduration fieldへ格納し、
ASTRA-Sim側はその値だけ未来の完了イベントを登録する。

```text
現在tick = T
COMP runtime = R

完了event時刻 = T + R
```

この経路ではCOMP時間はprofile latencyのreplayである。ASTRA-Simのanalytical
network modelがNPU kernel時間を独自推定するのではない。

`roofline_enabled`の場合は`num_ops / performance`でruntimeを計算する別経路があるが、
今回の通常実行の中心ではない。

---

## 9. Memory nodeの実行

`issue_mem()`はnodeの`tensor_loc`に応じてmemory APIを選ぶ。

```cpp
switch (node->tensor_loc()) {
case LOCAL_MEMORY:
    sys->local_mem->issue(node->tensor_size(), wlhd);
    break;
case REMOTE_MEMORY:
    sys->remote_mem->issue(node->tensor_size(), wlhd);
    break;
case CXL_MEMORY:
    sys->cxl_mem->issue(node->tensor_size(), wlhd);
    break;
case STORAGE_MEMORY:
    sys->storage_mem->issue(node->tensor_size(), wlhd);
    break;
}
```

memory APIへ渡す中心情報はtensor sizeとlocation/deviceである。memory側は生成済みの
`memory_expansion.json`や`system.json`にあるbandwidth/latency設定を使い、完了時に
Workloadへcallbackする。

通常構成では、最初のembedding入力を`REMOTE:<node_id>`から読むMEM_LOADと、最後の
sampler出力をREMOTEへ書くMEM_STOREが生成される。

```text
CPU/REMOTE
    │ MEM_LOAD
    ▼
NPU layer graph
    │ MEM_STORE
    ▼
CPU/REMOTE
```

そのため、traceの最初と最後のmemory locationが正しくREMOTEになっている必要がある。

---

## 10. Collective communicationの実行

`COMM_COLL_NODE`は`comm_type`によって、`Sys`のcollective generatorへ渡される。

```text
ALL_REDUCE    → generate_all_reduce()
ALL_TO_ALL    → generate_all_to_all()
ALL_GATHER    → generate_all_gather()
REDUCE_SCATTER → generate_reduce_scatter()
```

例えばALLREDUCE:

```cpp
DataSet* fp = sys->generate_all_reduce(
    node->comm_size(),
    involved_dim,
    comm_group,
    node->comm_priority()
);
```

ここから`Sys::generate_collective()`へ進み、`system.json`のdimensionごとのcollective
implementationと`network.yml`のtopology/bandwidth/latencyを使って通信イベントを
生成する。

`comm_size`はLLMServingSim側でtotal data sizeを渡す。ASTRA-Sim内部のcollective
algorithmがring等の参加node・phaseに応じてmessageを分割するため、Python側で
per-NPUへ割った値を渡してはいけない。

---

## 11. `involved_dim`の役割

複数次元topologyでは、collectiveがどのdimensionへ参加するかを指定する。

trace例:

```text
ALLREDUCE:1,0
ALLTOALL:0,1
```

converterは`:`より後ろをBoolListへ変換する。

```python
involved_dim = [v == '1' for v in dim_str.split(',')]
```

Chakra nodeには次のattributeとして保存される。

```python
ChakraAttr(
    name="involved_dim",
    bool_list=BoolList(values=involved_dim),
)
```

`Workload::issue_comm()`がこれを読み出し、collective generatorへ渡す。

```text
2次元topology [TP dimension, DP dimension]

TP ALLREDUCE
  involved_dim = [True, False]

DP/EP ALLTOALL
  involved_dim = [False, True]
```

これにより、同じ物理topology上でもcollectiveの参加範囲を限定できる。

---

## 12. 複数NPUで同じBatchを実行する仕組み

converterは同じlogical BatchからNPUごとの`.et`を生成する。各NPUは自分の
`sys_id`に対応するgraphを持つ。

```text
Python Batch 0
    │
    ▼
llm.0.et → ASTRA-Sim Sys 0
llm.1.et → ASTRA-Sim Sys 1
llm.2.et → ASTRA-Sim Sys 2
llm.3.et → ASTRA-Sim Sys 3
```

各graphは同じRequest集合を表すが、NPU rankに応じて担当expertなどのnode構成が
異なることがある。

Analytical backendは`start_npu_ids`をinstance controllerとして扱い、その
controller NPUが管理する他のNPUへ同じworkload base pathを渡す。

```cpp
systems[npu_id]->workload->add_workload(
    new_filename,
    managed_systems[idx]
);
```

各managed systemはbase pathに自分の`.sys_id.et`を付けて読み込む。

このC++側の配布と、Python Scheduler側の`Batch.fired`は別の役割である。

```text
Batch.fired
  → Python側で、どのNPUイベントへ同じBatchを応答済みか管理

NPUごとの.et + managed_systems
  → ASTRA-Sim側で、各NPU rankへ実行graphを配布
```

---

## 13. Node完了時の処理

COMP/MEM/COMMが完了すると`Workload::call()`へcallbackされる。

完了したnodeについて、

1. hardware resourceを解放
2. 子nodeの依存を解放
3. 新たに依存が解けたnodeを発行
4. 完了nodeをETFeederから削除

する。

```cpp
hw_resource->release(node);
et_feeder->freeChildrenNodes(node->id());
issue_dep_free_nodes();
et_feeder->removeNode(node->id());
```

collective完了時も同じ考え方で、対応するCOMM nodeの子を解放する。

したがってASTRA-Sim内部は、layer番号を単純なfor-loopで進めるのではなく、Chakra
依存グラフとイベントcallbackによって次のnodeを進める。

---

## 14. 1つのworkloadが完了する条件

次のすべてを満たしたとき、そのNPUのworkload iterationが完了する。

```cpp
!et_feeder->hasNodesToIssue()
&& num_in_flight_cpu_ops == 0
&& num_in_flight_gpu_comp_ops == 0
&& num_in_flight_gpu_comm_ops == 0
```

つまり、

```text
未発行nodeがない
かつ
実行中CPU opがない
かつ
実行中GPU computeがない
かつ
実行中GPU communicationがない
```

ことが完了条件である。

graph上の最後のCOMP nodeを発行しただけでは完了ではない。最後のnodeのcallbackまで
返り、in-flight resourceがすべて0になる必要がある。

---

## 15. 完了cycleがPythonへ返るまで

workload完了時、analytical backendは`report()`を呼び、次の形式をstdoutへ出す。

```text
sys[0] iteration 3 finished, 123456 cycles,
exposed communication 23456 cycles.
Waiting
```

`serving/core/controller.py`は`Waiting`が出るまでstdoutを読み、正規表現で値を取る。

```python
pattern = (
    r"sys\[(\d+)\] iteration (\d+) finished, "
    r"(\d+) cycles, exposed communication (\d+) cycles."
)
```

Pythonへ戻る値:

```python
{
    'sys': sys_id,
    'id': iteration_id,
    'cycle': completion_cycle,
}
```

この`cycle`が`serving/__main__.py`の`current`になり、Schedulerの`add_done()`へ
Batch完了時刻として渡される。

```text
ASTRA-Sim completion cycle
    │
    ▼
current
    │
    ▼
Scheduler.add_done(..., finish=current)
    │
    ├─ num_computed_tokens更新
    ├─ TTFT/ITL/latency更新
    └─ Requestを待ち行列またはdoneへ移動
```

---

## 16. PythonからASTRA-Simへ返すcommand

ASTRA-Simが`Waiting`を出すと、Pythonはstdinへ次のいずれかを書き込む。

| Command | 意味 |
|---|---|
| workload path | 次の`.et` graphを実行する |
| `pass` | 今回は新しいBatchを渡さない |
| `done` | このinstanceをsleepさせる |
| `exit` | simulator全体を終了する |

通常のBatchがある場合:

```text
Python → <workload base path>\n → ASTRA-Sim
```

ASTRA-Simはそのbase pathへNPU ID suffixを付け、次のgraphを読み込んで`fire()`する。

---

## 17. Queue時間とASTRA-Sim時間の境界

今回の結果解釈で最も重要な境界は次の通り。

```text
RequestがScheduler.requestに存在
    │
    │ queueing_before_ttft_ns / decode_queueing_ns
    │ Python側の待ち時間
    ▼
Batchへ採用、ASTRA-Simへgraphを投入
    │
    │ COMP + MEM + COMM graph実行時間
    │ ASTRA-Sim側の時間
    ▼
Batch完了cycle
```

ASTRA-Simは、RequestがSchedulerの待ち行列で何秒待つかを決定しない。
ASTRA-Simが計算するのは、採用済みBatchをgraphへ変換した後の実行時間である。

一方、PythonのSchedulerはcollectiveのring hopやnetwork latencyを計算しない。
これらはASTRA-Simへ渡したCOMM nodeから計算される。

---

## 18. 今回解析対象に含めないASTRA-Sim詳細

本シミュレータの処理を理解するため、以下は入口と役割のみ扱い、内部実装の全展開は
行わない。

- 各collective implementationのphase/state machine全体
- ring/treeごとのpacket生成式の全分岐
- congestion-aware backendの詳細なcontention model
- rendezvous protocolの全状態遷移
- ns-3/HTSim backend
- roofline mode
- PIM memory timingの内部モデル全体

必要になった場合は、通信精度を検証するときに
`Sys::generate_collective()`以下、memory精度を検証するときに各Memory API以下を
個別に追うのが適切である。

---

## 19. まとめ

LLMServingSimに必要な範囲でASTRA-Sim内部をまとめると、次の処理になる。

```text
1. Python Batchをlayer traceへ展開
2. traceをNPU別Chakra依存graphへ変換
3. 各NPUのETFeederが依存解決済みnodeを取得
4. node typeに応じてCOMP/MEM/COMMへdispatch
5. COMPはprofile latencyをevent時間としてreplay
6. MEMはlocation別memory APIへtensor sizeを渡す
7. COMMはsize/involved_dimをcollective generatorへ渡す
8. callbackで子nodeの依存を解放
9. graphとin-flight opがすべて空になればiteration完了
10. 完了cycleをstdout経由でPythonへ返す
```

したがって、シミュレーション結果の時間は大きく次の2層から構成される。

```text
Python scheduler/router
  → Requestのqueue待ちとBatch構成

ASTRA-Sim
  → 採用済みBatchのcompute/memory/communication実行時間
```

## 関連ファイル

- `serving/core/trace_generator.py` — Batchからlayer traceを生成
- `serving/core/graph_generator.py` — Chakra converterを起動
- `astra-sim/extern/graph_frontend/chakra/src/converter/llm_converter.py` — graph nodeと依存を生成
- `astra-sim/astra-sim/workload/Workload.cc` — graph nodeの発行・callback・完了判定
- `astra-sim/astra-sim/system/Sys.cc` — collective生成とevent登録
- `astra-sim/astra-sim/network_frontend/analytical/congestion_unaware/main.cc` — 対話ループ
- `serving/core/controller.py` — stdout解析とstdin command送信
- `kondoFolder/tutorial/detail/scheduler.md` — Batch構成と完了後のRequest更新
- `kondoFolder/tutorial/detail/kv_cache_capacity.md` — Batch採用時のKV容量判定
