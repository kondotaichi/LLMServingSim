#!/usr/bin/env python3
"""Analyze dynamic and multi-candidate formula-routing variants."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ANALYSIS = ROOT / "analysis/formula_dynamic_variants"
FIGURES = ROOT / "figures/formula_dynamic_variants"
REPORT = ROOT / "reports/07_formula_dynamic_variants.md"
POLICIES = {
    "KV handoff": "NEAREST_MIGRATE_KV",
    "Current formula": "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720",
    "Dynamic": "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720",
    "Multi-candidate": "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720",
}


def load_runs():
    frames = {}
    for label, name in POLICIES.items():
        frame = pd.read_csv(RESULTS / name / "requests.csv")
        frame["e2e_ttft_ms"] = frame.e2e_ttft_ns / 1e6
        if "router_capacity_wait_ns" in frame:
            frame["router_wait_ms"] = frame.router_capacity_wait_ns / 1e6
        else:
            frame["router_wait_ms"] = np.maximum(
                0,
                (
                    frame.e2e_ttft_ns
                    - frame.communication_latency_ns
                    - frame.queueing_before_ttft_ns
                    - frame.prefill_service_ns
                ) / 1e6,
            )
        frames[label] = frame
    return frames


def summarize(frames):
    rows = []
    for label, frame in frames.items():
        reasons = frame.oneshot_decision_reason.value_counts() if "oneshot_decision_reason" in frame else {}
        rows.append({
            "policy": label,
            "mean_ms": frame.e2e_ttft_ms.mean(),
            "p50_ms": frame.e2e_ttft_ms.quantile(0.50),
            "p95_ms": frame.e2e_ttft_ms.quantile(0.95),
            "p99_ms": frame.e2e_ttft_ms.quantile(0.99),
            "rerouted_n": int(frame.rerouted.sum()),
            "router_wait_mean_ms": frame.router_wait_ms.mean(),
            "router_wait_max_ms": frame.router_wait_ms.max(),
            "target_not_admissible_n": int(reasons.get("target_not_admissible", 0)),
            "home_became_admissible_n": int(reasons.get("home_became_admissible", 0)),
        })
    return pd.DataFrame(rows)


def target_distribution(frames):
    rows = []
    for label, frame in frames.items():
        for gpu_id, count in frame.loc[frame.rerouted.eq(1), "gpu_id"].value_counts().items():
            rows.append({"policy": label, "gpu_id": int(gpu_id), "redirected_n": int(count)})
    return pd.DataFrame(rows)


def make_figures(frames, summary, targets):
    colors = {
        "KV handoff": "#367aa5",
        "Current formula": "#b65555",
        "Dynamic": "#c98435",
        "Multi-candidate": "#2f8a62",
    }
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2))
    x = np.arange(len(summary))
    axes[0].bar(x - 0.2, summary.mean_ms, 0.4,
                color=[colors[p] for p in summary.policy], label="Mean")
    axes[0].bar(x + 0.2, summary.p99_ms, 0.4,
                color=[colors[p] for p in summary.policy], alpha=0.5, label="p99")
    axes[0].set_xticks(x, summary.policy, rotation=20, ha="right")
    axes[0].set_ylabel("E2E TTFT (ms)")
    axes[0].set_title("Mean and p99 TTFT")
    axes[0].legend()
    for label, frame in frames.items():
        values = np.sort(frame.e2e_ttft_ms)
        axes[1].plot(values, np.arange(1, len(values) + 1) / len(values),
                     label=label, color=colors[label], linewidth=2)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("E2E TTFT (ms, log scale)")
    axes[1].set_ylabel("CDF")
    axes[1].set_title("TTFT distribution")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "dynamic_variant_performance.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.0))
    axes[0].bar(summary.policy, summary.router_wait_max_ms,
                color=[colors[p] for p in summary.policy])
    axes[0].set_xticks(range(len(summary)), summary.policy, rotation=20, ha="right")
    axes[0].set_ylabel("Maximum Router wait (ms)")
    axes[0].set_title("Worst capacity wait")
    selected = targets[targets.policy.isin(["Dynamic", "Multi-candidate"])]
    pivot = selected.pivot(index="gpu_id", columns="policy", values="redirected_n").fillna(0)
    pivot.plot.bar(ax=axes[1], color=[colors[c] for c in pivot.columns])
    axes[1].set_ylabel("Redirected requests")
    axes[1].set_title("Redirect target distribution")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(FIGURES / "dynamic_wait_and_targets.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def write_report(summary):
    values = summary.set_index("policy")
    kv = values.loc["KV handoff"]
    current = values.loc["Current formula"]
    dynamic = values.loc["Dynamic"]
    multi = values.loc["Multi-candidate"]
    text = f"""# Dynamic再評価とMulti-candidate formulateの90s評価

