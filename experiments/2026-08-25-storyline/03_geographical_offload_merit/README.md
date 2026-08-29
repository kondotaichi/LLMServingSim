# 03: Geographical offload merit

Hongoの既存workloadを用い、総request数とtoken列を変えずに、Hongoの登録ユーザー分布からGPU別RAN VRAM制約を導出する。

- RRC_CONNECTED peak: 900 UE
- Active TCP ratio: 20%
- PP: 1
- Network: APN（10.7 Gbps、fixed one-way propagation 300.5 us）
- Peak: 5x--10x
- Requests: 600/condition
- AI request配置: Tokyo 70%、Kagoshima 30%（総数・session・token・arrivalは不変）
- `local_only`: `NEAREST_KV`
- `redirect_cold`: `NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE`

`redirect_cold`はhome GPUが収容不能な場合にcapacity pressureが最小のGPUへredirectする。KV cache migrationは行わず、redirect先ではprefixを再計算する。PPおよびKV cache migrationは04で評価する。

GPU別AI VRAMは`scripts/prepare_experiment.py`で生成する。

## Results

- [集計CSV](analysis/summary.csv)
- [Peak 5x--10x sweep](figures/peak_5x_to_10x_sweep.png)
- `figures/peak_<N>x_ttft_breakdown.png`: 各PeakのTTFT breakdown
- `figures/peak_<N>x_ttft_cdf.png`: 各PeakのTTFT・capacity-wait CDF

図と集計は`scripts/analyze.py`で再生成できる。

## Archived preliminary run

Hongo自然分布の弱い不均衡を使った最初の12条件は
`archive_weak_imbalance/`へ分離した。このrunでは全GPUが同時に逼迫し、cold
redirectのprefix再計算も重なったため、redirect側のTTFTが悪化した。現行の
Tokyo 70% / Kagoshima 30%条件とは混在させない。
