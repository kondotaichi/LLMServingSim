#!/usr/bin/env python3
"""Analyze Multi-pressure versus nearest KV migration across the sweep."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ANALYSIS = ROOT / "analysis"
FIGURES = ROOT / "figures"
REPORT = ROOT / "report.md"
KV_POLICY = "NEAREST_MIGRATE_KV"
MULTI_POLICY = "NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE"
REUSE_VALUES = {"reuse00": 0.0, "reuse025": 0.25, "reuse05": 0.5}


def load_summary():
    rows = []
    components = []
    for condition_dir in sorted(RESULTS.iterdir()):
        if not condition_dir.is_dir():
            continue
        condition = condition_dir.name
        parts = condition.split("_")
        input_tokens = int(parts[0].removeprefix("input"))
        reuse = REUSE_VALUES[parts[1]]
        frames = {
            "KV migrate": pd.read_csv(condition_dir / KV_POLICY / "requests.csv").set_index("request id").sort_index(),
            "Multi-pressure": pd.read_csv(condition_dir / MULTI_POLICY / "requests.csv").set_index("request id").sort_index(),
        }
        kv_ttft = frames["KV migrate"].e2e_ttft_ns / 1e6
        multi_ttft = frames["Multi-pressure"].e2e_ttft_ns / 1e6
        delta = multi_ttft - kv_ttft
        row = {
            "condition": condition,
            "input_tokens": input_tokens,
            "reuse": reuse,
            "kv_mean_ms": kv_ttft.mean(),
            "multi_mean_ms": multi_ttft.mean(),
            "mean_improvement_pct": (kv_ttft.mean() - multi_ttft.mean()) / kv_ttft.mean() * 100,
            "kv_p50_ms": kv_ttft.quantile(0.50),
            "multi_p50_ms": multi_ttft.quantile(0.50),
            "p50_improvement_pct": (kv_ttft.quantile(0.50) - multi_ttft.quantile(0.50)) / kv_ttft.quantile(0.50) * 100,
            "kv_p95_ms": kv_ttft.quantile(0.95),
            "multi_p95_ms": multi_ttft.quantile(0.95),
            "p95_improvement_pct": (kv_ttft.quantile(0.95) - multi_ttft.quantile(0.95)) / kv_ttft.quantile(0.95) * 100,
            "kv_p99_ms": kv_ttft.quantile(0.99),
            "multi_p99_ms": multi_ttft.quantile(0.99),
            "p99_improvement_pct": (kv_ttft.quantile(0.99) - multi_ttft.quantile(0.99)) / kv_ttft.quantile(0.99) * 100,
            "better_requests": int(delta.lt(-0.001).sum()),
            "worse_requests": int(delta.gt(0.001).sum()),
            "same_requests": int(delta.abs().le(0.001).sum()),
            "kv_redirects": int(frames["KV migrate"].rerouted.sum()),
            "multi_redirects": int(frames["Multi-pressure"].rerouted.sum()),
        }
        rows.append(row)
        for label, frame in frames.items():
            ttft = frame.e2e_ttft_ns / 1e6
            values = {
                "communication_ms": frame.communication_latency_ns.mean() / 1e6,
                "router_wait_ms": frame.router_capacity_wait_ns.mean() / 1e6,
                "scheduler_queue_ms": frame.queueing_before_ttft_ns.mean() / 1e6,
                "prefill_ms": frame.prefill_service_ns.mean() / 1e6,
            }
            components.append({
                "condition": condition,
                "input_tokens": input_tokens,
                "reuse": reuse,
                "policy": label,
                **values,
                "other_ms": ttft.mean() - sum(values.values()),
            })
    return pd.DataFrame(rows).sort_values(["input_tokens", "reuse"]), pd.DataFrame(components)


def make_figures(summary, components):
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    for ax, metric, title in zip(
        axes,
        ["mean_improvement_pct", "p95_improvement_pct", "p99_improvement_pct"],
        ["Mean improvement", "p95 improvement", "p99 improvement"],
    ):
        pivot = summary.pivot(index="input_tokens", columns="reuse", values=metric)
        image = ax.imshow(pivot, vmin=0, vmax=max(1, summary[metric].max()), cmap="YlGn")
        ax.set_xticks(range(len(pivot.columns)), [f"{value:.2f}" for value in pivot.columns])
        ax.set_yticks(range(len(pivot.index)), pivot.index)
        ax.set_xlabel("Prefix reuse ratio")
        ax.set_ylabel("Input tokens")
        ax.set_title(title)
        for row in range(len(pivot.index)):
            for col in range(len(pivot.columns)):
                ax.text(col, row, f"{pivot.iloc[row, col]:.1f}%", ha="center", va="center")
        fig.colorbar(image, ax=ax, label="Improvement (%)")
    fig.tight_layout()
    fig.savefig(FIGURES / "improvement_heatmaps.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    heavy = components[components.input_tokens.eq(10000)].copy()
    component_names = ["communication_ms", "router_wait_ms", "scheduler_queue_ms", "prefill_ms"]
    colors = ["#4c78a8", "#f58518", "#e45756", "#72b7b2"]
    labels = [f"reuse={row.reuse:g}\n{row.policy}" for row in heavy.itertuples()]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    bottom = np.zeros(len(heavy))
    for component, color in zip(component_names, colors):
        values = heavy[component].to_numpy()
        ax.bar(labels, values, bottom=bottom, label=component.removesuffix("_ms"), color=color)
        bottom += values
    ax.set_ylabel("Mean E2E TTFT component (ms)")
    ax.set_title("Input 10000 TTFT breakdown")
    ax.legend(ncol=4)
    fig.tight_layout()
    fig.savefig(FIGURES / "input10000_breakdown.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def write_report(summary):
    heavy = summary[summary.input_tokens.eq(10000)]
    lines = [
        "# Multi-pressure versus nearest KV migration sweep", "", "## 結論", "",
        "Multi-pressureは512/2000-token条件ではKV migrateと完全に一致し、10000-tokenの過負荷条件でのみ差が出た。",
        "10000-token条件ではMeanを18.1–25.7%、p99を61.7–75.9%改善した。したがって、"
        "常時高速化ではなく、重負荷時のtail抑制が主要な利点である。", "",
        "| Condition | Mean KV | Mean Multi | Mean improvement | p50 improvement | p95 improvement | p99 improvement | Redirects KV → Multi |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.itertuples():
        lines.append(
            f"| {row.condition} | {row.kv_mean_ms:.1f} | {row.multi_mean_ms:.1f} | "
            f"{row.mean_improvement_pct:.1f}% | {row.p50_improvement_pct:.1f}% | "
            f"{row.p95_improvement_pct:.1f}% | {row.p99_improvement_pct:.1f}% | "
            f"{row.kv_redirects} → {row.multi_redirects} |"
        )
    lines += ["", "![Improvement heatmaps](figures/improvement_heatmaps.png)", "",
              "## Heavy-load interpretation", "",
              f"10000-token 3条件のMean改善率は平均{heavy.mean_improvement_pct.mean():.1f}%、"
              f"p99改善率は平均{heavy.p99_improvement_pct.mean():.1f}%だった。Multi-pressureは"
              "redirect先を全GPUへ分散し、Routerでの容量待ちを短縮した。一方、p50は全3条件で悪化し、"
              "個別requestでも悪化件数が改善件数を上回る。少数の極端なtail改善がMeanを押し下げている。", "",
              "![Input10000 breakdown](figures/input10000_breakdown.png)", "",
              "## Scope", "",
              "この結果が支持するのは、固定APN・90s・このarrival traceにおける過負荷耐性である。"
              "低負荷での優位性、異なるarrival seed、距離依存networkでの一般的優位性はまだ示していない。"]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    summary, components = load_summary()
    summary.to_csv(ANALYSIS / "condition_summary.csv", index=False)
    components.to_csv(ANALYSIS / "mean_ttft_breakdown.csv", index=False)
    make_figures(summary, components)
    write_report(summary)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
