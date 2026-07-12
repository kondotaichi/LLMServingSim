# `scripts/compile.sh` 詳細解説

作成日: 2026-07-12  
対象コマンド:

```bash
./scripts/compile.sh
```

対象実装:

- `scripts/compile.sh`
- `astra-sim/extern/graph_frontend/chakra/pyproject.toml`
- `astra-sim/extern/graph_frontend/chakra/setup.py`
- `astra-sim/extern/graph_frontend/chakra/setup.cfg`
- `astra-sim/build/astra_analytical/build.sh`
- `astra-sim/build/astra_analytical/CMakeLists.txt`

このコマンドは、LLMServingSimのPythonコードをコンパイルするものではない。
`python -m serving`が実行時に必要とするChakra Python packageとASTRA-Sim C++
binaryを準備する。

---

## 1. このコマンドの目的（一言で）

**「テキストtraceをChakra protobufへ変換するPython環境と、そのprotobuf graphを
実行するASTRA-Sim analytical backendを準備すること」**。

```text
./scripts/compile.sh
    │
    ├─ Step 1: Chakra Python packageをinstall
    │              │
    │              └─ trace → .et converterを利用可能にする
    │
    └─ Step 2: ASTRA-Sim analytical backendをbuild
                   │
                   └─ .et graphを実行するC++ binaryを生成
```

両方が必要である。

```text
Chakraだけある
  → .etは作れるがASTRA-Simを起動できない

ASTRA-Simだけある
  → traceから.etを生成できない
```

---

## 2. `compile.sh`全体

実体は20行程度の薄いshell wrapperである。

```bash
#!/bin/bash
set -e

SCRIPT_DIR=$(dirname "$(realpath $0)")
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)

(
cd ${REPO_ROOT}/astra-sim/extern/graph_frontend/chakra
pip3 install .
)

(
cd ${REPO_ROOT}/astra-sim
bash ./build/astra_analytical/build.sh
)
```

`set -e`があるため、Chakra installまたはASTRA-Sim buildのどちらかが失敗すると、
そこでscript全体が終了する。

2つの処理はそれぞれsubshell `(...)`内で実行される。このため、内部の`cd`が
呼び出し元shellのworking directoryを変更しない。

---

## 3. Repository rootの解決

```bash
SCRIPT_DIR=$(dirname "$(realpath $0)")
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
```

実行時のcurrent directoryではなく、`compile.sh`自身のpathからrepository rootを
求める。

したがってrepo root以外から、

```bash
/path/to/LLMServingSim/scripts/compile.sh
```

と実行しても、ChakraとASTRA-Simのpathを解決できる。

---

## 4. Step 1: Chakra Python packageのinstall

```bash
cd ${REPO_ROOT}/astra-sim/extern/graph_frontend/chakra
pip3 install .
```

PyPI上の別versionではなく、ASTRA-Sim submodule内にあるChakra forkを現在の
Python環境へinstallする。

package metadata:

```toml
[project]
name = "chakra"
version = "0.0.4"
requires-python = ">=3.7"
```

install後、例えば次がimport可能になる。

```python
from chakra.schema.protobuf import et_def_pb2
from chakra.src.converter.llm_converter import LLMConverter
```

LLMServingSimでは`generate_graph()`が次のmoduleをsubprocessで起動する。

```bash
python -m chakra.src.converter.converter LLM ...
```

---

## 5. `pip3 install .`が行うこと

このChakra packageはPEP 517 buildを使用する。

```toml
[build-system]
requires = ["setuptools", "setuptools-grpc"]
build-backend = "setuptools.build_meta"
```

大まかな流れ:

```text
pyproject.tomlを読む
    │
    ▼
build用の隔離環境を準備
    │
    ▼
setuptools / setuptools-grpcでpackageをbuild
    │
    ▼
wheelを生成
    │
    ▼
現在のPython environmentへinstall
```

`setup.py`は通常のbuild commandへgRPC/protobuf生成stepを追加する。

```python
class build_grpc(build):
    sub_commands = [("build_grpc", None)] + build.sub_commands

setup(cmdclass={"build": build_grpc})
```

`setup.cfg`は対象protoと出力先を指定する。

```ini
[build_grpc]
proto_files = et_def.proto
grpc_files = et_def.proto
proto_path = schema/protobuf/
output_path = schema/protobuf/
```

これにより、Chakra Execution Trace schemaをPythonから扱うためのprotobuf codeが
準備される。

---

## 6. Chakra packageの主要な役割

