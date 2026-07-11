# 検証2-1-2 実機再現 要件定義書

**文書バージョン:** 2.0(全面書き直し)
**作成日:** 2026-07-06
**宛先:** 実機2GPU環境(RTX4090 × 2、6km APN接続)側のClaude Code
**目的:** LLMServingSimでのシミュレーション結果(方法Cが優れているという結論)を、実機のvLLM 2台構成で検証する

---

## 0. このドキュメントについて

このドキュメントは、別マシン(以下「実機側」)のClaude Codeが、他の会話コンテキストを
一切参照せずにこの1ファイルだけを読んで作業を進められるように書かれている。実機側で
不明点があれば、まず本ドキュメントの「9. 既知の近似・要判断事項」を確認し、それでも
判断できない場合はシミュレーション側の担当者に確認すること。

---

## 1. これは何の実験か(最重要: シミュレーションとの差分はここだけ)

LLMServingSim上で、地理分散した2GPU環境を想定し、4つのリクエストルーティング方式
(`NEAREST_KV` / `NEAREST_REJECT` / `NEAREST_MIGRATE` / `NEAREST_MIGRATE_KV`)を
比較する実験(`exp212`)を行い、**方式C(`NEAREST_MIGRATE_KV`)が他の3方式よりTTFTで
優れている**という結果を得た。今回はこれを実機で検証する。

**今回の実機実験で、シミュレーションと変わる点は次の2つだけである。**

1. **GPU2台とGPU間通信が「実物」になる。** シミュレーションでは、GPU間のリクエスト
   転送・KVキャッシュ移送にかかる時間を`--gpu-backbone-bandwidth-gbps` /
   `--gpu-backbone-distance-m`というパラメータで解析式に代入して計算していた
   (シミュレーションでは1Gbps・5kmという値を使用)。実機ではこの部分を、
   実際にAPN網(約6km)を経由した実際のネットワーク通信に置き換える。
2. **推論を実際に行う。** シミュレーションでは、プロファイル済みの実測latency
   テーブルを引いてTTFT/TPOTを計算していた(実際にモデルを動かしてはいない)。
   実機では実際にvLLMでLlama-3.1-8Bを動かし、実際に推論する。

**それ以外は、シミュレーションと完全に同じにする。** 具体的には次の項目は
**変更しない・作り直さない**:

- GPUハードウェア: **RTX4090**(シミュレーション側で使ったプロファイルもRTX4090
  なので、今回の実機もRTX4090であれば、そのまま同じ設定・同じ想定で良い。
  ハードウェアが変わったことによる再検証は不要)
- GPU間通信のスループット: シミュレーションで使った値(1Gbps相当)を**そのまま
  引き継いで良い**と仮定する。実際に計測して大きく違うようであれば later
  差し替えれば良いが、今回は「実機のAPNもだいたい同じくらいのスループット」
  という前提で進めて構わない。距離だけ実際の6kmに変わる(シミュレーションの
  5kmという値は使わない)。
- ワークロードの中身(どのリクエストがどちらのGPUへ行くべきか、到着タイミング、
  プロンプト内容、KV再利用トークン数など): すべてシミュレーション側が生成した
  JSONLファイルをそのまま使う(後述2章・3章)。
- モデル・エンジン設定(`meta-llama/Llama-3.1-8B`、bf16、`max_num_seqs=24`など):
  シミュレーションと同じ値を使う。

つまりこの実験は「地理分散したユーザ群を実機で再現する」ものではない。**ユーザの
散らばり(100人が10km四方に分布し、どちらのGPUに近いか)は、シミュレーション側が
既に計算し終えてJSONLに焼き込んだ固定データとして扱い、実機側では一切再計算・
再現しない。** 実機で試すのは「GPU2台の実際の計算」と「GPU間の実際の通信」だけである
(詳細は9.1節)。

---

## 2. 転送すべき成果物(シミュレーション側から)

以下のファイルをシミュレーション側から実機側へコピーすること。パスはシミュレーション側
リポジトリ(`LLMServingSim`)のルートから見た相対パス。

