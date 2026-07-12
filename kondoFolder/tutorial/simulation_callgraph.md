
# LLMServingSim コールグラフ詳細

作成日: 2026-07-09

対象コマンド:

```bash
./scripts/compile.sh
```

```bash
python -m serving \
  --cluster-config 'configs/cluster/single_node_single_instance.json' \
  --block-size 16 \
  --dataset 'workloads/example_trace.jsonl' \
  --output 'outputs/example_single_run.csv' \
  --log-interval 1.0
```

本ドキュメントは、この2つのコマンドが実行するコード全体の呼び出し関係を、
ファイル・関数・行番号レベルで追跡したものである。

---

## 1. `./scripts/compile.sh` のコールグラフ

> Chakra packageのinstall、protobuf生成、CMake target、生成binary、clean/debug buildの
> 詳細は[`detail/compile.md`](detail/compile.md)を参照。

`scripts/compile.sh`(全24行、後半のns-3 buildはコメントアウト)は、
ビルドツールというより**2つの外部コマンドを
順に実行するだけの薄いラッパー**である。

```
scripts/compile.sh
├── (1) cd astra-sim/extern/graph_frontend/chakra && pip3 install .
│       └── ChakraのPythonパッケージ(protobuf生成コード et_def_pb2.py 含む)を
│           現在のPython環境にインストールする。
│           - setup.py 経由でホイールをビルドし、`chakra.schema.protobuf.et_def_pb2`
│             などが import 可能になる。
│           - このステップでコンパイルされる protobuf の「gencode」バージョンが、
│             後で `python -m serving` 実行時に必要になる protobuf ランタイムの
│             バージョン下限を決める(不一致だと `VersionError` になる — 直近の
│             エラー事例を参照)。
│
└── (2) cd astra-sim && bash ./build/astra_analytical/build.sh
        └── ASTRA-Sim C++本体(analytical network backendのみ)をcmakeでビルドする。
            - 生成物: astra-sim/build/astra_analytical/build/AnalyticalAstra/bin/AnalyticalAstra
              (`serving/__main__.py` がこのバイナリパスをハードコードして起動する)
            - ns-3バックエンドのビルド(`build/astra_ns3/build.sh`)はコメントアウト
              されており、デフォルトでは実行されない。
```

**要点:**
- `compile.sh`自体には独自のキャッシュ管理は無い。`pip3 install .`は毎回packageの
  build/installを実行する一方、ASTRA-Sim側は既存CMake build directoryを再利用する
  incremental buildであり、毎回すべてのC++ sourceを再コンパイルするわけではない。
- `python -m serving`側は、ここで生成された2つの成果物 — (a) `chakra`パッケージと
  (b) `AnalyticalAstra`バイナリ — に依存している。どちらか一方でも古い/壊れていると
  シミュレーションは動かない。

---

## 2. `python -m serving` のコールグラフ

### 2.1 全体構造(フェーズ分割)

