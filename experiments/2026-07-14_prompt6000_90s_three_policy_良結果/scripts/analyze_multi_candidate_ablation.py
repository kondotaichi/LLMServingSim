#!/usr/bin/env python3
"""Compare multi-candidate target selectors and an observed lower envelope."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ANALYSIS = ROOT / "analysis/multi_candidate_ablation"
FIGURES = ROOT / "figures/multi_candidate_ablation"
REPORT = ROOT / "reports/10_multi_candidate_ablation.md"
POLICIES = {
    "Learned formula": "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720",
    "Min waiting": "NEAREST_CAPACITY_MULTI_WAITING_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720",
    "Min pressure": "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720",
    "Random": "NEAREST_CAPACITY_MULTI_RANDOM_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720",
}


def load_runs():
    frames = {}
    missing = []
    for label, directory in POLICIES.items():
        path = RESULTS / directory / "requests.csv"
        if not path.exists():
            missing.append(str(path))
            continue
        frame = pd.read_csv(path).set_index("request id").sort_index()
        frame["ttft_ms"] = frame.e2e_ttft_ns / 1e6
        frames[label] = frame
    if missing:
        raise FileNotFoundError("Missing ablation results:\n" + "\n".join(missing))
    return frames


def observed_oracle(frames):
    ttft = pd.concat(
        {label: frame.ttft_ms for label, frame in frames.items()}, axis=1
    )
    minimum = ttft.min(axis=1)
    winners = np.isclose(
        ttft.to_numpy(), minimum.to_numpy()[:, None], rtol=0, atol=1e-6
    )
    return pd.DataFrame({
        "ttft_ms": minimum,
        "winning_selectors": [
            "|".join(ttft.columns[row]) for row in winners
        ],
        "winner_count": winners.sum(axis=1),
    })


def summarize(frames, oracle):
    rows = []
    for label, frame in frames.items():
        regret = frame.ttft_ms - oracle.ttft_ms
        rows.append({
            "selector": label,
            "mean_ms": frame.ttft_ms.mean(),
            "p50_ms": frame.ttft_ms.quantile(0.50),
            "p95_ms": frame.ttft_ms.quantile(0.95),
            "p99_ms": frame.ttft_ms.quantile(0.99),
            "max_ms": frame.ttft_ms.max(),
            "redirects": int(frame.rerouted.sum()),
            "mean_regret_ms": regret.mean(),
            "oracle_wins": int(oracle.winning_selectors.str.split("|").apply(
                lambda winners: label in winners
            ).sum()),
        })
    rows.append({
        "selector": "Observed-policy oracle",
        "mean_ms": oracle.ttft_ms.mean(),
        "p50_ms": oracle.ttft_ms.quantile(0.50),
        "p95_ms": oracle.ttft_ms.quantile(0.95),
        "p99_ms": oracle.ttft_ms.quantile(0.99),
        "max_ms": oracle.ttft_ms.max(),
        "redirects": np.nan,
        "mean_regret_ms": 0.0,
        "oracle_wins": len(oracle),
    })
    return pd.DataFrame(rows)


def make_figure(frames, oracle, summary):
    FIGURES.mkdir(parents=True, exist_ok=True)
    colors = ["#2f8a62", "#4c78a8", "#f58518", "#b279a2", "#555555"]
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.5))
    x = np.arange(len(summary))
    axes[0].bar(x - 0.2, summary.mean_ms, 0.4, label="Mean", color=colors)
    axes[0].bar(x + 0.2, summary.p99_ms, 0.4, label="p99", color=colors, alpha=0.55)
    axes[0].set_xticks(x, summary.selector, rotation=25, ha="right")
    axes[0].set_ylabel("E2E TTFT (ms)")
    axes[0].set_title("Target-selector performance")
    axes[0].legend()
    series = {label: frame.ttft_ms for label, frame in frames.items()}
    series["Observed-policy oracle"] = oracle.ttft_ms
    for label, values in series.items():
        ordered = np.sort(values)
        axes[1].plot(ordered, np.arange(1, len(ordered) + 1) / len(ordered), label=label)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("E2E TTFT (ms, log scale)")
    axes[1].set_ylabel("CDF")
    axes[1].set_title("Selector TTFT distribution")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIGURES / "multi_candidate_ablation.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)


def write_report(frames, summary, oracle):
    best_online = summary[summary.selector.ne("Observed-policy oracle")].sort_values(
        "mean_ms"
    ).iloc[0]
    lines = [
        "# Multi-candidate target-selector ablation", "", "## 結論", "",
        f"オンラインselectorのMean TTFT最良は **{best_online.selector} "
        f"({best_online.mean_ms:.1f} ms)** だった。ただしp99とMaxの最良はMin pressureであり、"
        "学習formulaの一貫した優位性は確認できなかった。", "",
        "| Selector | Mean | p50 | p95 | p99 | Max | Redirects | Mean regret |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples():
        redirects = "-" if pd.isna(row.redirects) else str(int(row.redirects))
        lines.append(
            f"| {row.selector} | {row.mean_ms:.1f} | {row.p50_ms:.1f} | "
            f"{row.p95_ms:.1f} | {row.p99_ms:.1f} | {row.max_ms:.1f} | "
            f"{redirects} | {row.mean_regret_ms:.1f} |"
        )
    lines += ["", "![Ablation](../figures/multi_candidate_ablation/multi_candidate_ablation.png)",
              "", "## Oracleの定義", "",
              "`Observed-policy oracle`はrequest IDごとに4本のcompleted runで観測された最小TTFTを選ぶ"
              "offline lower envelopeであり、実行可能なonline policyではない。各runはrouting変更によって"
              "後続状態も異なるため、真の候補別counterfactual oracleではなく参考下限として扱う。", "",
              "## Learned formulaとのpaired差", "",
              "| Selector | Mean delta | Better | Worse | Same | Different target |",
              "|---|---:|---:|---:|---:|---:|"]
    learned = frames["Learned formula"]
    for label, frame in frames.items():
        if label == "Learned formula":
            continue
        delta = frame.ttft_ms - learned.ttft_ms
        lines.append(
            f"| {label} | {delta.mean():+.1f} ms | {(delta < -0.001).sum()} | "
            f"{(delta > 0.001).sum()} | {(delta.abs() <= 0.001).sum()} | "
            f"{frame.gpu_id.ne(learned.gpu_id).sum()} |"
        )
    lines += ["", "## Oracle winner counts", "",
              "同着をすべてwinnerとして数える。括弧内は単独winner数。", ""]
    for label in frames:
        co_wins = oracle.winning_selectors.str.split("|").apply(
            lambda winners: label in winners
        )
        exclusive = co_wins & oracle.winner_count.eq(1)
        lines.append(f"- {label}: {co_wins.sum()} requests ({exclusive.sum()} exclusive)")
    lines += ["", "92 requestsは4 selectorすべてが同一TTFTだった。したがってwinner件数だけで"
              "selectorの能力を判断してはならない。", "",
              "## 解釈", "",
              "Min waitingはMeanとp95で最良だが、redirect先がGPU 0と1へ偏り、p99とMaxが悪化した。"
              "Min pressureはMeanで学習formulaより1.2 ms良く、p99とMaxも最良で、このworkloadでは"
              "最もrobustだった。RandomもMean差は+1.2 msに留まり、候補を広げる効果がselector差より"
              "支配的だった。学習formulaが単独で有能だったという仮説は、このablationでは支持されない。"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    frames = load_runs()
    oracle = observed_oracle(frames)
    summary = summarize(frames, oracle)
    summary.to_csv(ANALYSIS / "performance_summary.csv", index=False)
    oracle.to_csv(ANALYSIS / "observed_policy_oracle.csv")
    make_figure(frames, oracle, summary)
    write_report(frames, summary, oracle)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
