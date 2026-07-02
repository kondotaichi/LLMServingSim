# コードリーディング: `python -m serving` の内部処理

対象コマンド:
```bash
python -m serving --cluster-config 'configs/cluster/single_node_single_instance.json' \
    --dtype bfloat16 --block-size 16 \
    --dataset 'workloads/example_trace.jsonl' --output 'outputs/example_single_run.csv' \
    --num-req 10
```

エントリポイントは `serving/__main__.py`。以下、実行順に処理を追う。
（行番号はこの調査時点のもの。実装が変わればずれる可能性がある）

---

## 0. 全体像

```
argparse                              → 実行設定を決める
  ↓
config_builder.build_cluster_config   → クラスタ定義 → ASTRA-Sim入力3ファイル生成
  ↓
Scheduler / Controller / Router 初期化
  ↓
router.load_requests                  → workloads/*.jsonl を読み込み、pendingキューへ
  ↓
ASTRA-Sim サブプロセス起動 (Popen)
  ↓
┌─ while True: ───────────────────────────────────────────────┐
│ controller.read_wait/parse_output → ASTRA-Simの1イテレーション完了通知を読む │
│ router.route_arrived_requests     → 到着時刻を過ぎたリクエストをschedulerへ  │
│ scheduler.add_done                → 前バッチの完了処理（TTFT/ITL記録など）  │
│ scheduler.schedule                → 新しいバッチを組む（vLLM風連続バッチング）│
│ trace_generator.generate_trace    → プロファイル済み遅延からトレース生成    │
│ graph_generator.generate_graph    → トレース→Chakra .et プロトブロブ変換   │
│ controller.write_flush            → ワークロードパスをASTRA-Simに送る      │
│ (1秒おきにスループット/メモリ使用率をログ出力)                              │
│ 全リクエスト完了判定 → 完了なら "exit" を送ってループ脱出                  │
└──────────────────────────────────────────────────────────────┘
  ↓
集計・CSV書き出し・ASTRA-Sim入力ファイルのクリーンアップ
```

ASTRA-Sim（C++バイナリ）とはサブプロセス + stdin/stdout の行ベースIPCで通信する。
Pythonが「1バッチ分のワークロード（.etファイルパス）」を渡し、ASTRA-SimがNPU/通信の
サイクル数をシミュレートして結果を1行返す、というやり取りを全リクエストが終わるまで
繰り返す。

---

## 1. 起動直後: 作業ディレクトリの切り替え (`__main__.py:193-199`)

```python
cwd = os.getcwd()
astra_sim = os.path.join(cwd, "astra-sim")
os.chdir(astra_sim)
```

`python -m serving` はリポジトリルートから実行するが、以降のコード（`config_builder`や
`trace_generator`など）はすべて **`astra-sim/` を基準とした相対パス** で書かれている。
そのためここで最初に `cwd` を `astra-sim/` に切り替える。`workloads/...` のようなリポジトリ
ルート相対パスはコード内部で `../workloads/...` と `../` を前置して参照される
（例: `router.py:114` の `path = f'../{path}'`）。

## 2. 引数パース (`__main__.py:202-295`)

argparse で約30個のCLIフラグを定義。今回のコマンドで明示指定されたのは4つ:

| フラグ | 値 | 効果 |
| --- | --- | --- |
| `--cluster-config` | `configs/cluster/single_node_single_instance.json` | クラスタ定義ファイル |
| `--dtype` | `bfloat16` | 重みのdtype。プロファイルの`bf16` variantフォルダに対応 |
| `--block-size` | `16` | KVキャッシュのブロックサイズ（トークン数） |
| `--dataset` | `workloads/example_trace.jsonl` | 入力リクエストトレース |
| `--output` | `outputs/example_single_run.csv` | 結果CSVの出力先 |
| `--num-req` (`--num-reqs`) | `10` | データセットから読み込む件数の上限 |

明示しなかった主要フラグはデフォルト値が使われる:
`--max-num-seqs=128`, `--max-num-batched-tokens=2048`, `--enable-prefix-caching=True`（デフォルトON）,
`--enable-chunked-prefill=True`（デフォルトON）, `--request-routing-policy=LOAD`,
`--log-level=WARNING`, `--cleanup-inputs=True` など。

