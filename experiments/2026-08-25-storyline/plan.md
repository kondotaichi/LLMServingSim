# Storyline experiment plan

## 目的

本ディレクトリでは、地理分散したAI-RAN siteにおけるstateful LLM servingについて、問題設定から提案方式の有効性までを以下の4段階で示す。

1. RAN負荷とAI需要を反映したworkloadおよびAI向けVRAMモデルを構築し、VRAM imbalanceが発生することを示す。
2. Single-GPUで完結するLLM servingを対象に、地理分散AI-RAN GPUがcloud-like clusterと同等の応答性能を保ちつつ、既設設備の活用によって追加コストを抑えられることを示す。次にRANとのVRAM共有を加え、性能上の優位性は薄れても経済性が残ることを示す。
3. AI workloadの地理的offloadがresource imbalanceの緩和に有効である一方、stateful inferenceでは単純なoffloadに限界があることを示す。
4. Site内PPとsite間KV cache migrationを組み合わせた提案方式が、その限界を緩和することを示す。

各段階では、単に性能値を比較するだけでなく、次の段階を必要とする理由が明確になるように結果を整理する。

## 01. RAN-aware workload and resource-imbalance characterization

対象ディレクトリ: `01_make_workload_considering_ran/`

### 問い

RAN負荷とAI requestの時空間変動をどのようにworkloadへ反映するか。また、そのworkloadの下でAI-RAN site間にどの程度のresource imbalanceが発生するか。

### 示したいこと

- RAN処理とAI推論がGPUのVRAMを共有するため、AIが利用できるVRAM容量はRAN負荷に応じて減少・変動する。
- RAN負荷とAI需要の分布は、各siteに固定配置されたGPU数と常に一致するとは限らない。
- あるsiteではAI requestがqueueingしている一方、別siteではAIに利用可能なGPU資源が余っている状態が発生する。
- 全siteの合計資源が不足しているのではなく、資源と需要の地理的な配置不一致が性能低下を生むケースが存在する。

### Workloadで表現する要素

- 東京・鹿児島の2 AI-RAN sites
- 合計8台のGH200（東京4台、鹿児島4台）
- SoftBank実証の`20 cells/GH200`を基準とする全160 logical cells（各site 80 cells）
- SiteごとのRAN負荷の時系列
- SiteごとのAI request arrival rate
- RAN負荷からAI利用可能VRAM容量への変換規則
- Single-turn requestとmulti-turn session
- Session内のturn間隔、user think time、またはtool execution time
- Input/output token数と、turnの進行に伴うcontextおよびKV cacheの増加
- Site間のnetwork latencyとbandwidth

東京・鹿児島の地理配置とuser mappingは`experiments/2026-08-02-kagosima-tokyo`を必要に応じて援用する。Site間networkは`experiments/2026-08-24-simulate-both-local-and-cloudlike`にあるAPN（10.7 Gbps、300.5 us）とnormal WAN（1.0 Gbps、5 ms）を使用する。

### 評価指標

- Site別のRAN負荷とAI arrival rateの時系列
- Site別のAI利用可能VRAM容量
- Site別のqueue lengthおよびwaiting time
- Site別のGPU utilizationとKV cache occupancy
- Site間で余剰と不足が同時に発生している時間割合
- Local-only配置におけるTTFT/TPOTとSLO violation

### 次段階への接続

この段階で定義したRAN-aware workloadとVRAM制約を02以降の共通入力として用いる。02では、まずnetworkだけを変えた比較でinterconnectの影響を分離し、次にRANによるVRAM制約を含めた条件でAI-RANとcloud-like clusterの性能・経済性を比較する。

## 02. Merit of using distributed AI-RAN GPUs

対象ディレクトリ: `02_merit_of_use_distributed_gpu/`

### 問い

RAN workloadの存在によってAI向けVRAMが制約される状況でも、地理分散AI-RAN GPUをLLM servingに利用するメリットはあるか。そのメリットは推論性能ではなく、主として電力を中心とする経済性として説明できるか。

