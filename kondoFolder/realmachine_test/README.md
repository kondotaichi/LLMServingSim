# 実機2 GPU・4論理サーバによるKV-aware routing検証要件

作成日: 2026-07-21

## 1. 目的

実機上で、Prefix KVを維持したGPU間redirectとmulti-candidate routingが、ユーザ全体のE2E TTFT、特にtail TTFTを削減できるか確認する。

シミュレーション
`experiments/2026-07-21_mixed_workload_model_value`で比較した次の3方式を、可能な限り同じ判断規則で再現する。

1. KV nearest migrate
2. Multi-candidate, no model
3. Multi-candidate, learned

主たる検証仮説は次の二つとする。

- 第二近傍固定よりも、全候補からcapacity pressureが小さい移動先を選ぶ方が、Router waitとp95/p99 TTFTを削減する。
- Learned policyがno-model policyを上回るかを実機で確認する。ただし、シミュレーションでは3.33 rpsでno-model policyが最良だったため、learned superiorityを前提としない。

## 2. 実験結果として主張する範囲

本実験で直接主張できるのは、2台の物理GPU上に構成した4つの論理serving endpointにおける結果である。物理構成はTopology Aで確定している。

現時点で確定している接続先は次の2ホストである。

| Role | SSH destination | 備考 |
|---|---|---|
| Local-side server | `nakaolab@192.168.100.11` | RTX 4090 24 GB x 1、独立1B model endpoint x 2 |
| Remote-side server | `nakaolab@192.168.100.50` | RTX 4090 24 GB x 1、独立1B model endpoint x 2 |

認証用password、秘密鍵、tokenは本リポジトリへ保存しない。SSH host key fingerprintは初回接続時に管理者が確認し、実験metadataには秘密情報を含まない接続先と確認日時だけを記録する。

「2 GPU x 2 Docker」の物理配置は次で確定している。

```text
2 hosts
x 1 physical RTX 4090 per host
x 2 Docker endpoints sharing that GPU
= 2 physical GPUs and 4 logical endpoints
```

GPU間にNVLinkはなく、MIGも利用できない。2ホストは約50 km離れており、高スループットの光回線で接続されている。現時点の概算値はRTT約1 ms、throughput約20 Gbpsである。実験では概算値を設定値として固定せず、Phase 0で実効値を測定する。

```text
Physical GPU 0
  +-- Logical server 0: Docker container 0
  +-- Logical server 1: Docker container 1

Physical GPU 1
  +-- Logical server 2: Docker container 2
  +-- Logical server 3: Docker container 3
```

同一GPU上の二つのコンテナは、VRAM、compute、memory bandwidth、PCIe/NVLink経路を共有する。したがって、この構成を4台の独立GPUとして扱ってはならない。結果には必ず次の二種類の移動を区別して記録する。

- Intra-GPU redirect: 同じ物理GPU上の別コンテナへ移動
- Inter-GPU redirect: 別の物理GPU上のコンテナへ移動

主解析ではInter-GPU redirectをKV移送効果の中心に置く。Intra-GPU redirectはコンテナ間分離と論理routingの検証に使い、独立GPU間KV移送と同一視しない。

## 3. Phase 0: 環境記録と通信性能測定

Serving software、Docker、model、Prefix KV transfer機能は構築済みで正常動作する前提とする。Phase 0ではsoftware構築の可否を検討せず、再現性のためのversion記録、2 endpoint/GPU共有状態の確認、ホスト間KV移送経路の性能測定を行う。

### 3.1 ハードウェア

- 両ホストへSSH接続できること
- 各ホストのhostname、管理IP、実験data-plane IP
- GPU型番: 両ホストともNVIDIA GeForce RTX 4090
- VRAM: 各24 GB
- GPU台数: 各ホスト1台、合計2台
- Endpoint割当: 各GPUを二つのDocker endpointで共有
- MIG: 非対応・不使用
- NVLink: なし
- 各GPUとNIC間のPCIe topology
- Host間GPU Direct/P2P相当機能の利用可否
- ホスト間network interface、link speed、MTU
- ホスト間RTT、TCP/UDP実効帯域、packet loss
- Firewallと実験用portの利用可否
- Host RAM容量
- NUMA topology
- DockerからのGPU割当方法
- GPUごとのpower/clock制限