`args.run_id` は `resolve_run_id()` (`run_paths.py:20`) でユニークID
（`run_<timestamp_us>_<pid>`）が生成され、ASTRA-Simの中間生成物は
`astra-sim/inputs/runs/<run_id>/` 以下に隔離される（並列実行しても衝突しない）。

## 3. クラスタ設定 → ASTRA-Sim入力ファイル生成 (`config_builder.build_cluster_config`, `__main__.py:327`)

`configs/cluster/single_node_single_instance.json` を読み、以下3ファイルを
`astra-sim/inputs/runs/<run_id>/{network,system,memory}/` に生成する
（`config_builder.py:273` 以降。詳細は `configs/cluster/README.md` 参照）:

- **`network.yml`** — トポロジと帯域（今回は1ノード・1NPU・TP=1なのでシンプルな1ノード構成）
- **`system.json`** — スケジューリングポリシーとメモリ帯域
- **`memory_expansion.json`** — CPU（REMOTE）メモリ設定。`npu_mem`/`cpu_mem`の`mem_bw`など
  クラスタ設定JSONの値がそのまま反映される

同時に `build_cluster_config` は Python 側で使う正規化済みの辞書
（`num_instances`, `instances`, `inst2node_mapping`, `inst2npu_mapping`,
`npu2inst_mapping`, `placement`, `total_npu` など）を返す。今回の構成では:
- `num_instances = 1`
- `instances[0]`: `model_name=meta-llama/Llama-3.1-8B`, `hardware=RTXPRO6000`, `tp_size=1`, `num_npus=1`

## 4. Scheduler / Controller / Router / PowerModel 初期化 (`__main__.py:347-474`)

- `_build_instance_runtime_configs()` (`__main__.py:154`) — インスタンスごとのランタイム設定
  （`max_num_seqs`, `dtype`, `kv_cache_dtype` 等）を、クラスタ設定JSONの
  per-instance override > CLI引数 の優先順位で解決する。今回はoverrideなしなので
  全部CLI引数（またはデフォルト）どおり。
- **`Scheduler`** (`scheduler.py:18`) をインスタンスごとに1つ生成。内部で
  `MemoryModel` (`memory_model.py:17`) も生成され、モデルの重みサイズ・KVキャッシュ
  ブロックサイズなどを計算する。
- **`Controller`** (`controller.py:4`) — ASTRA-Simサブプロセスとの行ベースIPC担当。
- **`Router`** (`router.py:7`) — `request_routing_policy=LOAD`（デフォルト）でインスタンス間
  ルーティング。1インスタンスしかないので実質何もしない。
- `power_modeling` はクラスタ設定に`power`セクションが無いので `False` → `PowerModel`は
  生成されない。

## 5. データセットロード (`router.load_requests`, `__main__.py:477`)

```python
router.load_requests(dataset, enable_prefix_caching=any_prefix_caching, is_init=is_init)
```

`Router.load_requests` (`router.py:103`):
1. `workloads/example_trace.jsonl` を `../workloads/example_trace.jsonl` として開く
   （cwdが`astra-sim/`のため）。
2. 1行ずつJSONを読み、`sub_requests`キーがあれば **エージェント的セッション**
   （`_load_agentic_session`）、なければ **フラットリクエスト**（`_load_flat_request`）
   として処理。`example_trace.jsonl`はフラット形式10行。
3. `--num-req 10` により `req_num=10` が渡っているので、10行読んだ時点で打ち切り
   （全10行しかないので実質フルロード）。
4. 各行から `Request`用の辞書（`input_toks`, `output_toks`, `arrival_time_ns`,
   prefix caching有効時は`input_hash_ids`/`output_hash_ids`）を作り
   `self._pending_requests` に貯める。
5. 最後に到着時刻でソート（`_pending_requests.sort(key=lambda r: r['arrival_time_ns'])`）。

この時点ではまだ `Scheduler` にリクエストは渡っていない。実際のルーティング
（`sched.add_request(...)`）はメインループの `router.route_arrived_requests(current)`
が到着時刻を迎えるたびに行う（**リアルタイムルーティング**、`router.py:189`）。