### 全体方針

02は次の二つの小実験に分け、いずれもPPを用いないsingle-GPU model instanceを主評価とする。

- `a_interconnect_only/`: RANを考慮せず、single-GPUで完結するCloudとDistributedを比較する。
- `b_ran_aware_economics_10percent/`および`b_ran_aware_economics_20percent/`: 01で作成したRAN workloadによるVRAM制約をDistributedへ適用し、Active TCP UE率ごとの性能低下と経済性を比較する。

順序を分けることで、まず地理分散そのものが性能を悪化させないことを示し、次にRANとのVRAM共有によってその性能上の優位性が薄れることを示す。02では並列化を提案・評価しない。Site内PPは、03で明らかになるstateful sessionの収容問題に対する04の提案要素として初めて導入する。

### 02-a. Interconnectだけを変えた比較

#### 問い

Single-GPUで実行可能なモデルを各GPUへ独立配置した場合、地理分散AI-RANはcloud-like clusterと同等の応答性能を保ちつつ、追加コストを削減できるか。

#### 比較条件

- `Distributed`: 地理分散siteのbandwidthとlatencyを使用する。
- `Clustered`: Miyabi相当のcluster interconnectを使用する。
- GPU数、GPU性能、AI向けVRAM、モデル、scheduler、request配置は同一にする。
- RAN workloadによるVRAM制約は、この比較では適用しない。
- 各model instanceは1 GPU内で完結させ、PPを使用しない。

#### 示したいこと

- Model executionが1 GPU内で完結する場合、地理分散配置でもCloudとTTFTがほぼ変わらない。
- Distributedではユーザに近いGPUへrequestを送れるため、地理分散そのものによる性能ペナルティはない。
- 既設AI-RAN GPUを利用する追加電気料金がMiyabiのサービス利用料金を下回り、経済的メリットがある。

#### 評価指標

- TTFT、TPOT、end-to-end latency
- Network latencyの寄与
- KV migrationまたはrequest transfer時間
- ThroughputとSLO attainment

### 02-b. RANのVRAM制約を含む経済性比較

#### 問い

RAN workloadによってAI向けVRAMとstateful sessionの収容能力が制約されると、02-aで確認した性能面の同等性はどの程度失われるか。また、その場合でも既設AI-RAN GPUを利用する経済的メリットは残るか。

#### 比較条件

- `AI-RAN`: 01で作成したRAN workloadを適用し、時刻ごとのAI向けVRAMを制限する。
- `Miyabi/cloud-like`: AI専用GPUとしてcoreとVRAMを利用できるcluster環境を想定する。
- 両環境で同じAI workload、モデル、出力token数、評価時間または完了request数を使用する。
- 主比較ではPP=1とし、各model instanceを1 GPU内で完結させる。

#### 基本仮説

- AI-RANではRANがVRAMを使用するため、stateful sessionの収容数が減り、admission waitingやqueueingによってCloudに対する性能上の優位性が薄れる。
- 一方、AI-RANでは既設GPUの余剰資源を利用するため、AI処理に伴う追加電力を主なincremental costとして評価できる。
- Miyabiは8ノードで`306 yen/hour`の利用料金を仮定する。
- したがって、AI-RANの主なメリットは絶対的な推論性能ではなく、許容可能なSLOを満たす範囲でのcost reductionとして評価する。

#### Costの定義

AI-RANについては、RAN-only時に対してAI処理を追加したことで増えた電力をincremental energyとする。

```text
AI-RAN incremental energy
  = energy(RAN + AI) - energy(RAN only)

AI-RAN incremental electricity cost
  = AI-RAN incremental energy * electricity rate
```

Miyabiについては、以下を基本の利用料金とする。

```text
Miyabi usage cost
  = 306 yen/hour * execution time in hours
```

`306 yen/hour`は、追加AI workloadをCloudで処理するときに支払う8ノードのサービス利用料金として扱う。AI-RAN GPUはRAN設備として既に保有しているため、追加AI workloadの配置判断に必要な限界費用として追加電気料金を用いる。

