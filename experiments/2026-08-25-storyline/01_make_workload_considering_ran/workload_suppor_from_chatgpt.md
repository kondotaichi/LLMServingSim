本郷 AI-RAN Baseline：Active UE Workload 仮定

最終確認日: 2026-08-25
目的: 本郷キャンパスを想定した AI-and-RAN / RAN workload simulation の baseline 定義
位置づけ: Baseline scenario assumption。本郷キャンパスの携帯網における Active UE 数を直接実測した値ではない。

1. 要約

Baseline のピーク workload は、次式で定義する。

$$
N_{\mathrm{Active,baseline}} =
N_{\mathrm{population}}
\times
p_{\mathrm{present}}
\times
p_{\mathrm{RRC}}
\times
p_{\mathrm{active}}
$$

ここで、

$$
N_{\mathrm{population}} = 27{,}000,
\quad
p_{\mathrm{present}} = 0.261,
\quad
p_{\mathrm{RRC}} = 0.128,
\quad
p_{\mathrm{active}} = 0.10
$$

とする。

したがって、

$$
27{,}000 \times 0.261 \times 0.128 \times 0.10 =
90.2016
\approx
90
$$

となる。

よって、本研究では baseline peak load を 90 Active UE とする。

ただし、この 90 UE は本郷キャンパスにおける直接観測値ではなく、scenario-based なピーク workload 推計値として扱う。

この推計は、

対象人口、

キャンパス内に存在しネットワークを利用し得る人口の proxy、

RRC_CONNECTED にある UE の割合、

そのうち実際に通信トラフィックを持つ Active UE の割合、

を順に掛け合わせたものである。

研究論文・修士論文等では、次のような表現が最も安全である。

本研究では、ピーク時の baseline workload として 90 traffic-active UEs を仮定する。この値は、本郷キャンパスの携帯網における直接実測値ではなく、人口規模と公開ネットワークデータおよび RAN engineering reference を組み合わせた scenario-based estimate である。

2. Baseline の導出

段階

記号

Baseline

人数

意味

対象人口

$N_{\mathrm{population}}$

27,000

27,000

本郷 workload が表現する母集団

ピーク時に存在・接続し得る人口

$p_{\mathrm{present}}$

26.1%

7,047

Campus presence / connected-device proxy

RRC_CONNECTED 比率

$p_{\mathrm{RRC}}$

12.8%

902

RRC_CONNECTED にある UE の代表値

Traffic-active 比率

$p_{\mathrm{active}}$

10%

90

RRC_CONNECTED UE のうち実際に traffic を持つ割合

中間値は、

$$
27{,}000\times0.261=7{,}047
$$

および、

$$
7{,}047\times0.128=902.016
$$

である。

最後に、

$$
902.016\times0.10=90.2016
$$

となる。

したがって、

$$
\boxed{N_{\mathrm{Active,baseline}}\approx90}
$$

である。

また、元の 27,000 人に対する Active UE の割合は、

$$
\frac{90}{27{,}000}
\approx
0.00333 =
0.333\%
$$

である。

3. 各パラメータの根拠の強さ

各パラメータは、同じ強さの evidence を持つわけではない。研究上は、その違いを明示すべきである。

パラメータ

値

Evidence level

推奨される解釈

$N_{\mathrm{population}}$

27,000

Scenario assumption

Workload が表現する対象人口

$p_{\mathrm{present}}$

26.1%

Scenario proxy

Peak campus presence / potentially connected-device fraction

$p_{\mathrm{RRC}}$

12.8%

Range-supported assumption

公開 LTE 文献で報告される connected-user 比率の範囲内にある代表値

$p_{\mathrm{active}}$

10%

Directly supported engineering baseline

公開 RAN cell-load definition で採用されている Active UE / RRC UE 比

したがって、baseline は「本郷キャンパス固有の4つの直接実測値を掛け合わせたもの」ではない。

より正確には、複数の公開情報を組み合わせた cross-study workload model である。

4. 対象人口：27,000 人

本 workload では、

$$
N_{\mathrm{population}}=27{,}000
$$

を、本郷シナリオが表現する target population とする。

ただし、27,000 人を「現在の本郷キャンパスに常時存在する人数」あるいは「本郷キャンパスの公式在籍者数」と直接記述することは避ける。

東京大学公式の UTokyo by the Numbers では、2025年更新値として大学全体で、

学生：29,669 人

教職員：11,948 人

が示されている [1]。

