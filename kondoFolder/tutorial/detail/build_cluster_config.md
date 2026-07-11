# `build_cluster_config()` 詳細解説

作成日: 2026-07-09
対象関数: `serving/core/config_builder.py:273` の `build_cluster_config()`
対象コマンド:

```bash
python -m serving \
  --cluster-config 'configs/cluster/single_node_single_instance.json' \
  --block-size 16 \
  --dataset 'workloads/example_trace.jsonl' \
  --output 'outputs/example_single_run.csv' \
  --log-interval 1.0
```

`serving/__main__.py`の`main()`が最初に呼ぶ大物関数がこれ(`__main__.py:380`)。
以降のScheduler/Controller/Router構築、メインループのすべてが、この関数の
戻り値(`cluster`辞書)に依存している。

---

## 1. このフェーズの目的(一言で)

**「人間が書いた簡潔なクラスタ定義(JSON)」を、次の2つに変換すること。**

1. **ASTRA-Simがそのまま読める入力ファイル群**(`network.yml` / `system.json` /
   `memory_expansion.json`)
2. **Python側のメインループが以後ずっと参照する「解決済みの対応表」**
   (`cluster`辞書。instance↔node↔NPU番号の対応、並列度、メモリ配置など)

ポイントは「解決済み」という言葉。元のJSONは`tp_size`だけ書いて`pp_size`を
省略する、といった**省略ありの定義**になっている。このフェーズで、省略された
値を全部推論・検証して確定させたうえで、確定値をASTRA-Sim用ファイルに
書き写す。

言い換えると、この関数は「1つの入力(クラスタ設定JSON)を、複数の出力
(`cluster`辞書 + ASTRA-Sim用3ファイル)に変換する、ただのコンパイル処理」
だと捉えると全体像が掴みやすい。

---

## 2. 題材にする設定ファイル

以降の説明はすべて、この最小構成(GPU1台・model 1つ)を実際に流し込んだ
場合の**具体的な値**で追う。

```json
{
    "num_nodes": 1,
    "link_bw": 16,
    "link_latency": 20000,
    "nodes": [
        {
            "num_instances": 1,
            "cpu_mem": {
                "mem_size": 512,
                "mem_bw": 256,
                "mem_latency": 0
            },
            "instances": [
                {
                    "model_name": "meta-llama/Llama-3.1-8B",
                    "hardware": "RTXPRO6000",
                    "npu_mem": {
                        "mem_size": 96,
                        "mem_bw": 1597,
                        "mem_latency": 0
                    },
                    "num_npus": 1,
                    "tp_size": 1,
                    "pd_type": null
                }
            ]
        }
    ]
}
```

(`configs/cluster/single_node_single_instance.json`)

### 各行の意味(注釈付き版)

※ JSONは本来コメントを書けないため、以下は**説明用に`//`コメントを
足した非正規のJSON**(そのままではパースできない)。実際のファイルは
上のコメント無し版を参照。