## 6. ASTRA-Sim サブプロセス起動 (`__main__.py:509-529`)

- 最初のリクエスト到着時刻まで「イベント待ち」の空トレースを1つ用意
  (`generate_event` → `generate_graph(event=True)`)。これはASTRA-Simを最初の
  `Waiting`プロンプトまで進めるためのダミーワークロード。
- `binary = astra-sim/build/astra_analytical/build/AnalyticalAstra/bin/AnalyticalAstra`
  （`--network-backend analytical` がデフォルトのため。C++の解析的ネットワークシミュレータ）
- `subprocess.Popen(astra_args, stdin=PIPE, stdout=PIPE, stderr=PIPE, ...)` でASTRA-Simを起動。
  `--workload-configuration` / `--system-configuration` / `--network-configuration` /
  `--memory-configuration` を渡す。

## 7. メインループ (`__main__.py:550-946`)

`while True:` の1周が「ASTRA-Simの1イテレーション完了 → 次のバッチをスケジュールして送る」
というサイクル。今回の構成（単一インスタンス・DPなし・PIMなし）で実際に通る経路だけ追う。

### 7.1 ASTRA-Simからの出力を読む

```python
out = controller.read_wait(p)
out_dict = controller.parse_output(out[-2])
```

`Controller.read_wait` (`controller.py:13`) はASTRA-Simの標準出力を、`"Waiting"` という
行が出るまで1行ずつ読み進める（＝ASTRA-Simが次の入力を待っている状態になるまでブロック）。
`parse_output` (`controller.py:39`) は正規表現で
`sys[<npu_id>] iteration <batch_id+1> finished, <cycles> cycles, exposed communication <cycles> cycles.`
という行から `sys`（NPU ID）, `id`（バッチID+1）, `cycle`（現在時刻=ns）を取り出す。
この `cycle` が **シミュレーション内の現在時刻** `current` になる。

### 7.2 到着リクエストのルーティング

```python
if dataset is not None:
    router.route_arrived_requests(current)
```

`current` 時刻までに到着したリクエストを `_pending_requests` から取り出し、
`_least_load_select`（LOADポリシー、`router.py:74`）でインスタンスを選び、
`scheduler.add_request(...)` (`scheduler.py:822`) でそのSchedulerの待ちキュー
`self.request` に `bisect.insort` で到着時刻順に挿入する。

### 7.3 完了バッチの後処理: `scheduler.add_done` (`scheduler.py:661`)

前のイテレーションで送ったバッチのうち、今回のNPU (`sys`) が計算完了したことを
`batch.end` に記録する。**そのバッチが使う全NPUが完了して初めて** 後処理に進む
（TP=1・単一NPUなので即座に条件成立）。バッチ内の各リクエストについて:
- **プレフィルが完了**したリクエスト → `req.set_ttft(finish)` でTTFT記録、
  `prompt_t` にトークン数加算、prefix cache有効時はキャッシュ登録
  （`memory.cache_unfinished_req`）
- **デコード中**のリクエスト → `req.add_itl(finish)` でinter-token latency記録、
  `req.num_computed_tokens += 1`
- 出力トークン数に到達したら `req.add_latency(finish)` で最終レイテンシ確定し
  `self.done` へ、KVキャッシュを解放（prefix caching有効時は
  `memory.cache_finished_req` でキャッシュに残す）
- 未完了なら `pool` に戻して次のスケジューリング対象に

`prompt_t`/`gen_t`（このイテレーションで進んだトークン数）が呼び出し元に返り、
`__main__.py`側でスループット集計 (`prompt_th`, `gen_th`, `total_prompt`, `total_gen`)
に加算される。

### 7.4 新しいバッチのスケジューリング: `scheduler.schedule` (`scheduler.py:55`)

`enable_prefix_caching=True`（デフォルト）なので実際には `schedule_with_prefix`
(`scheduler.py:333`) が呼ばれる。ロジックの骨格は `schedule_base` (`scheduler.py:69`)
と同じなので、そちらを基準に説明する（今回のワークロードはリクエスト間で
共通プレフィックスが無いためprefix cache hitは0%になり、実質`schedule_base`と
同じ挙動になる）:

