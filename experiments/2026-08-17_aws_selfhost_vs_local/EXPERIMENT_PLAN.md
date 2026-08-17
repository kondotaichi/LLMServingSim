# AWSセルフホストLLMとローカル分散LLMの性能・コスト比較計画

## 0. 文書の目的

本計画は、AWS上にセルフホストしたLLMと、LLMServingSimで想定するローカル分散LLMを、
同一モデル・同一ワークロードで比較するための実験仕様である。AWS上で作業するCodexへ
この文書と対象リポジトリを渡し、環境構築、workload replay、計測、artifact回収を依頼
できる状態にすることを目的とする。

Amazon Bedrockなどのmanaged APIは今回の対象外とする。AWS側でもモデルとserving
softwareを自分たちで固定し、ブラックボックスなモデル差を避ける。

この文書は実験前の契約である。結果を見た後に有利なinstance、負荷点、timeout、集計対象を
選ばない。変更が必要な場合は、変更前の設定と理由を`DECISIONS.md`へ記録し、pilotとfinalを
分離する。

## 1. 研究上の問い

### 1.1 主質問

同一のMeta Llama 3.1 8B、同一の生成token数、同一のopen-loop arrival scheduleを使った
とき、AWS集中型poolとローカル分散LLMの間で、次がどう異なるか。

1. E2E TTFT、TPOT、completion latency、tail latency
2. 最大持続throughputとSLO達成率
3. 高負荷時のqueueing、redirect、KV容量不足
4. 1,000 successful requestsおよび100万生成token当たりのコスト
5. どのrequest rate／設備利用率でAWSとローカルのTCOが逆転するか

### 1.2 副質問

- AWSの集中配置は、ローカル配置より大きなnetwork latencyを負ってもqueueingを減らせるか
- ローカルPP2 + KV migrateは、AWS poolに対してどの負荷点までTTFT優位を保てるか
- ローカル側の出力長制御は、Router queueが発生する領域でSLO/costを改善するか
- AWSとローカルの差のうち、hardware差、network差、serving policy差はどの程度か

## 2. この実験で主張しないこと

- RTX 4090とAWS GPUのkernel性能が同一であるとは仮定しない
- AWSの可用性一般や、全regionの価格を代表するとは主張しない
- 1 seedの結果を一般化しない
- 異なるモデル品質を含む比較は行わない
- Simulatorの値を実機測定値として扱わない
- Peak 10xだけを見て通常運用の優劣を決めない

## 3. 比較Arm

### 3.1 必須Arm

| ID | 環境 | 構成 | 目的 |
|---|---|---|---|
| `aws_pool` | AWS EC2 | 同一モデルの1-GPU replica pool | クラウド集中配置の実測 |
| `local_pp1_kv` | Simulator | Redirect + KV migrate、PP1 | ローカル基準 |
| `local_pp2_kv` | Simulator | Redirect + KV migrate、PP2 | 現在のローカル推奨構成 |

投機的KV prewarmは既存検証で不採用としたため含めない。出力長制御はbaseline比較が成立した
後に`local_pp2_kv_output_control`として追加する。

### 3.2 任意の診断Arm

| ID | 内容 | 用途 |
|---|---|---|
| `aws_single` | AWS 1 replica | 1 GPUのservice curve取得 |
| `aws_pool_no_network` | AWSと同一AZのclient | service-only latency |
| `aws_pool_e2e` | 本郷側client | Internet/WANを含むE2E latency |
| `local_pp2_cold` | PP2 cold redirect | KV migrateの寄与分離 |

診断Armを主比較へ後付けで混ぜない。

## 4. 固定するモデルとsoftware

| 項目 | 固定値／方針 |
|---|---|
| Model | `meta-llama/Llama-3.1-8B` |
| Weight dtype | `bfloat16`を第一候補。AWS GPUの対応確認後に固定 |
| KV dtype | `auto`／weight dtypeと整合。変更時は両環境を再評価 |
| Tokenizer | Modelと同一revision |
| Serving engine | vLLM v0.19.0 |
| Prefix caching | 有効 |
| Chunked prefill | 有効 |
| Streaming | 有効 |
| Temperature | 0 |
| `top_p` | 1 |
| Model revision | 実行前にcommit SHAを固定 |
| Container | image tagだけでなくdigestを保存 |

ローカルsimulatorはprofileとruntimeのvLLM/version、dtype、token budgetをAWS側metadataへも
記録する。AWS側だけquantizationを使うなど、モデル実行条件を片側だけ変えてはならない。

## 5. AWS構成

### 5.1 Primary candidate

