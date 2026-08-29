# 叩き台のまとめ

## 実験環境の前提

- 対象地域の総ユーザ数は、これまでの本郷workloadを引き継いで27,000人とする。
- AI-RAN基盤は東京と鹿児島の2 site、合計8台のGH200で構成する。各siteにGH200を4台配置する。
- GH200のGPU、VRAM、CPU/NUMA、NICおよび実測interconnect仕様は、`experiments/2026-08-21-local-hongo-workload-gh200-test/gh200-info.txt`を参照する。
- SoftBankの屋外実証で示された`20 cells / NVIDIA GH200`をRAN収容密度の基準とし、1 site当たり80 logical cells、全体で160 logical cellsを収容する。
- 東京・鹿児島の地理配置とuser mappingは、`experiments/2026-08-02-kagosima-tokyo`の設定を必要に応じて援用する。
- Site間interconnectは、`experiments/2026-08-24-simulate-both-local-and-cloudlike`のAPNおよびnormal WAN設定を使用する。
- 各siteに4台を置くことで、後続実験において同じ物理GPU数のままPP=1とPP=2を比較できるようにする。

## RAN workload

- 時間帯ごとのRRC_CONNECTED UE数の推移は、`/Users/taichikondo/airan-workload-calculate/ran_workload_analysis`で作成した日内変動曲線を利用する。
- Population-derivedな高負荷条件として、全体のピークRRC_CONNECTED UE数を900とする。
- RRC_CONNECTED UEのうち、実際にユーザプレーンのトラフィックを持つActive UEの割合をbaselineで10%とし、ピークActive UE数を90とする。
- 実装で取得した`0 Active UE/GH200で40%`、`10 Active UE/GH200で56%`というVRAM使用率を校正点として使用する。この実測UEは、RRC接続だけを維持するUEではなく、iperfでTCPトラフィックを流したUEである。
- 1 GH200につき1 RUとする。90 Active UEを8台へ均等配分した場合、1台当たり平均11.25 UE、整数配置で最大12 UEとなり、ピークRAN VRAM使用率は最大59.2%と推定される。
- 各時刻のUEを東京と鹿児島へ配分し、siteごとのRAN負荷を生成する。最初は均等配分をbaselineとし、その後、siteごとの時間帯変動や偏りを追加する。
- 校正点の間およびその近傍では、RAN VRAM使用率がActive TCP UE数に比例して増える一次近似を用いる。傾きは1 Active UE当たり1.6 percentage pointsである。
- RANとAIのcompute core共有および実行干渉は本研究の対象外とし、今後の研究課題とする。

## AI request workload

- これまでの本郷workloadで作成したShareGPT由来のmulti-turn conversationを基本とする。
- AI workloadの母集団は27,000人、AI DAU率は20%、1 active user当たり40 turns/dayを暫定baselineとする。
- これにより、daily averageは2.5 turns/s、busy hourは9 turns/sとなる。
- 既存の300/600-request traceは到着レート確認用の短時間sampleであり、実際に5,400人のAI DAU全員を生成したものではない。今回のworkloadでは、population、RRC_CONNECTED UE、AI active session、request/turn数を区別して記録する。

# Codexによって設定した具体案の草案

## 1. 物理構成

| 項目 | 暫定設定 |
|---|---:|
| AI-RAN site数 | 2 sites（東京・鹿児島） |
| GH200数 | 8台 |
| GH200数/site | 4台 |
| GPU仕様の参照元 | `experiments/2026-08-21-local-hongo-workload-gh200-test/gh200-info.txt` |
| Logical cell数/GH200 | 20 cells |
| Logical cell数/site | 80 cells |
| Logical cell総数 | 160 cells |
| 総ユーザ数 | 27,000人 |
| 均等baselineのユーザ数/site | 13,500人 |
| 平均ユーザ数/cell | 約169人 |

GPU関連の数値は上記の実機情報を正とする。同ファイルでは、GPUは`NVIDIA GH200 120GB`、`nvidia-smi`が報告したGPU memoryは`97,871 MiB`である。VRAM容量をGBへ換算してシミュレータへ設定する際は、公称120 GBを自動的に使用せず、この実機報告値および実行時に利用可能な容量との対応を確認する。Site内通信の帯域・遅延を設定する場合も、同ファイルに記録されたInfiniBand、GPUDirect RDMAおよびNCCLの実測値を参照する。