このrepositoryで主に使う部分は次の2つ。

### 6.1 Protobuf schema

```text
chakra.schema.protobuf.et_def_pb2
```

Chakra graphのmetadata、node type、attribute、dependencyなどをPythonから生成する。

### 6.2 LLM converter

```text
chakra.src.converter.converter
chakra.src.converter.llm_converter
```

`trace_generator`が作ったテキストtraceを、NPUごとの`.et` protobufへ変換する。

```text
instance0_batch0.txt
    │
    ▼
LLMConverter
    │
    ├─ llm.0.et
    ├─ llm.1.et
    └─ ...
```

変換処理の詳細は[`generate_graph.md`](./generate_graph.md)を参照。

---

## 7. Python dependencies

`pyproject.toml`では次を依存として指定している。

```toml
dependencies = [
    "protobuf==6.*",
    "graphviz",
    "networkx",
    "pydot",
    "HolisticTraceAnalysis @ git+https://github.com/...",
]
```

したがって`pip3 install .`は、必要に応じてnetwork accessを使って依存packageを
取得する。

特にprotobuf runtimeは`6.*`へ固定されている。別packageが古いprotobufを要求する
Python environmentへinstallすると、dependency conflictが起きる可能性がある。

---

## 8. Protobuf gencode/runtimeの整合性

`et_def_pb2.py`は、特定世代の`protoc`/protobuf toolingで生成される。そのPython
moduleをimportするruntime側protobufが古すぎると、例えば`VersionError`が起きる。

```text
生成codeが要求するruntime version
    >
実際にimportされたprotobuf runtime version

→ import時にVersionError
```

これはASTRA-Sim C++実行中ではなく、通常は`generate_graph()`がChakra converterを
subprocess起動した時点で表面化する。

確認command:

```bash
python -c "import google.protobuf; print(google.protobuf.__version__)"
```

```bash
python -c "from chakra.schema.protobuf import et_def_pb2; print(et_def_pb2.__file__)"
```

2つ目で、想定しているPython environmentのChakra packageをimportしているかも
確認できる。

---

## 9. 「現在のPython環境」とは何か

`pip3 install .`には`--user`や明示的なvirtual environment pathがない。
したがって、commandを実行したときの`pip3`が指す環境へinstallされる。

```bash
which python
which pip3
python -m pip --version
pip3 --version
```

Simulatorを実行する`python`と、installに使った`pip3`が異なるenvironmentを指すと、

```text
ModuleNotFoundError: No module named 'chakra'
```

または別versionのChakra/protobufをimportする問題が起きる。

実運用では、`compile.sh`と`python -m serving`を同じcontainer/venv内で実行することが
重要である。

---

## 10. Step 2: ASTRA-Sim analytical backendのbuild

Chakra installが成功すると、次へ進む。

```bash
cd ${REPO_ROOT}/astra-sim
bash ./build/astra_analytical/build.sh
```

このscriptは、

1. Chakra protobufのC++/Python生成物を確認
2. CMake build directoryを準備
3. ASTRA-Sim coreとanalytical network backendをbuild
4. 後方互換pathへsymlinkを作成

する。

---

## 11. Build prerequisites

`build.sh`から直接必要になる主要command:

```text
bash
realpath
nproc
cmake
C++17 compiler
protoc
```

`CMakeLists.txt`ではC++17を要求する。

```cmake
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
```

protobuf C++ source/headerが存在しない場合だけ`protoc`を使うため、初回buildでは
特に`protoc`が必要になる。

---

## 12. Chakra protobufのC++/Python生成

`compile_chakra_et()`は次の生成物を確認する。

```text
schema/protobuf/et_def.pb.h
schema/protobuf/et_def.pb.cc
schema/protobuf/et_def_pb2.py
```

C++生成物がどちらか無ければ、

```bash
protoc et_def.proto \
  --proto_path=<chakra/schema/protobuf> \
  --cpp_out=<chakra/schema/protobuf>
```

Python生成物が無ければ、

```bash
protoc et_def.proto \
  --proto_path=<chakra/schema/protobuf> \
  --python_out=<chakra/schema/protobuf>
```

を実行する。

ポイントは、**fileが既に存在すれば再生成しない**こと。`et_def.proto`を変更しただけ
では既存生成物が残り、再生成されない可能性がある。その場合は後述のcleanを行う。

---

## 13. Build directoryの準備

build directory:

```text
astra-sim/build/astra_analytical/build/
```

存在しない場合のみ作る。

