# 02-b: RAN-aware economics

完了結果と解釈は[REPORT.md](REPORT.md)にまとめる。

## Peak 1xの実行条件

- AI workload: 600 requests、busy-hour 9 turns/s、Peak 1x、seed 1
- Routing: `NEAREST_KV`
- Physical GPU: GH200 8台
- Cloud: AI専用GPUとして既存のcloud configのVRAM容量を使用
- AI-RAN: 01の均等配置におけるRAN peakを適用
- RAN peak: 900 RRC_CONNECTED UE、Active TCP UE率10%、全体90 Active UE
- 最大Active UE/GH200: 12
- RAN VRAM使用率: `0.40 + 0.016 * 12 = 0.592`
- AI利用可能VRAM: `95.577 GB * (1 - 0.592) = 38.995416 GB/GPU`
- 主評価: PPなし（PP=1）のsingle-GPU model instance
- 参考run: 将来の04設計確認用としてPP=2も保存するが、02-bの性能・経済性の主張には使用しない
- 東京―鹿児島のaccess/backbone: APN 10.7 Gbps、300.5 us、およびWAN 1.0 Gbps、5 ms

この最初の実験では600 requestsが約66.64秒に到着するため、RAN日内曲線はほぼ変化しない。そこで日内変動を無理に補間せず、01で定義したRAN peak時のVRAM制約を固定snapshotとして適用する。CloudとAI-RANの差は、AI専用VRAMとRAN共有後VRAMの差として評価する。

実行スクリプトは`scripts/run_peak1.sh`であり、Cloud、AI-RAN APN、AI-RAN WANについてPP=1/2を組み合わせた6条件を6並列で生成する。02-bの本文ではCloud PP=1、AI-RAN APN PP=1、AI-RAN WAN PP=1の3条件だけを主比較に用いる。PP=2の3条件は削除せず参考データとして保持する。

Peak 10xも同じ6条件・600 requestsで`scripts/run_peak10.sh`から実行する。Peak 10xはRAN制約下のKV容量がbindingすると予測される高負荷条件であり、PP=1を02-bの主比較、PP=2を参考結果として保存する。

## Active TCP UE率20%の比較設定

次のRAN-aware実験では、Active TCP UE率だけを10%から20%へ変更する。地理集中は使用せず、東京・鹿児島と各site内GPUへの均等配置を維持する。

| Site | Active TCP UE | 最大UE/GH200 | RAN VRAM | AI向けVRAM/GPU |
|---|---:|---:|---:|---:|
| Tokyo | 90 | 23 | 76.8% | 22.174 GB |
| Kagoshima | 90 | 23 | 76.8% | 22.174 GB |

生成configは`configs/airan_active20_peak_pp1.json`および`configs/airan_active20_peak_pp2.json`とする。既に完了したActive率10%結果の再現性を保つため、従来の`airan_peak_pp*.json`は上書きしない。20%結果のrun名には`active20`を含める。
