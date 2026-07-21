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

本実験で直接主張できるのは、2台の物理GPU上に構成した4つの論理serving endpointにおける結果である。

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

## 3. Phase 0: 実現可能性確認

長時間実験やrouting実装の前に、以下をread-only調査と小規模測定で確定する。

### 3.1 ハードウェア

- GPU型番、VRAM、compute capability
- GPU間接続: PCIe、NVLink、その他
- GPU間P2P accessの可否
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

### 3.2 Serving stack

- OS、kernel、NVIDIA driver、CUDA
- Docker image digest
- vLLMまたは使用するserving engineのversionとGit commit
- KV transfer backendとversion
- Model ID、revision、dtype、quantization
- Prefix caching、chunked prefill、KV connectorの設定
- Request-level metricsを取得するhook/API

### 3.3 4 replicaの収容可否

各物理GPUに二つのmodel replicaを同時配置できるか確認する。

Llama 3.1 8B bf16はweightだけでも約16 GBを必要とするため、24 GB GPUへ二つの完全replicaを置く構成は通常成立しない。以下のいずれかを明示的に選ぶ。

1. 十分なVRAMを持つGPUを使用する。
2. 小さいmodelまたは量子化modelを使う。
3. 物理GPUごとにengineを一つだけ起動し、二つの論理endpointが同じengineを共有する。
4. MIG対応GPUでhardware partitionを使う。

3を選ぶ場合、4 endpointは4 replicaではなく2 engineへの論理aliasである。この制約をレポートに明記する。

### 3.4 Phase 0の合格条件

- 選択した構成で全endpointが同時にhealth checkへ応答する。
- 1 requestずつ実行してOOM、process crash、出力不一致がない。
- Inter-GPU KV transferが実装可能である、または実装上の明確な代替案がある。
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

単独転送と同時転送について、Intra-GPUとInter-GPUを分けてmicrobenchmarkする。

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

- [ ] GPU型番とVRAM
- [ ] 4 endpointを4 replicaにするか、2 engineのaliasにするか
- [ ] Modelとdtype/quantization
- [ ] Serving engineとversion
- [ ] KV transfer backend
- [ ] Physical GPU間の転送経路
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