| ファイル | 用途 |
| --- | --- |
| `workloads/generated/geo_2gpu100_kv_workload_1to2_n50.jsonl` | 今回使ったワークロード本体(50リクエスト)。**これをそのままコピーして使う**(再生成しない。乱数由来のユーザ割当・reuse_prefix_toks等を完全一致させるため) |
| `configs/cluster/single_node_multi_instance_rtx4090.json` | シミュレーション側で使ったクラスタ設定。GPUがRTX4090であることを前提に書かれているので、そのまま参考にできる |
| `results/exp212-1to2-nearest-kv.csv`<br>`results/exp212-1to2-nearest-reject.csv`<br>`results/exp212-1to2-nearest-migrate.csv`<br>`results/exp212-1to2-nearest-migrate-kv.csv` | シミュレーション結果(比較対象の基準データ) |
| `outputs/image/2026-07-05_exp212_n50_1to2_rtx4090_kv_with_reject_ttft_breakdown.png`<br>`outputs/image/2026-07-05_exp212_n50_1to2_rtx4090_kv_with_reject_ttft_cdf.png`<br>`outputs/image/2026-07-05_exp212_n50_1to2_rtx4090_kv_with_reject_ttft_plots.ipynb` | シミュレーション側のTTFT内訳・CDF可視化(9.2節で実機側が作る図の見た目の参考。notebookは実機のCSVパスに差し替えれば同じ形式で再利用できる) |

`geo_2gpu100_kv_workload_1to2_n50.jsonl`の1行(1リクエスト)には少なくとも次のフィールドがある。

```
input_toks, output_toks, arrival_time_ns, input_tok_ids, output_tok_ids,
request_id, user_id, user_x_m, user_y_m, request_send_time_ns,
assigned_instance_id, gpu_id, gpu_x_m, gpu_y_m, distance_m,
second_nearest_gpu_id, second_nearest_distance_m,
network_throughput_mbps, distance_latency_ns_per_meter,
request_payload_bytes, first_token_payload_bytes,
uplink_distance_latency_ns, uplink_serialization_latency_ns, uplink_latency_ns,
downlink_distance_latency_ns, downlink_serialization_latency_ns, downlink_latency_ns,
communication_latency_ns, gpu_arrival_time_ns,
reuse_prefix_toks, kv_migration_bandwidth_gbps, kv_migration_distance_m
```

実機再現で実際に使うのは主に次のフィールド:

- `input_tok_ids` / `output_toks`: 実際にvLLMへ送るプロンプトのトークンID列と、生成させる出力トークン数
- `arrival_time_ns`: リクエストを送出するタイミング(相対時刻、最小値を0とみなして良い)
- `assigned_instance_id`: 最近傍GPU(0または1) — **この値をそのまま「どちらへ送るか」の答えとして使う**
- `second_nearest_gpu_id`: 2番目に近いGPU(リダイレクト先) — **この値をそのまま使う**
- `reuse_prefix_toks`: このリクエストのプロンプトのうち、先頭何トークン分が「既にキャッシュ済み」とみなせるか(KV再利用/移送の対象トークン数)

`distance_m` / `second_nearest_distance_m` / `network_throughput_mbps`(UE↔GPUのアクセス回線、10Mbps固定)は、**実機では再現しない**(9.1節)。1章で述べた通り、これらは実機で再計算せず、シミュレーションが既に計算した`uplink_latency_ns`/`downlink_latency_ns`をそのまま定数として使う。

---

## 3. 検証仮説(実機で確認したいこと)

> 方法C(`NEAREST_MIGRATE_KV`)は、GPU2台の実際の計算とGPU間の実際の通信(6km APN)の下でも、他の3方式(`NEAREST_KV`, `NEAREST_REJECT`, `NEAREST_MIGRATE`)よりTTFT(特にP95/P99)が優れているか。

比較する主指標: 平均TTFT, P50/P95/P99 TTFT, 平均総latency, リダイレクト率。

### 3.1 シミュレーションで実際に得られた目標値(比較の基準)

`Diary/output/2026-07-05-exp212-kv-cache-1to2-routing-report.md`に記録済みの
実際の数値。実機側はこれと同じオーダー・同じ順序関係が再現できるかを確認する
(厳密に同じ値になる必要はない。GPU計算・実ネットワークが入るため多少ずれるのは
当然だが、**方式間の大小関係と、内訳の支配的要因が一致するか**が本質)。