記録コマンドの出力を成果物へ保存する。

- `nvidia-smi -L`
- `nvidia-smi topo -m`
- `nvidia-smi --query-gpu=...`
- `docker version`
- `docker info`

両ホストで同じinventoryを取得し、host名を付けて別ファイルへ保存する。

### 3.2 Serving stackの記録

Software環境は構築済みであることを前提に、変更せず次をmetadataへ記録する。

- OS、kernel、NVIDIA driver、CUDA
- Docker image digest
- vLLMまたは使用するserving engineのversionとGit commit
- KV transfer backendとversion
- Model ID、revision、dtype、quantization
- Prefix caching、chunked prefill、KV connectorの設定
- Request-level metricsを取得するhook/API

### 3.3 4論理endpointの実体

各ホストで二つのDocker containerを起動し、それぞれに独立したAI serving engineと`meta-llama/Llama-3.2-1B-Instruct` replicaを配置する。合計4 model replicas、4 scheduler、4 KV cachesを構成する。

```text
Host 192.168.100.11 / RTX 4090 #0
  +-- server-0 container: independent engine + model + scheduler + KV cache
  +-- server-1 container: independent engine + model + scheduler + KV cache

Host 192.168.100.50 / RTX 4090 #0
  +-- server-2 container: independent engine + model + scheduler + KV cache
  +-- server-3 container: independent engine + model + scheduler + KV cache
```

Model IDは`meta-llama/Llama-3.2-1B-Instruct`とする。Revision、dtype、quantization、max model lengthは実装時に固定し、全4 replicasで一致させる。Hugging Face認証tokenは環境変数またはsecret storeから渡し、repository、Docker image、ログ、metadataへ保存しない。

#### 初期serving設定

`experiments/2026-07-21_mixed_workload_model_value`との比較可能性を優先し、実機pilotの初期設定を次で固定する。

| Setting | Initial value | 根拠 |
|---|---:|---|
| Model | `meta-llama/Llama-3.2-1B-Instruct` | 実機用1B model |
| Revision | Setup時に解決したcommit hash | `main`の変化を避け、4 replicasで同一化 |
| Weight dtype | `bfloat16` | シミュレーションと一致 |
| Quantization | なし | 量子化影響を混ぜない |
| KV cache dtype | `auto`、実効bf16を確認 | シミュレーションと一致 |
| Max model length | 12,288 tokens | Mixed workload最大context 11,021 tokensへ余裕を確保 |
| Block size | 16 tokens | シミュレーションのreuse block境界と一致 |
| GPU memory utilization | 0.40 / replica | 1 GPU上の2 replicas合計を0.80とし安全余裕を確保 |
| Max sequences | 128 / replica | シミュレーションと一致 |
| Max batched tokens | 2,048 / replica | シミュレーションと一致 |
| Chunked prefill | Enabled | 2,048 tokens超のinputを分割 |
| Prefix caching | Enabled | KV reuse実験に必須 |
| Tensor parallel size | 1 | Replicaは1 GPU内で実行 |

Revisionはbranch名ではなくHugging Face snapshotのcommit hashをmetadataへ保存する。全containerが同じsnapshotをread-onlyで使用する。

`gpu_memory_utilization=0.40`は初期値であり、KV cache budgetそのものではない。各engine起動後にmodel weight memory、non-KV runtime memory、KV cache bytes、GPU block数、1 block当たりbytes、1 token当たりKV bytes、理論上収容可能な総cached tokensを記録する。