```jsonc
{
    "num_nodes": 1,          // クラスタを構成する物理ノード(サーバ)の台数。
                             // 下の "nodes" 配列の要素数と一致していないとエラーになる
    "link_bw": 16,           // ノード間(トポロジ上の各次元)のリンク帯域。GB/s→Gbpsに変換するときは8倍が必要
                             // そのまま astra-sim/inputs/.../network.yml の bandwidth に転写される
    "link_latency": 20000,   // 同リンクの伝搬遅延(ns)。network.yml の latency に転写される
                             //これら２つがGPU間の遅延や帯域を表す。
    "nodes": [               // ノードごとの設定を並べた配列(要素数 = num_nodes)
        {
            "num_instances": 1,   // このノードに配置する「instance」(モデルのデプロイ単位)の数。
                                  // 下の "instances" 配列の要素数と一致していないとエラーになる
            "cpu_mem": {          // このノードのCPU側(リモート)メモリの設定。　cpuとcpuのdramとかの間の通信速度。
                                  // KVキャッシュのオフロード先などに使われる想定
                "mem_size": 512,      // 容量(GB)。cluster["cpu_mem_size"]としてPython側に保持される
                "mem_bw": 256,        // 帯域(GB/s)。
                                      // memory_expansion.json の "remote_mem" の "mem-bw" にそのまま転写される
                "mem_latency": 0      // レイテンシ(ns)。同じく "remote_mem" の "mem-latency" に転写される
            },
            "instances": [        // このノードに載せる「instance」(モデル1個分のデプロイ)の配列。
                                  // 要素数 = num_instances
                {
                    "model_name": "meta-llama/Llama-3.1-8B",
                        // HuggingFaceのモデル名。
                        // configs/model/meta-llama/Llama-3.1-8B.json を読みに行くための鍵になる
                    "hardware": "RTXPRO6000",
                        // プロファイル済みハードウェア名。
                        // profiler/perf/RTXPRO6000/ ディレクトリと一致していないと、
                        // trace_generator が「プロファイルデータが見つからない」とエラーになる
                    "npu_mem": {   // このinstanceが使うNPU(GPU)1枚あたりのメモリ設定　gpuとgpuメモリ（HBM）との通信速度
                        "mem_size": 96,     // 容量(GB)。KVキャッシュ等の空き容量計算に使われる
                        "mem_bw": 1597,     // 帯域(GB/s)。
                                            // astra-sim/inputs/.../system.json の "local-mem-bw" に上書きされる
                        "mem_latency": 0    // レイテンシ(ns)
                    },
                    "num_npus": 1,   // このinstanceが使うGPUの枚数
                    "tp_size": 1,    // テンソル並列度。num_npus / tp_size から pp_size が推論される
                                     // (今回はどちらも1なので pp_size=1 に決まる)
                    "pd_type": null  // Prefill/Decode分離構成での役割。
                                     // null = 一体型(COLOCATED、prefillもdecodeも同じGPUで行う)
                                     // "prefill" または "decode" を指定すると分離構成になる
                }
            ]
        }
    ]
}
```

---

## 3. ステップ1: JSONの読み込みとバリデーション

```python
def build_cluster_config(astra_sim, cluster_config_path, enable_local_offloading=False,
                          enable_attn_offloading=False, inputs_root=None):
    cluster_config_path = f'../{cluster_config_path}' # move out from astra-sim folder

    with open(cluster_config_path, 'r') as f:
        cluster_config = json.load(f)

    inputs_root, network_config_path, system_config_path, memory_config_path = (
        _prepare_input_config_paths(astra_sim, inputs_root)
    )

    num_nodes = cluster_config["num_nodes"]
    nodes = cluster_config["nodes"]

    if len(nodes) != num_nodes:
        raise ValueError(...)
    if cluster_config.get("link_bw") is None or cluster_config.get("link_latency") is None:
        raise KeyError(...)
```

ここで最初に注目すべき点が2つある。

1. **`cluster_config_path = f'../{cluster_config_path}'`** — `main()`が既に
   `os.chdir("astra-sim")`を実行済みなので、リポジトリルートにある
   `configs/cluster/...json`を読むには一段上に上がる必要がある。CLAUDE.mdの
   「working directory」の注意点(「`serving/__main__.py`はastra-sim/へcwdを
   変更する。全ての相対パスはastra-sim/基準で解決される」)がここで実際に
   現れている箇所。
2. **`num_nodes`と`len(nodes)`の突合せ** — JSONの`"num_nodes": 1`という数字と、
   実際に`"nodes": [...]`配列に何個要素があるかを照合するだけの、単純な
   整合性チェック。人為的なコピペミス(nodeを1つ増やしたのに`num_nodes`を
   書き換え忘れる、など)を早期に検出するためのガード。