ただし、これは本郷・駒場・柏等を含む大学全体の値であり、本郷キャンパスだけの人口を直接示すものではない。

推奨表現

本研究では、本郷シナリオが表現する対象人口を 27,000 人と設定する。

避けるべき表現

本郷キャンパスには正確に 27,000 人が存在する。

本郷固有の公式データを別途得られた場合のみ、後者のような強い表現を検討する。

5. ピーク時 presence / connected-device proxy：26.1%

本モデルでは、

$$
p_{\mathrm{present}}=26.1\%
$$

とする。

これにより、

$$
27{,}000\times0.261=7{,}047
$$

人程度が、ピーク時に対象エリア内に存在し、ネットワークを利用し得る人口・端末として表現される。

5.1 なぜ中間の presence / connectivity factor が必要か

キャンパスに所属する全人口が、同じ時刻にキャンパス内に存在し、同時に携帯網トラフィックを生成するとは考えにくい。

したがって、人口から直接 RRC UE や Active UE を推計するのではなく、

$$
N_{\mathrm{population}}
\rightarrow
N_{\mathrm{present}}
\rightarrow
N_{\mathrm{RRC}}
\rightarrow
N_{\mathrm{Active}}
$$

という段階的なモデルにすることは妥当である。

東京大学の Wi-Fi 導入事例では、2023年の大学全体の Wi-Fi rollout において、ピーク時に約 18,000 台の同時接続端末があったことが報告されている [2]。

また、東京大学 UTokyo Wi-Fi の公式説明では、1アカウント当たりの接続端末数に制限がないことが明記されている [3]。

このことから、

大学キャンパスではピーク時に非常に多数の端末が同時接続し得ること、

Wi-Fi の「端末数」と「人の数」は1対1対応しないこと、

が分かる。

5.2 26.1% が意味するもの

本 workload では、26.1%を 本郷キャンパスの cellular attachment probability の直接実測値としては扱わない。

代わりに、

$$
p_{\mathrm{present}}
$$

を、

27,000 人の scenario population のうち、ピーク時に対象 RAN エリア内に存在し、ネットワーク利用の可能性がある人口・端末の割合を表す proxy

と解釈する。

したがって、

$$
p_{\mathrm{present}}
\neq
P(\mathrm{RRC_CONNECTED})
$$

である。

また、

「本郷キャンパス人口の26.1%がピーク時に携帯網へ接続している」

と断定すべきではない。

5.3 26.1% の source audit 上の注意

大学 Wi-Fi に関する公開 survey では、「同時に接続している端末が1台である回答者の割合」など、別の意味で 26.1% という数値が現れる場合がある [4]。

このような値を、

「大学人口の26.1%がピーク時に接続する」

という意味に読み替えることはできない。

したがって、本モデルでは 26.1% を direct measurement ではなく scenario proxy と明示する。

5.4 現時点での弱点

現在のbaselineの中では、この26.1%が最も不確実性の高いパラメータである。

今後、

本郷キャンパスの Wi-Fi controller statistics

入構・滞在人口データ

携帯事業者の PM counters

実際の RRC_CONNECTED UE 数

等が取得できる場合は、最優先でこの26.1%を置き換えるべきである。

6. RRC_CONNECTED 比率：12.8%

Baseline では、

$$
p_{\mathrm{RRC}}=12.8\%
$$

とする。

これを7,047人に適用すると、

$$
7{,}047\times0.128\approx902
$$

RRC_CONNECTED UE となる。

6.1 オーダーとしての妥当性

Zhang の LTE Optimization Engineering Handbook では、attached subscribers と RRC-connected users の関係が議論されており、live network の例として RRC-connected users が attached subscribers のおよそ15%以下となるケースが示されている [5]。

また、Ericsson の LTE connected-user dimensioning guideline では、mature network において RRC_CONNECTED users が attached subscribers の20%未満となる観測例が述べられている [6]。

したがって、12.8%という値は、

$$
10\%-20\%
$$

程度の low-teens の範囲にある値として、少なくとも LTE-like workload の代表値としては不自然ではない。

ただし、

12.8%が普遍的な cellular network の定数である

という意味ではない。

6.2 RRC_CONNECTED 比率が変動する要因

RRC_CONNECTED UE 数は、少なくとも以下に依存する。

RRC inactivity timer

Application traffic pattern

Background traffic

Mobility

Operator configuration

LTE / NR の違い

NSA / SA architecture

Measurement interval

PM counter の定義