4 replicas同時起動、最大context request、2 replicas同時負荷でOOMがなければ0.40を本実験値として固定する。OOMまたはmemory pressureがある場合のみ、全4 replicasを同じ値へ下げてpilotをやり直す。Policyごとに値を変えてはならない。

`max_num_seqs=128`は論理上限であり、実際のadmissionはKV block budgetにも制約される。1B modelでは8Bシミュレーションよりtoken当たりKV量が小さいため、同じ128でもcapacity不足の発生点は一致しない。Request rateとHome偏りをcalibrationし、capacity境界を実機上で作る。

Docker containerは独立していても、同一hostの二つのreplicaは一つのRTX 4090を共有する。MIGによるhardware partitionはないため、次の資源は物理的に共有される。

- SM/compute time
- VRAM容量とmemory bandwidth
- PCIe bandwidth
- Power/thermal budget

したがって、endpoint単位のKV capacityはengine設定で論理的に制限する。各replicaについて、model load後VRAM、KV cache budget、`gpu_memory_utilization`相当設定、最大sequence数、最大batch token数を固定して記録する。2 replicasの予約VRAM合計に安全余裕を設け、OOMが発生しないことを確認する。

同一GPU上で一方のreplicaだけを実行した場合と、二つを同時実行した場合を比較し、compute contentionによるTTFT/TPOT悪化を事前測定する。この干渉は本実験の結果から除去できないため、system-level telemetryとともに報告する。

### 3.4 ホスト間通信・KV移送経路の測定

別ホスト間のKV移送backendは事前に決め打ちせず、構築済み環境が実際に使用する経路を特定して測定する。

確認対象:

- EthernetまたはInfiniBandの種別
- NIC vendor、model、link speed、driver、firmware
- TCP、RDMA、NIXLなど実際のtransport/backend
- GPUDirect RDMAの利用可否と実利用有無
- GPU -> CPU -> network -> CPU -> GPU stagingの有無
- Host memoryがpinned memoryかpageable memoryか
- MTU、TCP congestion control、socket buffer
- 単一flowと複数flowのthroughput
- RTT、jitter、packet loss、retransmission

測定を次の3層に分ける。

#### A. Network baseline

- ICMPまたは同等手段によるRTT分布: p50、p95、p99、max
- `iperf3`等による片方向・双方向TCP throughput
- 1、2、4 parallel streams
- 30秒以上のsteady-state測定を最低3回
- 両方向を個別測定

#### B. Host memory transfer

- 実際のKV payloadに近いsizeでhost-to-host転送
- Payload size: 64 MiB、128 MiB、256 MiB、384 MiB、512 MiB、1 GiB
- Latency、goodput、CPU使用率、copy回数
- 1、2、4 concurrent transfers

#### C. End-to-end KV transfer

- Source GPU上のKVをtarget GPUへ登録完了するまでを測定
- GPU readout、CPU staging、network、target upload、registrationを可能な範囲で分解
- Inferenceなし、および両GPUでinference実行中を比較
- 1、2、4 concurrent KV transfers
- Prefix長、KV bytes、input長別に測定

各測定はtimestamp、bytes、duration、実効Gbps、source/target host、backend、concurrencyをCSVへ保存する。概算RTT 1 ms、throughput 20 Gbpsとの差も報告する。

### 3.5 Phase 0の合格条件

- 選択した構成で全endpointが同時にhealth checkへ応答する。
- 1 requestずつ実行してOOM、process crash、出力不一致がない。
- 各containerが独立process、独立scheduler、独立KV cacheを持つことを確認できる。
- 同一GPU上の2 replicasを同時負荷してもOOMにならない。
- Inter-host KV transferが動作し、transport、staging経路、実効帯域を観測できる。
- Prefix KVをtargetで利用し、cold prefillより実prefill token数が減ることをログで確認できる。
- すべてのendpointで同一model revisionとKV layoutを使う。

Phase 0を満たさない場合、本実験へ進まず構成を変更する。

## 4. 論理topology

各endpointへ固定IDを付与する。

