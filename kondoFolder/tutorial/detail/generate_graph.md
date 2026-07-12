# `generate_graph()` 詳細解説

作成日: 2026-07-11  
対象関数: `serving/core/graph_generator.py:10` の `generate_graph()`  
関連実装:

- `serving/core/utils.py` の `get_workload()`
- `serving/core/run_paths.py` の `input_path()`
- `astra-sim/extern/graph_frontend/chakra/src/converter/converter.py`
- `astra-sim/extern/graph_frontend/chakra/src/converter/llm_converter.py`

`generate_graph()`は、`trace_generator.generate_trace()`が出力したlayer単位の
テキストtraceを、ASTRA-Simが読めるNPU別Chakra protobuf graph (`.et`)へ変換する。

---

## 1. このフェーズの目的（一言で）

**「人間が読めるlayer traceを、各NPUが実行する依存関係付きChakra graphへ
変換すること」**。

```text
Scheduler Batch
    │
    ▼
trace_generator
    │ layerごとのテキストtrace
    ▼
generate_graph
    │ Chakra converterをsubprocess起動
    ▼
LLMConverter
    │ NPUごとの依存graph
    ▼
llm.<npu_id>.et
    │
    ▼
ASTRA-Sim Workload
```

`generate_graph()`自体はgraph nodeを組み立てない。パスとcommandを構築し、
Chakra converterへ変換処理を委譲する薄いラッパーである。

---

## 2. なぜテキストtraceのまま渡さないのか

テキストtraceはlayerごとの属性を表すが、依存graphそのものではない。

```text
Layername
comp_time
input_loc / input_size
weight_loc / weight_size
output_loc / output_size
comm_type / comm_size
misc
```

ASTRA-Simが必要とするのは、次のようなnodeと依存関係である。

```text
MEM_LOAD_NODE
      │
      ▼
COMP_NODE(qkv_proj)
      │
      ▼
COMP_NODE(attention)
      │
      ▼
COMP_NODE(o_proj)
      │
      ▼
COMM_COLL_NODE(ALLREDUCE)
```

Chakra converterは、traceの各行をnodeへ変換し、`data_deps`で実行順序を表す。

---

## 3. 関数の引数

```python
def generate_graph(
    batch,
    hardware,
    num_npus,
    node_id=0,
    instance_id=0,
    npu_offset=0,
    enable_local_offloading=False,
    event=False,
    workload_name=None,
    inputs_root=None,
    cleanup_trace=True,
):
```

| 引数 | 意味 |
|---|---|
| `batch` | traceの対象Batch。event graphでは`None` |
| `hardware` | trace/output directory名に使うhardware名 |
| `num_npus` | このinstanceが使用するNPU数 |
| `node_id` | ログcontext用の物理node ID |
| `instance_id` | file名とログcontext用のinstance ID |
| `npu_offset` | instanceのlocal NPU IDをglobal NPU IDへずらすoffset |
| `enable_local_offloading` | converterへ`--local-offloading`を渡すか |
| `event` | 起動時event handler graphを作るか |
| `workload_name` | DP groupで共有するoutput folder名 |
| `inputs_root` | run固有のASTRA-Sim inputs root |
| `cleanup_trace` | 変換成功後に元の`.txt`を削除するか |

`node_id`はgraph構造の決定には直接使われず、debug logのcontextへ付けられる。

---

## 4. 入力・出力パスの構築

通常Batchでは、まず論理file名を作る。

```python
file_name = (
    f'{hardware}/{batch.model}/'
    f'instance{instance_id}_batch{batch.batch_id}'
)
```

例えば、

```text
hardware   = RTXPRO6000
model      = meta-llama/Llama-3.1-8B
instance   = 0
batch_id   = 12
```

なら、

```text
RTXPRO6000/meta-llama/Llama-3.1-8B/instance0_batch12
```

となる。

### 入力trace

```python
trace_path = input_path(
    inputs_root,
    "trace",
    f"{file_name}.txt",
)
```

実際の形:

```text
astra-sim/inputs/runs/<run_id>/trace/
  RTXPRO6000/meta-llama/Llama-3.1-8B/
    instance0_batch12.txt
```

