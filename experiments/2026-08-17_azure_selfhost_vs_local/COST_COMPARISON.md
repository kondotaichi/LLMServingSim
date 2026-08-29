# Local GPU electricity vs Azure PAYG cost

更新: 2026-08-19

## 結果

Hongo Peak 1x、5x、10xの元300-request sweepについて、提案手法
（Local RTX 4090 x12、PP=2 + KV migrate）のGPU電気代と、Azure H100 NVL x8の
VM compute料金を比較した。

| Load | Local duration | Local GPU energy | Local electricity | Azure duration | Azure compute | Azure / Local |
|---|---:|---:|---:|---:|---:|---:|
| Peak 1x | 51.18 s | 36.02 Wh | ¥1.12 | 37.37 s | $0.840 / ¥126.1 | 112.9x |
| Peak 5x | 42.08 s | 23.78 Wh | ¥0.74 | 23.45 s | $0.527 / ¥79.1 | 107.3x |
| Peak 10x | 41.89 s | 23.29 Wh | ¥0.72 | 23.69 s | $0.533 / ¥79.9 | 110.7x |

AzureはTTFTを短縮する一方、この限定的な比較では300 requestあたり約79〜126円で、
ローカルGPU電気代推定の約107〜113倍である。

## 前提

### Local

- `gpus.csv`に記録されたGPUごとのbusy/idle時間を使用
- RTX 4090 busy: 450 W
- RTX 4090 idle: 19 W
- 電気料金: 31円/kWh
- GPU 12枚の消費電力だけを計上
- CPU、DRAM、NIC、storage、PSU損失、冷却、設備、GPU購入・償却費は含めない

RTX 4090の450 Wと19 WはNVIDIAの公式仕様値である。31円/kWhは資源エネルギー庁が
省エネ金額換算に使用している目安単価である。したがってLocal値は実測電力量ではなく、
simulationの稼働時間に仕様上の電力を掛けた概算である。

### Azure

- Region: Japan East
- SKU: `Standard_NC40ads_H100_v5`
- Linux Pay-as-you-go: $10.121 / VM-hour
- VM数: 8
- cluster rate: $80.968 / hour
- request 0の送信開始から先頭300件の最後のresponse完了までを課金時間として換算
- 表示換算: 1 USD = 150 JPY（固定仮定）
- disk、network egress、Bastion、login nodeなどは含めない

単価は2026-08-19にMicrosoft Azure Retail Prices APIで確認した。実際の請求はVMをallocateしていた
全時間に対して発生するため、表のper-run costより大きい。8台を1時間確保すると$80.968
（仮定レートで約12,145円）、2時間確保すると$161.936（約24,290円）である。

今回の実験は約1時間GPU jobを確保していたため、GPU VM computeの実支出規模は約$81である。
正確な請求額はAzure Cost Managementでresourceとmeterを確認する必要がある。

## 解釈上の注意

この表はLocalでは限界費用の一部であるGPU電気代だけ、Azureではhardware、host、運用を含む
VM利用料を比較している。所有GPUの購入費と償却費を除外しているため、Localを有利に評価する。
一方、Azure側もdisk、network、Bastionなどを除外している。

公平な総保有コスト比較には、LocalへGPU/host購入費、償却期間、稼働率、冷却、保守を追加し、
Azureへ付帯resourceと準備・idle時間を追加する必要がある。

## Artifacts

- `analysis/cost_proposed_vs_azure.csv`
- `figures/cost_proposed_vs_azure.svg`
- `figures/cost_proposed_vs_azure.png`
- `scripts/analyze_cost_comparison.py`