| Endpoint | Physical GPU | Container | API port | Role |
|---|---:|---|---:|---|
| `server-0` | 0 | `llm-server-0` | TBD | Home/candidate |
| `server-1` | 0 | `llm-server-1` | TBD | Home/candidate |
| `server-2` | 1 | `llm-server-2` | TBD | Home/candidate |
| `server-3` | 1 | `llm-server-3` | TBD | Home/candidate |

実装時の割当は次を基準とする。

| Endpoint | Host | Host-local GPU | Global physical GPU |
|---|---|---:|---:|
| `server-0` | `192.168.100.11` | 0 | 0 |
| `server-1` | `192.168.100.11` | 0 | 0 |
| `server-2` | `192.168.100.50` | 0 | 1 |
| `server-3` | `192.168.100.50` | 0 | 1 |

APIは可能なら管理用SSHとは別のdata-plane interfaceを使用する。`192.168.100.0/24`をdata-planeにも使用する場合は、その制約を明記する。

次を設定ファイルとして保存し、コードへ埋め込まない。

- Endpoint IDとURL
- Physical GPU ID
- Container ID
- Candidate順序
- Intra/Inter-GPU区分
- KV転送経路
- 帯域・固定遅延の測定値

Home割当はworkload内で固定する。3方式間で同じrequestのHomeを変更してはならない。

## 5. 比較する3方式

### 5.1 KV nearest migrate

シミュレーションの`NEAREST_MIGRATE_KV`に対応する。

```text
if Homeがadmissible:
    Homeで実行
elif 固定した第二候補がadmissible:
    第二候補へPrefix KVとrequestを移送
else:
    Router queueで待機し、capacity解放時に再評価
```

第二候補はrequestごとに事前固定し、run中の負荷状態で変更しない。

### 5.2 Multi-candidate, no model

シミュレーションの`NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE`に対応する。

```text
if Homeがadmissible:
    Homeで実行
else:
    全candidateのうちadmissibleなendpointを列挙
    capacity pressureが最小のcandidateを選択
    Prefix KVとrequestを移送
    admissible candidateがなければRouter queueで待機
```

Capacity pressureの定義は実機で取得可能なKV block情報に合わせて固定し、全runで変更しない。推奨定義は次である。

```text
capacity pressure
  = (reserved KV bytes + request required KV bytes)
    / KV budget bytes
```

同値の場合のtie-breakも固定する。例: Inter-GPU転送cost、endpoint IDの順。

### 5.3 Multi-candidate, learned

シミュレーションの`NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE`に対応する。

- no-model版と同じcandidate集合を使用する。
- Version固定したoffline artifactでlocal/redirect TTFTを予測する。
- Localで待つ予測とredirect候補の予測TTFTを比較する。
- Admissible candidateを予測TTFTで順位付けする。
- 使用したmodel artifactのhash、特徴量、係数、閾値を保存する。
- 実験結果を使って同じrun中に再学習してはならない。

既存formulaはLlama 3.1 8Bのシミュレーション結果から得たものであり、Llama 3.2 1B実機へそのまま適用しない。Learned policy用artifactは、1B実機のtraining用runだけから作成し、本評価のseedとrequestを学習へ混入させない。Training/evaluationのworkload、seed、期間を分離し、artifact作成後にfreezeする。

予測入力が欠損した場合のfallbackはno-model policyとする。fallback理由をrequestログへ残す。

## 6. Admissionと予約

4 endpointで同一のadmission定義を使う。

最低限、次を確認する。

```text
running requests + reserved incoming requests < max_num_seqs
AND
request required KV blocks <= available KV blocks after reservations
```

- Routing決定と同時にtarget capacityをatomic reservationする。
- KV転送中requestもtargetの将来負荷として数える。
- Reservationにはtimeoutを設ける。
- 転送失敗、client切断、request失敗時はreservationを確実に解放する。
- 同じrequestを二つのendpointへ同時admitしない。
- Capacity stateの更新世代またはtimestampを記録する。