```bash
if [[ ! -d "${BUILD_DIR}" ]]; then
  mkdir -p "${BUILD_DIR}"
fi
```

build並列数はhost CPU数を使うが、最大16 threadに制限する。

```bash
NUM_THREADS=$(nproc)
if [[ ${NUM_THREADS} -ge 16 ]]; then
  NUM_THREADS=16
fi
```

---

## 14. CMake configureとbuild

通常buildでは次を実行する。

```bash
cd astra-sim/build/astra_analytical/build
cmake .. -DBUILDTARGET=all
cmake --build . -j <threads>
```

default build typeは`RelWithDebInfo`。

```cmake
set(CMAKE_BUILD_TYPE RelWithDebInfo)
set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "-O2 -g -fno-omit-frame-pointer")
```

つまり最適化`-O2`を有効にしつつ、debug symbolも保持する。

CMakeは主に次をbuild treeへ追加する。

```text
ASTRA-Sim core library
analytical network backend
analytical network frontend executable
```

既存build directoryを消さないため、通常はCMakeのincremental buildになる。
毎回すべてのC++ sourceを必ず再コンパイルするわけではない。

---

## 15. Default build target

`build.sh`の既定値:

```bash
build_target="all"
```

利用可能なtarget:

```text
all
congestion_unaware
congestion_aware
```

`compile.sh`は追加引数を渡さないため、通常は`all`をbuildする。

LLMServingSimの`--network-backend analytical`が通常起動するのは、
`congestion_unaware`側binaryである。

---

## 16. 生成されるbinary

主要な実体binary:

```text
astra-sim/build/astra_analytical/build/bin/
  AstraSim_Analytical_Congestion_Unaware
  AstraSim_Analytical_Congestion_Aware
```

さらに後方互換用symlinkを作る。

```text
build/AnalyticalAstra/bin/AnalyticalAstra
  → build/bin/AstraSim_Analytical_Congestion_Unaware
```

```text
build/AstraCongestion/bin/AstraCongestion
  → build/bin/AstraSim_Analytical_Congestion_Aware
```

したがって、チュートリアルで示している、

```text
astra-sim/build/astra_analytical/build/
  AnalyticalAstra/bin/AnalyticalAstra
```

は通常、実体binaryではなくcongestion-unaware binaryへのsymlinkである。

---

## 17. `python -m serving`がbinaryを選ぶ処理

analytical backend指定時、まず互換symlink pathを見る。

```python
binary = os.path.join(
    astra_sim,
    "build/astra_analytical/build/AnalyticalAstra/bin/AnalyticalAstra",
)
```

存在しなければ実体binary pathへfallbackする。

```python
if not os.path.exists(binary):
    binary = os.path.join(
        astra_sim,
        "build/astra_analytical/build/bin/"
        "AstraSim_Analytical_Congestion_Unaware",
    )
```

したがってsymlink生成に失敗しても、実体binaryが正常にbuildされていれば起動できる。

---

## 18. Debug buildとtarget指定

`compile.sh`からは指定できないが、下位の`build.sh`はoptionを持つ。

### Debug build

```bash
cd astra-sim
bash ./build/astra_analytical/build.sh -d
```

Debug modeでは、

```text
-O0
AddressSanitizer
UndefinedBehaviorSanitizer
LeakSanitizer
debug symbols
```

などが有効になる。

### congestion-unawareだけbuild

```bash
cd astra-sim
bash ./build/astra_analytical/build.sh -t congestion_unaware
```

### congestion-awareだけbuild

```bash
cd astra-sim
bash ./build/astra_analytical/build.sh -t congestion_aware
```

通常のLLMServingSim実行だけなら`compile.sh`のdefaultでよい。

---

## 19. Clean

下位scriptの`-l` optionはbuild directoryとprotobuf生成物を削除する。

```bash
cd astra-sim
bash ./build/astra_analytical/build.sh -l
```

削除対象:

```text
astra-sim/build/astra_analytical/build/
et_def.pb.cc
et_def.pb.h
et_def_pb2.py
```

cleanだけ行って終了し、同じ呼び出し内では再buildしない。

完全に作り直す場合:

```bash
cd astra-sim
bash ./build/astra_analytical/build.sh -l
cd ..
./scripts/compile.sh
```

これは生成物を削除する操作なので、通常のincremental buildで解決しない場合に限る。

---

## 20. ns-3 backendはbuildされない

`compile.sh`末尾にはns-3 buildがあるが、現在はコメントアウトされている。

