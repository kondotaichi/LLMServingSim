# LLMServingSim まとめ — 何ができるか / 仕組み / 限界 / 今回のアップデート

作成日: 2026-07-08
対象ブランチ: `experiment/sim-hack`

このドキュメントは、LLMServingSim（本リポジトリのシミュレータ）が何を
シミュレートしていて、何を入出力とし、どういう仕組みで動いていて、どこまで
信頼できて、今回のセッションで何を追加したかを一次情報（コード・diff・
Diary記録）から整理したものです。

---

## 1. LLMServingSimは何をするシミュレータか

**サイクルレベルのLLMサービングシミュレータ**。実際にvLLMを都度起動して
推論させるのではなく、事前にvLLMで実測したカーネル単位のlatencyテーブル
（プロファイル）を引いて、リクエストのTTFT/TPOT/スループットを計算する。

構成は2つに分かれる。

| レイヤ | 実装 | 担当 |
| --- | --- | --- |
| Pythonフロントエンド (`serving/`) | このリポジトリ | リクエストのルーティング・スケジューリング（continuous batching, chunked prefill, prefix caching）、メモリ会計、trace生成 |
| C++バックエンド (`astra-sim/`, ASTRA-Sim) | サブモジュール | compute/collective通信のサイクル数計算（ネットワークトポロジ込み） |

両者はサブプロセスパイプ（stdin/stdout）で、ワークロードファイルのパスと
「Waiting + cycle数」だけをやり取りする単純なプロトコルで通信する。

できることの例（`docs/docs/getting-started/overview.mdx`より）:

- TP / PP / EP / DP+EP を組み合わせた任意のクラスタトポロジ
- Chunked prefill、prefix caching（RadixAttention、NPU/CPU/CXL階層）
- MoE expert routing、KVキャッシュのCXL/CPUへのoffload
- 新しいGPUやCXL、PIMデバイスをプロファイルして即座にシミュレーションに投入
- ShareGPTトレース、agenticセッション（ツール呼び出しを挟む依存チェーン）
- 実vLLM（v0.19.0）に対しTTFT/TPOT/throughputでsub-3%誤差の検証済み
- 今回のセッションで追加：地理分散環境でのUE↔GPU間通信・GPUリダイレクト・
  KVキャッシュ移送/障害復旧のシミュレーション（詳細は5節）

---

## 2. 入力・出力

### 2.1 入力

| 種類 | パス | 内容 |
| --- | --- | --- |
| クラスタ設定 | `configs/cluster/*.json` | ハードウェア種別、`model_name`、`tp_size`/`pp_size`/`ep_size`、NPU/CPUメモリ帯域、リンク帯域・遅延など |
| モデル設定 | `configs/model/{org}/{model}.json` | HF `config.json` のサブセット（`hidden_size`, `num_attention_heads`, `head_dim` など） |
| プロファイルCSV | `profiler/perf/<hw>/<model>/<variant>/tp<N>/{dense,per_sequence,attention,moe,skew,skew_fit}.csv` | vLLMを実機で動かして採取した実測レイヤ別latency（マイクロ秒） |
| ワークロードJSONL | `workloads/*.jsonl` | 1行1リクエスト。`input_toks`/`output_toks`/`arrival_time_ns`が基本。flat / agentic（`sub_requests[]`）/ geographic（`user_id`,`distance_m`等）/ failover（`failover_mode`等）の各拡張フィールドを持てる |
| CLI引数 | `python -m serving ...` | ルーティングポリシー、スケジューラのバジェット（`--max-num-seqs`, `--max-num-batched-tokens`）、prefix caching有無など |

### 2.2 出力