### 出力workload base path

```python
output_path = input_path(
    inputs_root,
    "workload",
    output_name,
    "llm",
)
```

実際の形:

```text
astra-sim/inputs/runs/<run_id>/workload/
  RTXPRO6000/meta-llama/Llama-3.1-8B/
    instance0_batch12/llm
```

`llm`は単一fileではなく、converterがNPU ID suffixを付けるためのbase pathである。

```text
llm.0.et
llm.1.et
...
```

---

## 5. runごとに入力を分離する理由

`inputs_root`は通常、

```text
astra-sim/inputs/runs/<run_id>/
```

を指す。`run_id`未指定時はtimestampとprocess IDから生成される。

```python
run_<timestamp>_<pid>
```

これにより、複数simulationを同時実行しても、traceや`.et`、network/system/memory
configが同じ固定pathを上書きしない。

`input_path()`は`inputs_root`を絶対pathへ変換してから各要素を連結する。

---

## 6. Chakra converter commandの組み立て

`generate_graph()`は次のcommandを配列として作る。

```python
cmd = [
    'python',
    '-m', 'chakra.src.converter.converter',
    'LLM',
    '--input', trace_path,
    '--output', output_path,
    '--num-npus', str(num_npus),
    '--npu-offset', str(npu_offset),
]
```

shell文字列ではなく引数配列として`subprocess.run()`へ渡す。

概念的な実行command:

```bash
python -m chakra.src.converter.converter LLM \
  --input <trace-path> \
  --output <workload-base-path>/llm \
  --num-npus 4 \
  --npu-offset 0
```

local offloading有効時だけ追加する。

```python
if enable_local_offloading:
    cmd.append('--local-offloading')
```

---

## 7. subprocessのcwd

converterは、

```text
astra-sim/extern/graph_frontend/chakra/
```

をcwdとして起動する。

```python
subprocess.run(
    cmd,
    cwd=chakra,
    text=True,
    check=True,
)
```

`python -m chakra.src.converter.converter`を、このリポジトリに含まれるChakra fork
からimportさせるためである。

`check=True`なので、converterが非0で終了した場合は
`subprocess.CalledProcessError`となり、壊れたgraphのままsimulationを続けない。

---

## 8. `converter.py`から`LLMConverter`へのdispatch

commandの`LLM` subcommandにより、次が呼ばれる。

```python
def convert_llm(args):
    converter = LLMConverter(
        args.input,
        args.output,
        args.num_npus,
        args.npu_offset,
        args.local_offloading,
    )
    converter.convert()
```

したがって役割分担は、

```text
graph_generator.py
  → process/path管理

converter.py
  → CLI dispatch

llm_converter.py
  → trace解析とgraph生成本体
```

となる。

---

## 9. Trace先頭3行の解析

`LLMConverter.convert()`は最初の3行を特別扱いする。

```text
1行目: execution type + model parallel NPU group数
2行目: layer数
3行目: table header
```

例:

```text
COLOCATED    model_parallel_NPU_group: 1
291
Layername    comp_time    input_loc ...
```

1行目の`execution_type`でconverter処理を分岐する。

| execution type | 呼ばれる処理 |
|---|---|
| `COLOCATED` | `convert_common()` |
| `PREFILL` | `convert_prefill()` |
| `DECODE` | `convert_common()` |
| `EVENT` | `convert_event()` |

`COLOCATED`、`PREFILL`、`DECODE`では`model_parallel_NPU_group > 0`が必須。

---

## 10. Layer行のparse

通常layerは空白でsplitし、11列として読む。

```text
0  name
1  comp_time
2  input_memory_loc
3  input_memory_size
4  weight_memory_loc
5  weight_memory_size
6  output_memory_loc
7  output_memory_size
8  comm_type
9  comm_size
10 misc
```

`EXPERT`と`PIM`はmarker行として別のparseを行う。

不正な行は、

```text
Cannot parse the following layer -- "..."
```

という`ValueError`になる。

---

## 11. Memory locationの変換

traceのmemory location文字列をenumとdevice IDへ分解する。