## 7. KV transfer要件

- Sourceとtargetでmodel、revision、dtype、KV block size、layer layoutを一致させる。
- 移送対象token数、block数、bytesを記録する。
- Source、target、physical GPUを記録する。
- Transfer start/end、target登録完了時刻を記録する。
- Target登録完了前にrequestをscheduleしない。
- Source KVの解放時点を明示する。
- 転送失敗時はcold prefillへfallbackし、失敗理由を残す。
- Targetで実際に再利用されたtoken数を記録する。
- Cold prefillとKV reuseで生成結果が許容誤差内で一致することを事前検証する。

単独転送と同時転送について、Intra-GPU、同一host内container間、50 kmのInter-hostを分けてmicrobenchmarkする。

- 1 transfer
- 2 simultaneous transfers
- 4 simultaneous transfers
- 推論なし
- 推論と同時

## 8. Workload

第一段階ではシミュレーションのmixed workloadを再利用する。

- 300 requests/run
- Input tokens: 512、2000、4000、6000、8000、10000を各50件
- Prefix reuse ratio: 0、0.25、0.5を各100件
- Traffic phase: normal 240件、burst 60件
- Seeds: 1、2、3
- 平均rate: 2.5 rpsと3.33 rpsを候補とする
- Output token数、request順序、到着時刻、Home割当をpolicy間で固定する

Mixed workloadで観測された最大値は次である。

- 最大input: 10,000 tokens
- 最大output: 1,021 tokens
- 最大input + output: 11,021 tokens

このため初期`max_model_len`を12,288 tokensとする。Chat templateや特殊token追加後も上限内であることをworkload生成時にtokenizerで再検証する。上限超過requestを暗黙にtruncateせず、生成前検証で失敗させる。

ただし、2 GPU共有構成のcapacityは10 GPUシミュレーションと異なる。予備実験でrateをcalibrationし、次の状態を作る。

```text
低負荷条件:
  Redirectがほぼ発生しないnegative control

境界条件:
  一部Homeがblockedになるが、クラスタ全体には受入余力がある

高負荷条件:
  複数endpointが同時にblockedになり、target選択差が現れる
```

3.33 rpsを無条件に採用せず、実機での実現負荷に合わせる。Calibrationで選んだrateと理由を保存する。

## 9. 実験matrix

本実験の最小matrixは次とする。

```text
2 load levels
x 3 seeds
x 3 policies
x 3 repetitions
= 54 runs
```

実行時間が大きい場合は、まず境界または高負荷条件だけで次を実行する。

```text
1 load level x 3 seeds x 3 policies x 3 repetitions = 27 runs
```

同一seed・loadでは全policyへ同じworkloadを使う。A/B/Cの実行順は反復ごとにrotationまたはrandomizationする。

- Rep 1: KV nearest -> no model -> learned
- Rep 2: no model -> learned -> KV nearest
- Rep 3: learned -> KV nearest -> no model

## 10. Run間の状態管理

- 測定前に同一手順でwarm-upする。
- Run開始前にprefix cacheを既定状態へ戻す。
- CUDA graph、allocator、model load状態をpolicy間で揃える。
- Cacheをpreloadする場合は同じprefix集合と順序を使う。
- 前runのKV、reservation、waiting requestを残さない。
- GPU温度とclockが安定してから開始する。
- Run開始・終了時のGPU memory、utilization、temperature、powerを保存する。
- Failure runは同じ`run_id`へ上書きせず、新しいattempt IDで再実行する。

## 11. Request単位ログ

すべてのtimestampは同一hostのmonotonic clockを使う。複数hostへ拡張する場合はclock同期誤差を別途測定する。

### 11.1 Request identityと条件