| 手法 | GPU0:GPU1割当 | rerouted | Mean E2E TTFT | P50 | P99 | Mean total latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| A `NEAREST_KV` | 17:33(リダイレクト無しなので不変) | 0/50 | 2075.48ms | 51.77ms | 11717.29ms | 17674.21ms |
| B1 `NEAREST_REJECT` | 26:24 | 9/50 | 613.66ms | 154.43ms | 9342.91ms | 17875.77ms |
| B2 `NEAREST_MIGRATE` | 26:24 | 9/50 | 613.66ms | 154.43ms | 9342.91ms | 17876.41ms |
| C `NEAREST_MIGRATE_KV` | 26:24 | 9/50 | 495.11ms | 51.77ms | 7277.78ms | 16668.64ms |

TTFT内訳(Queue / KV transfer / Compute / RTT。RTTの定義は7.1節参照):

| 手法 | Queue | KV transfer | Compute | RTT |
| --- | ---: | ---: | ---: | ---: |
| A `NEAREST_KV` | 2004.54ms | 0.00ms | 67.32ms | 3.62ms |
| B1 `NEAREST_REJECT` | 450.92ms | 0.00ms | 156.67ms | 3.63ms |
| B2 `NEAREST_MIGRATE` | 451.56ms | 0.00ms | 156.67ms | 3.63ms |
| C `NEAREST_MIGRATE_KV` | 267.95ms | 155.36ms | 67.16ms | 3.63ms |

読み方: Aはリダイレクトしないため1:2負荷の混雑側でQueueが支配的
(2004.54msのうち大半)。B1/B2はリダイレクトで負荷を均す分Queueが下がるが、
KVを引き継がないためCompute(cold prefill)が上がる。Cはリダイレクト+KV引き継ぎで
Queue・Computeとも下がるが、KV transferが追加コストとして乗る。それでも合計では
Cが最小になる、という関係が実機でも再現されるかが最大の関心事。

---

## 4. vLLMサーバ起動コマンド(2台とも)

各GPUで、OpenAI互換APIサーバとしてvLLMを起動する。この設定値は、同じRTX4090上で
別途行ったシミュレータ検証(bench)で**動作確認済みの値をそのまま使う**もので、
今回改めて調整する必要はない。

```bash
# GPU0側(nearest gpu_id=0)
python3 -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B \
  --dtype bfloat16 \
  --max-num-seqs 24 \
  --max-num-batched-tokens 2048 \
  --max-model-len 4352 \
  --kv-cache-dtype auto \
  --seed 42 \
  --enable-prefix-caching \
  --port 8000

# GPU1側(nearest gpu_id=1)
python3 -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B \
  --dtype bfloat16 \
  --max-num-seqs 24 \
  --max-num-batched-tokens 2048 \
  --max-model-len 4352 \
  --kv-cache-dtype auto \
  --seed 42 \
  --enable-prefix-caching \
  --port 8000
```

`--enable-prefix-caching`は常時有効にする(vLLMのAutomatic Prefix Caching、
以下APC)。4方式とも同一条件(APC常時有効)で比較すること。

### 4.1 `--max-model-len`の選び方(重要)

このチャートの形(方法Aで2005msのQueueが支配的、C以外はComputeが小さい等)は、
**GPUの詰まりが純粋に`--max-num-seqs 24`という「本数」の上限だけで起きている**、
というシミュレーションの前提が実機でも成立して初めて再現できる。

`--max-model-len`を必要以上に大きく取ると、vLLM起動時にKVキャッシュ用へ
確保できるブロック数が減り、「本数は24未満なのにKVキャッシュのメモリが
足りず待たされる」という、シミュレーションには無い別のボトルネックが
実機にだけ発生しうる。そうなるとTTFTの内訳がこのチャートと一致しなくなる
(Queueの原因が`max_num_seqs`なのかメモリなのか区別できなくなる)。

このため、**必要な長さぎりぎりに絞る**方針を取る。ワークロード
(`geo_2gpu100_kv_workload_1to2_n50.jsonl`)50件のうち、最長のリクエストでも
`input_toks + output_toks = 4197`トークンなので、`--max-model-len 4352`
(4197に少し余裕を持たせた値)を使う。

以前bench検証で使った`--max-model-len 8192`は、この実験とは無関係な
別の検証(単一リクエストの実機vLLM検証)で決めた値であり、そのまま
流用すると不必要にKVキャッシュブロックを圧迫するので**使わないこと**。

