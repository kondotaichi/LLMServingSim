# 02-b: Active TCP UE rate 20%

## 10%条件との差分

変更する変数はActive TCP UE率と、それから導出されるAI利用可能VRAMだけである。

```text
RRC_CONNECTED UE peak = 900
Active TCP UE rate    = 20%
Active TCP UE         = 180
配置                  = Tokyo/Kagoshima均等
最大Active UE/GH200   = 23
RAN VRAM              = 76.8%
AI VRAM               = 22.173864 GB/GPU
```

AI workload、Cloud config、GH200数、model、routing、network、seed、request数は10%条件から変更しない。地理集中とWANは使用しない。

## Sweep

- Peak: 10xから1xの降順
- Environment: Cloud、AI-RAN APN
- PP: 1、2
- Routing: `NEAREST_KV`
- Requests: 600/condition
- Conditions: 10 peaks x 2 environments x 2 PP = 40
- Parallelism: 8 processes

実行スクリプトは`scripts/run_sweep_8parallel.sh`である。

## Results

40条件はすべて600 requestsで完了した。結果の解釈、集計表、図は[REPORT.md](REPORT.md)にまとめた。

主結果として、PP=1ではPeak 2xまでcapacity waitがなくCloudとほぼ同等、Peak 3xでVRAM容量待ちが発生し、Peak 4xから性能差が明確になった。