```
python -m serving (serving/__main__.py: main())
│
├── Phase A: 起動・設定解決
│   ├── os.chdir("astra-sim")                              [__main__.py:199]
│   ├── argparse でCLI引数を解析                            [__main__.py:202-348]
│   ├── run_paths.resolve_run_id() / build_run_paths()      [run_paths.py]
│   ├── logger.configure_logger() / print_banner() など     [logger.py]
│   ├── config_builder.build_cluster_config()               [config_builder.py:273] ★詳細は2.2
│   ├── _build_instance_runtime_configs()                   [__main__.py:154]
│   ├── Scheduler(...) をinstance数だけ生成                  [scheduler.py] ★詳細は2.3
│   ├── Controller(total_npu)                                [controller.py]
│   └── Router(...) 生成 → router.load_requests(dataset, ...) [router.py] ★詳細は2.4
│
├── Phase B: ASTRA-Sim起動前の初期グラフ生成
│   ├── generate_event(alarm)                                [trace_generator.py:1569]
│   ├── generate_graph(None, None, ..., event=True)          [graph_generator.py:10] ★詳細は2.5
│   │     └── subprocess: python -m chakra.src.converter.converter LLM ...
│   ├── get_workload(None, None, event=True)                 [utils.py:27]
│   └── subprocess.Popen([AnalyticalAstraバイナリ, ...])       [__main__.py:590]
│         (stdin/stdout/stderrをパイプで接続、以後IPCで対話)
│
├── Phase C: メインシミュレーションループ(while True)         [__main__.py:611-1007] ★詳細は2.6
│   ├── controller.read_wait(p)                              [controller.py:13]
│   ├── controller.parse_output(...)                         [controller.py:39]
│   ├── router.route_arrived_requests(current)               [router.py] ★詳細は2.4
│   ├── scheduler.add_done(id, sys, current)                 [scheduler.py:667]
│   ├── router.notify_request_completed(...) / transfer_prefill_request(...) [router.py]
│   ├── scheduler.schedule(current, sys, id)                 [scheduler.py:55] ★詳細は2.3
│   ├── (バッチがあれば) trace_generator.generate_trace(...)   [trace_generator.py:1448] ★詳細は2.6.1
│   ├── (バッチがあれば) graph_generator.generate_graph(...)   [graph_generator.py:10]
│   │     └── subprocess: python -m chakra.src.converter.converter LLM ...
│   ├── utils.get_workload(...)                              [utils.py:27]
│   ├── controller.write_flush(p, workload_path または "pass"/"done"/"exit") [controller.py:32]
│   └── (ログ間隔ごとに) スループット・メモリ使用量を標準出力に表示
│
└── Phase D: 終了処理
    ├── controller.check_end(p)                              [controller.py:23]
    ├── prefix caching統計の集計
    ├── 標準出力へ最終結果を表示 (Throughput / Prefix Caching / Power / Per-Instance)
    ├── scheduler.print_result() を各instanceで呼ぶ           [scheduler.py:897]
    ├── (--output指定時) scheduler.save_output() を各instanceで呼ぶ [scheduler.py:957]
    ├── (--geographic-*-output指定時) geo_report.* で集計CSV書き出し
    └── (--cleanup-inputs、既定on) _cleanup_inputs_root() でinputs/runs/<run_id>を削除
```

以下、各フェーズを詳細に見る。

---

### 2.2 `build_cluster_config()` — クラスタ設定の解決

`config_builder.py:273`

```
build_cluster_config(astra_sim, cluster_config_path, ...)
├── configs/cluster/single_node_single_instance.json を読み込み
├── ノード数・instance数・必須キーのバリデーション            [config_builder.py:294-330]
├── (power指定があれば) power configの必須キー検証            [config_builder.py:332-377]
├── instanceごとに:
│   ├── utils.get_config(model_name) でモデル設定(config.json)を読み込み
│   ├── _resolve_parallelism(instance, model_config)          [config_builder.py:55]
│   │     → tp_size / pp_size / ep_size / dp_group を解決・検証
│   ├── inst2node_mapping, inst2npu_mapping, npu2inst_mapping を構築
│   └── placement(weights/kv_loc/kv_evict_locの階層設定)を構築 [config_builder.py:563-617]
├── _resolve_dp_groups(total_instances)                       [config_builder.py:132]
│     → DPグループのtp_size/ep_size整合性チェック、ep_total/local_ep算出
├── _sync_system_collective_dims(system_config_path, ...)     [config_builder.py:259]
│     → astra-sim/inputs/.../system.json のcollective実装配列をトポロジ次元数に合わせる
├── _create_network_config(network_config_path, ...)         [config_builder.py:672]
│     → astra-sim/inputs/.../network.yml を生成(topology, npus_count, bandwidth, latency)
├── memory_expansion.json を書き出し
├── _validate_memory_config(memory_config_path, placement, ...) [config_builder.py:695]
│     → placementで指定されたweights/kv_loc/kv_evict_locが実際のmemory_configに
│       存在するデバイスと整合するか検証
└── cluster dict を返す(num_instances, instances, 各種mapping, placement, ...)
```

**生成される3つのASTRA-Sim入力ファイル**(いずれも`astra-sim/inputs/runs/<run_id>/`配下):
- `network/network.yml`
- `system/system.json`(既定テンプレートをコピーしてcollective実装だけ上書き)
- `memory/memory_expansion.json`

---

### 2.3 `Scheduler` の構築とスケジューリング