`_prepare_input_config_paths()`は、`inputs_root`(既定では
`astra-sim/inputs/runs/<run_id>/`)配下に`network/`・`system/`・`memory/`の
各ディレクトリを作り、既定の`system.json`テンプレートをそこにコピーしておく
だけの下ごしらえ関数。

---

## 4. ステップ2: power設定の有無チェック(今回はスキップされる)

```python
required_keys = ["num_instances", "cpu_mem", "instances"]
power_modeling = True
for node_config in nodes:
    if power_modeling and "power" not in node_config:
        power_modeling = False
    for key in required_keys:
        if key not in node_config:
            raise KeyError(...)

if power_modeling:
    # base_node_power / npu / cpu / dram / link / nic / storage の
    # 必須キーをそれぞれ検証する、長いバリデーションブロック
    ...
```

今回の`node_config`には`"power"`キーが無いので、このループの1回目で
`power_modeling`は`False`になる。**1つのノードでも`"power"`が欠けていれば、
クラスタ全体で電力モデリングを無効化する**という「全か無か」の判定方式に
なっている。今回はこれにより、その直後にある電力設定の必須キー検証ブロック
(`base_node_power` / `npu` / `cpu` / `dram` / `link` / `nic` / `storage`の
存在確認)が丸ごとスキップされる。実行ログに電力関連の表示が一切
出ていなかったのはこのため。

---

## 5. ステップ3: instanceごとのループ(最重要パート)

### 5.1 `get_config()` + `_resolve_parallelism()` — 並列度を確定させる

```python
model_config = get_config(instance["model_name"])   # configs/model/meta-llama/Llama-3.1-8B.json を読む
_resolve_parallelism(instance, model_config)
```

`_resolve_parallelism`(`config_builder.py:55`)は、JSONに書かれた部分的な
情報から残りを推論する関数。今回のinstanceは`num_npus=1, tp_size=1,
pd_type=null`としか書かれておらず、`pp_size`も`ep_size`も省略されている。

推論の流れ:

1. `is_moe`判定: Llama-3.1-8Bの設定に`num_local_experts`/`num_experts`キーが
   無いので`False`(dense modelとして扱う)
2. `pp_size`: 未指定なので既定値`1`を採用
3. `num_npus(1)`と`tp_size(1)`が両方指定済みのケース →
   `num_npus == tp_size * pp_size`(`1 == 1*1`)を検証、一致するのでそのまま
4. `ep_size`: 未指定・dense modelなので`1`(MoEなら`tp_size`と同じ値になる
   ルール)
5. 最終検証: `num_npus != tp_size*pp_size`でないか、各並列度が1以上か、
   `dp_group`が無いのに`ep_size > tp_size`になっていないか、をチェックして
   すべてOK

結果、`instance`辞書に`tp_size=1, pp_size=1, ep_size=1, dp_group=None`が
**書き戻される**。この関数は戻り値だけでなく、**引数の`instance`辞書を
直接書き換える副作用**を持つ点に注意(Pythonの可変オブジェクト渡しを
利用した、やや暗黙的な設計)。

もし仮に`num_npus=4, tp_size=2`だけを指定していたら、`pp_size`は
`num_npus // tp_size = 2`と自動推論される。「一部の情報だけ書けば残りは
埋めてくれる」という、CLAUDE.mdに書かれている「Parallelism inference」の
実装本体がここ。

### 5.2 対応表(mapping)の構築

```python
instance["node_id"] = node_id          # 0
instance["instance_id"] = inst_id      # 0
inst2node_mapping[inst_id] = node_id   # {0: 0}
inst_id += 1
```

この時点では「instance 0はnode 0に属する」という対応しか作られない。
GPU番号(NPU番号)との対応は、後段の別ループ(`config_builder.py:502`以降、
`for idx, instance in enumerate(instances):`)で作られる。

