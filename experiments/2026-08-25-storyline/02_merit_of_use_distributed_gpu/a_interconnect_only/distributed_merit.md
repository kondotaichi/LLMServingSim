# Interconnect-only comparison: distributed GPUのメリット

## 主張

RAN workloadをまだ入れず、同じ8台のGH200と`NEAREST_KV`を用いた比較から、次の二点が得られた。

PPを用いず、single-GPUで完結するmodel instanceを各GPUへ配置した場合、CloudとDistributedのTTFTは実質同一である。Model executionが1 GPU内で完結するため、site間linkは推論critical pathへ入らない。Distributedはユーザに近いGPUを選択でき、現在の配置モデルではcommunicationが平均約1.75 us短い。短縮量自体は小さいが、地理分散配置が応答性能を悪化させないことを示している。

さらに、既設AI-RAN GPUを利用する電気料金proxyはMiyabiのサービス利用料金より低い。したがって02-aの主張は、「1 GPUに収まるモデルなら、地理分散AI-RANはCloudと同等の応答性能を保ちつつ、追加コストを削減できる」である。並列化はこの段階の前提にも提案にも含めない。

## 経済性の位置付け

ユーザが想定するMiyabi 8ノードの利用料金を306円/hourとする。これは追加AI workloadをクラウドで処理する場合に支払うサービス利用料金である。一方、AI-RAN側のGPUはRAN処理のためにオンプレミスへ既に導入・保有されているものとし、追加AI workloadを処理する際の限界費用である電気料金を比較対象とする。東京の電力単価は`2026-08-02-kagosima-tokyo`で用いた23.0円/kWhを援用し、`gpus.csv`のGPU utilizationからGH200 8台の電気料金を推定した。

```text
u = busy_time_ns / observation_time_ns
P_GPU(u) = 117 W + (900 W - 117 W) * u
cost = sum(P_GPU) / 1000 * 23.0 yen/kWh
```

| 条件 | 推定電気料金 | Miyabi比 |
|---|---:|---:|
| PP=1, Peak 2x--10x | 135.6--156.0円/hour | 44.3--51.0% |

同じ600 requestの観測時間を用いた1,000 request当たりcostでも、AI-RAN側はPP=1で1.05--2.70円であり、対応するMiyabi料金2.36--5.31円を下回った。したがって、要求SLOを満たすなら既設GPUを追加利用する方がクラウドへ同じ処理を投入するより経済的である。

この比較で両者の費用項目を同じ原価構成へ揃える必要はない。クラウド料金には電力以外の設備・運用費とサービス提供者の費用が含まれる一方、AI-RAN設備の取得・保守費はRAN設備として既に負担される固定費だからである。研究上の問いは総保有コストの比較ではなく、「追加AI workloadをクラウドへ投入するか、既設AI-RAN GPUの余剰資源へ投入するか」という配置判断であり、それぞれの追加支出であるサービス利用料金と電気料金を比較する。

ただし、AI-RAN側の値は実測電力ではなくGPU utilizationからの線形推定である。117 Wと900 WはNVIDIA Aerial資料に掲載されたGH200の低負荷表示値とGPU power limitを端点とするproxyである。そのため、02-bではRAN-only時とRAN+AI時の電力差を取得できれば、AI workloadによる増分電力を使って推定精度を高める。

電力proxyの根拠は[NVIDIA Aerial CUDA-Accelerated RAN](https://docs.nvidia.com/aerial/cuda-accelerated-ran/25-2/aerial-cuda-accelerated-ran.pdf)のGH200表示例、GH200 Superchip全体の上限との区別は[NVIDIA GH200 Benchmark Guide](https://docs.nvidia.com/gh200-superchip-benchmark-guide.pdf)を参照する。

## 図と詳細

- [PP=1の結果](pp1.md)
- [PP=1 peak sweep](figures/pp1/peak_sweep_ttft.png)
- [PP=1 power/cost](figures/pp1/peak_sweep_power_cost.png)
- [全結果CSV](analysis/ttft_summary.csv)
- [Cloud/Distributed paired comparison](analysis/paired_comparison.csv)
- [使用可能result coverage](analysis/coverage.csv)

## 02-bへの接続

02-aでは、RAN制約がなくmodelが1 GPU内で完結する場合、DistributedがCloudと同等の応答性能と低い追加コストを両立することを示した。次の02-bでは01で定義したRAN VRAM使用率を適用し、RANとの資源共有によって性能上の優位性が薄れる一方、経済性が残るかを評価する。02-bの主比較もPPなしで行う。

既に取得したPP=2結果は削除せず[参考結果](pp2.md)として保存するが、02のストーリーおよび主張には使用しない。