```text
LOCAL      → LOCAL_MEMORY, device 0
REMOTE:0   → REMOTE_MEMORY, device 0
REMOTE:1   → REMOTE_MEMORY, device 1
CXL:0      → CXL_MEMORY, device 0
STORAGE:0  → STORAGE_MEMORY, device 0
```

converterはMEM nodeへ次を付ける。

```python
tensor_size
tensor_loc
tensor_device
```

PIM locationの`REMOTE:1.3`のような形式では、device 1、channel 3として分解する。

---

## 12. COMP nodeの生成

各layerの計算は`COMP_NODE`になる。

```python
node = self.get_node(
    "COMP_NODE_" + layer_name,
    COMP_NODE,
)
node.duration_micros = comp_time
```

field名は`duration_micros`だが、本リポジトリの処理系ではtraceの`comp_time`は
nanosecondsとして渡され、ASTRA-Sim側も変換せずruntimeとして再生する。

つまり通常のlayer compute時間は、profiler latencyをtrace generatorがnsへ変換した
値である。

---

## 13. Collective nodeの生成

traceの`comm_type`が`NONE`以外なら、`COMM_COLL_NODE`を作る。

```python
node.attr.append(
    ChakraAttr(name="comm_type", int64_val=...)
)
node.attr.append(
    ChakraAttr(name="comm_size", int64_val=comm_size)
)
```

対応する通信:

```text
ALLREDUCE    → ALL_REDUCE
ALLTOALL     → ALL_TO_ALL
ALLGATHER    → ALL_GATHER
REDUCESCATTER → REDUCE_SCATTER
```

通常はlayerのCOMP nodeを親として、通信nodeを後ろへ接続する。

```python
self.add_parent(comm_coll_node, comp_node)
```

```text
o_proj COMP
    │
    ▼
ALLREDUCE
    │
    ▼
次layer COMP
```

---

## 14. `involved_dim`の変換

multi-dimensional topology向けのtrace表現、

```text
ALLREDUCE:1,0
ALLTOALL:0,1
```

をparseする。

```python
comm_type, dim_str = s.split(':', 1)
involved_dim = [v == '1' for v in dim_str.split(',')]
```

Chakra nodeにはBoolList attributeとして保存する。

```python
ChakraAttr(
    name="involved_dim",
    bool_list=BoolList(values=involved_dim),
)
```

これをASTRA-Simの`Workload::issue_comm()`が読み、collectiveを実行するtopology
dimensionを限定する。

---

## 15. Graph dependencyの生成

`add_parent()`は、child nodeの`data_deps`へparent IDを追加する。

```python
def add_parent(child_node, parent_node):
    child_node.data_deps.append(parent_node.id)
```

converterはlayer列を順に処理し、主に次の依存を付ける。

```text
最初のinput MEM_LOAD
    → 最初のCOMP

前layerのCOMP/COMM
    → 次layerのCOMP

layer COMP
    → layer後のcollective

最後のCOMP/COMM
    → output MEM_STORE
```

KV load/evictがtrace先頭にある場合は、最初の計算nodeがそれらにも依存する。

この依存graphが、ASTRA-Sim内部でnodeを発行できる順序になる。

---

## 16. NPUごとの`.et`生成

`convert_common()`はmodel parallel groupとNPU数から、group当たりNPU数を計算する。

```python
npus_per_group = num_npus // num_npu_group
```

その後、groupとgroup内rankを走査する。

```python
for npu_group in range(num_npu_group):
    for npu_offset in range(npus_per_group):
        npu_id = (
            npu_group * npus_per_group
            + npu_offset
            + self.npu_offset
        )
```

output file:

```python
output_filename = f"{output_path}.{npu_id}.et"
```

例えば、

```text
num_npus  = 4
npu_offset = 0
```

なら、

```text
llm.0.et
llm.1.et
llm.2.et
llm.3.et
```

を作る。

---

## 17. `npu_offset`が必要な理由

複数instanceでは、それぞれのlocal NPU rankが0から始まる一方、ASTRA-Simでは
クラスタ全体のglobal NPU IDでfileを識別する。