| 種類 | 出力先 | 内容 |
| --- | --- | --- |
| リクエスト単位CSV | `--output` で指定したパス | 1リクエスト1行。`instance id, request id, input, output, arrival, end_time, latency, queuing_delay, TTFT, TPOT, ITL` が基本列。地理/KV/リダイレクト系の追加列は6節参照 |
| スループットログ | 標準出力（`--log-interval`秒毎） | `prompt_t`/`decode_t`（tok/s）、`npu_mem`、prefix hit率、PIM使用率、power等、有効な機能に応じて表示内容が変わる |
| 電力サマリ | 標準出力（クラスタ設定に`power:`がある場合、終了時） | NPU active/standby/idle、CPU、DRAM、Link等のノード別エネルギー内訳 |
| 地理ワークロード集計CSV | `--geographic-user-output` / `--geographic-gpu-output` 等 | ユーザ単位・GPU単位の集計（今回追加、5節） |

---

## 3. どういう仕組みでシミュレーションしているか

### 3.1 プロファイリング（事前準備）

`profiler/`がvLLMの`layerwise_profile()`を使い、実GPU上でレイヤ単位の
カーネル時間を採取する。TP度数は常に`tensor_parallel_size=1`で起動し、
`hidden_size`等を割って各TPランクの形状をエミュレートする（collective自体の
時間はASTRA-Sim側が計算するのでプロファイル対象外）。出力は
`dense.csv`（token数線形）、`per_sequence.csv`（シーケンス数線形）、
`attention.csv`（4次元: prefill_chunk, kv_prefill, n_decode, kv_decode）、
`moe.csv`（tokens × activated_experts）などのCSVバンドル。

### 3.2 メインループ（実行時）

`serving/__main__.py`が駆動する10ステップループ:

1. CLI引数パース、クラスタ設定ロード
2. `config_builder`がASTRA-Sim用入力（`network.yml`, `system.json`,
   `memory_expansion.json`）を生成
3. インスタンス毎のSchedulerとグローバルRouterを初期化、データセットをロード
4. ASTRA-Simサブプロセスを起動
5. `controller.read_wait()`でASTRA-Simから"Waiting"を待つ（あるNPUの
   1イテレーション完了通知）
6. `router.route_arrived_requests(current)`で到着済みリクエストを各
   Schedulerのキューへ投入
7. `scheduler.schedule(current, sys)`がバッチ（`Batch`または`None`）を決定
   （vLLM V1スタイルのcontinuous batching + chunked prefill）
8. バッチがあれば`trace_generator`がプロファイルCSVを引いてレイヤ毎の
   compute_timeを含むテキストtraceを生成 → Chakra converterがprotobuf
   `.et`グラフに変換 → `controller.write_flush`でASTRA-Simへパスを渡す
9. DPグループがあれば全メンバーのスケジュールが揃うまでtrace送信を遅延
   （ALLTOALLの`comm_size`を同期）
10. ASTRA-Simがサイクル数を返したら`scheduler.add_done(...)`でリクエスト
    状態・メトリクスを更新。全インスタンスidle かつ pending/deferred
    request が無くなったら終了、CSV書き出し

つまり「Pythonが何を計算するか決め、C++（ASTRA-Sim）がそれに何サイクル
かかるかを計算する」役割分担。両者は同じシミュレーション時計（`current`,
ナノ秒単位）を共有する。

### 3.3 レイテンシの引き方

`trace_generator._lookup_dense/_lookup_per_sequence/_lookup_attention/_lookup_moe`
が該当カテゴリのCSVをtoken数・シーケンス数・4次元キー(prefill_chunk,
n_decode)+双線形(kv_prefill, kv_decode)・(tokens, activated_experts)で検索する。
プロファイルの実測点以外は**すべて線形外挿**（クランプしない）。decodeバッチが
不均一な長さを持つ場合の追加コストは`skew_fit`（5軸bucketごとのα値）で
mean→max直線上の補正として反映する。

---

## 4. 限界（このシミュレータが表現できないこと）

1. **実カーネル実行ではなく実測テーブルの参照+補間/外挿。**
   プロファイルでカバーしていない条件（極端なバッチサイズ、未知のtoken長域
   など）では外挿精度が落ちる。`meta.yaml`の`engine_effective`とCLI引数の
   差がある場合は一度だけ警告が出る。
2. **KVキャッシュは実tensorを保持しない。**
   RadixCacheのmetadata（token列のhash）とtoken数から計算したbyte量
   （`memory_model.get_kv()` = `2 * kv_dim * seq * n_layer * kv_fp // num_npus`）
   としてのみ扱う。KV移送も「メタデータを事前登録し、byte量×帯域で転送時間を
   課金する」抽象化であり、実際のテンソルシリアライズ/転送は模擬していない
   （`Diary/output/2026-07-04-kv-cache-failover-migration-report.md`）。
3. **ネットワークは`propagation_only`モデル。**
   距離比例の伝搬遅延 + payloadサイズ÷帯域のシリアライズ時間のみ。輻輳、
   ジッタ、パケットロス、再送、RANスケジューリングは考慮しない
   （`Diary/output/2026-07-03-geographic-rtt-communication-breakdown.md`）。
4. **障害（fault injection）は自動検知ではない。**
   「GPU Aが落ちてGPU Bが引き継ぐ」ケースは、ワークロードJSONL側で
   `failover_mode`/`target_instance_id`を明示するか、`NEAREST_REJECT`/
   `NEAREST_MIGRATE`系のリダイレクトポリシーが容量超過を検知した時にだけ
   発生する。クラスタ全体の自動障害復旧ポリシーの評価には追加実装が必要
   （同レポート「注意点」節）。
5. **リダイレクト/移送は最大1回、2番目近傍のみ。**
   `NEAREST_REJECT`/`NEAREST_MIGRATE`/`NEAREST_MIGRATE_KV`とも、一度
   リダイレクトされたリクエストは再リダイレクトされない。3台以上のGPUへの
   カスケード的な再配置は未実装。
6. **未解明のギャップが既知。**
   `example_trace.jsonl`（非地理ワークロード）で、TTFTからqueuing_delayを
   引いた実行時間が、プロファイルCSVの単純なレイヤ別合計の約2.4倍になる
   ケースが観測されており、原因はASTRA-Sim側のイテレーション粒度か
   メモリ/ネットワークモデルにあると推測されるが未特定
   （`2026-07-03-geographic-rtt-communication-breakdown.md`結論4）。
7. **単体テストスイートが無い。**
   `AGENTS.md`に明記の通り、変更の妥当性は個別の`python -m serving`実行と
   出力CSV確認、または`python -m bench validate`（実vLLM比較）で担保する
   運用。

---

## 5. 今回（このセッション/ブランチ）加えたアップデート

`git diff`（作業ツリーの未コミット変更）と`Diary/`の記録から、今回のテーマは
一貫して **「地理的に分散したUEとGPU間で、キューイングと通信/移送コストの
トレードオフをどうシミュレートするか」** であり、以下を段階的に積み上げた。

### 5.1 地理ワークロード生成 (`workloads/generators/geographic.py`)

- ユーザ座標・GPU座標から**最近傍GPU**を実距離で計算してリクエストを割り当てる
  ジェネレータ（既存）。
- 今回、`_two_nearest_gpus()`を追加し、**2番目近傍GPU**の距離とIDも同時に
  出力するよう拡張（`second_nearest_gpu_id`, `second_nearest_distance_m`）。
  これが後述のリダイレクト系ポリシーの入力になる。
- `--kv-reuse-prefix-toks`（各リクエストに`reuse_prefix_toks`を付与）、
  `--kv-migration-bandwidth-gbps`/`--kv-migration-distance-m`（行単位の
  KV移送条件オーバーライド）を追加。

### 5.2 KVキャッシュ障害復旧/移送（静的指定、既存機能の起点）

`Diary/output/2026-07-04-kv-cache-failover-migration-report.md`で実装。
ワークロードJSONLの各行に`failover_mode`（`cold`/`migrate_kv`）,
`failed_instance_id`, `target_instance_id`, `reuse_prefix_toks`を書くことで、

- `cold`: ターゲットGPUでプロンプト全体をゼロからprefill
- `migrate_kv`: `reuse_prefix_toks`分のKVメタデータをターゲットGPUの
  prefix cacheへ事前登録（`MemoryModel.seed_migrated_prefix()`）し、
  そのbyte量に対して帯域・距離ベースの移送遅延を課金してから、残りだけ
  prefillする

を比較できるようにした。実験では、1024トークンのprefixを移送するコスト
（約10.79ms）より、prefillを1152→128トークンに減らせる恩恵（約35.92ms）が
大きく、`migrate_kv`が約24.92ms速いという結果が出ている。

### 5.3 容量超過時リダイレクト系ルーティングポリシー（今回の中心）

`serving/core/router.py`に4つの新しいルーティングポリシーを追加
（`serving/__main__.py`の`--request-routing-policy`のchoicesにも追加）。
いずれも「最近傍GPUに空きがなければ2番目近傍GPUへ、最大1回だけリダイレクト」
という判定ロジックは共通で、リダイレクトに伴う追加コストのモデル化と
KVキャッシュの扱いだけが異なる。

| ポリシー | 空きが無い時の挙動 | KVキャッシュ |
| --- | --- | --- |
| `NEAREST` | 常に最近傍GPU固定（既存） | — |
| `NEAREST_KV` | 常に最近傍GPU固定 | `reuse_prefix_toks`を最近傍GPUのprefix cacheへ移送コストなしでseed（同一GPU上に既にある想定） |
| `NEAREST_REJECT` | UEに一旦差し戻し、2番目近傍GPUへ再送（UE往復コストを課金） | 引き継がない（cold） |
| `NEAREST_MIGRATE` | GPU間バックボーン経由でサーバ側から直接転送（UE往復は課金しない、GPU間ホップのみ） | 引き継がない（cold） |
| `NEAREST_MIGRATE_KV` | `NEAREST_MIGRATE`と同じ転送 | 転送先GPUへ`reuse_prefix_toks`分のKVも移送してseed |

`NEAREST_MIGRATE`系のためにCLIへ`--gpu-backbone-bandwidth-gbps`/
`--gpu-backbone-distance-m`（UE↔GPUのアクセス回線とは別の、GPU間バック
ボーン回線のパラメータ）を追加した。

途中で「NEAREST_REJECT/NEAREST_MIGRATEは常にcold prefill、
NEAREST_KV/NEAREST_MIGRATE_KVは常にローカルキャッシュヒット」という
不公平な比較になっていた設計ミスに気づき、**4ポリシー全てが「各リクエストの
prefixは元々自分のホームGPUにキャッシュ済み」という同じ前提から出発し、
リダイレクトされた場合にそのキャッシュが追従するかどうかだけがポリシー毎に
異なる**、という公平な比較になるよう`router.py`を修正した（コード中
`SPEC: fair-comparison KV baseline`コメント）。

### 5.4 CSV出力・Requestフィールドの拡張

`serving/core/request.py` / `serving/core/scheduler.py`に以下を追加:

- `nearest_gpu_id`, `rerouted`, `reject_penalty_ns`, `migration_latency_ns`
  （リダイレクト系ポリシー専用、他ポリシーでは0/空欄）
- 既存の`failover_mode`, `kv_migration_*`列は維持（`local_kv`モードを追加）
- TTFTのボトルネック分析（`ttft_bottleneck`, `communication_ratio`,
  `queueing_ratio`, `prefill_ratio`, `decode_ratio`等）は本セッション以前に
  導入済みで、今回はそこへ`migration_latency_ns`等を足す形で拡張した

### 5.5 検証実験（RTX4090構成）

`configs/cluster/single_node_multi_instance_rtx4090.json`等の新設定と、
`Diary/output/2026-07-05〜07-07`の一連のレポートで、Llama-3.1-8B /
RTX4090 / `max_num_seqs=24`環境で4ポリシーを比較。詳細な数値は6.3節、
または`Diary/output/2026-07-07-exp212-simulation-environment-summary.md`
を参照。

---

## 6. `queue` / `kv` / `share` / `compute` / `rtt` の定義

今回の実験レポート（特に`2026-07-05-exp212-kv-cache-1to2-routing-report.md`,
`2026-07-07-exp212-simulation-environment-summary.md`）で使われている
指標名を、CSV列・コード上の計算式にひもづけて定義する。

### queue（キュー待ち）

- **CSV列**: `queueing_before_ttft_ns`（列名は`queueing_before_ttft_ns`、
  レポート中の「Queue」はこれの平均/合計）
- **定義**: リクエストがGPUに到着してから、実際に最初のバッチへ組み込まれ
  computeが始まるまでの待ち時間。`Request.account_admission()`が
  `waiting_since_ns`から現在時刻までの差分をTTFT前は
  `queueing_before_ttft_ns`、TTFT後（decode中の再待ち）は
  `decode_queueing_ns`に積算する。
- **発生条件**: `scheduler.py`はvLLM V1スタイルのcontinuous batchingで、
  **同時実行中リクエスト数が`--max-num-seqs`を超えられない**ことが唯一の
  ハード上限（プロンプト長やトークン予算とは独立）。あるGPUへの割当総数が
  `max_num_seqs`を超えていれば、到着タイミングを分散させてもキューは
  解消されず蓄積し続ける。

### kv（KVキャッシュ移送コスト）

- **CSV列**: `kv_migration_latency_ns`（内訳:
  `kv_migration_distance_latency_ns` + `kv_migration_serialization_latency_ns`）、
  移送量は`kv_migration_tokens`/`kv_migration_bytes`
- **定義**: GPU間でKVキャッシュ（のメタデータ相当byte量）を転送するのに
  かかる時間。`kv_migration_bytes = reused_tokens × memory_model.get_kv(1) × num_npus`
  相当のbyte数を、指定帯域・距離（`kv_migration_bandwidth_gbps` /
  `kv_migration_distance_m`、既定100Gbps・10km、`NEAREST_MIGRATE_KV`では
  `--gpu-backbone-*`にフォールバック）で転送するコスト。
  `migration_latency_ns = distance_m × 5ns/m（distance latency） + payload_bytes ÷ bandwidth（serialization latency）`
  という伝搬遅延+シリアライズ時間の合計（RTTと同じ式の形）。
- **レポートでの「KV transfer」列**はこれの平均値。ローカル再利用のみ
  （`NEAREST_KV`や`local_kv`）の場合は転送が発生しないため常に0。

### share（KVキャッシュ共有/再利用）

- **CSV列**: `reuse_prefix_toks`（要求された再利用トークン数）、
  `prefix_cache_hit`（Request内部、実際にヒットしたトークン数、CSV非出力）
- **定義**: 「あるプロンプトprefixのKVキャッシュを、計算し直さずに再利用する」
  仕組み全般。`memory_model.seed_migrated_prefix(token_ids, prefix_len)`が
  対象GPUのNPU RadixCacheへprefix metadataを事前登録し（block_size単位で
  切り下げ、メモリ不足時はevictable cacheをevict）、以降の通常のprefix
  matchingでその分が`prefix_cache_hit`としてヒットし、prefillから除外される。
  2つの経路がある。
  - **ローカル共有**（`NEAREST_KV`、または`failover_mode=local_kv`）:
    同一GPU上に既にキャッシュがある想定で、転送コストなしでseedするだけ。
  - **移送共有**（`NEAREST_MIGRATE_KV`、または`failover_mode=migrate_kv`）:
    別GPUへ実際に転送してからseedする。転送コストは上記kvの定義通り課金
    される。