```python
inst2npu_mapping[instance_id] = current_npu_start   # {0: 0}  ← instance 0 は NPU 0番から始まる
start_npu_ids += str(current_npu_start) + ","        # "0,"
...
for npu_id in range(current_npu_start, current_npu_start + effective_npus):
    npu2inst_mapping[npu_id] = instance_id           # {0: 0}  ← NPU 0番は instance 0のもの
current_npu_start += effective_npus                  # 次のinstanceがあればNPU 1番から
```

GPUが1台だけなのでどちらも`{0: 0}`という自明な対応になるが、複数instance
構成だとここで「instance 1はNPU 1番から」のように積み上がっていく。

`effective_npus`は基本`num_npus`と同じだが、`pd_type == "prefill"`
(Prefill/Decode分離構成)の場合だけ`num_npus * 2`になる点に注意。これは
Prefill instanceが「送信元」として2倍のNPU番号帯を占有する扱いになって
いるため(`prefill_instance`リストにも追加される)。今回は`pd_type=null`
なのでこの分岐には入らない。

### 5.3 `placement`(重み・KVキャッシュの置き場所)の構築

```python
placement_cfg = (instance or {}).get("placement") or {}   # 今回は {} (未指定)

default_cfg = placement_cfg.get("default") or {}
d_weights = _mem_str(default_cfg.get("weights", "npu"), node_id)      # "LOCAL"
d_kv      = _mem_str(default_cfg.get("kv_loc", "npu"), node_id)       # "LOCAL"
d_evict   = _mem_str(default_cfg.get("kv_evict_loc", "cpu"), node_id) # "REMOTE:0"
```

`placement`をJSONで何も指定しなければ、既定で「重みもKVキャッシュもNPU上
(`LOCAL`)、追い出す時だけCPU(`REMOTE:0`)へ」という設定になる。

```python
inst_placement = {
    "default": {"weights": "LOCAL", "kv_loc": "LOCAL", "kv_evict_loc": "REMOTE:0"},
    "block": [],   # レイヤー範囲ごとの上書き(今回は無し)
    "layer": {},   # レイヤー名ごとの上書き(今回は無し)
}
placement.append(inst_placement)
block_mode_on.append(False)
```

これが`instance`辞書ではなく、`cluster`辞書の`placement`リスト(instance番号
で引ける)に積まれる。もしJSONに以下のような指定があれば、レイヤーごとに
違う置き場所(一部の層の重みだけCXLに逃がす、など)を細かく指定できる。

```json
"placement": {
    "default": {"weights": "npu", "kv_loc": "npu", "kv_evict_loc": "cpu"},
    "blocks": [
        {"blocks": "0-15", "weights": "cpu"}
    ],
    "layers": {
        "lm_head": {"weights": "cxl"}
    }
}
```

これがこのリポジトリの重み/attentionオフロード実験(CLI引数
`enable_local_offloading`/`enable_attn_offloading`と対になる仕組み)の
入り口にあたる。優先順位は**layer指定 > block指定 > default**
(`get_device()`関数、`config_builder.py:754`)。

---

## 6. ステップ4: `cpu_mem`/`npu_mem` → ASTRA-Sim側ファイルへの反映

### 6.1 `cpu_mem` → `remote_mem`

```python
cpu_mem = node_config["cpu_mem"]   # {mem_size: 512, mem_bw: 256, mem_latency: 0}
...
if not cpu_mem_enabled:
    memory_config["remote_mem"] = {
        "memory-type": "PER_NODE_MEMORY_EXPANSION",
        "mem-bw": cpu_mem["mem_bw"],       # 256
        "mem-latency": cpu_mem["mem_latency"],  # 0
        "num-devices": num_nodes,          # 1
    }
    cpu_mem_enabled = True
```

`cpu_mem`の`mem_bw=256`が、そのままASTRA-Sim側の「リモート(CPU)メモリ帯域」
としてASTRA-Simの`memory_expansion.json`に転写される。