```text
Instance 0
  local rank 0,1
  global NPU 0,1
  npu_offset = 0

Instance 1
  local rank 0,1
  global NPU 2,3
  npu_offset = 2
```

Instance 1について、

```text
local 0 + offset 2 = llm.2.et
local 1 + offset 2 = llm.3.et
```

となる。

`generate_graph()`の呼び出しでは通常、

```python
npu_offset = inst2npu_mapping[instance_id]
```

を渡す。これはinstanceとglobal NPU file名を接続する値である。

---

## 18. PP groupへのlayer割り当て

`num_npu_group`はtrace generatorが書くmodel parallel NPU group数で、converterは
layerをgroup間へ分割する。

```python
layers_per_group = num_layers // num_npu_group
remain_layers = num_layers % num_npu_group
```

余りlayerは先頭groupから1つずつ追加する。

group境界では、前groupのoutputを次groupへ渡す`COMM_SEND_NODE` / `COMM_RECV_NODE`
を生成する。

```text
PP group 0
  last COMP
    │
    ▼
  SEND
    │
    ▼
  RECV
    │
    ▼
PP group 1
  first COMP
```

---

## 19. `event=True`の特別処理

ASTRA-Sim起動前には通常Batchではなくevent handler graphを作る。

```python
generate_graph(
    None,
    None,
    total_npu,
    event=True,
    ...,
)
```

file名は固定で、

```text
trace/event_handler.txt
workload/event_handler/llm.<npu_id>.et
```

になる。

trace先頭のexecution typeは`EVENT`で、`convert_event()`が全NPUについてevent用
COMP nodeを生成する。この初期graphによりASTRA-Simが最初のarrival/log interval
まで進み、Pythonとの対話ループを開始できる。

---

## 20. DP group時の`workload_name`

通常はinstanceごとに別workload directoryを使う。

```text
instance0_batch3/llm.*.et
instance1_batch4/llm.*.et
```

DP+EP同期では、複数instanceの`.et`を同じworkload folderへ置く必要がある。

```python
dp_workload_name = (
    f'{hardware}/{model}/dp_{dp_group}_batch{batch_id}'
)
```

各instanceについて入力traceは固有の`file_name`から読むが、出力だけ共有
`workload_name`へ書く。

```text
入力:
  instance0_batch3.txt
  instance1_batch4.txt

出力:
  dp_groupA_batch3/
    llm.0.et
    llm.1.et
    llm.2.et
    llm.3.et
```

global NPU ID suffixが異なるため、同じbase pathへ安全に共存できる。これにより
DP group memberがmatching collective streamへ参加できる。

---

## 21. `get_workload()`との対応

`generate_graph()`は戻り値としてpathを返さない。変換後、`utils.get_workload()`が
同じ規則でworkload base pathを再構築する。

```python
workload = get_workload(
    batch,
    hardware,
    instance_id,
    workload_name=...,
    inputs_root=...,
)
```

返るのは、

```text
.../workload/<name>/llm
```

というbase pathであり、`.0.et`などの個別fileではない。

Pythonはこのbase pathをASTRA-Simのstdinへ送る。ASTRA-Sim側の各`Workload`が
自分の`sys_id`をsuffixとして付け、対応する`.et`を読む。

---

## 22. 元のtraceのcleanup

変換成功後、`cleanup_trace=True`ならテキストtraceを削除する。

```python
if cleanup_trace:
    try:
        os.remove(trace_path)
    except FileNotFoundError:
        pass
```

削除対象は入力`.txt`だけであり、生成した`.et`はASTRA-Simがこの後読むため残す。

converterが失敗した場合は`subprocess.run()`が例外を投げるため、このcleanup処理には
到達しない。失敗時の入力traceはdebug材料として残る。

`--no-cleanup-inputs`相当の設定ではtraceを残し、converter入力を直接確認できる。

---

## 23. エラーが表面化する場所

このフェーズで代表的に起きるエラー:

### 入力traceが存在しない

`LLMConverter`の`open(input_filename)`で`FileNotFoundError`。

### trace行の列数・型が不正

