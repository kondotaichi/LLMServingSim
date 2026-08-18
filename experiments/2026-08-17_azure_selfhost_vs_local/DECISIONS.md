# Decisions

実験条件の変更を結果取得前に記録する。各項目に日付、決定者、選択肢、選択理由、性能・コスト・
比較可能性への影響を記載する。

## Initial decisions

| 項目 | 決定 | 状態 |
|---|---|---|
| Cloud方式 | Azure VM上への同一モデルのセルフホスト | 確定 |
| Managed API | 今回は対象外 | 確定 |
| Model | Meta Llama 3.1 8B | 暫定、revision未固定 |
| Serving engine | vLLM v0.19.0 | 暫定、image digest未固定 |
| Primary Azure VM | `Standard_ND96asr_v4`（A100 40 GB×8） | Phase 0でregion/quota確認 |
| Fallback Azure VM | `Standard_ND96amsr_A100_v4`（A100 80 GB×8） | Phase 0でregion/quota確認 |
| Azure GPU数 | 単一VMのA100×8、vLLM TP=8 | 確定 |
| Cloud/local GPU数 | Azure 8枚対local 12枚で不一致 | GPU当たり指標を併記 |
| Primary workload | Hongo 2000件、Peak 2x/5x/10x | 確定 |
| Proactive KV prewarm | 比較対象外 | 確定 |

## Decision log

### YYYY-MM-DD: Title

- Context:
- Options:
- Decision:
- Reason:
- Cost impact:
- Comparability impact:
- Approved by:
