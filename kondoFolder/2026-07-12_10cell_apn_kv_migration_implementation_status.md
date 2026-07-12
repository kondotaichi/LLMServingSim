# 10セルAPN・KV migrationシミュレーション 実装状況

対象仕様: `kondoFolder/2026-07-12_10cell_apn_kv_migration_simulation_spec.md`
関連計画ファイル: `~/.claude/plans/compressed-bubbling-galaxy.md`

## 1. 経緯

仕様書の内容を実装済みコードと突き合わせた結果、ルーティングポリシー
（`NEAREST_KV`/`NEAREST_REJECT`/`NEAREST_MIGRATE`/`NEAREST_MIGRATE_KV`）自体は
既に実装済みだったが、以下3点が未実装で、仕様どおりのコマンドを組めない状態
だった。

1. GPU間・redirect時の**固定APN片道伝搬遅延（300,500ns）**。既存実装は常に
   「物理距離×固定ns/m係数」で伝搬遅延を計算しており、仕様は「架空の距離へ
   換算して代用しない」ことを明示的に要求していた。
2. **CPUステージングを含むKV migration時間モデル**（source GPU→CPU→APN→
   CPU→target GPU の直列合計、仕様8.3）。既存実装は単一ホップの
   距離＋serializationのみだった。
3. 仕様が指定する**固定3-4-3千鳥格子GPU座標＋Voronoi層化20ユーザ配置＋
   `kv_reuse_ratio`**を生成するワークロードジェネレータ、および
   **10ノードRTX4090クラスタ設定**が存在しなかった。

上記を実装し、既存の動作（新フラグ未指定時）が変化しないことをbefore/after
比較で確認した。

## 2. 加えた修正

### 2.1 `serving/core/router.py`

- `Router.__init__`に3つの新規kwargを追加（すべてデフォルト`None`）。
  - `apn_fixed_propagation_ns`
  - `kv_staging_bandwidth_gbytes_per_s`
  - `kv_staging_latency_ns`
- `_maybe_reject_and_redirect`（UE resend / `NEAREST_REJECT`）: `apn_fixed_propagation_ns`が
  設定されている場合、容量確認往復（`reject_penalty_ns`）と第2近傍GPUへの
  resend伝搬遅延を、距離比例式の代わりに固定値（RTTは片道の2倍）で計算する
  よう分岐を追加。
- `_maybe_migrate_and_redirect`（GPU forward / `NEAREST_MIGRATE`）: GPU_A→GPU_B
  のbackbone転送、およびGPU_B→UEのdownlinkの両方の伝搬遅延を同様に分岐。
  あわせて、`apn_fixed_propagation_ns`が指定されていれば
  `--gpu-backbone-distance-m`を必須としないようバリデーションを緩和。
- `_apply_kv_migration_if_needed`の`migrate_kv`分岐（`NEAREST_MIGRATE_KV`）:
  - APN伝搬遅延を同様に固定値へ分岐可能に変更。
  - `kv_staging_bandwidth_gbytes_per_s`と`kv_staging_latency_ns`の両方が
    指定されている場合のみ、source側・target側のCPUステージング遅延を
    APN転送の前後に追加する式（仕様8.3の3項直列モデル）を実装。両方とも
    未指定なら従来どおり単一ホップのまま（後方互換）。
- `_has_capacity`をsequence slotだけの判定から、次のAND条件へ拡張。
  - `running_reqs < max_num_seqs`
  - block丸めした再利用prefixと次回prefill chunkのKV容量が、現在のNPU物理空き容量以内
  - 次回chunkは`max_num_batched_tokens`と`long_prefill_token_threshold`を考慮
  - evict可能領域は空きに含めず、evictionが必要ならredirect対象

新規kwargがすべて`None`の場合、既存の距離比例モデルの計算式は一切変更して
いない。

### 2.2 `serving/__main__.py`

以下3つのCLIフラグを追加し、`Router(...)`呼び出しへ渡すよう配線した。

- `--apn-fixed-propagation-ns`
- `--kv-staging-bandwidth-gbytes-per-s`
- `--kv-staging-latency-ns`

### 2.3 `workloads/generators/cell_apn.py`（新規） + `cell-apn`サブコマンド

`regional-ratio`の出力（`user_id`/`gpu_id`/`region`/`arrival_time_ns`が付与済み）
を入力とし、以下を追加する新規ジェネレータ。`workloads/generators/__main__.py`
へ`cell-apn`として配線済み。

- 仕様4.2の10GPU固定座標（3-4-3千鳥格子）をハードコード。
- `random.Random(42)`の単一ストリームで、各GPUのVoronoiセル内に2ユーザずつ
  棄却法（rejection sampling）で層化配置。配置後に「最寄りGPU＝割当GPU」を
  アサーションで検証（仮定せず必ず確認）。
- 各ユーザの実座標から最近傍・第2近傍GPUを再計算し、
  `second_nearest_gpu_id`/`second_nearest_distance_m`を付与。
- `reuse_prefix_toks = floor(floor(input_toks * kv_reuse_ratio) / block_size) * block_size`
  （仕様7.2、`--kv-reuse-ratio`デフォルト0.5、`--block-size`デフォルト16）。
- `network_throughput_mbps=10700`（10.7Gbit/s、APN帯域と一致させるため）。
- `arrival_time_ns`は**そのまま維持**し、別途UEアップリンク遅延は加算しない
  （`regional-ratio`出力の到着時刻は既にGPU到着時刻であり、二重計上を避ける
  ため）。`uplink_*`/`downlink_*`/`communication_latency_ns`列も出力しない
  （`request.py`側で未設定時は自動的に0扱いになるため）。
- `users.csv`/`gpus.csv`/`metadata.json`もあわせて出力。

### 2.4 `configs/cluster/ten_node_rtx4090_apn.json`（新規）

- `num_nodes: 10`、各ノード`num_instances: 1`（物理ノード数10、1ノード1GPU）。
- 各ノードの`cpu_mem`: `mem_size: 128, mem_bw: 33.8, mem_latency: 102.9`
  （仕様8.1）。
- 各インスタンス: `hardware: RTX4090`、`npu_mem: {mem_size: 24, mem_bw: 1008,
  mem_latency: 0}`（仕様3章）、`model_name: meta-llama/Llama-3.1-8B`、
  `tp_size: 1`。
- トップレベルの`link_bw`/`link_latency`はASTRA-SimのNoC設定用で、本実験は
  TP=1・PP=1で cross-node collective が発生しないため実質不使用
  （既存configの値を流用）。

## 3. 検証したこと

- **後方互換性の確認**: 既存のスモークワークロード
  （`geo_2gpu_test.json` + `geo_reject_smoke_workload.jsonl`、
  `NEAREST_MIGRATE`）に対し、router.py修正の**前後**でシミュレーションを
  実行し、出力CSVが**完全に一致**することを確認した（新フラグ未指定時の
  挙動が変わっていないことの直接証拠）。
- **`cell-apn`ジェネレータの動作確認**: 実際に300件の本番ワークロード
  （`workloads/generated/wildchat_regional/sharegpt_300_weekday_ratio_1min.jsonl`）
  に対して実行し、出力を検査した。
  - 例: `user_id=17`は`region=Texas`→`gpu_id=8`に割当られ、`users.csv`上でも
    `assigned_gpu_id=8`・最近傍距離が実座標から計算した値と一致。
  - 全20ユーザの`assigned_gpu_id`（最近傍）と`second_nearest_gpu_id`が
    地理的に妥当な値になっていることを確認。
- **CLI/構文チェック**: 変更・新規追加した全Pythonファイルの`py_compile`、
  新クラスタ設定JSONの`json.load`、`python -m serving --help`および
  `python -m workloads.generators cell-apn --help`の出力を確認し、フラグが
  正しく解釈されることを確認した。
- **実際のシミュレーション本実行（`max_num_seqs=1`本番300件、および
  `max_num_seqs=128`）はまだ行っていない**（後述）。

## 4. 今後行うべきこと

### 4.1 シミュレーション本実行（未着手）

- `cell-apn`で生成した300件ワークロードに対し、4方式
  （`NEAREST_KV`/`NEAREST_REJECT`/`NEAREST_MIGRATE`/`NEAREST_MIGRATE_KV`）×
  2条件（`max_num_seqs=1`機能確認 → `max_num_seqs=128`本設定）＝計8回の
  `python -m serving`実行が必要（コマンドは計画ファイル参照）。
- **注意**: 手元のDocker環境（`servingsim_docker`、`astrasim/tutorial-micro2024`
  イメージ）はApple Silicon上でx86_64エミュレーション実行になっており、
  1バッチごとにサブプロセス（Chakra converter + ASTRA-Sim）を起動する
  現行アーキテクチャと組み合わさって非常に遅い。試しに10GPU・
  `max_num_seqs=1`で300件本番実行を開始したところ、25分でようやく
  1インスタンスあたり100〜150バッチ程度までしか進まなかった
  （300件・出力トークン数百〜のワークロードでは全体で数時間〜それ以上かかる
  見込み）。本実行前に以下のいずれかを検討したい。
  - x86ネイティブ環境（Linux実機やクラウドインスタンス等）での実行。
  - 実行時間を許容し、バックグラウンドで長時間走らせる。
  - まずは`--num-reqs`で件数を絞った縮小版で結果の傾向を確認してから本実行に
    進む。
- 機能確認（`max_num_seqs=1`）では、`rerouted`列・`kv_migration_bytes`列が
  0でないことをチェックリスト（仕様11章）どおり確認する。

### 4.2 出力・可視化（未着手）

- 方式別リクエスト単位CSV・ユーザ単位集計CSV・GPU単位集計CSV・
  metadata JSONの生成は`--output`/`--geographic-user-output`/
  `--geographic-gpu-output`/`--geographic-metadata-output`で対応可能（配線済み・
  既存フラグ）だが、実行していないため未生成。
- TTFT breakdown図・TTFT CDF図・GPU別リクエスト数図・GPU別redirect件数図・
  KV migration時間分布図（仕様10.3）は未着手。既存の`exp212`成果物
  （`outputs/exp212_*`、`outputs/image/*_ttft_plots.ipynb`）にプロット生成
  ロジックがNotebook埋め込みの形で存在するのみで、再利用可能なスクリプトは
  ないため、Notebookを流用するか新規にプロットコードを書く必要がある。

### 4.3 未着手のまま仕様に残っている項目

- **9.3 Prefix cache無効ケース**（`NEAREST`/`NEAREST_REJECT`/`NEAREST_MIGRATE`の
  coldベースラインと、KV有無比較）は今回のスコープ外としており未着手。
- **`kv_reuse_ratio`感度分析**（0.2/0.5/0.8の比較、仕様7.2）は`cell-apn`の
  `--kv-reuse-ratio`フラグで対応可能だが、複数回実行して比較する工程は未実施。

### 4.4 後片付け

- 途中で中断した実行が生成した`astra-sim/inputs/runs/`配下の中間ファイルは
  クリーンアップ済み（`cellapn-*`、`backcompat-*`）。
- 生成物出力先は方式ごとに別パス（`outputs/cell_apn/<POLICY>/...`）に
  分離する設計としており、共通入力ワークロードを上書きしない。