**起動後の確認手順:**

1. vLLM起動ログの`# GPU blocks: N`(またはそれに相当するKVキャッシュ
   ブロック数のログ)を確認する。
2. `N × 16(block_size) ≥ 24 × 4197`(最悪ケースで24本すべてが最長
   リクエストになっても収まるか)を確認する。厳密には全リクエストが
   同時に最長になることは無いので、これは十分条件でありもっと緩くても
   実際には動作しうるが、安全側の確認として推奨する。
3. 実験実行中、vLLMのログにpreemption/swapイベントが出ていないか確認する。
   出ている場合は`max_num_seqs`ではなくメモリが真のボトルネックに
   なっている証拠であり、8.1節のレポートにその旨を明記すること
   (この場合、`--max-model-len`をさらに下げるか`--gpu-memory-utilization`
   を上げて再確認する)。

---

## 5. 4方式それぞれの実機での実装方法

いずれも「ハーネス(Pythonスクリプト)がワークロードを再生し、GPU0/GPU1の
vLLM OpenAI互換APIへHTTPリクエストを送る」という共通構造。方式ごとの違いは
ハーネスのルーティングロジックにある。

### 5.1 共通: キャパシティ管理

各GPUについて、ハーネス自身が「現在そのGPUに送信済みで、まだ完了していない
リクエスト数」を管理する(vLLMサーバの`/metrics`エンドポイントの
`vllm:num_requests_running` + `vllm:num_requests_waiting`を使ってもよいが、
シミュレータの定義(`running_reqs = 実行中のリクエスト数`、`available =
running_reqs < max_num_seqs(=24)`)に合わせるため、まずはハーネス側の
自己管理カウンタで実装することを推奨)。

```python
inflight = {0: 0, 1: 0}  # gpu_id -> 現在の未完了リクエスト数
MAX_NUM_SEQS = 24

def has_capacity(gpu_id):
    return inflight[gpu_id] < MAX_NUM_SEQS
```

リクエスト送信時に`inflight[gpu_id] += 1`、完了時(ストリーミングの場合は
最初のトークン受信ではなく**リクエスト完了時**)に`inflight[gpu_id] -= 1`する。

### 5.2 `NEAREST_KV`(方法A相当)

1. `assigned_instance_id`のGPUへ送信する(容量チェック無し、常にそこへ送る。
   キューはvLLM自身のスケジューラに任せる)。
2. `reuse_prefix_toks > 0`の場合、**このリクエストを送る前に、先頭
   `reuse_prefix_toks`トークン分のプロンプトを使った「予熱(priming)リクエスト」
   を同じGPUへ事前に送っておく**(出力は1トークンで良い、`max_tokens=1`)。
   これによりvLLMのAPCがそのプレフィックスをキャッシュし、本番リクエストの
   prefill時間が実際に短縮される(=ローカルKV再利用の実機再現)。
   - 予熱リクエストのタイミング: 本番リクエストの`arrival_time_ns`より
     十分前(例えば1秒前)に送っておく。計測対象区間(TTFT計測)には含めない。
3. 計測するTTFT・latencyは、本番リクエストの送信〜応答のみ。

### 5.3 `NEAREST_REJECT`(方法B-1)

1. `assigned_instance_id`のGPUに`has_capacity()`があれば、そこへ送信。
2. 無ければ「reject」とみなし、以下を行う:
   a. 実際にはUEが最近傍GPUに一度確認しにいく想定なので、**GPU0↔UE間の
      往復遅延に相当する時間だけ待ってから**(シミュレータでは
      `2 × distance_m × 5ns/m`だが、実機にはUEが無いので下記9.1節の
      近似方針に従う)、
   b. `second_nearest_gpu_id`のGPUへ改めて送信する(容量チェック無し、
      無条件に受理)。
3. `--enable-prefix-caching`は有効のままで良いが、**予熱(priming)リクエストは
   絶対に送らないこと**(9.6節)。この方式はシミュレーションでは
   「cold prefill」(KV再利用なし)を前提としており、誤って予熱を送ると
   シミュレーションとの前提が崩れる。

### 5.4 `NEAREST_MIGRATE`(方法B-2)

