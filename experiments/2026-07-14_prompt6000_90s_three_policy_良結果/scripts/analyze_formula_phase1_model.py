#!/usr/bin/env python3
"""Compare the phase1-trained TTFT formula against prior 90s policies."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ANALYSIS = ROOT / "analysis/formula_phase1_model"
FIGURES = ROOT / "figures/formula_phase1_model"
REPORT = ROOT / "reports/06_formula_phase1_model_analysis.md"
WORKLOAD_LABEL = "Prompt 6000 / 90s"

NEW = "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"
OLD = "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE_M200MS_D1S_MODEL20260716"
POLICIES = {
    "Wait local": "NEAREST_KV",
    "Cold migrate": "NEAREST_MIGRATE",
    "KV handoff": "NEAREST_MIGRATE_KV",
    "Old formula": OLD,
    "Phase1 formula": NEW,
}


def load(name):
    frame = pd.read_csv(RESULTS / name / "requests.csv")
    frame["request_id"] = frame["request id"].astype(int)
    frame["e2e_ttft_ms"] = frame.e2e_ttft_ns / 1e6
    return frame


def performance_summary(frames):
    rows = []
    for label, frame in frames.items():
        rows.append({
            "policy": label,
            "mean_ms": frame.e2e_ttft_ms.mean(),
            "p50_ms": frame.e2e_ttft_ms.quantile(0.50),
            "p95_ms": frame.e2e_ttft_ms.quantile(0.95),
            "p99_ms": frame.e2e_ttft_ms.quantile(0.99),
            "rerouted_n": int(frame.rerouted.sum()),
        })
    return pd.DataFrame(rows)


def paired_analysis(frames):
    new = frames["Phase1 formula"].copy()
    old = frames["Old formula"].copy()
    local = frames["Wait local"].set_index("request_id")
    handoff = frames["KV handoff"].set_index("request_id")

    decision = new.oneshot_decision_reason.ne("home_admissible")
    paired = new.loc[decision].copy()
    paired["actual_local_ttft_ms"] = paired.request_id.map(local.e2e_ttft_ms)
    paired["actual_handoff_ttft_ms"] = paired.request_id.map(handoff.e2e_ttft_ms)
    paired["predicted_local_point_ms"] = (
        paired.oneshot_formula_local_route_ms
        + paired.oneshot_formula_local_scheduler_ms
        + paired.oneshot_formula_local_compute_ms
        + paired.downlink_latency_ns / 1e6
    )
    paired["predicted_local_decision_ms"] = paired.oneshot_predicted_local_ttft_ns / 1e6
    paired["predicted_redirect_ms"] = paired.oneshot_predicted_redirect_ttft_ns / 1e6
    paired["selected_actual_ms"] = np.where(
        paired.oneshot_selected_route.eq("local"),
        paired.actual_local_ttft_ms,
        paired.actual_handoff_ttft_ms,
    )
    paired["oracle_actual_ms"] = paired[[
        "actual_local_ttft_ms", "actual_handoff_ttft_ms"
    ]].min(axis=1)
    paired["oracle_route"] = np.where(
        paired.actual_local_ttft_ms <= paired.actual_handoff_ttft_ms,
        "local", "redirect",
    )
    paired["selected_route_binary"] = np.where(
        paired.oneshot_selected_route.eq("local"), "local", "redirect"
    )
    paired["decision_correct"] = paired.selected_route_binary.eq(paired.oracle_route)
    paired["paired_regret_ms"] = paired.selected_actual_ms - paired.oracle_actual_ms

    rows = []
    for label, frame in (("Old formula", old), ("Phase1 formula", new)):
        decided = frame[frame.oneshot_decision_reason.ne("home_admissible")].copy()
        decided["actual_local_ttft_ms"] = decided.request_id.map(local.e2e_ttft_ms)
        decided["actual_handoff_ttft_ms"] = decided.request_id.map(handoff.e2e_ttft_ms)
        selected_redirect = decided.oneshot_selected_route.ne("local")
        oracle_redirect = decided.actual_handoff_ttft_ms < decided.actual_local_ttft_ms
        selected_actual = np.where(
            selected_redirect, decided.actual_handoff_ttft_ms, decided.actual_local_ttft_ms
        )
        oracle_actual = np.minimum(
            decided.actual_local_ttft_ms, decided.actual_handoff_ttft_ms
        )
        actionable = decided.oneshot_target_admissible.eq(1)
        rows.append({
            "model": label,
            "capacity_decisions_n": len(decided),
            "redirect_n": int(selected_redirect.sum()),
            "local_n": int((~selected_redirect).sum()),
            "local_within_margin_n": int(
                (decided.oneshot_decision_reason == "local_within_margin_and_deadline").sum()
            ),
            "paired_oracle_agreement": float(np.mean(selected_redirect == oracle_redirect)),
            "paired_mean_regret_ms": float(np.mean(selected_actual - oracle_actual)),
            "actionable_decisions_n": int(actionable.sum()),
            "actionable_oracle_agreement": float(np.mean(
                selected_redirect[actionable] == oracle_redirect[actionable]
            )),
            "actionable_mean_regret_ms": float(np.mean(
                selected_actual[actionable] - oracle_actual[actionable]
            )),
        })
    return paired, pd.DataFrame(rows)


def prediction_metrics(paired):
    redirected = paired.oneshot_selected_route.ne("local")
    local_error = paired.predicted_local_point_ms - paired.actual_local_ttft_ms
    redirect_error = (
        paired.loc[redirected, "predicted_redirect_ms"]
        - paired.loc[redirected, "e2e_ttft_ms"]
    )
    return {
        "capacity_decisions_n": len(paired),
        "local_point_mae_ms_vs_nearest_kv": float(local_error.abs().mean()),
        "local_point_bias_ms_vs_nearest_kv": float(local_error.mean()),
        "local_decision_upper_coverage_vs_nearest_kv": float(np.mean(
            paired.actual_local_ttft_ms <= paired.predicted_local_decision_ms
        )),
        "redirected_n": int(redirected.sum()),
        "redirect_prediction_mae_ms_vs_realized": float(redirect_error.abs().mean()),
        "redirect_prediction_bias_ms_vs_realized": float(redirect_error.mean()),
    }


def make_figures(frames, summary, paired, decisions):
    colors = ["#8c8c8c", "#c98435", "#367aa5", "#b65555", "#2f8a62"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    x = np.arange(len(summary))
    axes[0].bar(x - 0.18, summary.mean_ms, width=0.36, label="Mean", color=colors)
    axes[0].bar(x + 0.18, summary.p95_ms, width=0.36, label="p95",
                color=colors, alpha=0.55)
    axes[0].set_xticks(x, summary.policy, rotation=24, ha="right")
    axes[0].set_ylabel("E2E TTFT (ms)")
    axes[0].set_title("Mean and tail TTFT")
    axes[0].legend()
    for (label, frame), color in zip(frames.items(), colors):
        values = np.sort(frame.e2e_ttft_ms)
        axes[1].plot(values, np.arange(1, len(values) + 1) / len(values),
                     label=label, color=color, linewidth=2)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("E2E TTFT (ms, log scale)")
    axes[1].set_ylabel("CDF")
    axes[1].set_title("Request-level TTFT distribution")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "formula_policy_performance.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)

    redirected = paired.oneshot_selected_route.ne("local")
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2))
    axes[0].scatter(paired.actual_local_ttft_ms, paired.predicted_local_point_ms,
                    s=30, alpha=0.75, color="#b65555")
    limit = max(paired.actual_local_ttft_ms.max(), paired.predicted_local_point_ms.max())
    axes[0].plot([0, limit], [0, limit], "--", color="#222222")
    axes[0].set_xlabel("Paired NEAREST_KV actual TTFT (ms)")
    axes[0].set_ylabel("Predicted local point TTFT (ms)")
    axes[0].set_title("Local counterfactual prediction")
    axes[1].scatter(
        paired.loc[redirected, "e2e_ttft_ms"],
        paired.loc[redirected, "predicted_redirect_ms"],
        s=30, alpha=0.75, color="#2f8a62",
    )
    limit = max(
        paired.loc[redirected, "e2e_ttft_ms"].max(),
        paired.loc[redirected, "predicted_redirect_ms"].max(),
    )
    axes[1].plot([0, limit], [0, limit], "--", color="#222222")
    axes[1].set_xlabel("Realized redirected TTFT (ms)")
    axes[1].set_ylabel("Predicted redirect TTFT (ms)")
    axes[1].set_title("Redirect prediction")
    fig.tight_layout()
    fig.savefig(FIGURES / "formula_prediction_accuracy.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.7))
    axes[0].bar(decisions.model, decisions.actionable_oracle_agreement * 100,
                color=["#b65555", "#2f8a62"])
    axes[0].set_ylim(0, 100)
    axes[0].set_ylabel("Paired oracle agreement (%)")
    axes[0].set_title("Local vs redirect choice")
    axes[1].bar(decisions.model, decisions.actionable_mean_regret_ms,
                color=["#b65555", "#2f8a62"])
    axes[1].set_ylabel("Mean paired regret (ms)")
    axes[1].set_title("Cost of routing choice")
    fig.tight_layout()
    fig.savefig(FIGURES / "formula_decision_quality.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def write_report(summary, metrics, decisions):
    by_policy = summary.set_index("policy")
    old = by_policy.loc["Old formula"]
    new = by_policy.loc["Phase1 formula"]
    handoff = by_policy.loc["KV handoff"]
    wait = by_policy.loc["Wait local"]
    old_decision = decisions.set_index("model").loc["Old formula"]
    new_decision = decisions.set_index("model").loc["Phase1 formula"]
    text = f"""# Phase1学習済みformulate modelの{WORKLOAD_LABEL}評価

## 結論

Phase1学習済みmodelのmean E2E TTFTは **{new.mean_ms:.1f} ms** で、旧formulateの
{old.mean_ms:.1f} msから **{(old.mean_ms-new.mean_ms)/old.mean_ms*100:.1f}%改善**した。
NEAREST_KV比では{(wait.mean_ms-new.mean_ms)/wait.mean_ms*100:.1f}%改善し、
NEAREST_MIGRATE_KVとの差は{new.mean_ms-handoff.mean_ms:+.1f} msだった。

## E2E性能

| Policy | Mean | p50 | p95 | p99 | Redirects |
|---|---:|---:|---:|---:|---:|
"""
    for row in summary.itertuples():
        text += (f"| {row.policy} | {row.mean_ms:.1f} ms | {row.p50_ms:.1f} ms | "
                 f"{row.p95_ms:.1f} ms | {row.p99_ms:.1f} ms | {row.rerouted_n} |\n")
    text += f"""

![Policy performance](../figures/formula_phase1_model/formula_policy_performance.png)

## 予測精度

Capacity判断が必要だった{metrics['capacity_decisions_n']} requestsを評価した。

- Local point prediction MAE: {metrics['local_point_mae_ms_vs_nearest_kv']:.1f} ms
- Local point prediction bias: {metrics['local_point_bias_ms_vs_nearest_kv']:+.1f} ms
- Local上側予測の被覆率: {metrics['local_decision_upper_coverage_vs_nearest_kv']*100:.1f}%
- Redirect prediction MAE: {metrics['redirect_prediction_mae_ms_vs_realized']:.1f} ms
- Redirect prediction bias: {metrics['redirect_prediction_bias_ms_vs_realized']:+.1f} ms

Localの実測には、同一requestをNEAREST_KVで処理した結果を使用した。Redirect側は新formulate
runで実際にredirectされたrequestの実測と比較した。

Redirect予測はMAE {metrics['redirect_prediction_mae_ms_vs_realized']:.1f} msと比較的良好だが、
local point predictionはMAE {metrics['local_point_mae_ms_vs_nearest_kv']:.1f} ms、bias
{metrics['local_point_bias_ms_vs_nearest_kv']:+.1f} msで、今回の{WORKLOAD_LABEL} long-tailをまだ大きく過小予測している。
したがって、今回の性能改善はpoint modelが十分正確になった結果ではなく、
held-out residualの上側予測で危険なlocal選択を避けた効果が大きい。

![Prediction accuracy](../figures/formula_phase1_model/formula_prediction_accuracy.png)

## Routing判断の改善

| Model | Capacity decisions | Redirect | Local | 誤local候補 | Actionable oracle一致率 | Actionable regret |
|---|---:|---:|---:|---:|---:|---:|
| Old formula | {int(old_decision.capacity_decisions_n)} | {int(old_decision.redirect_n)} | {int(old_decision.local_n)} | {int(old_decision.local_within_margin_n)} | {old_decision.actionable_oracle_agreement*100:.1f}% | {old_decision.actionable_mean_regret_ms:.1f} ms |
| Phase1 formula | {int(new_decision.capacity_decisions_n)} | {int(new_decision.redirect_n)} | {int(new_decision.local_n)} | {int(new_decision.local_within_margin_n)} | {new_decision.actionable_oracle_agreement*100:.1f}% | {new_decision.actionable_mean_regret_ms:.1f} ms |

`誤local候補`は`local_within_margin_and_deadline`でlocalに残した件数である。新modelでは上側予測を
deadline判断に使うため、この経路は解消した。`target_not_admissible`の場合は予測にかかわらず
local待ちとなる。
Actionable指標はredirect先が収容可能で、modelが実際にlocal/redirectを選べたrequestだけを評価する。
新modelは誤local候補を{int(old_decision.local_within_margin_n)}件から{int(new_decision.local_within_margin_n)}件へ減らした一方、actionable paired oracle一致率は
{old_decision.actionable_oracle_agreement*100:.1f}%から{new_decision.actionable_oracle_agreement*100:.1f}%へ下がった。
上側予測はfalse-localを避ける保守的な判断であり、requestごとの最適選択精度を上げる
model改良は依然必要である。

![Decision quality](../figures/formula_phase1_model/formula_decision_quality.png)

## Counterfactualの制約

Paired oracleは同一request IDのNEAREST_KVとNEAREST_MIGRATE_KVを比較している。ただしpolicyが
変わると先行requestの配置とGPU負荷も変わるため、厳密に同一system stateでの反実仮想ではない。
実runのE2E比較を主結果、paired oracleをrouting判断の診断値として扱う。
"""
    REPORT.write_text(text, encoding="utf-8")


def main():
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    frames = {label: load(name) for label, name in POLICIES.items()}
    summary = performance_summary(frames)
    paired, decisions = paired_analysis(frames)
    metrics = prediction_metrics(paired)
    summary.to_csv(ANALYSIS / "performance_summary.csv", index=False)
    paired.to_csv(ANALYSIS / "paired_prediction_diagnostics.csv", index=False)
    decisions.to_csv(ANALYSIS / "decision_quality.csv", index=False)
    (ANALYSIS / "prediction_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    make_figures(frames, summary, paired, decisions)
    write_report(summary, metrics, decisions)
    print(summary.to_string(index=False))
    print(json.dumps(metrics, indent=2))
    print(decisions.to_string(index=False))


if __name__ == "__main__":
    main()