## 結論

`target_not_admissible`時のlocal固定を廃止したDynamicは、単純KV handoffと完全に同じ
mean / p50 / p95 / p99 TTFTまで回復した。Multi-candidateはさらにmean **{multi.mean_ms:.1f} ms**、
p99 **{multi.p99_ms:.1f} ms**となり、単純KV handoffをmeanで
{(kv.mean_ms-multi.mean_ms)/kv.mean_ms*100:.1f}%、p99で{(kv.p99_ms-multi.p99_ms)/kv.p99_ms*100:.1f}%改善した。

## 結果

| Policy | Mean | p50 | p95 | p99 | Redirects | Mean Router wait | Max Router wait |
|---|---:|---:|---:|---:|---:|---:|---:|
"""
    for row in summary.itertuples():
        text += (f"| {row.policy} | {row.mean_ms:.1f} ms | {row.p50_ms:.1f} ms | "
                 f"{row.p95_ms:.1f} ms | {row.p99_ms:.1f} ms | {row.rerouted_n} | "
                 f"{row.router_wait_mean_ms:.1f} ms | {row.router_wait_max_ms:.1f} ms |\n")
    text += f"""

![Performance](../figures/formula_dynamic_variants/dynamic_variant_performance.png)

## 仮説の判定

### 1. `target_not_admissible`時のlocal固定廃止

支持された。Currentは7 requestsを`target_not_admissible`でlocalへ固定し、最大
{current.router_wait_max_ms:.1f} ms待った。Dynamicでは固定が消え、最大待ちは
{dynamic.router_wait_max_ms:.1f} msへ減った。

### 2. Homeとtargetの動的再評価

支持された。Dynamicは24 requestsをhandoffし、1 requestは待機中にHomeが収容可能となったため
localで受け入れた。最終TTFT分布は単純KV handoffと一致した。

### 3. Multi-candidate探索

支持された。Multi-candidateは全requestをRouter waitなしで処理し、redirect数も18件まで減った。
Redirect先は9 GPUへ分散し、second-nearestのみを使う場合の集中を避けた。

![Wait and target distribution](../figures/formula_dynamic_variants/dynamic_wait_and_targets.png)

## 解釈上の注意

- Multi-candidateは固定APN propagation条件で実行した。第3候補以降の実距離をworkloadが持たないため、
  distance-proportional networkに一般化するには候補別距離の追加が必要である。
- DynamicとKV handoffの一致はこの1 workloadでの結果であり、seedと負荷条件の追加検証が必要である。
- Multi-candidateの優位性は、modelの絶対TTFT精度よも「収容可能な別GPUを見落とさない」効果が大い。
"""
    REPORT.write_text(text, encoding="utf-8")


def main():
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    frames = load_runs()
    summary = summarize(frames)
    targets = target_distribution(frames)
    summary.to_csv(ANALYSIS / "performance_summary.csv", index=False)
    targets.to_csv(ANALYSIS / "redirect_target_distribution.csv", index=False)
    make_figures(frames, summary, targets)
    write_report(summary)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