1. `self.request`（到着済み・未処理）から `req.arrival <= current` のものを抽出。
2. `max_num_seqs`（デフォルト128）と実行中リクエスト数から使える枠数を計算し、
   その枠数までバッチに詰める。
3. **Chunked prefill有効時**（デフォルトON）: デコード中のリクエストを先に、
   プレフィル中のリクエストを後に並べ、`max_num_batched_tokens`（デフォルト2048）の
   トークン予算内で1ステップ分のトークンを割り当てる。デコードは1リクエスト=1トークン、
   プレフィルは残りトークン全部（または`--long-prefill-token-threshold`で制限）。
4. 割り当てたリクエスト群から `Batch` オブジェクト (`request.py`) を作り、
   `self.inflight` に登録。バッチに新規リクエストが含まれなければ `None` を返す
   （＝このNPUにはこのイテレーション送るものが無い）。

今回のデータセットは10リクエストのみで `max_num_seqs=128` に対して十分小さいため、
到着済みのリクエストはほぼキューイングなしで即座にバッチへ乗る
（前回作成した`Diary/output/`の考察メモで「平均queuing_delay 4.4ms」と確認済み）。

### 7.5 トレース生成: `trace_generator.generate_trace` (`trace_generator.py:1448`)

`new_req`（新しいバッチ）が得られたら、DP groupが無い単一インスタンス構成なので
`__main__.py:748-763` の「独立インスタンス」分岐に入る:

```python
generate_trace(new_req, instance["hardware"], instance["tp_size"], instance["pp_size"],
               instance["local_ep"], instance["ep_total"], instance["pd_type"],
               node_id, instance_id,
               inst_cfg["max_num_batched_tokens"], inst_cfg["max_num_seqs"],
               placement[instance_id], block_mode_on[instance_id],
               expert_routing_policy, inst_cfg["enable_prefix_caching"],
               inst_cfg["enable_attn_offloading"], power_model, pim_models[node_id],
               inst_cfg["enable_sub_batch_interleaving"], inst_cfg["fp"],
               dtype=inst_cfg["dtype"], kv_cache_dtype=inst_cfg["kv_cache_dtype"], ...)
```

`generate_trace` の内部処理:
1. `resolve_variant(dtype="bfloat16", kv_cache_dtype="auto", config)` (`trace_generator.py:52`)
   → variant名 `"bf16"` を解決。
2. `_synthesize_trace` (`trace_generator.py:1294`) を呼ぶ
   （`enable_sub_batch_interleaving=False` なので単一バッチ版）。
   - `_build_trace_ctx` (`trace_generator.py:829`) が `_load_perf_db(hardware="RTXPRO6000",
     model="meta-llama/Llama-3.1-8B", variant="bf16", tp_needed=1, model_type="llama")`
     (`trace_generator.py:326`) を呼び、
     `profiler/perf/RTXPRO6000/meta-llama/Llama-3.1-8B/bf16/tp1/{dense,per_sequence,
     attention}.csv`（Llama-3.1-8Bは非MoEなので`moe.csv`は無い）を読み込んでメモリ上の
     ルックアップテーブルを構築する。
   - `_build_batch_ctx` (`trace_generator.py:869`) がバッチの実際のトークン数・
     KV長などをコンテキストに詰める。
   - `_emit_prologue` → `embedding`層（入力はREMOTE=CPUから）
   - `num_hidden_layers`（Llama-3.1-8Bは32層）回、`_build_transformer_block`
     (`trace_generator.py:1202`) を呼び、各層について
     `qkv_proj → rotary_emb → attention → o_proj → gate_up_proj → act_fn → down_proj`
     を `_emit_sequence` (`trace_generator.py:1144`) が順に書き出す。各レイヤーの
     `comp_time`は `_lookup_dense`/`_lookup_attention`（4Dルックアップ:
     `prefill_chunk, kv_prefill, n_decode, kv_decode`）でCSVから最近傍/線形補間して
     求める（TP=1なので`o_proj`/`down_proj`後のALLREDUCEは実質コストゼロ、
     GPU台数が1なので通信は発生しない）。
   - `_emit_final_layers` (`trace_generator.py:1232`) → `final_layernorm → lm_head →
     sampler`（`sampler`の出力はREMOTEへ、CPU側にサンプリング結果を返す想定）。
3. トレースをタブ区切りテキストファイルとして
   `astra-sim/inputs/runs/<run_id>/trace/RTXPRO6000/meta-llama/Llama-3.1-8B/
   instance0_batch<N>.txt` に書き出す（`generate_trace`後半、行1514-1560）。
   フォーマットの詳細は `CLAUDE.md` の「Trace file format」節を参照。

なお実行ログに出た

```
[MemoryModel] [node=0,inst=0] INFO NPU: model weight 15316MB loaded
```

は `MemoryModel.__init__` (`memory_model.py:17`, ログは121行目付近) が
Llama-3.1-8B（bfloat16、TP=1）の重みサイズを計算して一度だけ出すログで、
`generate_trace`より前、Scheduler初期化時点（ステップ4）で出ている。

### 7.6 Chakraグラフ変換: `graph_generator.generate_graph` (`graph_generator.py:10`)

```python
generate_graph(new_req, instance["hardware"], instance["num_npus"], node_id,
               instance_id, inst2npu_mapping[instance_id],
               inst_cfg["enable_local_offloading"],
               inputs_root=run_paths.inputs_root, cleanup_trace=args.cleanup_inputs)
```

サブプロセスとして
```
python -m chakra.src.converter.converter LLM \
  --input <trace .txt> --output <workload dir>/llm \
  --num-npus 1 --npu-offset 0
```
を実行（cwdは`astra-sim/extern/graph_frontend/chakra`）。これがトレーステキストを
Chakraのprotobuf `.et` ファイル（ASTRA-Simが読める形式）に変換する。
`--cleanup-inputs`（デフォルトON）が効いているので、変換後に元の`.txt`トレースは
即削除される（`os.remove(trace_path)`）。

### 7.7 ASTRA-Simへワークロードを送信

```python
workload = get_workload(new_req, instance["hardware"], instance_id, inputs_root=run_paths.inputs_root)
controller.write_flush(p, workload)
```

`get_workload`（`graph_generator.py`内、`input_path`ベースでファイル名を組み立てる
ヘルパー）が `.et` ファイルのパス文字列を作り、`Controller.write_flush`
(`controller.py:32`) がそれをASTRA-Simの標準入力に1行書き込んで `flush()` する。
ASTRA-Simはこの行を読んで該当ワークロードのシミュレーションを再開し、次のイテレーション
完了時に再び `"sys[...] iteration ... finished, ..."` を出力 → ループの先頭
（7.1）に戻る。

### 7.8 スループット・メモリ使用率のログ (`__main__.py:779-894`)

`--log-interval`（デフォルト1.0秒 = `current`のns換算で1e9ごと）を超えるたびに:
```
[1.0s] Avg prompt throughput: 115.0 tokens/s, Avg generation throughput: 313.0 tokens/s
        ├─Running Instance[0]: 6 reqs, Waiting: 0 reqs, Total # 1 NPUs, Each NPU Memory Usage ...
        └─Node[0]: Total CPU Memory Usage ...
```
のようなツリー表示をRichで出力する。前回の観察記録どおり、これはRichの
ライブレンダリングに依存しているため、`docker exec`越しのログをそのままパイプで
キャプチャすると行の上書き（carriage return）がテキストとして混ざり文字化けする
（実データはCSV出力を見た方が正確）。

### 7.9 終了判定 (`__main__.py:895-943`)

各インスタンスについて「待ちリクエストが無く（`is_request_empty()`）、
ルーターの未ルーティング分・エージェントセッションの残りも無い」状態になったら
`done_instance` に加える。全インスタンスが完了したら:
```python
schedulers[inst_idx].memory.free_prefix_cache()
schedulers[inst_idx].memory.free_weight()
schedulers[inst_idx].memory.is_free()   # メモリリークチェック
controller.write_flush(p, "exit")
break
```
でASTRA-Simに `"exit"` を送りループを抜ける。まだ生きているインスタンスには
`"done"`（そのインスタンスをスリープさせる）、送るものが無いだけならDP同期用の
分岐（今回は使わない）を経て `"pass"` を送る。

