# Decisions

実験条件の変更を結果取得前に記録する。各項目に日付、決定者、選択肢、選択理由、性能・コスト・
比較可能性への影響を記載する。

## Initial decisions

| 項目 | 決定 | 状態 |
|---|---|---|
| Cloud方式 | AWS EC2上への同一モデルのセルフホスト | 確定 |
| Managed API | 今回は対象外 | 確定 |
| Model | Meta Llama 3.1 8B | 暫定、revision未固定 |
| Serving engine | vLLM v0.19.0 | 暫定、image digest未固定 |
| Primary AWS GPU | G6/L4 1-GPU instance候補 | Phase 0確認待ち |
| Fallback AWS GPU | G6e/L40S | Phase 0確認待ち |
| AWS GPU数 | 12 | Quota/capacity確認待ち |
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