- `run_id`、`attempt_id`、`policy`、`seed`、`request_id`
- `input_tokens`、`output_tokens`
- `prefix_tokens_available`、`prefix_tokens_reused`
- `arrival_ns`
- `home_endpoint`、`selected_endpoint`
- `home_physical_gpu`、`selected_physical_gpu`
- `redirected`、`inter_gpu_redirect`
- `traffic_phase`

### 11.2 Timestamp

- Router receive
- Routing decision start/end
- Capacity wait start/end
- Reservation created
- KV transfer start/end
- Target KV registration completed
- Scheduler enqueue
- First schedule
- Prefill start/end
- First token server ready
- First token client received
- Request completed

### 11.3 Routing時のstate

Homeおよび全candidateについて次を保存する。

- Running/waiting request数
- Free/used/reserved KV blocks
- KV budget
- Capacity pressure
- Admissible flagと拒否理由
- In-flight incoming transfer数と予約量
- Scheduler token budget
- 選択scoreまたは予測TTFT
- Candidate順位
- 同一physical GPU上のsibling endpoint IDと負荷

### 11.4 Transferと結果

- KV transfer tokens、blocks、bytes
- Transfer duration、実効帯域
- Intra/Inter-GPU
- Fallback有無と理由
- Router wait
- Scheduler wait
- Prefill service
- E2E TTFT
- TPOT、completion latency
- HTTP status、engine error、timeout

## 12. System-level telemetry

100 ms以下を目標とした一定周期で次を記録する。

- GPU utilization、memory utilization、allocated memory
- SM clock、memory clock、temperature、power
- Container CPU、RAM、network I/O
- Engine別running/waiting request数
- KV cache utilization
- Host-to-device、device-to-host、P2P traffic

同じ物理GPUを共有する二つのcontainerについて、container単位とphysical GPU単位の両方を保存する。

Docker/NVIDIA runtimeからcontainer別GPU utilizationを直接取得できない場合、engine process PIDとNVML accounting/process metricsを対応付ける。取得不能な指標を推定値で埋めず、physical GPU aggregateのみであることを明示する。

## 13. 評価指標

### 13.1 Primary

- E2E TTFT mean
- E2E TTFT p50、p95、p99、max
- TTFT SLO violation率
- Seed/replicationを考慮した95% confidence interval

### 13.2 TTFT breakdown

```text
E2E TTFT
  = Router wait
  + KV transfer/registration
  + Scheduler wait
  + Prefill service
  + Network/other
```

全request、redirected only、not redirected onlyで分ける。

### 13.3 Secondary

- Redirect件数と割合
- Inter-GPU/Intra-GPU redirect件数
- Candidate別redirect集中度
- Prefix reuse保持率
- KV transfer総量と実効帯域
- TPOT
- Request/output-token throughput
- Completion latency
- Failure、fallback、OOM率
- GPU別load fairness

### 13.4 Paired comparison

同じseed、request ID、replicationで比較する。

- No-model minus KV nearest
- Learned minus KV nearest
- Learned minus no-model

Input長、reuse率、traffic phase、Home、target、Intra/Inter-GPU別に集計する。

## 14. 成功条件

機能面の必須条件:

- KV転送後にtargetでPrefix KVが実際に再利用される。
- 3方式のrouting差以外のmodel、workload、scheduler設定が一致する。
- Request単位でTTFT breakdownを再構成できる。
- Failure、fallback、reservation leakがない、または発生率と原因を説明できる。

性能面の主判定:

- No-model MultiがKV nearestに対してp95またはp99 TTFTを有意に削減するか。
- 改善が3 seedsおよび反復で同じ方向か。
- Non-redirect requestとTPOTを重大に悪化させないか。
- Learnedがno-modelを上回るか。上回らない結果も有効な実験結果として扱う。

固定した有意差閾値は予備測定後に設定する。結果を見てから閾値を変更しない。

## 15. 実施順序