1. `assigned_instance_id`のGPUに`has_capacity()`があれば、そこへ送信。
2. 無ければ、**ローカルの容量チェックのみで即座に判断**(UEへの往復無し
   ―― この判断はハーネスが自己管理する`inflight`カウンタで無料に行える)、
   `second_nearest_gpu_id`のGPUへ直接送信する(容量チェック無し、無条件受理)。
   この「直接送信」自体が実際にGPU間APN網を経由するので、**追加の遅延計算は
   不要**(実ネットワークの遅延がそのまま計測される)。
3. `NEAREST_REJECT`と同様、**予熱(priming)リクエストは送らない**
   (この方式もcold prefillが前提、9.6節)。

### 5.5 `NEAREST_MIGRATE_KV`(方法C)

`NEAREST_MIGRATE`と同じ判断ロジックに加えて:

1. リダイレクトが発生した場合、`second_nearest_gpu_id`側へ本番リクエストを
   送る**前に**、先頭`reuse_prefix_toks`トークン分のプロンプトを使った
   予熱(priming)リクエストを、`second_nearest_gpu_id`へ**GPU間APN経由で**
   送っておく(`max_tokens=1`)。
2. 予熱完了(レスポンス受信)を待ってから、本番リクエストを同じGPUへ送信する。
3. 計測されるTTFT・total latencyには、この予熱リクエストの実際の転送時間
   (実APN網を経由する分)が自然に反映される。これが「KVキャッシュ移送の
   実ネットワークコスト」の実機での近似になる(9.3節に限界を明記)。

### 5.6 全方式共通の注意

- 最大リダイレクト回数は1回(シミュレータと同じ、2番目のGPUで再度容量チェック・
  再リダイレクトはしない)。
- 各方式ごとに**vLLMサーバを再起動してから**実行すること(APCのキャッシュ状態が
  前の方式の実行結果に汚染されないようにするため)。

---

## 6. 実行手順

### 6.1 ワークロードの配置

```bash
mkdir -p workloads/generated results
# geo_2gpu100_kv_workload_1to2_n50.jsonl をここに配置
```

### 6.2 4回の実行

シミュレーション側の`exp212`と対応させ、以下4パターンを順に実行する
(各回の前にvLLMサーバ2台を再起動する)。

```bash
python3 harness.py --policy NEAREST_KV \
  --dataset workloads/generated/geo_2gpu100_kv_workload_1to2_n50.jsonl \
  --gpu0-url http://<GPU0ホスト>:8000 \
  --gpu1-url http://<GPU1ホスト>:8000 \
  --max-num-seqs 24 \
  --output results/exp212_realhw_nearest_kv.csv

python3 harness.py --policy NEAREST_REJECT \
  --dataset workloads/generated/geo_2gpu100_kv_workload_1to2_n50.jsonl \
  --gpu0-url http://<GPU0ホスト>:8000 \
  --gpu1-url http://<GPU1ホスト>:8000 \
  --max-num-seqs 24 \
  --output results/exp212_realhw_nearest_reject.csv

python3 harness.py --policy NEAREST_MIGRATE \
  --dataset workloads/generated/geo_2gpu100_kv_workload_1to2_n50.jsonl \
  --gpu0-url http://<GPU0ホスト>:8000 \
  --gpu1-url http://<GPU1ホスト>:8000 \
  --max-num-seqs 24 \
  --output results/exp212_realhw_nearest_migrate.csv

python3 harness.py --policy NEAREST_MIGRATE_KV \
  --dataset workloads/generated/geo_2gpu100_kv_workload_1to2_n50.jsonl \
  --gpu0-url http://<GPU0ホスト>:8000 \
  --gpu1-url http://<GPU1ホスト>:8000 \
  --max-num-seqs 24 \
  --output results/exp212_realhw_nearest_migrate_kv.csv
```

`harness.py`は本ドキュメント5章の仕様に従って実機側で新規実装するスクリプト
(このリポジトリには存在しない。`bench/core/runner.py`の`AsyncLLM`直接呼び出し
ではなく、OpenAI互換HTTP API経由でGPU間ネットワークを実際に使う構成にすること
―― これが実機検証の本質的な部分)。

`--dataset`の各行を`arrival_time_ns`順に読み、実時間で(最初のリクエストの
到着時刻を基準に)ペースを合わせて送出する(`bench/core/runner.py`の
`AsyncLLM`版と同じ考え方を、HTTP版に置き換える)。