#### 評価指標

- TTFT、TPOT、throughput、SLO attainment
- RANのVRAM制約によるsession収容数と性能の低下率
- AI-RANのincremental energyとincremental electricity cost
- Miyabiの実行時間と利用料金
- Cost per 1,000 completed requests
- Cost per session
- Cost per output token
- 同じSLOを満たすために必要なGPU/node数とcost
- AI-RANがMiyabiより安価になるbreak-even条件

単位時間当たりの料金だけでは、性能の遅い方式が見かけ上安くなる、または処理時間の長期化で逆に高くなる可能性がある。したがって、同じ完了仕事量とSLOを基準に正規化して比較する。

### 次段階への接続

02-aでは、PPなしなら地理分散AI-RANがCloudと同等の応答性能を保ち、追加コストを削減できることを示す。02-bでは、RANとのVRAM共有により性能上の優位性は薄れるが、経済性は維持されることを示す。その上で、RAN負荷による局所的な性能低下を改善するため、03では余剰siteへのAI workload offloadを評価する。PPは02の主張には用いず、04で提案手法の構成要素として導入する。

## 03. Merit and limitation of geographical offload

対象ディレクトリ: `03_geographical_offload_merit/`

### 問い

過負荷siteのAI workloadを余剰計算資源のあるremote siteへoffloadすることで、network overheadを上回るqueueing削減効果を得られるか。また、stateful inferenceでは何が障壁になるか。

### 示したいこと

- Local-only servingでは、過負荷siteにqueueingが発生していても他siteの余剰GPUを利用できない。
- Geographical offloadにより、地域間network latencyが追加されても、queueing latencyとTTFTを削減できる負荷領域が存在する。
- Offloadは常に有利なのではなく、local queueing、remote queueing、network latency、transfer sizeを考慮して判断する必要がある。
- Stateful sessionの次requestだけをremote siteへ送るnaive offloadでは、過去contextの再prefillが必要になり、TTFTが増加する。
- KV cacheを利用できる理想的なoffloadとnaive offloadの差が、KV cache migrationによって解消すべき余地である。
- Remote siteがLLMを処理可能でも、KV cacheを収容するVRAM容量が不足するとsessionを受け入れられない。

### 比較候補

- `Local only`: 到着siteだけで処理する。
- `Remote recompute`: Remote siteへroutingし、過去contextを再prefillする。
- `Ideal offload`: KV transfer costと容量制約を無視し、remote siteでKV cacheを即座に利用できる上限ケース。
- 必要に応じて`Stateless routing`: 各requestを独立requestとして扱う比較。

### 評価指標

- TTFTおよびそのp50/p95/p99
- Queueing latency
- Prefill/recomputation latency
- Network/transfer latency
- Redirected request/session数
- Remote GPU utilization
- Site間のutilization imbalance
- KV capacity不足によってoffloadできなかったsession数
- SLO attainmentまたはSLO violation rate

### 示すべき境界条件

次の条件を満たす場合にoffloadが有効になることを整理する。

```text
回避できるlocal queueing
  > remote側の追加queueing + network latency + state移送または再計算cost
```

Multi-turn sessionでは、移送後の後続turn数が多いほど、一度のmigration costを複数turnに分散できる可能性がある。

### 次段階への接続

Geographical offload自体にはqueueingを削減する可能性があるが、stateful inferenceでは再prefill costとremote siteのKV capacityが障壁になる。04では、KV cache migrationで再prefillを回避し、site内PPでremote siteのKV収容能力を増やす。

## 04. Proposed method: intra-site PP and inter-site KV migration

対象ディレクトリ: `04_proposed_method/`

### 問い

Site内PPによるKV cache容量の確保と、site間KV cache migrationによるsession relocationを組み合わせることで、stateful AI workloadをremote siteへ効率的に再配置できるか。

### 提案方式の役割分担