`enable_attn_offloading`が有効な場合はこの前段で分岐があり、`cpu_mem`に
`pim_config`キーが必須になり、`PIMModel`経由でDRAM設定(PIMデバイスの
帯域・レイテンシ)が`cpu_mem["mem_bw"]`/`["mem_latency"]`を**上書き**する
(`config_builder.py:436-459`)。今回は`enable_attn_offloading=False`なので
素通り。

### 6.2 `npu_mem` → `system.json`の`local-mem-bw`

```python
npu_mem = instance.get("npu_mem")   # {mem_size: 96, mem_bw: 1597, mem_latency: 0}
...
if not npu_mem_enabled:
    with open(system_config_path) as f:
        system_config = json.load(f)
    system_config["local-mem-bw"] = int(npu_mem["mem_bw"])   # 1597
    with open(system_config_path, "w", encoding="utf-8") as f:
        json.dump(system_config, f, ensure_ascii=False, indent=2)
    npu_mem_enabled = True
```

`npu_mem`の`mem_bw=1597`が、既にコピー済みの`system.json`の
`local-mem-bw`フィールドに上書きされる。ASTRA-SimはこのASTRA-Sim側の
`system.json`だけを見ているので、Python側のクラスタ設定JSONの数値は
**この時点で失われずに、正しくASTRA-Simへ橋渡しされている**ことになる。

`npu_mem_enabled`というフラグ名からも分かる通り、**このコードは
「1種類のNPUメモリ設定しかサポートしていない」**(複数instanceがあっても
最初のinstanceの値で`system.json`が一度だけ書き換えられ、以降は無視される)。
異なるNPUメモリ帯域を持つinstanceを混在させる構成では、この制約に
注意が必要。

つまりこの段階で、**クラスタ設定JSONの`cpu_mem`/`npu_mem`の数値が、
ASTRA-Simの「リモートメモリ帯域」「ローカルメモリ帯域」としてそのまま
転写される**ことが分かる。

---

## 7. ステップ5: `_resolve_dp_groups()` — 今回は「何もしない」ことを確認するのが大事

```python
dp_groups = {}
for inst in all_instances:
    dg = inst.get("dp_group")
    if dg is not None:
        dp_groups.setdefault(dg, []).append(inst)
```

今回のようにどのinstanceにも`dp_group`が設定されていない場合、`dp_groups`は
空のままで、後続の「DPグループごとのtp_size/ep_size整合性チェック」ループは
実質何もしない。

ただし内部で`_compute_network_dims(instances)`を呼び、「ASTRA-Simの
トポロジは何次元にすべきか」を計算している。

```python
# dp_groups が空 → else分岐(独立instance構成)
total_npu = 1    # pd_type != "prefill" なので num_npus そのまま
total_pp = 1
num_instances = 1  # len(instances) + prefill補正(0)
# total_npu == total_pp なので
npus_per_group = total_npu // num_instances  # 1
dims = [1, 1]
# 末尾の1を削除するルール
dims = [1]
```

GPU1台だけなので次元は最終的に`[1]`(1次元、NPU1個)になる。

```python
for inst in all_instances:
    if inst.get("dp_group") is None:
        inst["dp_group_size"] = 1
        inst["local_ep"] = inst["ep_size"]   # 1
        inst["ep_total"] = inst["ep_size"]   # 1
        inst["tp_dim"] = local_dim           # None (次元数が1つしか無いため)
        inst["ep_dim"] = None                # ep_size=1のため
```

DPグループが実際に設定されているケース(複数instanceが同じ`dp_group`
文字列を共有する場合)では、ここで`tp_size`/`ep_size`の一致検証、
`ep_total`(グループ全体のEP次数)と`local_ep`(1instanceあたりの担当expert数)
の算出、ALLREDUCE/ALLTOALLをどのトポロジ次元に限定するか(`tp_dim`/`ep_dim`)
の決定が行われる。CLAUDE.mdの「DP+EP wave synchronization」の前提となる
情報がここで作られている。

