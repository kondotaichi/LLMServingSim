# KV転送の投機的実行(scheduler隠し + 投機的先行転送)実装・検証

Redirect+KVキャッシュ移送(migrate_kv)発生時、KV転送時間をターゲットGPUの
scheduler queue待ちと並列化する(Method A: scheduler隠し)、およびredirect
決定がまだ確定していない待機中にKV転送を投機的に先行させる(Method B:
投機的先行転送)、という2つの機構を`serving/core/`(router.py / scheduler.py
/ request.py)へ実装し、`experiments/2026-07-22_pp2_five_workloads`
(PP2、10 GPU→5台のpipeline-parallel環境)を使って検証した記録。

このディレクトリは実装・検証の**説明・分析レポート、Stage 4の比較グラフ、
そしてMethod A/B(`pp2_scheduler_hide`・`pp2_spec_scheduler_hide`)の
シミュレーション出力**を置く場所。PP1/PP2ベースラインのシミュレーション
基盤(workload topology、cluster config、`pp2_input8000_reuse00`のような
オリジナルベースライン結果)は引き続き
`experiments/2026-07-22_pp2_five_workloads/`側に残し、そちらを共有基盤
として再利用している。

- `results/{pp2_scheduler_hide,pp2_spec_scheduler_hide}/` — 元は
  `experiments/2026-07-22_pp2_five_workloads/results/`配下に生成していたが、
  本ディレクトリへ移設した(対応する`logs/`・`status/`も同様)。
  `run_pp2_scheduler_hide_parallel.sh`/`run_pp2_speculative_parallel.sh`
  は`OUTPUT_DIR`環境変数(未指定時はスクリプト自身のディレクトリ、従来通り)
  で出力先を制御できるようにしてあるので、今後追加のworkload
  (`mixed_rate3p33_seed1`等)を実行する際はこちらを指定すればこの
  ディレクトリへ直接出力される。
- `figures/`・`analysis/` — `experiments/2026-07-22_pp2_five_workloads/
  scripts/analyze_pp_comparison.py`の出力(`ANALYSIS_OUTPUT_ROOT`環境変数で
  出力先をこちらに向けている)。同スクリプトはMethod A/Bアームの結果を
  まずここの`results/`から探し、無ければ`pp2_five_workloads/results/`へ
  フォールバックする(`_results_dir()`)。同スクリプトのデフォルト出力先
  である`experiments/2026-07-22_pp2_five_workloads/figures`・`analysis`は
  オリジナルのPP1/PP2 2-armベースライン結果(コミット済み)のまま、
  上書きしていない。

## 経緯

1. `experiments/2026-07-25_speculative_test`で「redirect判断は投機ゼロで
   使えるほど確度が高いか」を実測検証 → home側のTTFT予測が安全マージンに
   より常に飽和していることが判明。
2. TTFT予測モデルの回帰精度改善(`experiments/2026-07-16_ttft_component_regression`
   のroute_tail hyperparameter調整・特徴量追加)を実施。
3. ユーザーから「KVキャッシュを事前送信しておけばKV転送時間を隠せるはず」
   という提案を受け、設計を検討 → 「scheduler隠し」(Method A、確定的)と
   「投機的先行転送」(Method B、不確実性あり)の2手法に整理。
4. Method Bには「redirect決定が瞬時ではなく実際に待機する局面」が必要
   であることが判明し、home側予測の再較正(B-1)を先に実装。
5. プランモードで設計を確定し(`.claude/plans/elegant-dazzling-lobster.md`)、
   実装・段階的検証(Stage 0〜4)を実施。**本レポートはこの記録。**

6. Method A・旧Method B(request自身の判断待ちの間の投機)ともに実測では
   ほぼ効果が無いことが判明。この過程でユーザーが本来意図していた
   「投機的実行」が旧Method Bとは別物(request非依存、GPU容量逼迫を
   トリガーにした先回りキャッシュ移送)だと判明し、Method Cとして
   新規に設計した。

## 内容

- [`reports/implementation_and_verification.md`](reports/implementation_and_verification.md) —
  Method A・旧Method Bの設計・実装・検証結果の詳細レポート(本編)。
- [`reports/branch_predictor_analogy_analysis.md`](reports/branch_predictor_analogy_analysis.md) —
  「CPU分岐予測のように、確信度に関わらず常に投機する」方式と現行の
  confidence-gated pin方式を比較した分析。どちらがStage 3の結果に対して
  効果を持ちうるかを検討。
- [`reports/proactive_kv_prewarm_design.md`](reports/proactive_kv_prewarm_design.md) —
  **Method C**(容量逼迫トリガー型・投機的KVキャッシュ先回り移送)の設計
  レポート。Method A/Bとは独立に効く、request非依存のバックグラウンド
  機構。
- [`method_c_proactive_kv_prewarm/reports/verification.md`](method_c_proactive_kv_prewarm/reports/verification.md) —
  Method Cの段階的検証記録(Stage 0: 単体テスト49件 → Stage 1: 小規模実機で
  redirectの25%がhit・hit群TTFT-26%を確認)。結果が混ざらないよう、Method C
  専用のシミュレーション出力(`method_c_proactive_kv_prewarm/results/`)も
  Method A/B(`results/pp2_scheduler_hide`等)とは別ディレクトリに分離している。

## 関連ファイル

- 設計計画: `.claude/plans/elegant-dazzling-lobster.md`
- 実装: `serving/core/router.py`, `serving/core/scheduler.py`, `serving/core/request.py`, `serving/__main__.py`
- 単体テスト: `tests/test_second_ttft_reserve_router.py`, `tests/test_scheduler_hide_kv_migration.py`
  (Method A/B), `tests/test_proactive_kv_prewarm_router.py`(Method C)
- 実行スクリプト: `experiments/2026-07-22_pp2_five_workloads/run_pp2_scheduler_hide_parallel.sh`,
  `experiments/2026-07-22_pp2_five_workloads/run_pp2_speculative_parallel.sh`
- 比較分析スクリプト: `experiments/2026-07-22_pp2_five_workloads/scripts/analyze_pp_comparison.py`
- 段階的検証ログ(Stage 0〜3): `experiments/2026-07-22_pp2_five_workloads/reports/speculative_kv_transfer_verification.md`
