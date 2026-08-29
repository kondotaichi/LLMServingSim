# Workload設定

## 環境

- 本郷キャンパス相当の27,000ユーザ
- 東京・鹿児島の2サイトにGH200を各4台（合計8 GPU）
- AI requestは東京70%、鹿児島30%に配置

## AI workload

- AI DAU率20%、1ユーザ40 requests/dayを想定
- Busy-hour rate：`27,000 × 20% × 40 × 15% / 3,600 = 9 req/s`
- 評価負荷：Peak 2x〜5x（18、27、36、45 req/s）
- ShareGPT由来の600 requests、576 sessions
- 平均input 3,860 tokens、平均output 280 tokens
- Multi-turnではsessionと過去contextを維持し、inputの50%を再利用可能KVとして設定

## RAN workload

- ピークRRC_CONNECTED UE：900
- 導出：`27,000 × 無線接続率26.1% × RRC_CONNECTED率12.8% ≈ 902`を900へ丸める
- 今回はRRC_CONNECTED UEの20%がTCP通信中と仮定：180 Active TCP UE
- 登録ユーザ数に比例して8 GPUへ配分：22〜24 Active TCP UE/GPU
- 実測校正点：0 Active UEでVRAM 40%、10 Active UEで56%
- RAN VRAMモデル：`40% + 1.6% × Active TCP UE数`
- 今回のRAN VRAM使用率：75.2〜78.4%
- AI利用可能VRAM：約20.6〜23.7 GiB/GPU

※ 900 RRC_CONNECTED UEは実測値ではなくpopulation-basedなHigh-load推計。22〜24 Active UEのVRAM使用率は、10 UEの実測点からの一次外挿である。
