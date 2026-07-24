# LightGBM TTFT component model

既存のTTFT formulaと同じ入力・component分解・leave-one-scenario-out分割を使い、
Router queue発生、正値Router queue時間、Scheduler queue時間、Compute/prefill時間を
LightGBMで推定する比較実験である。

## 構成

```text
lightgbm/
├── scripts/
│   └── fit_ttft_lightgbm.py       学習、OOF評価、重要度出力
├── analysis/
│   ├── summary.json               LightGBMの評価指標
│   ├── model_comparison.csv       既存formulaとの比較
│   ├── feature_importance.csv     component別gain/split重要度
│   └── oof_predictions.csv        全scenario-held-out予測
├── figures/
│   ├── feature_importance_gain.png
│   └── ttft_lightgbm_oof.png
├── models/
│   └── ttft_lightgbm.joblib       全データで学習したmodel bundle
└── reports/
    └── ttft_lightgbm.md           結果と解釈
```

入力datasetと既存formulaの比較値は親実験の以下を使用する。

- `../analysis/post_routing/canonical_requests.csv`
- `../analysis/ttft_formula/summary.json`

## 実行

macOSではLightGBMとOpenMP runtimeを用意する。

```bash
brew install libomp
experiments/2026-07-16_ttft_component_regression/.venv/bin/pip install lightgbm
```

Repository rootから実行する。

```bash
MPLCONFIGDIR=/tmp/llmservingsim-mpl \
  PYTHONPYCACHEPREFIX=/tmp/llmservingsim-pycache \
  experiments/2026-07-16_ttft_component_regression/.venv/bin/python \
  experiments/2026-07-16_ttft_component_regression/lightgbm/scripts/fit_ttft_lightgbm.py
```

出力はすべてこの`lightgbm/`以下で更新され、親実験の既存formula成果物は上書きしない。

## 現在の結果

13,500 requests、15 scenariosのleave-one-scenario-out評価では、TTFT MAEは既存formulaの
716.08 msから706.81 msへ改善した。詳細と特徴量重要度の解釈は
`reports/ttft_lightgbm.md`を参照する。