`Layer` constructorが`ValueError`。

### execution typeが不正

`Unsupported execution type`の`ValueError`。

### model parallel groupが0以下

COLOCATED/PREFILL/DECODEで`ValueError`。

### Chakra protobufのversion不整合

converter subprocess内のimport時にprotobuf `VersionError`などが発生する。

### converterが非0終了

親Pythonでは`CalledProcessError`として表面化する。

`generate_graph()`は例外を握り潰さないため、graph欠損を後段ASTRA-Simの不可解な
エラーへ持ち越さない。

---

## 24. ASTRA-Sim内部との境界

この関数が完了した時点では、まだsimulation時間は進んでいない。

```text
generate_graph()完了
  = .et file生成完了
  ≠ Batch実行完了
```

後続処理は、

```text
get_workload()
    │ base path取得
    ▼
controller.write_flush()
    │ stdinへpath送信
    ▼
ASTRA-Sim Workload
    │ .<sys_id>.etを読込
    ▼
依存graphをイベント実行
    │
    ▼
完了cycleをstdoutへ出力
```

ASTRA-Sim内部のCOMP/MEM/COMM dispatchと完了条件は、
[`astra_sim_execution.md`](./astra_sim_execution.md)を参照。

---

## 25. 今回のsingle-instance構成での具体例

```text
Instance 0
hardware   = RTXPRO6000
model      = meta-llama/Llama-3.1-8B
num_npus   = 1
npu_offset = 0
batch_id   = 5
```

入力trace:

```text
<inputs_root>/trace/RTXPRO6000/meta-llama/Llama-3.1-8B/
  instance0_batch5.txt
```

converter command:

```bash
python -m chakra.src.converter.converter LLM \
  --input <inputs_root>/trace/RTXPRO6000/meta-llama/Llama-3.1-8B/instance0_batch5.txt \
  --output <inputs_root>/workload/RTXPRO6000/meta-llama/Llama-3.1-8B/instance0_batch5/llm \
  --num-npus 1 \
  --npu-offset 0
```

生成物:

```text
<inputs_root>/workload/RTXPRO6000/meta-llama/Llama-3.1-8B/
  instance0_batch5/
    llm.0.et
```

ASTRA-Simへ送るpath:

```text
<inputs_root>/workload/RTXPRO6000/meta-llama/Llama-3.1-8B/
  instance0_batch5/llm
```

ASTRA-Sim Sys 0が`llm.0.et`を開いて実行する。

---

## 26. まとめ

`generate_graph()`を一言でまとめると、

> **Batchから生成済みのlayer traceについて入出力pathとNPU配置を解決し、Chakra
> LLMConverterをsubprocessで起動して、ASTRA-Sim用のNPU別依存graphへ変換する処理**

重要点は次の通り。

1. `generate_graph()`自体は薄いsubprocess wrapperで、graph生成本体は`LLMConverter`
2. output pathはbase pathで、実fileは`llm.<global_npu_id>.et`
3. `npu_offset`がinstance local rankとglobal NPU IDを接続する
4. converterはCOMP/MEM/COMM nodeと`data_deps`を生成する
5. DP group時は`workload_name`を共有し、複数instanceの`.et`を同じfolderへ置く
6. 変換後のbase pathを`get_workload()`がASTRA-Simへ渡す
7. `.et`生成は実行準備であり、実際のcycle進行はその後のASTRA-Sim内部で行われる

## 関連ファイル

- `serving/core/graph_generator.py` — 本解説の入口
- `serving/core/trace_generator.py` — 入力テキストtraceの生成
- `serving/core/utils.py` — `get_workload()`とtrace formatter
- `serving/core/run_paths.py` — run固有pathの構築
- `astra-sim/extern/graph_frontend/chakra/src/converter/converter.py` — CLI dispatch
- `astra-sim/extern/graph_frontend/chakra/src/converter/llm_converter.py` — graph生成本体
- `kondoFolder/tutorial/detail/astra_sim_execution.md` — 生成graphのASTRA-Sim内部実行
- `kondoFolder/tutorial/detail/scheduler.md` — graphへ変換するBatchの構築