1. Hardware/serving stack inventory
2. 4 endpointの収容方式決定
3. 単一requestで出力一致とprefix reuse確認
4. KV transfer microbenchmark
5. 2 endpointでroutingとreservationのsmoke test
6. 4 endpointで低負荷calibration
7. Capacity境界と高負荷rateの探索
8. 3方式・1 seedのpilot
9. RequestログとTTFT breakdownの検証
10. 3 seeds・3 repetitionsの本実験
11. Paired解析、グラフ、Markdownレポート作成

各段階の成果物を確認してから次へ進む。長時間runを先に開始しない。

## 16. 成果物

```text
kondoFolder/realmachine_test/
  README.md
  inventory/
  configs/
  docker/
  router/
  workloads/
  results/<run_id>/
    metadata.json
    requests.parquet
    telemetry.csv
    logs/
  analysis/
  figures/
  reports/
```

最終レポートには次を含める。

- 実機topologyと仮想化の制約
- 3方式の実装差分
- Workloadとrate calibration
- 全体およびredirect別TTFT breakdown
- p50/p95/p99とconfidence interval
- Seed/replication整合性
- Input/reuse/burst/Intra/Inter-GPU別結果
- Failureとfallback
- シミュレーションとの定性的・定量的差

## 17. 実装開始前に確定する未決事項

- [x] SSH接続先: `nakaolab@192.168.100.11`、`nakaolab@192.168.100.50`
- [x] Topology: 2 hosts x 1 GPU x 2 Docker endpoints
- [x] GPU: 各host RTX 4090 24 GB x 1、合計2台
- [x] MIG: なし
- [x] NVLink: なし
- [x] ホスト間距離: 約50 km
- [x] Network概算: RTT約1 ms、throughput約20 Gbps
- [x] Endpoint実体: 各Dockerに独立した1B Meta Llama replica、engine、scheduler、KV cache
- [x] Model ID: `meta-llama/Llama-3.2-1B-Instruct`
- [x] Weight dtype: bfloat16
- [x] Quantization: なし
- [x] Initial max model length: 12,288
- [x] Initial GPU memory utilization: 0.40 / replica
- [x] Max sequences: 128 / replica
- [x] Max batched tokens: 2,048 / replica
- [x] KV cache dtype: auto、実効bf16を起動時確認
- [x] Block size: 16
- [x] Chunked prefill、Prefix caching: Enabled
- [ ] 各hostのhostname
- [ ] Model revision
- [ ] 起動後の実測KV cache bytes、GPU block数、tokens capacity
- [ ] Serving engineとversion
- [ ] KV transfer backend
- [ ] Physical GPU間の転送経路
- [ ] Host間network interface、link speed、MTU、RTT、実効帯域
- [ ] Transport/backend: TCP、RDMA、NIXL等
- [ ] GPUDirect利用可否と実利用有無
- [ ] CPU staging経路とcopy回数
- [ ] Management planeとdata planeを分離するか
- [ ] Firewallで許可するAPI/KV transfer/telemetry port
- [ ] Docker network構成
- [ ] Endpoint port
- [ ] Capacity pressureを計算するmetrics/API
- [ ] Atomic reservationの実装場所
- [ ] Learned artifactの場所、形式、hash
- [ ] Homeと第二候補の割当規則
- [ ] Calibration後のrequest rate
- [ ] Output token上限とsampling設定
- [ ] TTFT SLO閾値
- [ ] Run回数と最大実験時間

## 18. 関連するシミュレーション結果

- `experiments/2026-07-21_mixed_workload_model_value/README.md`
- `experiments/2026-07-21_mixed_workload_model_value/reports/final_three_policy_analysis.md`
- `experiments/2026-07-21_mixed_workload_model_value/figures/ttft_breakdown_rate3p33.png`

シミュレーションの3.33 rps・3 seeds集計では、Mean TTFTはKV nearest 716.4 ms、no-model Multi 607.5 ms、learned Multi 617.0 msだった。実機ではこの数値の一致ではなく、multi-candidateによるtail削減の方向性、KV transferの実測cost、物理GPU共有による差を検証する。