そのため、12.8%は representative point estimate として利用し、感度分析によって不確実性を補うことが望ましい。

7. Active UE / RRC UE 比率：10%

Baseline では、

$$
p_{\mathrm{active}}=10\%
$$

を採用する。

この値は、現在の推計 chain の中で最も直接的な engineering support を持つ。

7.1 Arm / OpenAirInterface の cell-load definition

2022年に OpenAirInterface 経由で公開された Arm の資料では、RAN の representative cell load として以下が定義されている [7]。

Load

Active UE

RRC UE

Active/RRC

Low

120

1,200

10%

Mid

240

2,400

10%

High

600

6,000

10%

すべての load level において、

$$
\frac{N_{\mathrm{Active}}}{N_{\mathrm{RRC}}}=0.10
$$

である。

したがって、Active UE / RRC UE = 10% という baseline は、公開された RAN engineering workload definition に直接的な precedent がある。

7.2 商用ネットワーク PM data

Lehoczký らは2026年に、商用 2G / LTE / NR network から取得した PM counters の公開 dataset を発表している [8,9]。

この dataset は15分間隔の network measurement を含み、例えば、

RRC-connected users

Active users

Resource utilisation

CQI

UL / DL data volume

Energy consumption

などを含む。

重要なのは、RRC-connected users と Active users が異なる counter として記録されていることである。

したがって、

$$
\mathrm{RRC_CONNECTED}
\neq
\mathrm{traffic\ active}
$$

である。

このため、

900 RRC_CONNECTED UE が全員同時に traffic-active である

と仮定するよりも、

900 RRC_CONNECTED UE のうち一部だけが実際に traffic を持つ

とする方が妥当である。

7.3 Baseline としての解釈

以上から、

$$
900\times0.10\approx90
$$

という設定は、LTE-like engineering workload の baseline として妥当である。

ただし、Active/RRC 比率も network・traffic condition に依存するため、10%を LTE / NR の普遍値として扱うべきではない。

8. Baseline と感度分析ケース

丸めた

$$
N_{\mathrm{RRC}}=900
$$

を基準として、以下の workload case を定義する。

Case

Active/RRC ratio

Active UE

全人口比

用途

Low

5%

45

0.167%

軽負荷

Baseline

10%

90

0.333%

主実験

High

30%

270

1.00%

高 activity sensitivity

NR-like sensitivity

45%

405

1.50%

NRを意識した高activity case

Stress

50%

450

1.67%

容量限界・stress test

丸め前の

$$
N_{\mathrm{RRC}}=902.016
$$

を使うと45% caseは、

$$
902.016\times0.45
\approx406
$$

となる。

ただし、baseline definitionとの整合性のため、表では900 RRC UEを基準に405 UEとしている。

推奨する主要実験セット

実験条件数を抑えたい場合は、

$$
\boxed{45,\ 90,\ 270,\ 450}
$$

で十分である。

NR-like な高 activity condition も明示的に評価する場合は、

$$
\boxed{45,\ 90,\ 270,\ 405,\ 450}
$$

とする。

45% NR-like case に関する注意

Lehoczký らの公開 dataset を独自解析して、特定の NR cell で Active/RRC 比率がおよそ45%となることを示す場合には、

使用した cell

filtering 条件

aggregation 方法

UL / DL の扱い

analysis script

出力結果

を experiment repository に保存する。

Scientific Data 論文および Zenodo dataset は source dataset として引用する。

ただし、その論文自体に「NRでは普遍的に45%である」と明記されていない限り、

「文献[8]が45%と報告している」

とは書かない。

9. 90 Active UE が表すもの

Baseline は、

ピーク時に traffic を持つ 90 Active UEs

を意味する。

以下の意味ではない。

キャンパス全体に存在する携帯端末が90台しかない。

本モデルでは、

$$
N_{\mathrm{population}}
\rightarrow
N_{\mathrm{present}}
\rightarrow
N_{\mathrm{RRC}}
\rightarrow
N_{\mathrm{Active}}
$$

という異なる population を明確に区別する。

数値としては、

$$
27{,}000
\rightarrow
7{,}047
\rightarrow
902
\rightarrow
90
$$

である。

各値は異なる意味を持つため、implementation・figure・paper でも混同しないこと。

10. Active UE 数と PHY / GPU 負荷は同じではない

AI-and-RAN の実験では、この点が非常に重要である。

Active UE 数だけでは、RAN GPU utilization は一意に決まらない。

例えば、同じ90 Active UEであっても、