---

## 8. ステップ6: `system.json`のcollective実装をトポロジ次元数に合わせる

```python
def _sync_system_collective_dims(system_config_path, instances):
    num_dims = len(_compute_network_dims(instances))   # 1
    for key in _COLLECTIVE_IMPL_KEYS:
        # ["all-reduce-implementation", "all-gather-implementation",
        #  "reduce-scatter-implementation", "all-to-all-implementation"]
        system_config[key] = ["ring"] * num_dims        # ["ring"]
```

ASTRA-Simの`system.json`は、collective通信(ALLREDUCE等)の実装方式を
**トポロジの次元数と同じ長さの配列**で指定する仕様になっている。DP+EPの
ような2次元トポロジでは`["ring", "ring"]`のように2要素になる。今回は
1次元なので`["ring"]`の1要素に揃えられる。

---

## 9. ステップ7: `network.yml`の生成

```python
def _create_network_config(network_config_path, instances, link_bw, link_latency):
    dims = _compute_network_dims(instances)   # [1]
    num_dims = len(dims)                       # 1
    topology_data = {
        "topology": ["FullyConnected"] * num_dims,          # ["FullyConnected"]
        "npus_count": dims,                                  # [1]
        "bandwidth": _normalize_network_dim_values(link_bw, num_dims, "link_bw"),      # [16.0]
        "latency": _normalize_network_dim_values(link_latency, num_dims, "link_latency"),  # [20000.0]
    }
    yaml.dump(topology_data, ...)
```

GPU1台なので通信トポロジ自体は事実上使われないが、形式上「1次元・
NPU1個・帯域16・遅延20000」というネットワーク定義ファイル(`network.yml`)が
生成される。`_normalize_network_dim_values`は、`link_bw`/`link_latency`が
単一の数値でもリスト(次元ごとに異なる値)でも受け付けられるように
正規化する役割を持つ(DP+EP構成でTP次元とDP次元で帯域が異なる場合などに
使われる)。

---

## 10. ステップ8: `memory_expansion.json`の書き出しと検証

### 10.1 書き出し

この時点の`memory_config`(ステップ4で組み立てたもの)をそのまま
JSONファイルとして書き出す。

```json
{
  "remote_mem": {
    "memory-type": "PER_NODE_MEMORY_EXPANSION",
    "mem-bw": 256,
    "mem-latency": 0,
    "num-devices": 1
  }
}
```

(`enable_local_offloading=False`なので`local_mem`キーは無し。`cxl_mem`も
クラスタ設定JSONに無いので無し)

### 10.2 `_validate_memory_config()` による検証

```python
allowed = set()
for mem_type, mem_details in memory_config.items():
    num_devices = int(mem_details.get("num-devices", 1))   # 1
    prefix = mem_type.split('_')[0].upper()                 # "remote_mem" -> "REMOTE"
    for i in range(num_devices):
        allowed.add(f"{prefix}:{i}")                        # {"REMOTE:0"}
```

そして`placement`で使われている場所(`LOCAL`, `LOCAL`, `REMOTE:0`)がこの
`allowed`集合と矛盾しないか確認する。

```python
def _ok(loc):
    loc_n = _norm(loc)   # 大文字化 + ":0"サフィックス付与
    if loc_n.startswith("LOCAL") and not enable_local_offloading:
        return True   # ← LOCALは無条件でOK(local offloading無効時)
    return loc_n in allowed
```

`LOCAL`は`enable_local_offloading=False`の間は**常に無条件でOK**になる。
これはCLAUDE.mdに明記されている仕様

> memory_expansion.json only has remote_mem by default — local_mem is not
> configured unless --enable-local-offloading is used; weight loads from
> LOCAL go through compute time, not memory