各instanceに1つずつ`Scheduler`オブジェクトが作られる(`__main__.py:508`)。
コンストラクタ内で`MemoryModel`も同時に構築される(重みサイズの計算、
`enable_prefix_caching`時は`RadixCache`も初期化)。

メインループ内での`scheduler.schedule(current, sys, id)`の内部分岐:

```
Scheduler.schedule()                                          [scheduler.py:55]
├── enable_prefix_caching = True (既定)
│     → schedule_with_prefix(current, sys, batch_id)          [scheduler.py:336]
└── enable_prefix_caching = False
      → schedule_base(current, sys, batch_id)                 [scheduler.py:69]
          ├── 到着済み・pp_size制約チェック
          ├── running_reqs = 現在inflightな全リクエスト数
          │   available_slots = max_num_seqs - running_reqs   [scheduler.py:83-86]
          │   ← ここが「max_num_seqsは本数だけを見るハード上限」の実装箇所
          ├── enable_chunked_prefill時は decode優先 → prefill を結合
          ├── STEP 1: token_budget(max_num_batched_tokens)内で
          │           scheduled_tokens dict を構築(chunk化)     [scheduler.py:116-172]
          ├── STEP 1.5: prefix lockの設定
          ├── STEP 2: KVサイズ計算・evict/load判定
          ├── STEP 4: memory.allocate()でNPUメモリ確保
          └── STEP 5: Batch オブジェクトを構築して返す           [scheduler.py:256-299]
```

`schedule_with_prefix`(prefix caching有効時)はほぼ同じ流れだが、
`memory.prefix_match(req)`を呼んでキャッシュヒット分の`num_computed_tokens`を
先に埋めてから残りトークン数を計算する点が異なる。

`Batch`が返ってきた場合のみ、`__main__.py`のメインループが
`trace_generator.generate_trace()`を呼ぶ(バッチが無ければ`None`が返り、
`controller.write_flush(p, "pass")`でASTRA-Sim側を1ステップ空進行させる)。

---

### 2.4 `Router` — リクエストのロードとルーティング

```
Router(num_instances, schedulers, num_req, request_routing_policy, ...)
router.load_requests(dataset, enable_prefix_caching, is_init)   [router.py]
├── datasetのJSONLを1行ずつ読み、
│   ├── "sub_requests"キーがあれば _load_agentic_session()（マルチターン対応）
│   └── 無ければ _load_flat_request()（通常のフラットなリクエスト）
│       → reuse_prefix_toks / geo情報(地理シミュレーション用) をreq_dataに格納
└── _pending_requests を arrival_time_ns でソート

（メインループ内、毎イテレーション）
router.route_arrived_requests(current)
├── current時刻までに到着した_pending_requestsを順に処理
├── (NEAREST_REJECT/NEAREST_MIGRATE/NEAREST_MIGRATE_KV時)
│   容量チェック→リダイレクト判定(_maybe_reject_and_redirect / _maybe_migrate_and_redirect)
├── _select_instance()で通常のルーティングポリシー(LOAD/RR/RAND/...)を適用
│   （NEAREST系はgeo情報のassigned_instance_idをそのまま使用）
├── (NEAREST_KV/NEAREST_MIGRATE_KV時) _attach_local_kv_reuse() / _apply_kv_migration_if_needed()
│   でKV再利用・KV移送のメタデータをreq_dataに付与
└── sched.add_request([...], is_init, geo, failover) を呼び、
    対象instanceのSchedulerの pending queue に積む             [scheduler.py:843]
```

`add_done`後、完了したリクエストがあれば`router.notify_request_completed()`
(agentic sessionの次サブリクエスト解放)や`router.transfer_prefill_request()`
(Prefill/Decode分離構成でのハンドオフ)が呼ばれる。

---

### 2.5 `generate_graph()` — Chakraへのサブプロセス委譲

> `generate_graph()`のpath解決、converter引数、NPU別`.et`生成の詳細は
> [`detail/generate_graph.md`](detail/generate_graph.md)を参照。
>
> Chakra graphがASTRA-Sim内部でどのように発行・完了判定され、cycleがPythonへ
> 戻るかは
> [`detail/astra_sim_execution.md`](detail/astra_sim_execution.md)を参照。

`graph_generator.py:10`