---

## 7. 記録すべき出力スキーマ

`results/exp212_realhw_<policy>.csv`は1リクエスト1行、最低限次の列を持つこと
(シミュレーション側CSVの列名にできるだけ合わせてある)。

```
instance_id          最終的にリクエストを処理したGPU(0 or 1)
request_id
input_toks
output_toks
arrival_time_ns       ワークロード上の到着時刻(ハーネスが送出した時刻)
queued_ts             GPUへリクエストを送信した実時刻(送信キュー投入時刻)
scheduled_ts          vLLM側で実際にスケジュールされた時刻(可能なら)
first_token_ts        最初のトークンを受信した実時刻
last_token_ts         最後のトークンを受信した実時刻
nearest_gpu_id        assigned_instance_id(元々の最近傍GPU)
rerouted              0 or 1(リダイレクトされたか)
migration_latency_ns  実測されたGPU間転送(予熱含む)の所要時間。方式Cはpriming分も含む
communication_latency_ns  UE分の近似値(9.1節) + migration_latency_ns の合計
ttft_ms               = (first_token_ts - queued_ts) × 1000
latency_ms            = (last_token_ts - queued_ts) × 1000
```

`bench/core/validate.py`のTTFT定義(`TTFT = first_token_ts - queued_ts`、
時計系のズレがある場合は`queued_ts`を基準にフォールバック)を踏襲すること。

### 7.1 重要: `communication_latency_ns`は`migration_latency_ns`を含む(二重に足さないこと)

**`communication_latency_ns`列には`migration_latency_ns`が既に加算されている。**
8.2節の内訳バーチャート(Queue / KV transfer / Compute / RTT)を作る際、
「RTT」バケットには`communication_latency_ns`をそのまま使わず、必ず

```
RTT = communication_latency_ns - migration_latency_ns
KV transfer = migration_latency_ns
```

として分離すること。シミュレーション側の元データ(`results/exp212-1to2-nearest-*.csv`)
も同じ構造(`communication_latency_ns`に`kv_migration_latency_ns`相当が
加算済み)になっており、3.1節の目標値テーブルはこの分離を行った後の値である。
分離せずにそのまま合算すると、方式Cで「RTT」が本来の3.63ms程度ではなく
150ms超の見かけ上大きい値になり、内訳チャートがシミュレーション側と
一致しなくなる(実装時に実際に一度このミスをしたため、明記しておく)。

---

## 8. 集計・比較レポート

### 8.1 数値サマリ

各方式について次を算出し、シミュレーション側の`results/exp212-1to2-nearest-*.csv`
の対応する値と並べて比較すること(`bench/core/validate.py`と同様の手法で
`summary.txt`形式にまとめると良い)。

- 平均・中央値・P90/P95/P99 TTFT
- 平均・P99 総latency
- リダイレクト率(`rerouted`の割合)
- GPU0/GPU1それぞれの平均queue時間
- 4方式の順位(シミュレーションと同じ順位になるか? 特に方法Cが最良か?)

### 8.2 可視化成果物(必須)

シミュレーション側では、この`exp212`(4方式)の結果について既に
`outputs/image/`配下に次の成果物を作成済みである(2章の転送ファイルに含めて
渡す。実機側はこれと同じ形式・同じ2枚+notebookの構成で作ること)。

- `outputs/image/2026-07-05_exp212_n50_1to2_rtx4090_kv_with_reject_ttft_breakdown.png`
- `outputs/image/2026-07-05_exp212_n50_1to2_rtx4090_kv_with_reject_ttft_cdf.png`
- `outputs/image/2026-07-05_exp212_n50_1to2_rtx4090_kv_with_reject_ttft_plots.ipynb`

**実機側でも同じ形式の成果物を`outputs/image/`(このリポジトリのルート直下に
無ければ新規作成)に保存すること。** ファイル名は`realhw`を含めて区別する
(例: `<日付>_exp212_realhw_ttft_breakdown.png`)。

1. **TTFT内訳バーチャート**: 4方式(`NEAREST_KV` / `NEAREST_REJECT` /
   `NEAREST_MIGRATE` / `NEAREST_MIGRATE_KV`)を縦に4本並べた横積みバー。
   各バーはQueue(queueing部分)/ KV transfer(`migration_latency_ns`)/
   Compute(prefill部分)/ RTT(`communication_latency_ns - migration_latency_ns`、
   7.1節参照)の4区分に色分けし、セグメントとバー全体に値をラベル表示する。
