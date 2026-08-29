# メソッド再検証・改善実験

検証全体の結果と最終方針は[`report/final_method_review.md`](report/final_method_review.md)
にまとめている。

この実験では、従来選択した設定が最適であると仮定せず、routing手法を二つの独立した
トラックに分けて再検証する。

## Track 1: PPとKV migrateの交互作用

確認事項は、PP2にredirectが発生するだけの負荷をかけた場合、KV-aware redirectが
cold redirectよりTTFTを改善するか、さらにその改善幅がPP1より大きいかである。

各負荷点で次の2×2比較を行う。

| Arm | PP | Redirect時の処理 |
|---|---:|---|
| `pp1_cold` | 1 | リクエストだけを移送し、prefillを再計算 |
| `pp1_kv` | 1 | リクエストと再利用可能KVを移送 |
| `pp2_cold` | 2 | リクエストだけを移送し、prefillを再計算 |
| `pp2_kv` | 2 | リクエストと再利用可能KVを移送 |

主要な評価量は次の交互作用である。

```text
(TTFT_pp2_cold - TTFT_pp2_kv) - (TTFT_pp1_cold - TTFT_pp1_kv)
```

正の値なら、PP2におけるKV migrateの効果がPP1より大きい。平均、p50、p95、p99
TTFTについて算出し、redirect数・率とKV移送時間も併記する。PP2でredirectが0件の
比較は、この確認事項に対する検証として扱わない。

最初に負荷パイロットを行う。KV版の結果を見る前にcold版だけで発火点を探し、
redirect率が5%以上となる最小の条件を採用する。全GPUが同時に飽和して移送先が
なくなる条件と、局所的なhotspotに対して他instanceに余剰capacityがある条件を
区別する。

## Track 2: Proactive KV prewarm

従来の本郷プローブは頻出ユーザー予測の評価には不適切だった。300リクエスト中
288件が異なるセッションで、再訪は12件しかなかった。一方、元の2000件には
1129セッションと871件の再訪がある。先頭300行への切り捨てによって、Method Cが
必要とする再訪信号がほぼ失われていた。

次の二つを分離して確認する。

1. **ワークロードの妥当性**: 再訪予測を評価できるだけの、因果的な時系列を持つ
   再訪が含まれているか
2. **ポリシーの品質**: 妥当なワークロード上で、無駄な先回り配置を抑えながら
   有用なKVを配置できるか

Method Cの結果を見てから都合のよいワークロードを選ばない。実行前に次を公開する。

- リクエスト数と観測時間
- ユニークユーザー／セッション数と再訪率
- ユーザーごとのリクエスト数分布
- 再訪間隔のp50/p95
- 再利用可能prefixを持つリクエストの割合
- 観測区間と、分離した評価区間それぞれの上記統計

主要な終了条件には時間窓を用いる。計算量制限として固定件数を使う場合は、完全な
セッションを維持するか、セッション長で層化してサンプリングする。先頭`N`行だけを
採用してはならない。

### 予測器のbaseline

現行のrecent-count方式を次と比較する。

- `none`: proactive prewarmなし
- `recent_count`: 現行実装
- `recency_decay`: 指数減衰を適用したリクエスト回数
- `utility`: 減衰付き再訪score、再利用KV量、回避できる移送時間を組み合わせ、
  明示的な有効期限を持つ方式

減衰半減期と有効期限は観測区間だけで調整し、分離した評価区間で一度だけ評価する。
主要指標は配置あたりのhit率、有用移送byte数／全投機移送byte数、redirectされた
リクエストのTTFT、全体p95 TTFT、cache eviction overheadとする。大半のリクエストが
Method Cと無関係になる可能性があるため、全体平均TTFTだけでは判断しない。

## 常に明記する制約

- 複数の到着seed・ユーザーseedを用いる。1 seedは動作確認であり、証拠ではない。
- 本郷実験ではnetwork contentionが無効であり、migrationに有利な可能性がある。
- PP1とPP2ではlogical instance数とbatching挙動も同時に変化する。
- proactive background transferは現在freeとしてモデル化されている。推定byte数と
  移送時間は必ず報告する。
- 負荷点や予測器パラメータの調整と最終評価に同じデータを使わない。

## スクリプト

- `scripts/audit_workload.py`: 再訪予測を評価可能なワークロードか監査する
- `scripts/build_hongo_workloads.py`: 従来の本郷生成ロジックを再利用し、2000件版を
  この実験内へ生成する
- `scripts/make_load_sweep.py`: コンテンツを変えず到着時間軸だけを圧縮する
- `scripts/make_spatial_skew.py`: 決定論的な局所hotspotを生成する
- `scripts/run_track1.sh`: 条件を揃えたPP×KV実験を実行する
- `scripts/analyze_interaction.py`: 2×2比較と交互作用を集計する