- Local optimization: Site内でsingle-GPU-class modelを複数GPUに分割し、各GPUのmodel-weight footprintを減らしてKV cacheに利用可能なVRAM容量を増やす。
- Global optimization: 過負荷siteから余剰siteへKV cacheとともにsessionを移し、以後のturnを移送先site内で処理する。
- 地域間ではmodel parallelismを行わず、migration時だけstateを転送する。

PPを増やすとKV cache容量は増える一方、同じGPU数から作れる独立model replica数は減る。そのため、PPは常に性能を改善する手段ではなく、request処理の並列性とKV収容能力のバランスを変える手段として評価する。

### 必須のablation

| 条件 | Geographical offload | KV migration | Site内PP | 確認すること |
|---|---:|---:|---:|---|
| Local only | No | No | No | 地理的imbalanceによるqueueing |
| Naive offload | Yes | No | No | Remote recomputationのcost |
| KV migration only | Yes | Yes | No | PPなしでのKV capacity制約 |
| PP only | No | No | Yes | PP単体のcapacity/latency trade-off |
| Proposed | Yes | Yes | Yes | 二つを組み合わせた効果 |
| Ideal | Yes | Free | Unlimited | 達成可能な上限との距離 |

### 中心的に示す因果関係

```text
PPなし:
  Remote siteにcompute余力がある
  -> KV cache容量が不足する
  -> Stateful sessionを受け入れられない
  -> 元siteでqueueingが継続する

PPあり:
  Remote siteのKV cache容量が増える
  -> KV cacheとともにsessionを移送できる
  -> Remote computeを利用できる
  -> 元siteのqueueingとTTFTが低下する
```

### Migration policyで考慮する量

- Local siteの予測queueing time
- Remote siteのcompute loadとKV occupancy
- KV cache size
- Site間bandwidthとlatency
- Migration完了までの時間
- User think timeまたはtool execution time中に転送を隠蔽できるか
- 予測される後続turn数
- Session終了や配置予測失敗によるwasted migration

Reactive migrationとproactive migration/prewarmは分けて評価する。

- Reactive: 次request到着後にmigrationを開始する。
- Proactive: Request間のidle interval中にmigrationまたはprewarmを行う。

### 評価指標

- TTFTおよびp50/p95/p99
- TPOT
- Queueing latency
- Migration latencyと、そのうちcritical pathに現れた時間
- Recomputed prefill tokens
- Migration数、成功数、prewarm hit数、wasted migration数
- KV capacityによるadmission failure数
- Site別GPU utilizationとutilization imbalance
- Site別KV cache occupancy
- 収容可能なactive session数
- SLO attainment
- Energyおよびnetwork traffic overhead

### 感度分析候補

- PP degree
- Site間bandwidth/latency
- KV cache sizeおよびcontext length
- Sessionのturn数
- Think/tool time
- RAN負荷とAI arrival rate
- Site間の負荷偏り
- GPU VRAM容量
- Migration thresholdまたはprediction accuracy

## 全体として最終的に示すこと

4段階を通じて、最終的に以下を示す。

1. RAN負荷とAI需要の時空間変動により、AI向けVRAMが変動し、全体ではVRAMが余っていても局所的なsession収容不足とqueueingが発生する。
2. 地理分散AI-RAN GPUは専用clusterより推論性能が低下する可能性がある一方、既設GPUの余剰資源を利用することで、SLOを満たす条件では電力を中心とするincremental costを抑えられる可能性がある。
3. AI workloadの地理的offloadはこの不均衡を緩和できるが、stateful inferenceでは再prefill costとKV capacityが障壁になる。
4. KV cache migrationとsite内PPを組み合わせることで、remote GPUのVRAM capacityをstateful sessionに利用でき、queueing latencyとTTFTを抑制できる。

なお、4番は「PPが常に高速である」という主張ではない。PPによるreplica数減少やpipeline overheadを含めても、KV capacityが律速となる条件では、PPとKV migrationの組合せが有効になることを示す。