2. **TTFT経験累積分布(CDF)**: 4方式を1つのグラフに重ね書きした経験CDF、
   横軸は対数スケール、p50/p99にマーカーを付ける。
3. **再現用notebook**: 上記2枚を7章のCSV(`results/exp212_realhw_<policy>.csv`)
   から再生成できる自己完結したノートブック(実行してエラーなく完走することを
   確認すること)。

---

## 9. 既知の近似・要判断事項

実機にはシミュレーションが想定していた要素の一部(地理分散したUE群)が
存在しないため、以下は**近似・割り切りが必要**な箇所である。実装時に
判断に迷ったらここを参照し、対応方針を結果に明記すること。

### 9.1 UE(エンドユーザ)は実機に存在しない

シミュレーションでは「100人のユーザが10km四方に分布し、UE↔GPU間は10Mbps」
というモデルを使っていたが、実機には物理的な分散UEクライアントが無い。
ハーネス(あるいはハーネスを動かすマシン)自体がUEの代役を果たす。

**方針:** UE↔GPU間の通信時間は実ネットワークで計測しようとせず、
シミュレーションと同じ**解析的な値をそのまま定数として加算**して良い
(ワークロードJSONLに既に入っている`uplink_latency_ns`/`downlink_latency_ns`
をそのまま使い、`communication_latency_ns`に足し込む)。これにより、UE区間は
実機でもシミュレーションでも同じ値になり、**GPU間バックボーン区間(6km APN)の
違いだけ**を実機で検証する、という切り分けができる。

### 9.2 `NEAREST_REJECT`のUE差し戻し往復時間

実機にUEが無いため、「最近傍GPUへの容量確認往復」は物理的に発生しない。
9.1節と同じ理由により、**この往復時間もシミュレータと同じ解析式
(`2 × distance_m × 5ns/m`)で計算した定数を加算してよい**(実測しない)。
ハーネスが容量チェック自体はローカルで即座に行い、その判定コストとして
上記の定数を`communication_latency_ns`に加えるだけで良い。

### 9.3 KVキャッシュ移送の実装(方法C)は「予熱リクエスト」による近似

シミュレータは`NEAREST_MIGRATE_KV`で「KVキャッシュのバイト数」を計算し、
そのバイト数を帯域で割った転送時間を課している。実機でvLLMの生KVテンソルを
プロセス間・マシン間で直接転送する機構は無い(vLLMの標準APIではサポートされない)。

**方針:** 5.5節の「予熱(priming)リクエストを転送先GPUへ実際に送る」方式を
採用する。これは以下の点でシミュレーションと異なる近似であることを認識し、
結果の考察に明記すること。

- 転送されるデータは「トークンID列」であり、シミュレータが想定する
  「計算済みKVキャッシュの生バイト列」より一般的にずっと小さい
  (実際のネットワーク負荷は過小評価される可能性が高い)。
- 転送先GPUは受け取ったトークンを**その場で計算し直して**からキャッシュする
  (真の意味での「計算済みKVをそのまま移送」ではない)ので、転送先の
  計算コストがシミュレータの想定より余分にかかる可能性がある。
- 対応策(どちらか、または両方を実施し比較): 
  (a) そのまま実施し、上記の近似であることを明記した上で結果を報告する。
  (b) より厳密にしたい場合、priming用のダミーペイロード(トークンIDとは
      別に、シミュレータが計算する`kv_migration_bytes`相当のダミーバイト列)
      を追加でAPN経由の別コネクションで転送し、その実測転送時間を
      `migration_latency_ns`に加算する(ネットワーク負荷だけは実際の
      KVサイズ相当を再現する)。

どちらを採用したか、8章のレポートに明記すること。

### 9.4 「容量」の定義

シミュレータは`running_reqs = 現在バッチに含まれ実行中のリクエスト数`
(`max_num_seqs`との比較)で容量を判定している。実機ではvLLMサーバ自身が
継続バッチングを行うため、ハーネス側が管理する`inflight`カウンタ
(5.1節)を「実行中」の近似として使う。vLLMの`/metrics`
(`vllm:num_requests_running`)が使える場合はそちらの方がより正確なので、
可能であれば併用して比較すること。