```bash
# (
# cd ${REPO_ROOT}/astra-sim
# bash ./build/astra_ns3/build.sh
# )
```

したがって、

```bash
./scripts/compile.sh
```

だけで準備されるnetwork backendはanalytical backendである。

`python -m serving --network-backend ns3`を使用するには、ns-3側の依存関係とbuildを
別途行う必要がある。

---

## 21. Incremental buildの挙動

`compile.sh`を再実行した場合:

```text
Chakra:
  pip3 install .を再実行
  packageを再build/reinstall

ASTRA-Sim:
  既存CMake build directoryを再利用
  変更されたsourceと依存targetを中心にincremental build

protobuf C++/Python code:
  fileが存在すればbuild.shでは再生成しない
```

したがって「毎回完全なclean build」ではない。

---

## 22. 代表的な失敗と確認箇所

### `pip3 install .`でnetwork error

Chakra dependencyにGit repository由来のpackageがあるため、初回installではnetwork
accessが必要になることがある。

### `protoc: command not found`

protobuf C++/Python生成物が無い状態で、protobuf compilerがinstallされていない。

### CMake configure error

compiler、protobuf C++ library、その他ASTRA-Sim dependencyが不足している可能性がある。

### Chakraをimportできない

`pip3 install .`を行ったenvironmentと、`python -m serving`を実行しているenvironment
が異なる可能性がある。

### `VersionError`

生成済み`et_def_pb2.py`と、実行時にimportされたprotobuf runtimeの世代が不整合。

### Binaryが見つからない

確認path:

```bash
ls -l astra-sim/build/astra_analytical/build/AnalyticalAstra/bin/AnalyticalAstra
```

```bash
ls -l astra-sim/build/astra_analytical/build/bin/AstraSim_Analytical_Congestion_Unaware
```

---

## 23. Build後の最小確認

### Chakra import

```bash
python -c "from chakra.schema.protobuf import et_def_pb2; print(et_def_pb2.__file__)"
```

### Converter CLI

```bash
python -m chakra.src.converter.converter --help
```

### ASTRA-Sim binary

```bash
test -x astra-sim/build/astra_analytical/build/AnalyticalAstra/bin/AnalyticalAstra
```

### Symlink確認

```bash
ls -l astra-sim/build/astra_analytical/build/AnalyticalAstra/bin/AnalyticalAstra
```

---

## 24. Serving実行時との接続

build成果物は次のように使用される。

```text
python -m serving
    │
    ├─ generate_trace()
    │      └─ text traceを生成
    │
    ├─ generate_graph()
    │      └─ installed Chakra packageで.etを生成
    │
    └─ subprocess.Popen()
           └─ built AnalyticalAstra binaryを起動
```

Chakra converterの詳細は[`generate_graph.md`](./generate_graph.md)、生成したgraphの
ASTRA-Sim内部実行は[`astra_sim_execution.md`](./astra_sim_execution.md)を参照。

---

## 25. まとめ

`scripts/compile.sh`は、次の2つを順に準備するwrapperである。

```text
Step 1: pip3 install .
  → repository内のChakra forkを現在のPython環境へinstall
  → protobuf schemaとLLM converterを利用可能にする

Step 2: build/astra_analytical/build.sh
  → protobuf C++/Python生成物を確認
  → CMakeでASTRA-Sim analytical targetをbuild
  → congestion-unaware binaryへの互換symlinkを作る
```

重要点:

1. Chakraとservingは同じPython environmentを使う
2. protobuf gencode/runtime versionを整合させる
3. ASTRA-Sim buildは通常incremental
4. `AnalyticalAstra`はcongestion-unaware binaryへの互換symlink
5. ns-3 backendはdefaultではbuildされない

## 関連ファイル

- `scripts/compile.sh` — 本解説の入口
- `astra-sim/extern/graph_frontend/chakra/pyproject.toml` — Python package定義
- `astra-sim/extern/graph_frontend/chakra/setup.py` — protobuf build command追加
- `astra-sim/extern/graph_frontend/chakra/setup.cfg` — proto生成設定
- `astra-sim/build/astra_analytical/build.sh` — analytical backend build
- `astra-sim/build/astra_analytical/CMakeLists.txt` — CMake targetとbuild type
- `serving/__main__.py` — 実行binaryの選択
- `kondoFolder/tutorial/detail/generate_graph.md` — Chakra converterの利用箇所
- `kondoFolder/tutorial/detail/astra_sim_execution.md` — C++ binary内でのgraph実行