- 「shareした分だけcompute（prefill）が短縮される」というのが、この一連の
  実験全体の狙い。

### compute（計算時間）

- **CSV列**: `prefill_service_ns`（レポートの「Compute」列はこれ、prefill側）。
  decode側は`decode_active_ns`が対応する。
- **定義**: 実際にGPU上でトークンを計算する時間。プロファイルCSVから
  引いたレイヤ別latency（`trace_generator`のlookup結果）の積み上げが
  ASTRA-Sim経由で返ってきたもの。`num_computed_tokens`は
  `prefix_cache_hit`分を差し引いた残りから始まるため、shareが効くほど
  `prefill_service_ns`は短くなる（例: 5.3節のKV migration実験で
  1152→128トークンのprefillに減り、49.31ms→13.40msへ短縮）。

### rtt（往復通信時間）

- **CSV列**: `communication_latency_ns`（= `uplink_latency_ns` +
  `downlink_latency_ns`、必要ならリダイレクト/KV移送分も加算）
- **定義**: UE↔GPU間の**アクセス回線**での、伝搬遅延＋シリアライズ時間の
  往復合計（`propagation_only`ネットワークモデル。輻輳・ジッタ・
  パケットロスは含まない）。
  ```text
  uplink_latency_ns   = round(distance_m * distance_latency_ns_per_meter)
                       + round(8000 * request_payload_bytes / network_throughput_mbps)
  downlink_latency_ns = round(distance_m * distance_latency_ns_per_meter)
                       + round(8000 * first_token_payload_bytes / network_throughput_mbps)
  communication_latency_ns = uplink_latency_ns + downlink_latency_ns
  ```
  既定値は`distance_latency_ns_per_meter=5.0`（光ファイバ相当）。
  `NEAREST_MIGRATE`系でリダイレクトが起きた場合は、GPU間バックボーン回線
  （`--gpu-backbone-*`、UE↔GPUのアクセス回線とは別パラメータ）の転送時間
  （`migration_latency_ns`）が別枠で加算される。
  **既存の検証実験では、RTTがTTFTに占める割合は一貫して1%未満**
  （距離レンジ数百m〜数kmでは通信は無視できるほど小さく、支配要因は常に
  queueとprefill compute）。

### 6.3 参考: 4ポリシー比較（RTX4090, 50リクエスト, GPU0:GPU1割当≈17:33, max_num_seqs=24）

`Diary/output/2026-07-07-exp212-simulation-environment-summary.md`より抜粋:

| 方式 | GPU0:GPU1割当 | rerouted | Mean E2E TTFT | Queue | KV transfer | Compute | RTT |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A `NEAREST_KV` | 17:33 | 0/50 | 2075.48ms | 2004.54ms | 0.00ms | 67.32ms | 3.62ms |
| B1 `NEAREST_REJECT` | 26:24 | 9/50 | 613.66ms | 450.92ms | 0.00ms | 156.67ms | 3.63ms |
| B2 `NEAREST_MIGRATE` | 26:24 | 9/50 | 613.66ms | 451.56ms | 0.00ms | 156.67ms | 3.63ms |
| C `NEAREST_MIGRATE_KV` | 26:24 | 9/50 | 495.11ms | 267.95ms | 155.36ms | 67.16ms | 3.63ms |

読み方: リダイレクトしない方式Aはキューが溜まり続けTTFTが最悪
（2004.54ms待ち）。B1/B2はリダイレクトで割当を均してQueueを約450msまで
下げるが、cold prefillのままなのでComputeは高いまま。Cはリダイレクト＋
KV共有の両方を使い、Queueをさらに下げつつComputeも67ms台に戻す（代わりに
KV transferコストが155ms乗る）。いずれの方式でもRTT（通信）は3.6ms程度で
TTFTに対しほぼ無視できる。