主候補はNVIDIA L4 24 GBを搭載するEC2 G6の1-GPU instanceである。L4のメモリ容量が
ローカルRTX 4090の24 GB構成に近いため、まずquota、region availability、実効VRAM、
Llama 3.1 8B BF16の起動可否を確認する。AWS公式情報ではG6はL4を搭載し、最大8 GPU、
GPU当たり24 GBの構成を提供する。

G6でBF16 modelと必要なKV容量が安定して収まらない場合は、48 GB L40SのG6eをfallbackと
する。G6eはhardware条件がローカルより有利になるため、結果では別hardwareとして明記する。

参考：

- [Amazon EC2 G6 Instances](https://aws.amazon.com/ec2/instance-types/g6/)
- [Amazon EC2 G6e Instances](https://aws.amazon.com/ec2/instance-types/g6e/)
- [EC2 accelerated computing specifications](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)

### 5.2 Pool構成

Primaryは12個の1-GPU replicaを同一region・同一AZ・同一VPCへ配置する。これはローカル側の
物理GPU 12台とGPU数を揃えるためである。

```text
Load generator
      |
      v
Experiment router
      |
      +-- vLLM replica 0  (1 GPU)
      +-- vLLM replica 1  (1 GPU)
      +-- ...
      +-- vLLM replica 11 (1 GPU)
```

Routerは実験用に制御可能な軽量routerを使う。Primary policyは`least-active-requests`とし、
同率時はinstance IDで決定する。AWS ALB固有の不透明な分散だけに依存しない。Router自身の
queue時間とbackend選択をrequest単位で記録する。

Quotaやcapacity不足で12 replicaを確保できない場合、勝手にGPU数を減らしてfinal結果を作ら
ない。`aws ec2 describe-instance-type-offerings`、Service Quotas、実際のlaunch probeを記録し、
次のいずれかを明示的に選ぶ。

1. 別AZ／regionで12 replicaを確保する
2. 4または6 replicaのpilotとして実行し、finalとは呼ばない
3. Multi-GPU instanceへ変更し、topology差を明記する

### 5.3 Region

利用者に近いAWS東京regionを第一候補とする。ただしG6/G6eの提供状況、quota、価格をAWS
CLIで実測確認してから固定する。提供されない場合は、候補regionごとに本郷からのRTTと料金を
記録し、結果を見る前にregionを選ぶ。

### 5.4 Network測定を二つに分ける

1. `service-only`: 同一AZのload generatorからrouterへ送信
2. `e2e`: 本郷側のload generatorからAWS endpointへ送信

AWS内部処理とWAN差を混ぜない。Primary system comparisonはE2E、原因分析はservice-onlyを
用いる。Client側TTFTは単一のmonotonic clockで計測し、host間clock同期へ依存しない。

### 5.5 Infrastructure as Code

TerraformまたはAWS CDKで次を再現可能にする。

- VPC、private subnet、security group
- Experiment router
- GPU replica launch template
- IAM role（最小権限）
- S3 artifact bucket
- CloudWatch log group
- ECR repositoryまたは固定image参照
- Instance tag、experiment ID、owner、auto-expiry
- `destroy`手順

Public ingressは本郷側load generatorの固定IPだけに限定する。Model token、AWS credential、
prompt本文をGitへ保存しない。

## 6. ワークロード

### 6.1 Primary workload

`experiments/2026-08-09-test-some-workload/workloads/full/`にあるHongo 2000件版を使用する。
先頭300件だけを切り出さない。セッション継続、入出力長、arrival order、ユーザー配置を維持する。
正本ファイル名、SHA-256、統計、field semantics、AWS側の受入検査は
`WORKLOAD_HANDOFF.md`を唯一の引き継ぎ仕様とする。対象JSONLはGit管理されていないため、repoの
cloneとは別にAWS側へ転送し、hash一致を確認してから本計測を開始する。

負荷点は次の順で実行する。

| Phase | Workload | 用途 |
|---|---|---|
| Smoke | 10 requests | API、streaming、token数確認 |
| Pilot 1 | Peak 2x、2000件 | 計測系と低負荷baseline |
| Pilot 2 | Peak 5x、2000件 | 中負荷、queueing開始領域 |
| Stress | Peak 10x、2000件 | Router/KV容量飽和領域 |
| Final | Busy hour、2x、5x、10x × 複数seed | 最終比較 |

Peak倍率はarrival timelineだけを圧縮し、prompt、要求output、ユーザー割当を変えない。

### 6.2 Open-loop replay

元workloadの`request_send_time_ns`に従い、前requestの完了を待たず送信する。Client側の
concurrency不足がarrival scheduleを歪めないよう、十分な非同期task数を用意する。

各requestについて次を記録する。

- Scheduled send time
- Actual send time
- Launch lag
- First byte/token受信時刻
- Completion時刻
- Timeout、HTTP status、exception、retry
- Requested/actual input tokens
- Requested/actual output tokens
- Selected replica

Launch lagのp99が事前閾値を超えたrunは、serverではなくload generator飽和なので無効とする。

### 6.3 Token IDの再現

可能ならworkloadの`input_tok_ids`を直接vLLMへ渡す。HTTP APIがtoken ID入力を受けない場合は
同じ固定revisionのtokenizerでdecodeし、server側で再tokenizeしたIDが元と一致することを
smoke testで確認する。一致しないrequestがある場合は、勝手に近似せず件数と差を報告する。

### 6.4 出力長を二つのmodeに分ける

#### Controlled mode（主性能比較）

Simulatorの`output_toks`と実出力token数を一致させる。vLLMでEOSを無視できる設定を使い、
requestごとの出力長を固定する。これによりwork量を揃える。

#### Natural mode（実用感度分析）

EOSを許可し、同一promptに自然生成させる。Actual output tokensで正規化する。Natural modeを
controlled modeの代用にしない。

## 7. Measurement contract

### 7.1 時刻定義

```text
t_scheduled : workload上の送信予定
t_send      : clientがrequest送信を開始
t_first     : clientが最初の生成tokenを受信
t_end       : clientがstream完了を受信
```

```text
launch_lag             = t_send - t_scheduled
E2E_TTFT               = t_first - t_send
completion_latency     = t_end - t_send
TPOT                    = (t_end - t_first) / max(actual_output_tokens - 1, 1)
```

最初のHTTP headerや空chunkではなく、最初の生成tokenで`t_first`を取る。Simulator側の
`e2e_ttft_ns`と比較し、`simulator_ttft_ns`を混ぜない。

### 7.2 Primary metrics

- E2E TTFT: mean、p50、p95、p99、max
- TPOT: mean、p50、p95、p99
- Completion latency
- Request throughput、prompt tokens/s、generation tokens/s
- SLO達成率
- Failure、timeout、retry率
- Router queue、vLLM waiting/running
- GPU utilization、GPU memory、power
- Cost per 1,000 successful requests
- Cost per 1M actual tokens
- Cost per 1,000 SLO-successful requests

### 7.3 SLO

Pilot開始前に固定する。初期案は次のとおりで、変更する場合はfinal結果を見る前に行う。

- E2E TTFT ≤ 1 s
- E2E TTFT ≤ 2 s
- Timeoutなし
- Controlled modeで要求output tokensを完了

1秒と2秒を併記し、どちらか都合のよい方だけを採用しない。

### 7.4 Server telemetry

- vLLM Prometheus metrics
- Request running/waiting count
- Prompt/generation throughput
- KV cache utilization
- Prefix cache hit率
- CUDA OOM、engine restart
- `nvidia-smi`またはDCGMによるGPU utilization、memory、power（1秒間隔）
- Routerのqueue長、backend選択、backend response time

ログ時刻はUTC、run ID付きとする。Raw logは加工せずS3へ保存する。

## 8. 公平性を守るための分解

AWS L4/L40SとRTX 4090の速度差を配置方式の差と誤認しないため、結果を三層で示す。

### Layer A: End-to-end system comparison

実際のAWS実測とローカルsimulatorのE2E結果をそのまま比較する。これは利用者が選ぶsystem
全体の比較であり、hardware差を含む。

### Layer B: Service-only comparison

AWS同一AZ clientの結果からWANを除き、ローカル側もaccess networkを分離して示す。

### Layer C: Hardware-normalized sensitivity

AWS 1 GPUで得たservice curveをLLMServingSimのAWS hardware profileへ反映する、または
requests/GPU、tokens/GPUで正規化する。これは診断であり、Layer Aの置換ではない。

## 9. コスト計画

### 9.1 AWS実測コスト

PrimaryはOn-Demand料金とする。Spotは中断リスクを含む感度分析として別に示す。

```text
AWS run cost =
    GPU instance uptime × current On-Demand rate
  + router/load-generator EC2 cost
  + EBS cost
  + data transfer cost
  + load balancer/NAT等の実使用cost
```

価格を文書へ手入力して固定しない。Run開始時にAWS Price List APIからregion、OS、tenancy、
instance typeに一致する価格を取得し、取得日時、currency、SKUをmetadataへ保存する。AWS Price
List APIはSKU単位の価格照会を提供する。ただし公式説明どおり、price listとservice pricing
pageに差がある場合はservice pricing pageが優先されるため、請求結果でも検算する。

参考：

- [AWS Price List API](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/Welcome.html)
- [Calling AWS services and prices using the AWS Price List](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/price-changes.html)

Instance起動からmodel readiness、warm-up、実験、artifact upload、停止までを区間別に記録する。
主比較は実験占有時間コスト、運用判断にはmodel loadを含む総run costも併記する。

### 9.2 ローカルTCO

以下を外部parameterとしてJSONへ保存する。

- GPU、host、network設備の購入費
- 償却期間
- 平均消費電力とpeak power
- 電力単価
- PUEまたは冷却係数
- 保守費率
- 想定利用率

```text
hourly_capex = purchase_cost / amortization_hours
hourly_energy = average_kW * electricity_price_per_kWh * PUE
local_hourly_cost = hourly_capex + hourly_energy + maintenance + network
```

利用率10%、30%、50%、80%の感度分析を行う。購入済み設備だから0円とは扱わない一方、
意思決定目的に応じてmarginal electricity costも別に示す。

### 9.3 Break-even

次を求める。

- Requests/dayのbreak-even
- Input/output tokens/dayのbreak-even
- SLO-successful requests/dayのbreak-even
- 3年TCOのbreak-even utilization

## 10. 実験手順

### Phase 0: Cloud inventory（課金をほぼ発生させない）

1. AWS account、region、budget alert、IAM roleを確認
2. G6/G6e offeringとquotaをCLIで保存
3. On-Demand価格をPrice List APIで保存
4. 必要AMI、driver、CUDA、Docker availabilityを確認
5. `cloud_inventory.json`と`DECISIONS.md`を作成

Gate: Primary/fallback instance、region、最大replica数、概算上限費用が承認されるまでGPUを
起動しない。

### Phase 1: One-GPU smoke test

1. 1 instanceだけ起動
2. 固定container/model revisionでvLLMを起動
3. 10 requestsをstreaming実行
4. Token ID、output長、TTFT parser、metricsを確認
5. Model load時間とidle/active powerを記録
6. Instanceを停止

Gate: 10/10成功、token count一致、raw streamから最初のtokenを正しく抽出できること。

### Phase 2: One-GPU service curve

Concurrency 1、2、4、8、16、32を固定順ではなくseed付きで実行し、throughputとlatency curveを
取得する。Saturation point、OOM point、max stable concurrencyを決める。これをpool routerの
admission設定に使う。

Gate: 同一点を3回実行し、主要metricのばらつきを確認する。

### Phase 3: Pool pilot

1. 12 replicaを同一AZへ起動
2. Health check完了後にwarm-up
3. Peak 2x・2000件をservice-onlyで実行
4. 同じrunを本郷側E2Eで実行
5. Raw artifactsをS3へ保存
6. Instanceを停止

Gate: launch lag、failure率、token一致、telemetry欠損を確認する。Gate失敗runを性能結果へ使わない。

### Phase 4: Load sweep

Peak 2x、5x、10xを実行する。各run前に同じwarm-upを行い、run間でserverを再起動する条件と
継続運転条件を分ける。順序効果を避けるため、負荷点順序はseed付きでrandomizeする。

### Phase 5: Local simulator replay

AWS finalで使ったrequest ID、arrival schedule、input/output tokensをそのまま使い、次を実行する。

- `local_pp1_kv`
- `local_pp2_kv`

PP1/PP2ではcluster-wide `max_num_seqs`とtoken budgetを揃える。現在の方針どおり、PP2は
logical instance当たりの上限をPP1の2倍にする。Simulator commit、profile DB、config、CLIを
metadataへ保存する。

### Phase 6: Output-length control

BaselineでPP2のRouter queueが`npu_memory`により発生する負荷点だけを対象にする。

1. 制御なしbaseline
2. 同一workload・同一seedで制御あり
3. 生成token削減量を品質／utility lossとして明示
4. TTFT改善だけでなく、完了token数とcostを併記

出力を削った方式を、同じ品質・同じwork量で高速化したとは表現しない。

### Phase 7: Final repetition

Pilotでparameterを固定後、最低3 arrival seedsでfinalを実行する。可能なら日を分け、AWS
capacityの時間変動も観測する。Mean差だけでなくbootstrap confidence intervalを出す。

## 11. Run acceptance criteria

次のいずれかに該当するrunは無効とし、理由をmanifestへ残す。

- Requested 2000件の一部が送信されていない
- Client launch lag p99が事前閾値を超過
- Controlled modeでactual output tokensが不一致
- Model/container/revisionが他Armと異なる
- GPU replicaが途中restart/OOM
- Telemetryまたはraw request CSVが欠損
- AWS instance数が計画値と異なる
- Retryによる重複requestを成功件数へ二重計上

Failureやtimeoutがsystem saturationの結果ならrun自体を捨てない。Infrastructure/configuration
failureとsystem capacity failureを分類する。

## 12. 必須artifact schema

### 12.1 Directory layout

```text
experiments/2026-08-17_aws_selfhost_vs_local/
  EXPERIMENT_PLAN.md
  DECISIONS.md
  WORKLOAD_HANDOFF.md
  cloud/
    terraform/
    docker/
    router/
    runner/
    pricing/
  configs/
    experiment.yaml
    local_tco.json
  workloads/
    manifests/
    hongo/                 # S3から取得するGit管理外JSONL
  scripts/
    upload_workloads.sh
    download_workloads.sh
    validate_workloads.py
  results/
    aws/<run_id>/
    local/<run_id>/
  analysis/
  report/
```

### 12.2 Per-request CSV

最低限、次のcolumnを含める。

```text
run_id, environment, request_id, session_id,
scheduled_send_ns, actual_send_ns, launch_lag_ns,
first_token_ns, completion_ns, e2e_ttft_ns, completion_latency_ns,
requested_input_tokens, actual_input_tokens,
requested_output_tokens, actual_output_tokens,
replica_id, http_status, retry_count, error_type
```

### 12.3 Run metadata

```text
git commit, dirty state, region, AZ, instance type/count,
AMI, driver, CUDA, vLLM version, container digest,
model ID/revision, dtype, KV dtype,
vLLM CLI, router policy, workload hash,
start/end UTC, pricing SKU/rate/retrieved_at,
warm-up condition, client host specification
```

## 13. AWS側Codexへの依頼順序

AWS側Codexには一度に全権限を与えて全実験を走らせず、次の単位で依頼する。

1. **Read-only inventory**：region、quota、instance offering、価格を調査しartifact化
2. **IaC review**：Terraform planまで。GPU instanceはまだ起動しない
3. **One-GPU smoke**：予算上限と自動停止を設定して10件だけ実行
4. **Service curve**：1 GPUの飽和点を測定
5. **Pool pilot**：12 GPUを短時間起動してPeak 2xのみ
6. **Load sweep**：pilot承認後に2x/5x/10x
7. **Artifact export and destroy**：S3同期、resource残存確認、Cost Explorer確認

各段階で次を報告させる。

- 作成／変更したresource
- 実行したcommand
- 推定費用と実費
- Artifact path
- Acceptance gate結果
- 残存resourceと削除可否

## 14. Cloud Codexへ最初に渡す依頼文

```text
このリポジトリの
experiments/2026-08-17_aws_selfhost_vs_local/EXPERIMENT_PLAN.md
を完全に読み、Phase 0のread-only cloud inventoryだけを実施してください。

まだGPU instance、NAT Gateway、Load Balancerなど課金resourceを作成しないでください。
AWS account/regionの現状、G6/G6e offering、Service Quotas、On-Demand価格、
推奨primary/fallback構成をartifactへ保存してください。

計画から変更が必要な場合は、変更を実施せずDECISIONS.mdへ選択肢、費用、影響を記載し、
承認待ちにしてください。Credential、token、prompt本文はGitへ保存しないでください。
```

## 15. 未確定事項

AWSへ移る前またはPhase 0で次を確定する。

- AWS accountと予算上限
- Primary region
- G6の具体的な1-GPU sizeとquota
- BF16での安定起動可否
- 12 replica同時確保の可否
- Model weightの取得方法とlicense/token管理
- 本郷側load generatorの実行host
- Local設備購入費、償却期間、電力単価、PUE
- Final seed数とSLO閾値

これらを結果取得後に調整しない。

## 16. 最終成果物

1. AWS IaCと再現手順
2. Immutable raw request/telemetry/pricing artifacts
3. AWSとlocalのrequest-level統合CSV
4. TTFT CDF、TTFT breakdown、throughput、SLO曲線
5. Cost/request、cost/token、cost/SLO-success曲線
6. Break-even utilization／requests per day
7. Hardware差、network差、policy差を分けた批判的レポート
8. 全AWS resourceを削除したことの確認

最終結論は「どちらが常に優れているか」ではなく、負荷、SLO、利用率、network条件ごとに
AWSセルフホストとローカル分散の適用領域を示す形にする。