90 UE が sparse messaging traffic を生成する場合

90 UE が high-rate video traffic を継続的に受信する場合

では、PHY workload は大きく異なる。

したがって、より適切な workload path は、

$$
N_{\mathrm{Active}}
\rightarrow
\text{offered traffic}
\rightarrow
\text{scheduled PRBs}
\rightarrow
\text{MCS / MIMO / HARQ workload}
\rightarrow
\text{PHY compute}
\rightarrow
\text{GPU utilization}
$$

である。

Lehoczký らの commercial PM dataset にも、user counters とは別に、

radio-resource utilization

UL / DL data volume

が含まれている [8,9]。

これは、

UE concurrency と radio load は別の dimension として扱うべきである

ことと整合する。

したがって、本研究では、

90 Active UEs：baseline の user concurrency / traffic-generator population

RAN GPU load：Active UE が生成する traffic と、それにより生じる PRB utilization / PHY processing load

として分離する。

例えば、

90 Active UE = GPU utilization 90%

のような直接対応は仮定しない。

11. 論文・修士論文で使用する推奨記述

Methodology 用

本研究では、本郷キャンパスを想定したシナリオにおいて、対象人口を27,000人と設定する。対象人口の全員が同時にネットワークを利用するわけではないため、ピーク時に対象エリア内に存在しネットワークを利用し得る人口を表す proxy として26.1%を適用し、7,047人を得る。次に、その12.8%が RRC_CONNECTED 状態にあると仮定し、約900 RRC-connected UEs を得る。12.8%という値は、本郷キャンパスの直接実測値ではないものの、LTE dimensioning literature において attached subscriber に対する RRC-connected user の割合が概ね15〜20%以下となる例が報告されていることから、low-teens の代表値として設定した [5,6]。さらに、RRC-connected UE のうち10%が実際に traffic-active であると仮定する。この比率は、Arm/OpenAirInterface の cell-load definition において、low、mid、high load それぞれで 120/1200、240/2400、600/6000 の Active UE / RRC UE 比が採用されていることと整合する [7]。以上より、本研究では baseline peak workload を約90 Active UEs とする。なお、この値は本郷キャンパスの携帯網における直接実測値ではなく、人口規模と公開ネットワークデータおよび RAN engineering reference を組み合わせた scenario-based estimate である。

簡潔な式

$$
27{,}000
\times 0.261
\times 0.128
\times 0.10
\approx90
$$

感度分析の説明

Active UE 比率の不確実性を考慮し、RRC-connected UE に対する traffic-active UE の割合を5〜50%の範囲で変化させる。約900 RRC-connected UEs に対し、これは45〜450 Active UEsに相当する。10%（90 UE）を baseline、30%（270 UE）を high-load、50%（450 UE）を stress case とする。

12. 書いてよい主張／避けるべき主張

書いてよい

本研究では baseline peak として 90 Active UE を仮定する。

90 UE は、population-derived workload model から導出した scenario-based estimate である。

Active/RRC = 10% には、公開 RAN cell-load definition に直接的な precedent がある。

Low-teens の RRC_CONNECTED 比率は LTE dimensioning literature と整合的である。

Commercial network dataset では、RRC-connected users と active users が別々に測定されている。

Network / traffic condition への依存性を考慮し、感度分析を行う。

避けるべき

「本郷キャンパス人口の正確に26.1%がピーク時に携帯網へ接続する」

「本郷キャンパスの携帯加入者の正確に12.8%が RRC_CONNECTED である」

「LTE / 5G では普遍的に Active UE 比率は10%である」

「本郷キャンパスではピーク時に正確に90 Active UEが存在する」

「90 Active UEは一定のGPU utilizationに直接対応する」

13. パラメータ provenance のまとめ

パラメータ

Baseline

根拠

論文での扱い

Population

27,000

Study scenario

対象人口として明記

Presence/connectivity

26.1%

Cross-study / scenario proxy

直接実測値とは書かない

RRC_CONNECTED

12.8%

LTE文献で報告される範囲内の代表値

文献を引用し、感度分析で補う

Active/RRC

10%

Arm/OAI cell-load definition

Main baseline とする

Baseline Active UE

90

上記からの導出値

主実験

High Active UE

270

Sensitivity assumption

高負荷条件

Stress Active UE

450

Sensitivity assumption

容量限界条件

14. 再現性のために保存すべき情報

論文投稿・修士論文提出までに、experiment repository に以下を保存する。