## 8. ループ後: 集計・出力 (`__main__.py:948-1035`)

- `controller.check_end(p)` (`controller.py:23`) — ASTRA-Simが
  `"All Request Has Been Exited\n"` を出すまで読み切り、正常終了を確認。
- prefix caching統計を全インスタンス分集計（今回は該当リクエスト間に共通プレフィックス
  が無いため hit ratio 0%）。
- `Total requests` / `Total clocks (ns)` / スループットなどのサマリを `print_markup` で
  出力（このセクションが「Simulation results...」以下の表）。
- `schedulers[i].print_result()` (`scheduler.py:876`) — インスタンスごとのTTFT/ITL統計。
- `--output` 指定時: `schedulers[i].save_output(output_file, ...)` (`scheduler.py:922`)
  が各リクエストの `id, model, input, output, arrival, end_time, latency, queuing_delay,
  TTFT, TPOT, ITL` をCSVに書き出す（`outputs/example_single_run.csv`）。
- `--cleanup-inputs`（デフォルトON）: `_cleanup_inputs_root()` (`__main__.py:86`) が
  `astra-sim/inputs/runs/<run_id>/` 以下を丸ごと削除。安全のため、削除対象が
  `inputs/` や `inputs/runs/` そのものでないことを事前にチェックしている
  （誤って全run分を消さないためのガード）。

## 9. このコマンド固有の設定が処理に与えた影響まとめ

| 指定 | 影響したコード |
| --- | --- |
| `--dtype bfloat16` | `resolve_variant()` が探すCSVフォルダを`bf16`に固定。`float16`指定だと`fp16`フォルダを探しに行き、未プロファイルなら`FileNotFoundError`（実際に前回遭遇） |
| `--block-size 16` | `MemoryModel`のKVキャッシュブロック単位（トークン数）。`memory_model.py`のブロック計算に反映 |
| `--dataset workloads/example_trace.jsonl` | `Router.load_requests`が読み込む対象。フラット形式10行 |
| `--num-req 10` | `Router.load_requests`のロード上限（今回は全件と一致） |
| `--output outputs/example_single_run.csv` | `Scheduler.save_output`の出力先 |
| （デフォルト）`--enable-prefix-caching` | `Scheduler.schedule`が`schedule_with_prefix`経路を通る（RadixTreeでのprefix照合が走るが、このデータセットではhitしない） |
| （デフォルト）`--enable-chunked-prefill` | `schedule_base`/`schedule_with_prefix`内でデコード優先→プレフィルchunk化のロジックが有効 |
| （デフォルト）`--max-num-seqs 128` / `--max-num-batched-tokens 2048` | 今回のリクエスト数・トークン数に対して十分大きく、実質無制限として動作 |

## 10. 参照ファイルマップ

| 役割 | ファイル |
| --- | --- |
| エントリポイント・メインループ | `serving/__main__.py` |
| クラスタ設定→ASTRA-Sim入力生成 | `serving/core/config_builder.py` |
| 連続バッチングスケジューラ | `serving/core/scheduler.py` |
| リクエストルーティング・エージェントセッション | `serving/core/router.py` |
| プロファイル済み遅延→トレース生成 | `serving/core/trace_generator.py` |
| テキストトレース→Chakra `.et` 変換呼び出し | `serving/core/graph_generator.py` |
| ASTRA-Simサブプロセスとの行ベースIPC | `serving/core/controller.py` |
| NPU/CPU/CXLメモリ・KVキャッシュ管理 | `serving/core/memory_model.py` |
| Request/Batchデータクラス | `serving/core/request.py` |
| run_id・入力ディレクトリのパス管理 | `serving/core/run_paths.py` |
| モデルconfig読み込み等 | `serving/core/utils.py` |
| Richベースのログ・コンソール出力 | `serving/core/logger.py` |

## 関連メモ
- 実行結果の考察は `Diary/output/2026-07-02-single-instance-tutorial.md` を参照。