そのままの実装で、「NPU上の重みロードはメモリモデルを経由せず計算時間の
中に織り込まれるため、`memory_expansion.json`に対応エントリが無くても
問題ない」という判断がここに現れている。

`REMOTE:0`は`allowed`に含まれているのでOK。もし仮に`kv_evict_loc`を
`"cxl"`に指定していたら、`cxl_mem`をクラスタ設定に書いていない限り
`allowed`に`"CXL:0"`が存在せず、ここで`ValueError`になる
(「意図しない配置ミスを実行前に落とす」ためのガード)。

---

## 11. 最終的に返る`cluster`辞書(今回の値)

```python
{
    "num_nodes": 1,
    "num_instances": 1,
    "instances": [
        {
            "model_name": "meta-llama/Llama-3.1-8B",
            "hardware": "RTXPRO6000",
            "npu_mem": {"mem_size": 96, "mem_bw": 1597, "mem_latency": 0},
            "num_npus": 1, "tp_size": 1, "pp_size": 1, "ep_size": 1,
            "dp_group": None, "dp_group_size": 1, "local_ep": 1, "ep_total": 1,
            "tp_dim": None, "ep_dim": None,
            "node_id": 0, "instance_id": 0, "pd_type": None,
        }
    ],
    "inst2node_mapping": {0: 0},
    "inst2npu_mapping": {0: 0},
    "npu2inst_mapping": {0: 0},
    "prefill_instance": [],
    "decode_instance": [],
    "start_npu_ids": "0,",
    "end_npu_ids": "",
    "placement": [
        {"default": {"weights": "LOCAL", "kv_loc": "LOCAL", "kv_evict_loc": "REMOTE:0"},
         "block": [], "layer": {}}
    ],
    "block_mode_on": [False],
    "total_npu": 1,
    "cpu_mem_size": [512],
    "cxl_mem_size": 0,
    "power_modeling": False,
    "power_configs": [],
    "pim_models": [None],
    "link_bw": 16,
    "link_latency": 20000,
    "inputs_root": ".../astra-sim/inputs/runs/<run_id>",
    "network_config_path": ".../network/network.yml",
    "system_config_path": ".../system/system.json",
    "memory_config_path": ".../memory/memory_expansion.json",
}
```

`prefill_instance`/`decode_instance`が両方空リストなのは、`pd_type=null`
(Prefill/Decode一体型の"COLOCATED"構成)であるため。もしPD分離構成なら、
それぞれ該当するinstance_idが入る。

---

## 12. まとめ

このフェーズを一言でまとめると、

> **JSONの省略された部分を全部埋めて確定させながら、その過程で得られた
> 数値(帯域・レイテンシ・メモリサイズ)を、そのままASTRA-Sim用の3ファイル
> に書き写していく処理**

バリデーションとファイル生成が交互に出てくるので読みにくく見えるが、
実質は「1つの入力(クラスタ設定JSON)を複数の出力(`cluster`辞書 +
ASTRA-Sim入力3ファイル)に変換する、ただのコンパイル処理」だと捉えると
分かりやすい。

この後、`__main__.py`はこの`cluster`辞書の値(`instances`,
`inst2npu_mapping`など)を使って、instanceごとに`Scheduler`を1つずつ
構築していく(`__main__.py:494-521`)。

## 関連ファイル

- `serving/core/config_builder.py` — 本解説の対象
- `serving/core/utils.py` — `get_config()`(モデル設定の読み込み)
- `serving/core/pim_model.py` — `PIMModel`(attention offloading有効時の
  DRAM設定上書き)
- `configs/cluster/single_node_single_instance.json` — 本解説で題材にした
  設定ファイル
- `configs/model/meta-llama/Llama-3.1-8B.json` — 本解説で参照したモデル設定
- `kondoFolder/simulation_callgraph.md` — このフェーズを含む全体コールグラフ
- `kondoFolder/simulation_flow_walkthrough.md` — 全体の流れを読み物として
  まとめた版