SoftBankは、100 MHz、最大4-layer MIMOの5G 20セルを、GH200を搭載した1台のサーバーで処理する屋外実証環境を報告している。本実験では、この`20 cells/GH200`をRAN収容密度の基準として採用する。

- [SoftBank: AI-RAN屋外実証実験の概要](https://www.softbank.jp/corp/news/press/sbkk/2024/20241113_03/)
- [SoftBank: AITRAS RAN overview](https://www.softbank.jp/en/corp/set/data/technology/research/topics/117/pdf/sfc_media_omega1en.pdf)

この値は、商用網で常に20セルごとにGH200を1台配置するという規則ではなく、SoftBankが実証した処理密度である。本研究では、実証設定から大きく外れないためのreference configurationとして使用する。

NVIDIA Aerialの公式構成では、1セル当たり100 RRC_CONNECTED UEと、DL/ULそれぞれ16 UE/TTIが別の容量指標として示されている。本構成のピーク900 RRC UEは160セルへ均等配置すると平均5.625 UE/cellであるため、RRC接続数そのものは公式上限に対して十分小さい。VRAM負荷はこの900件を直接用いず、そのうち実際にTCPトラフィックを持つActive UE数から推定する。

- [NVIDIA Aerial CUDA-Accelerated RAN: configuration and KPIs](https://docs.nvidia.com/aerial/cuda-accelerated-ran-24-1.pdf)

## 2. Site構成

```text
Tokyo site:    GH200 x 4, 80 cells, uniform baseline 13,500 users
Kagoshima site: GH200 x 4, 80 cells, uniform baseline 13,500 users
```

最初のworkloadでは、ユーザとcellを東京・鹿児島へ均等に配置する。これは生成器の動作確認と、両siteに同じ負荷を与えた場合のcapacity確認に使う。

その後のresource-imbalance評価では、総ユーザ数と総request数を変えずに、東京と鹿児島のユーザ数、RRC_CONNECTED UE数、AI request arrival rateの時間帯をずらす。これにより、全体ではVRAMが足りていても一方のsiteだけが過負荷になる状態を作る。

`experiments/2026-08-02-kagosima-tokyo`の既存placementは、20,000人全員を東京の1 km²領域に物理配置し、その半数を鹿児島GPUへ割り当てたpressure scenarioである。今回の27,000人・2 site構成へそのまま流用せず、座標、東京―鹿児島間距離約1,050 km、地域ラベル、home/remote mappingの作り方を援用する。均等baselineと東京集中scenarioは別workloadとして保存する。

## 3. Network model

東京―鹿児島間のsite間networkは、`experiments/2026-08-24-simulate-both-local-and-cloudlike`の設定を使用する。

| Network | Bandwidth | Fixed latency | 参照config |
|---|---:|---:|---|
| APN | 10.7 Gbps（1.3375 GB/s） | 300.5 us | `gh200_no_pp_distributed_apn.json` |
| Normal WAN | 1.0 Gbps（0.125 GB/s） | 5 ms | `gh200_no_pp_distributed_normal_wan.json` |

これらはsite間のrequest routingおよびKV cache migrationに適用する。PPはsite内のGH200間だけで行い、東京―鹿児島間では行わない。Site内networkはsite間APN/WANと分離し、Miyabiで計測したlocal interconnect設定を候補とする。

## 4. RAN user model

ユーザ数に関して、以下の量を明確に分離する。

| 変数 | 意味 | 暫定設定 |
|---|---|---:|
| `population` | 対象地域に存在する全ユーザ | 27,000 |
| `rrc_connected_ues(t)` | 時刻`t`にRRC_CONNECTEDであるユーザ | 日内変動、ピーク900 |
| `active_traffic_ues(t)` | RRC_CONNECTED UEのうち、実際にTCPトラフィックを持つUE | baseline 10%、ピーク90 |
| `ai_dau` | 1日にAIを1回以上使うユーザ | 5,400 |
| `ai_active_sessions(t)` | 時刻`t`に継続中のAI session | workloadから導出 |
| `ai_turn_arrival_rate(t)` | 時刻`t`のLLM turn到着率 | 日内変動、busy-hour基準9 turns/s |

### ピーク900 RRC_CONNECTED UEの導出

ピーク900 UEは基地局で直接観測した値ではなく、`/Users/taichikondo/airan-workload-calculate/rrc_peak_connection_rate_estimation.md`で求めたpopulation-derived推計のHigh caseである。導出は次のとおりである。

1. 日本大学文理学部の公開値である最大同時Wi-Fi接続端末2,400台と、学生・教員9,205人から、大学人口に対するピーク時無線接続端末率を`2,400 / 9,205 = 26.1%`と置く。
2. 商用mobile network研究のRRC connection発生回数・継続時間と、公開RAN datasetのpeak/average比から、無線接続端末のうちピーク時にRRC_CONNECTEDである割合をLow 5.4%、Base 7.0%、High 12.8%と推計する。
3. High caseでは、全人口に対するピークRRC_CONNECTED率は`26.1% * 12.8% = 3.34%`となる。
4. 対象人口27,000人へ適用すると、`27,000 * 3.34% = 約902 UE`となるため、実験値を900 UEへ丸める。

| Scenario | ピーク時無線接続率 | 接続端末中のRRC割合 | 全人口中のピークRRC率 | 27,000人でのRRC UE |
|---|---:|---:|---:|---:|
| Low | 26.1% | 5.4% | 1.41% | 約381 |
| Base | 26.1% | 7.0% | 1.83% | 約494（既存実装defaultは540） |
| High | 26.1% | 12.8% | 3.34% | 約902（本実験では900） |

したがって、900 UEは平均的な予測値ではなく、固定8 GH200構成でRANとAIのVRAM共用が破綻しないかを確認するために採用したpopulation-derived High caseである。元の推計文書ではBase caseを丸めた540 UEがdefaultであるため、論文では900 UEを「標準的な実測ピーク」と表現しない。

なお、ピークの絶対値と日内変動の形状は別に決める。絶対値900 UEは上記のpopulation-derived推計から取り、各時刻への展開には`ran_workload_analysis/outputs/hongo/hongo_5g_rrc_profile.csv`のpeak-normalized日内曲線を使用する。

全体のRRC_CONNECTED UE数は、既存の5G日内曲線`L_ran(t)`を用いて次のように生成する。

```text
rrc_connected_ues_total(t) = round(900 * L_ran(t))
active_traffic_ues_total(t) = round(0.10 * rrc_connected_ues_total(t))
```

均等baselineでは、これを東京と鹿児島へ均等配分する。

```text
active_traffic_ues_site(t)
  = active_traffic_ues_total(t) / 2
```

ピーク時の平均は次のようになる。

```text
900 RRC UE / 160 cells = 5.625 RRC UE/cell
900 RRC UE * 10% = 90 Active UE
90 Active UE / 2 sites = 45 Active UE/site
90 Active UE / 8 GH200 = 11.25 Active UE/GH200
```

各GH200のRAN VRAM使用率は、iperf TCPを流した実機検証の2点を結ぶ次の一次式で求める。

```text
ran_vram_fraction(active_traffic_ues) = 0.40 + 0.016 * active_traffic_ues
```

代表値は次のとおりである。

| Active TCP UE/GH200 | RAN VRAM使用率 |
|---:|---:|
| 0 | 40% |
| 5 | 48% |
| 10 | 56% |
| 15 | 64% |
| 20 | 72% |

この線形式では37.5 Active UE/GH200で100%に達する。それ以上はVRAM使用率を単に100%へclampして処理可能とみなさず、RAN構成のcapacity超過として扱う。実測した上限は10 Active UE/GH200であり、11--12 UEは近傍外挿、20 UE以上は感度分析として区別する。

### 時間帯別のRRC_CONNECTED UE数

Baselineでは、`ran_workload_analysis/outputs/hongo/hongo_5g_rrc_profile.csv`の5G NR日内曲線を使用する。曲線の最大値を全体900 RRC_CONNECTED UEへ合わせ、10%をActive traffic UEへ変換する暫定係数とする。

```text
rrc_connected_ues_total(hour)
  = round(900 * peak_normalized_load_5g(hour))

active_traffic_ues_total(hour)
  = round(0.10 * rrc_connected_ues_total(hour))
```

Uniform baselineでは、Active UEを東京・鹿児島の2 site、各site内の4 GH200へ可能な限り均等に配分する。下表の`最大Active UE/GH200`は、8台へ整数配分した際に最も多くUEを受け持つGH200の値である。

| Source UTC | 参考JST | Normalized load | 全体RRC UE | 全体Active UE | 最大Active UE/GH200 | 最大RAN VRAM/GH200 |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 9 | 0.297 | 267 | 27 | 4 | 46.4% |
| 1 | 10 | 0.187 | 168 | 17 | 3 | 44.8% |
| 2 | 11 | 0.136 | 122 | 12 | 2 | 43.2% |
| 3 | 12 | 0.126 | 113 | 11 | 2 | 43.2% |
| 4 | 13 | 0.125 | 113 | 11 | 2 | 43.2% |
| 5 | 14 | 0.183 | 165 | 17 | 3 | 44.8% |
| 6 | 15 | 0.333 | 300 | 30 | 4 | 46.4% |
| 7 | 16 | 0.539 | 485 | 49 | 7 | 51.2% |
| 8 | 17 | 0.771 | 694 | 69 | 9 | 54.4% |
| 9 | 18 | 0.889 | 800 | 80 | 10 | 56.0% |
| 10 | 19 | 0.946 | 851 | 85 | 11 | 57.6% |
| 11 | 20 | 1.000 | 900 | 90 | 12 | 59.2% |
| 12 | 21 | 0.999 | 899 | 90 | 12 | 59.2% |
| 13 | 22 | 0.990 | 891 | 89 | 12 | 59.2% |
| 14 | 23 | 0.970 | 873 | 87 | 11 | 57.6% |
| 15 | 0 | 0.945 | 851 | 85 | 11 | 57.6% |
| 16 | 1 | 0.931 | 838 | 84 | 11 | 57.6% |
| 17 | 2 | 0.886 | 797 | 80 | 10 | 56.0% |
| 18 | 3 | 0.844 | 760 | 76 | 10 | 56.0% |
| 19 | 4 | 0.831 | 748 | 75 | 10 | 56.0% |
| 20 | 5 | 0.786 | 707 | 71 | 9 | 54.4% |
| 21 | 6 | 0.723 | 651 | 65 | 9 | 54.4% |
| 22 | 7 | 0.609 | 548 | 55 | 7 | 51.2% |
| 23 | 8 | 0.431 | 388 | 39 | 5 | 48.0% |

このsource profileの時刻は元datasetのUTCである。データセットの現地timezoneが不明なため、参考JST列はUTC+9を機械的に計算しただけであり、本郷・東京の生活時刻として検証された位相ではない。実験でJSTの日内変動として使用する前に、ピーク時刻の位相をそのまま採用するかを決定する。

### RAN VRAM容量の妥当性

`airan-workload-calculate/rrc_peak_connection_rate_estimation.md`では、27,000人の大学キャンパス人口からピークRRC_CONNECTED UE数を次の二段階で推計している。

1. ピーク時に無線networkへ接続している端末率を26.1%と置く。
2. そのうちRRC_CONNECTEDである割合をLow 5.4%、Base 7.0%、High 12.8%と置く。

これにより、全人口に対するピークRRC_CONNECTED率とUE数は次のように推計されていた。

| Scenario | 全人口に対するピークRRC_CONNECTED率 | 27,000人でのピークUE |
|---|---:|---:|
| Low | 1.41% | 約380 UE |
| Base | 1.83%、実装では2%へ丸める | 約494 UE、既存defaultは540 UE |
| High | 3.34% | 約900 UE |

この推計は、大学Wi-Fiの最大同時接続率、異なる商用mobile network研究のRRC connection回数・継続時間、公開RAN datasetの日内peak/average比を組み合わせたものである。大学キャンパスで直接測定したRRC_CONNECTED数ではなく、不確実性を含むcapacity-planning estimateである。

今回のVRAM実測で負荷を生成したUEは、単にRRC_CONNECTED状態を維持したUEではなく、iperfのTCPパケットを流したActive traffic UEである。したがって、上記のpopulation-derived RRC_CONNECTED UE数をVRAM一次式へ直接入力してはならない。RRC_CONNECTED UE数からActive traffic UE数への変換が必要である。

Active UE率10%は、商用LTE/NR基地局の15分粒度PM counterを公開したデータセットのうち、Dataset_01のLTE 2100 MHzについて、全レコードのActive UE数合計をRRC UE数合計で割って確認した値に基づく。DLは8.56%、ULは10.96%であり、baselineを10%とする根拠になる。

- [Performance Management Counters from Live 5G, 4G and 2G Radio Access Network](https://zenodo.org/records/17815388)
- [3GPP TS 28.552: RRC connection number and number of active UEs](https://www.etsi.org/deliver/etsi_ts/128500_128599/128552/18.07.00_60/ts_128552v180700p.pdf)

ただし、同じ公開データセットのNR 1800 MHzではDL 45.95%、UL 44.60%であり、10%は全network・全条件に共通する定数ではない。Network世代、RRC inactivity timer、セル当たりUE数、トラフィック構成およびcounter定義に依存する。このため10%をbaselineとし、5%、30%、50%を感度分析に使用する。

| Active UE率 | 全体RRC UE | 全体Active UE | 平均Active UE/GH200 | 均等整数配置時の最大推定VRAM |
|---:|---:|---:|---:|---:|
| 5% | 900 | 45 | 5.625 | 49.6%（最大6 UE/GH200） |
| 10% baseline | 900 | 90 | 11.25 | 59.2%（最大12 UE/GH200） |
| 30% | 900 | 270 | 33.75 | 94.4%（最大34 UE/GH200） |
| 50% | 900 | 450 | 56.25 | 131.2%（最大57 UE/GH200、capacity超過） |

10% baselineの均等配置では、ピーク時でもRAN使用VRAMは最大59.2%であり、約40.8%がAI model weightとKV cacheに残る。1台当たり12 Active UEは10 UEの実測校正点に近いため、一次近似の使用範囲として大きくは外れていない。一方、30%条件は34 UE/GH200まで外挿するため、高負荷の感度分析であって実測保証値ではない。

地理的偏在も区別する。90 Active UEが一方のsiteへ集中し、そのsite内4台へ均等配分される場合、最大23 Active UE/GH200となり、推定RAN VRAMは76.8%である。これはAI向けVRAMが約23.2%まで減少する、03のsite間offloadを動機付ける条件になる。ただし23 UE/GH200も実測範囲外である。

このVRAMモデルが表すのは、実測時と同じiperf設定におけるActive TCP UE数の増加である。UE当たりの目標帯域、通信方向、パケットサイズ、セル/RU配置が変われば傾きも変わり得る。特に、全UEが常時iperfを流す負荷を一般ユーザの実トラフィックと同一視しない。

以上から、01では次の値を混同しない。

- Population: 27,000人。
- RRC_CONNECTED UE: peak 900。
- Active traffic UE: baselineでRRCの10%、peak 90。
- VRAM一次式のUE: Active TCP UEであり、RRC_CONNECTED UEではない。

この整理では、8 GH200構成はbaselineで破綻せず、RANに約59%、AIに約41%のVRAMを割り当てる状態を作れる。Active UE率、地理的偏在および一次式の外挿に対する感度分析を行い、この結論の頑健性を確認する。

## 5. RAN resource model

### 後続実験で採用するheavy設定

Active TCP UE率10%のbaselineではPeak 1xにおいてAI向けVRAMに余裕が残ったため、後続のRAN-aware実験ではActive TCP UE率を20%へ引き上げる。変更する変数はActive TCP UE率だけとし、東京・鹿児島への均等配置は維持する。

```text
全体RRC_CONNECTED UE = 900
Active TCP UE率        = 10%
Active TCP UE率        = 20%
全体Active TCP UE      = 180
Active TCP UE/site    = 90
Active UE/GH200       = 平均22.5、最大23
RAN VRAM使用率        = 0.40 + 0.016 * 23 = 76.8%
AI利用可能VRAM        = 23.2%
```

実機報告値95.577 GB/GPUを基準にすると、両siteでAIが利用できるVRAMは22.174 GB/GPUとなる。10%条件との差はActive TCP UE率と、それから導出されるRAN VRAM使用率だけである。AI request workload、地理配置、GPU数、network、model、routing policyは変更しない。

Active UE率30%では均等配置でもRAN VRAMが94.4%となり、AI向けは約5.35 GB/GPUしか残らない。50%では131.2%となりRAN自体のcapacityを超える。そのため20%を、Llama-3.1-8Bを収容できるheavy設定として採用する。

RAN負荷から、AIへ残されるVRAM容量を次のように生成する。

```text
ai_vram_gb(site, t)
  = total_vram_gb - ran_vram_gb(site, t) - reserved_vram_gb
```

### VRAM

GH200 baselineでは、実装でiperf TCPトラフィックを流して取得した`0 Active UEで40%`、`10 Active UEで56%`を使用する。時間帯・site別のActive traffic UE数を上記の一次式へ入力し、RAN使用VRAMとAI利用可能VRAMを求める。

900 RRC UE、Active率10%の均等ピークでは、各GH200の平均は11.25 Active UE、整数配置時の最大は12 UEとなり、RAN VRAM使用率は最大59.2%となる。地理的不均衡の代表例として、全体90 Active UEを維持したまま東京へ全て偏らせると、東京siteでは最大23 UE/GH200、76.8%、鹿児島siteでは0 UE/GH200、40%になる。

### Compute sharingの扱い

本研究では、RANとAIのcompute core割当、kernel-level interference、memory-bandwidth contentionをモデル化しない。LLMのlayer latencyにはGH200 profileをそのまま使用し、RAN workloadに応じて変化させる資源はVRAM容量だけとする。この仮定はLLM推論性能に対して楽観的である可能性があり、compute resource sharingとの共同評価は今後の研究課題とする。

## 6. AI workload model

既存Hongo workloadとの連続性を保つため、最初のbaselineを以下とする。

```text
population                    = 27,000 users
AI DAU fraction               = 20%
AI DAU                        = 5,400 users/day
turns per active user per day = 40
total turns per day           = 216,000
daily-average arrival rate    = 2.5 turns/s
busy-hour arrival rate        = 9 turns/s
```

負荷レベルはbusy hourを1xとして生成する。

| 負荷レベル | 全体の到着率 | 均等時の到着率/site |
|---:|---:|---:|
| Daily average | 2.5 turns/s | 1.25 turns/s |
| Peak 1x | 9 turns/s | 4.5 turns/s |
| Peak 2x | 18 turns/s | 9 turns/s |
| Peak 5x | 45 turns/s | 22.5 turns/s |
| Peak 10x | 90 turns/s | 45 turns/s |

01では、まず既存レートとの連続性を確認する。その後、multi-turn/agentic workloadとして以下を改善する。

- 同一sessionに複数turnを持たせる。
- Turn間にuser think timeまたはtool execution timeを持たせる。
- 過去turnをcontextへ累積し、KV cacheがsession継続に伴って増えるようにする。
- 同一sessionのhome siteを固定する。
- RAN接続中のユーザとAI requestを生成するユーザの関係を記録する。

## 7. 01で最初に生成するworkload

最初の実装では、以下の3種類を作る。

### A. Uniform baseline

- 東京と鹿児島にユーザ、RAN UE、AI requestを均等配分する。
- 両siteで同じ位相の日内曲線を使用する。
- Workload generatorとresource modelが意図した値になるかを検証する。

### B. Temporal variation

- 総ユーザ数27,000人とsiteごとの人口は固定する。
- RAN負荷とAI arrival rateを時間帯によって変化させる。
- AIへ残るVRAM、session収容数、推論性能の時間変化を確認する。

### C. Spatiotemporal imbalance

- 全体のRAN UE数とAI request数はuniform baselineと同じにする。
- Siteごとに負荷曲線の位相または大きさを変える。
- あるsiteでsession収容不足とqueueingが発生する一方、別siteではAI向けVRAMが余る状態を作る。

このCを、03のgeographical offload評価へ引き渡す基本workloadとする。

## 8. 未確定事項

- 160 logical cellsを実際のRU数、sector数、carrier数へどう対応させるか。
- SoftBank実証の20セル処理時にRANが使用するGH200のVRAMとpower。
- RANとAIのcompute core共有、kernel interference、memory-bandwidth contentionのモデル化。
- Active UE率10%が東京・鹿児島の5G workloadに対して妥当か。5%、30%、50%で感度分析する。
- `10 Active UE/GH200`を超える領域でも、iperf実測から得たVRAM一次式をどこまで適用できるか。
- 実機iperf測定時のUE当たり帯域、通信方向、packet size、cell/RU割当と、生成workloadの対応。
- 東京・鹿児島間の人口分布とRAN/AI負荷曲線の位相差。
- AI requestの日内分布をRANの日内曲線と相関させるか、独立に生成するか。

これらにはまず暫定値を置き、時間帯・site別のRAN VRAM使用量、AI利用可能VRAM、KV cache収容可能量を確認する。さらに不確実な値を変化させ、局所的なVRAM不足がどの条件で発生するかを調べた上で、02以降に使用する代表条件を決定する。