```
generate_graph(batch, hardware, num_npus, node_id, instance_id, npu_offset, ...)
├── トレースファイルパス(trace_path)とワークロード出力パス(output_path)を解決
│   - event=True の場合: astra-sim/inputs/runs/<run_id>/trace/event_handler.txt
│   - 通常時: astra-sim/inputs/runs/<run_id>/trace/<hardware>/<model>/instance<id>_batch<id>.txt
├── subprocess.run([
│       'python', '-m', 'chakra.src.converter.converter', 'LLM',
│       '--input', trace_path, '--output', output_path,
│       '--num-npus', num_npus, '--npu-offset', npu_offset,
│   ], cwd=astra-sim/extern/graph_frontend/chakra, check=True)
│   └── chakra.src.converter.converter.LLM が
│       llm_converter.py（このリポジトリのchakraフォーク内）を呼び、
│       テキストトレース(タブ区切り) → Chakra protobuf .et ファイルに変換する。
│       - MEM_LOAD_NODE（最初のレイヤの入力）
│       - COMP_NODE（各計算レイヤ）
│       - MEM_STORE_NODE（最後のレイヤの出力）
│       - COMM_COLL_NODE（ALLREDUCE/ALLTOALL、involved_dim付き）
│       を生成する。ここで使われるprotobufスキーマ(et_def_pb2.py)が、
│       compile.sh の (1) でインストールされたものであり、
│       ランタイムprotobufとのバージョン不一致がここで表面化する
│       （今回遭遇した VersionError はこのサブプロセス内で発生していた）。
└── cleanup_trace=True（既定）ならテキストトレースファイルを削除
```

生成された`.et`ワークロードファイルのパスは、`utils.get_workload()`で
文字列として組み立てられ、`controller.write_flush(p, workload_path)`経由で
ASTRA-Simサブプロセスの標準入力に書き込まれる。

---

### 2.6 メインループの1イテレーションの詳細シーケンス

`__main__.py:611`の`while True:`ループは、ASTRA-Simプロセスとの
テキストベースIPCで駆動される。1回のループが「ASTRA-Sim側の1イベント完了通知」
に対応する。

```
┌─────────────────────────────────────────┬─────────────────────────────┐
│  Python (serving/__main__.py)            │  ASTRA-Sim (C++ subprocess) │
├───────────────────────────────────────────┼─────────────────────────────┤
│ controller.read_wait(p)                   │                             │
│   → p.stdout を "Waiting" が出るまで読む   │  イベント処理・"Waiting"出力 │
│ controller.parse_output(...)              │                             │
│   → 正規表現で sys/id/cycle を抽出         │                             │
│                                            │                             │
│ router.route_arrived_requests(current)     │                             │
│ scheduler[instance].add_done(id,sys,cur)   │                             │
│   → 完了したリクエストのTTFT/latency確定   │                             │
│ router.notify_request_completed(...)       │                             │
│                                            │                             │
│ scheduler[instance].schedule(cur,sys,id)   │                             │
│   → Batchオブジェクト（or None）           │                             │
│                                            │                             │
│ (Batchありなら)                            │                             │
│ trace_generator.generate_trace(batch,...)  │                             │
│   → テキストトレースファイルを書く         │                             │
│ graph_generator.generate_graph(batch,...)  │                             │
│   → subprocess: chakra converter           │                             │
│   → .et protobufファイルを書く             │                             │
│ utils.get_workload(batch,...)              │                             │
│   → .etファイルパスの文字列を組み立て      │                             │
│                                            │                             │
│ controller.write_flush(p, workload_path)   │                             │
│   → p.stdin に書き込み、flush              │  workload_pathを受け取り     │
│   （or "pass"/"done"/"exit"）              │  次のイベントを処理          │
└───────────────────────────────────────────┴─────────────────────────────┘
```

ループの終了条件(`__main__.py:958`): 全instanceの`is_request_empty()`が真、
かつ`router.has_pending_requests()`/`has_deferred_sessions()`が偽になった時点で
`done_instance`に加算していき、`len(done_instance) == num_instances`に達したら
`controller.write_flush(p, "exit")`を送ってループを`break`する。