27,000人という target population の由来

26.1%を presence/connectivity proxy として利用する理由

12.8% RRC-connected assumption の導出根拠

10% Active/RRC ratio の Arm/OAI source

Lehoczký らの dataset から LTE / NR Active/RRC ratio を算出する場合の analysis script

Simulator 内における "Active UE" の厳密な定義

各 Active UE に割り当てる traffic model

PRB / resource utilization の決定方法

RRC inactivity timer をモデル化する場合の設定値

少なくとも 5%、10%、30%、50% Active/RRC ratio の sensitivity result

参考文献

[1] The University of Tokyo, “UTokyo by the Numbers.” Updated Dec. 9, 2025.
https://www.u-tokyo.ac.jp/en/about/numbers.html

[2] Juniper Networks, “The University of Tokyo Case Study.”
東京大学の大学全体 Wi-Fi deployment において、ピーク時に約18,000台の concurrently connected devices が存在したことを報告。
https://www.juniper.net/us/en/customers/the-university-of-tokyo-case-study.html

[3] The University of Tokyo, utelecon, “UTokyo Wi-Fi.”
1アカウント当たりの接続端末数に制限がないことを記載。
https://utelecon.adm.u-tokyo.ac.jp/en/utokyo_wifi/

[4] EDUCAUSE Center for Analysis and Research (ECAR), student technology / simultaneously connected device survey material (2015).
26.1%という値を campus-wide peak connection rate と誤解しないための source-audit 用参考資料。
https://its.appstate.edu/sites/its.appstate.edu/files/ecar_study_of_students_and_technology_2015.pdf

[5] X. Zhang, LTE Optimization Engineering Handbook, Wiley-IEEE Press, 2018. DOI: 10.1002/9781119158981.
RRC-connected users と attached subscribers の関係を扱い、live-network example において connected users が概ね15%以下となるケースを示す。
https://doi.org/10.1002/9781119158981

[6] Ericsson AB, LTE Connected Users Definition and Dimensioning Guideline, Rev. PA1, Feb. 26, 2013.
Mature network において RRC Connected Users が attached subscribers の20%未満となる観測例、および traffic/application characteristic への依存性を説明。
Publicly indexed copy:
https://pdfcoffee.com/lte-connected-users-dimensioning-guideline-pdf-free.html

注: 最終的な論文 bibliography では、可能であれば Ericsson の公式 copy を優先する。

[7] Arm / OpenAirInterface, “Cell Load Definitions,” 2022.
Low/Mid/High case をそれぞれ 120/1200、240/2400、600/6000 Active UE / RRC UE と定義しており、すべて10%。
https://openairinterface.org/wp-content/uploads/2022/11/3-Mo-ARM.pdf

[8] P. Lehoczký, M. Turcsány, L. Krajčovičová, F. Zatroch, M. Kajan, and M. Galinski, “Performance Management Counters from Live 5G, 4G and 2G Radio Access Network,” Scientific Data, 2026. DOI: 10.1038/s41597-026-07723-0.
https://doi.org/10.1038/s41597-026-07723-0

[9] P. Lehoczký, M. Turcsány, L. Krajčovičová, F. Zatroch, M. Kajan, and M. Galinski, Performance Management Counters from Live 5G, 4G and 2G Radio Access Network [Dataset], Zenodo, 2026. DOI: 10.5281/zenodo.17815388.
https://doi.org/10.5281/zenodo.17815388

15. 最終的な baseline 設定

現在の AI-and-RAN 本郷 workload では、

$$
\boxed{
N_{\mathrm{Active,baseline}}=90
}
$$

を baseline peak traffic-active UE count として採用する。

その前段として、

$$
\boxed{
N_{\mathrm{RRC}}\approx900
}
$$

を想定する。

ただし、これらは scenario-based cross-study estimate であり、本郷キャンパス cellular network の直接観測値ではないことを明記する。

Core sensitivity set は、

$$
\boxed{
45,\ 90,\ 270,\ 450
}
$$

Active UE とする。

NR-like high-activity condition を明示的に検証する場合は、

$$
\boxed{
45,\ 90,\ 270,\ 405,\ 450
}
$$

を用いる。

現在のモデルにおける最大の不確実性は、

$$
\boxed{p_{\mathrm{present}}=26.1\%}
$$

である。

本郷固有の Wi-Fi statistics、campus attendance data、あるいは cellular PM counter が得られた場合、最初に置き換えるべきパラメータはこの26.1%である。