### 9.5 GPU間APNの実効スループット

1章の通り、今回はシミュレーションと同じスループット想定(1Gbps相当)で
進めて良い。ただし、実際の転送(5.4節・5.5節でGPU間を跨ぐリクエスト)に
明らかに長い時間がかかる、または短すぎる等、想定と大きく乖離する兆候が
あれば、`iperf3`等で実効帯域を計測し、その実測値を8章のレポートに
併記すること(必須の事前計測ではなく、結果解釈のための補足情報という位置づけ)。

### 9.6 APCを4方式共通でONにしたことの副作用(cold prefillの前提を壊さない)

4章の通り、実機では`--enable-prefix-caching`を4方式すべてでONにする
(シミュレーションでは`NEAREST_REJECT`/`NEAREST_MIGRATE`は
`--no-enable-prefix-caching`だったが、これはシミュレータ側のprefix cache
会計処理のバグを避けるための回避策であり、実機のvLLMには無関係なので
ONのままで良い、という判断)。

ただし、この判断が成立するのは**「B1/B2では誰もそのプロンプトの予熱
(priming)を行わない」という前提があってこそ**である。5.3節・5.4節に
明記した通り、`NEAREST_REJECT`と`NEAREST_MIGRATE`では予熱リクエストを
一切送らないこと。もし誤って送ってしまうと、実際にAPCがヒットして
prefillが短縮され、シミュレーションが想定した「cold prefill(KV再利用
なしの完全な再計算)」という前提から外れ、3.1節の目標値(B1/B2の
Compute ≈ 156.67ms)と一致しなくなる。

またこのワークロードの50リクエストは(ShareGPT由来の)互いに独立した
会話であり、通常は偶然の prefix 一致は起きない想定だが、万一APCの
ヒット率が0でない場合はログに記録し、8章のレポートで言及すること。

---

## 10. 受け入れ条件

1. GPU0・GPU1それぞれで実際にvLLMサーバが起動し、`/v1/completions`(または
   `/v1/chat/completions`)でリクエストを処理できること。
2. 4方式すべてで50リクエストが完走し、`results/exp212_realhw_<policy>.csv`
   (7章のスキーマ)が出力されること。
3. `NEAREST_MIGRATE`/`NEAREST_MIGRATE_KV`で、実際にリダイレクトされたリクエストが
   実際にGPU間APN網を経由して転送されたことが確認できること(ログまたは
   ネットワークキャプチャ等で)。
4. `NEAREST_MIGRATE_KV`で、予熱(priming)リクエストが実際に転送先GPUへ
   APN経由で送られ、本番リクエストのprefill時間がAPCにより短縮されている
   ことが(ログのprefill時間比較等で)確認できること。
5. 8.1節の集計・比較レポートが作成され、シミュレーション結果との一致度・
   乖離点が明記されていること。
6. 8.2節のTTFT内訳バーチャート・CDF・再現用notebookが`outputs/image/`に
   保存され、notebookがエラーなく完走すること。

---

## 11. 実装の優先順位

1. vLLMサーバ2台の起動・疎通確認
2. ハーネスの基本形(`NEAREST_KV`、リダイレクト無し)で1方式だけ動かし、
   7章のCSVが正しく出力されることを確認
3. `NEAREST_REJECT`(容量チェック+UE差し戻し近似)
4. `NEAREST_MIGRATE`(容量チェック+GPU間直接転送)
5. `NEAREST_MIGRATE_KV`(+ priming)
6. 4方式まとめて実行、8.1節の比較レポート作成
7. 8.2節のTTFT内訳バー・CDF・notebookを`outputs/image/`に生成

---

## 12. 質問・不明点の扱い

本ドキュメントに書かれていない実装細部(HTTPクライアントのタイムアウト値、
ストリーミングの実装方法など)は、実装しやすい合理的な方法を選び、
その判断をREADMEまたはレポートの「Assumptions」節に明記した上で進めること。
シミュレーション側の結論(方法Cが優れているか)に影響しうる重要な仕様上の
矛盾・未定義動作を発見した場合は、独断で重要な仕様を変更せず、まず
9章の近似方針に従うか、シミュレーション側に確認すること。