DP(Data Parallel)グループが設定されている場合は、上記シーケンスに加えて
`dp_pending`辞書でグループ内の全メンバーがバッチをスケジュールし終えるまで
トレース生成を遅延させ、全員揃った時点で`_pad_batch_to_max()`でトークン数を
グループの最大値に揃えてから一斉にトレース生成する(vLLMのCUDA Graph DP
paddingを模した処理、`__main__.py:664-808`)。

---

### 2.7 終了処理

```
while True ループを抜けた後
├── controller.check_end(p)                                   [controller.py:23]
│     → p.stdout から "All Request Has Been Exited" を待つ
├── prefix caching統計の集計（各schedulerのmemory.return_prefix_info()）
├── 標準出力へ結果表示:
│   ├── Throughput Results（総リクエスト数、latency、スループット等）
│   ├── Prefix Caching Results（ヒット率）
│   ├── Power Modeling Results（有効時）
│   └── instanceごとに scheduler.print_result()                [scheduler.py:897]
│         → TTFT/TPOT/E2E-TTFT/Queueing/Prefill/Decodeの統計を表示
├── (--output指定時) 各instanceで scheduler.save_output(output_file, is_append) [scheduler.py:957]
│     → リクエスト単位のCSVを書き出す(1つ目のinstanceのみヘッダ付き、以降追記)
├── (--geographic-*-output指定時) geo_report モジュールで
│   per-user / per-GPU 集計CSV・メタデータJSONを書き出し
└── (--cleanup-inputs、既定on) _cleanup_inputs_root(run_paths, logger)
      → astra-sim/inputs/runs/<run_id> 以下を丸ごと削除（安全チェック付き）
```

---

## 3. ファイル別の役割まとめ

| ファイル | 役割 |
| --- | --- |
| `serving/__main__.py` | エントリポイント。CLI解析、初期化、メインループ、終了処理 |
| `serving/core/config_builder.py` | クラスタ設定JSON→ASTRA-Sim入力ファイル(network.yml/system.json/memory_expansion.json)生成 |
| `serving/core/scheduler.py` | vLLM風continuous batchingスケジューラ + MemoryModel所有 |
| `serving/core/memory_model.py` | NPU/CPU/CXLメモリ管理、KVキャッシュ・RadixCache |
| `serving/core/router.py` | リクエストロード、instanceへのルーティング、リダイレクト方式 |
| `serving/core/request.py` | Request/Batchデータクラス |
| `serving/core/trace_generator.py` | プロファイル済みlatencyテーブルを引いてテキストトレースを生成 |
| `serving/core/graph_generator.py` | テキストトレース→Chakra protobuf変換をsubprocessで呼ぶ |
| `serving/core/controller.py` | ASTRA-SimサブプロセスとのIPC(標準入出力パイプ) |
| `serving/core/utils.py` | モデル設定ロード、ワークロードファイルパス組み立て、トレース行フォーマット |
| `serving/core/run_paths.py` | run_id解決、`inputs_root`配下のパス組み立て |
| `astra-sim/extern/graph_frontend/chakra` | テキストトレース→protobuf `.et`変換の実体(Chakraフォーク) |
| `astra-sim/build/astra_analytical/build/.../AnalyticalAstra` | ASTRA-Sim本体バイナリ(C++、サブプロセスとして起動される) |

## 4. サブプロセス境界(重要)

このシミュレータは**3つの独立したプロセス**が協調して動く:

1. **Python本体**(`python -m serving`) — スケジューリングロジック、トレース生成の起点
2. **Chakra converter**(`python -m chakra.src.converter.converter`) — イテレーションごとに
   毎回`subprocess.run()`で新規プロセスとして起動・終了する(常駐しない)
3. **ASTRA-Sim本体**(`AnalyticalAstra`バイナリ、C++) — シミュレーション開始時に
   `subprocess.Popen()`で1回だけ起動し、標準入出力パイプ経由でシミュレーション終了まで
   常駐し続ける

(2)は都度起動のため、protobufやChakraパッケージ自体のバージョン不整合は
**イテレーションのたびに**顕在化しうる(今回遭遇したprotobuf VersionErrorは
まさにこの経路で発生した)。(3)は起動時の1回だけなので、バイナリ自体が
古い/新しいコード変更を反映していない、という問題はシミュレーション開始時に
一度だけ気付ける。
