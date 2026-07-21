#!/usr/bin/env python3
"""Compare every completed routing-policy result in one experiment."""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


POLICIES = [
    ("Cold migrate", "NEAREST_MIGRATE"),
    ("Wait local", "NEAREST_KV"),
    ("KV handoff", "NEAREST_MIGRATE_KV"),
    ("Second-TTFT", "NEAREST_SECOND_TTFT_RESERVE_T160K_I1M"),
    ("Heuristic one-shot", "NEAREST_CAPACITY_ONESHOT_KV_RESERVE_T160K_I1M_M200MS_D1S"),
    ("Old formula, no reserve", "NEAREST_CAPACITY_ONESHOT_FORMULA_NO_RESERVATION_M200MS_D1S_MODEL20260716"),
    ("Old formula", "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE_M200MS_D1S_MODEL20260716"),
    ("Phase1 formula", "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"),
    ("Dynamic formula", "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"),
    ("Multi-candidate", "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720"),
]
BASE_COMPONENTS = ["Communication", "Router wait", "Scheduler queue", "Prefill service"]
COMPONENTS = BASE_COMPONENTS + ["Other / staging"]
COLORS = ["#4c78a8", "#f58518", "#e45756", "#72b7b2", "#b279a2"]
SHORTLIST = [
    "Cold migrate",
    "KV handoff",
    "Phase1 formula",
    "Dynamic formula",
    "Multi-candidate",
]


def load_runs(root):
    frames = {}
    for label, directory in POLICIES:
        path = root / "results" / directory / "requests.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        frame["TTFT"] = frame.e2e_ttft_ns / 1e6
        frame["Communication"] = frame.communication_latency_ns / 1e6
        frame["Scheduler queue"] = frame.queueing_before_ttft_ns / 1e6
        frame["Prefill service"] = frame.prefill_service_ns / 1e6
        if "router_capacity_wait_ns" in frame:
            frame["Router wait"] = frame.router_capacity_wait_ns.fillna(0) / 1e6
        else:
            frame["Router wait"] = 0.0
        component_sum = frame[BASE_COMPONENTS].sum(axis=1)
        frame["Other / staging"] = frame.TTFT - component_sum
        frames[label] = frame
    return frames


def summaries(frames):
    performance = []
    breakdown = []
    tail_breakdown = []
    for label, frame in frames.items():
        performance.append({
            "policy": label,
            "requests": len(frame),
            "mean_ms": frame.TTFT.mean(),
            "p50_ms": frame.TTFT.quantile(0.50),
            "p95_ms": frame.TTFT.quantile(0.95),
            "p99_ms": frame.TTFT.quantile(0.99),
            "max_ms": frame.TTFT.max(),
            "rerouted_n": int(frame.rerouted.sum()),
        })
        breakdown.append({"policy": label, **{c: frame[c].mean() for c in COMPONENTS}})
        tail = frame.nlargest(max(3, int(np.ceil(len(frame) * 0.01))), "TTFT")
        tail_breakdown.append({"policy": label, **{c: tail[c].mean() for c in COMPONENTS}})
    return (pd.DataFrame(performance), pd.DataFrame(breakdown),
            pd.DataFrame(tail_breakdown))


def stacked(ax, table, title):
    labels = table.policy.tolist()
    bottom = np.zeros(len(table))
    for component, color in zip(COMPONENTS, COLORS):
        values = table[component].to_numpy()
        ax.bar(labels, values, bottom=bottom, label=component, color=color)
        bottom += values
    ax.set_title(title)
    ax.set_ylabel("E2E TTFT component (ms)")
    ax.tick_params(axis="x", rotation=35)
    for tick in ax.get_xticklabels():
        tick.set_ha("right")


def make_figures(root, frames, performance, breakdown, tail_breakdown):
    output = root / "figures/all_policy_comparison"
    output.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 2, figsize=(15, 5.6))
    x = np.arange(len(performance))
    axes[0].bar(x - 0.2, performance.mean_ms, 0.4, label="Mean", color="#4c78a8")
    axes[0].bar(x + 0.2, performance.p99_ms, 0.4, label="p99", color="#e45756")
    axes[0].set_xticks(x, performance.policy, rotation=35, ha="right")
    axes[0].set_ylabel("E2E TTFT (ms)")
    axes[0].set_title("Mean and p99 TTFT")
    axes[0].legend()
    for label, frame in frames.items():
        values = np.sort(frame.TTFT)
        axes[1].plot(values, np.arange(1, len(values) + 1) / len(values), label=label)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("E2E TTFT (ms, log scale)")
    axes[1].set_ylabel("CDF")
    axes[1].set_title("Full TTFT distribution")
    axes[1].legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(output / "ttft_performance_and_cdf.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    shortlisted = {label: frames[label] for label in SHORTLIST if label in frames}
    fig, ax = plt.subplots(figsize=(8.5, 5.8))
    for label, frame in shortlisted.items():
        values = np.sort(frame.TTFT)
        ax.plot(values, np.arange(1, len(values) + 1) / len(values),
                label=label, linewidth=2)
    ax.set_xscale("log")
    ax.set_xlabel("E2E TTFT (ms, log scale)")
    ax.set_ylabel("CDF")
    ax.set_title("TTFT distribution of competitive policies")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output / "shortlist_ttft_cdf.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    stacked(axes[0], breakdown, "Mean TTFT breakdown")
    stacked(axes[1], tail_breakdown, "Top-1% request TTFT breakdown")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=5)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(output / "ttft_breakdown.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    shortlist_breakdown = breakdown[breakdown.policy.isin(shortlisted)].copy()
    shortlist_breakdown["policy"] = pd.Categorical(
        shortlist_breakdown.policy, categories=SHORTLIST, ordered=True)
    shortlist_breakdown = shortlist_breakdown.sort_values("policy")
    shortlist_tail = tail_breakdown[tail_breakdown.policy.isin(shortlisted)].copy()
    shortlist_tail["policy"] = pd.Categorical(
        shortlist_tail.policy, categories=SHORTLIST, ordered=True)
    shortlist_tail = shortlist_tail.sort_values("policy")
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.8))
    stacked(axes[0], shortlist_breakdown, "Mean TTFT breakdown")
    stacked(axes[1], shortlist_tail, "Top-1% request TTFT breakdown")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=5)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(output / "shortlist_ttft_breakdown.png", dpi=180,
                bbox_inches="tight")
    plt.close(fig)

    normalized = breakdown.set_index("policy")[COMPONENTS]
    normalized = normalized.div(normalized.sum(axis=1), axis=0) * 100
    fig, ax = plt.subplots(figsize=(11.5, 5.8))
    stacked(ax, normalized.reset_index(), "Mean TTFT component share")
    ax.set_ylabel("Share of explained TTFT (%)")
    ax.legend(ncol=5, fontsize=8)
    fig.tight_layout()
    fig.savefig(output / "ttft_breakdown_share.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(13, 6))
    quantiles = [0.50, 0.90, 0.95, 0.99]
    qvalues = np.array([[frame.TTFT.quantile(q) for q in quantiles]
                        for frame in frames.values()])
    width = 0.19
    x = np.arange(len(frames))
    for offset, q, color in zip(np.arange(4) - 1.5, quantiles,
                                ["#72b7b2", "#4c78a8", "#f58518", "#e45756"]):
        ax.bar(x + offset * width, qvalues[:, quantiles.index(q)], width,
               label=f"p{int(q * 100)}", color=color)
    ax.set_xticks(x, frames.keys(), rotation=35, ha="right")
    ax.set_ylabel("E2E TTFT (ms)")
    ax.set_title("TTFT quantiles across all policies")
    ax.legend(ncol=4)
    fig.tight_layout()
    fig.savefig(output / "ttft_quantiles.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    for ax, component in zip(axes.flat, COMPONENTS):
        values = [frame[component].to_numpy() for frame in frames.values()]
        ax.boxplot(values, showfliers=False, labels=list(frames.keys()))
        ax.set_title(component)
        ax.set_ylabel("ms")
        ax.tick_params(axis="x", rotation=35)
        for tick in ax.get_xticklabels():
            tick.set_ha("right")
    axes.flat[-1].axis("off")
    fig.suptitle("TTFT component distributions")
    fig.tight_layout()
    fig.savefig(output / "ttft_component_boxplots.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(16, 5.8))
    for label, frame in frames.items():
        time_s = (frame.arrival - min(f.arrival.min() for f in frames.values())) / 1e9
        bins = np.linspace(0, max(1, time_s.max()), 10)
        grouped = frame.assign(time_bin=pd.cut(time_s, bins, include_lowest=True)).groupby(
            "time_bin", observed=False).TTFT
        centers = (bins[:-1] + bins[1:]) / 2
        axes[0].plot(centers, grouped.mean(), marker="o", markersize=3, label=label)
        axes[1].plot(centers, grouped.quantile(0.95), marker="o", markersize=3, label=label)
    axes[0].set_title("Mean TTFT by arrival window")
    axes[1].set_title("p95 TTFT by arrival window")
    for ax in axes:
        ax.set_xlabel("Arrival time (s)")
        ax.set_ylabel("E2E TTFT (ms)")
    axes[1].legend(fontsize=7, ncol=2)
    fig.tight_layout()
    fig.savefig(output / "ttft_by_arrival_window.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    gpu_rows = []
    for label, frame in frames.items():
        for gpu_id, count in frame.gpu_id.value_counts().items():
            gpu_rows.append({"policy": label, "gpu_id": int(gpu_id), "requests": int(count)})
    gpu_table = pd.DataFrame(gpu_rows).pivot(index="policy", columns="gpu_id",
                                             values="requests").fillna(0)
    fig, ax = plt.subplots(figsize=(11, 6.5))
    image = ax.imshow(gpu_table, aspect="auto", cmap="Blues")
    ax.set_xticks(range(len(gpu_table.columns)), gpu_table.columns)
    ax.set_yticks(range(len(gpu_table.index)), gpu_table.index)
    ax.set_xlabel("Selected GPU ID")
    ax.set_title("Request distribution across GPUs")
    fig.colorbar(image, ax=ax, label="Requests")
    fig.tight_layout()
    fig.savefig(output / "gpu_request_distribution.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    routing = []
    for label, frame in frames.items():
        routing.append({
            "policy": label,
            "rerouted": int(frame.rerouted.sum()),
            "router_wait_mean": frame["Router wait"].mean(),
            "router_wait_max": frame["Router wait"].max(),
        })
    routing = pd.DataFrame(routing)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    axes[0].bar(routing.policy, routing.rerouted, color="#4c78a8")
    axes[0].set_ylabel("Requests")
    axes[0].set_title("Redirect count")
    axes[1].bar(routing.policy, routing.router_wait_max, color="#e45756")
    axes[1].set_ylabel("Maximum router wait (ms)")
    axes[1].set_title("Worst router capacity wait")
    for ax in axes:
        ax.tick_params(axis="x", rotation=35)
        for tick in ax.get_xticklabels():
            tick.set_ha("right")
    fig.tight_layout()
    fig.savefig(output / "routing_behavior.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    if "KV handoff" in frames:
        reference = frames["KV handoff"].set_index("request id").TTFT
        fig, axes = plt.subplots(1, 2, figsize=(15, 5.8))
        delta_rows = []
        reroute_sets = {}
        for label, frame in frames.items():
            current = frame.set_index("request id")
            common = reference.index.intersection(current.index)
            delta = current.loc[common, "TTFT"] - reference.loc[common]
            delta_rows.extend({"policy": label, "request_id": request_id,
                               "delta_vs_kv_ms": value}
                              for request_id, value in delta.items())
            values = np.sort(delta.to_numpy())
            axes[0].plot(values, np.arange(1, len(values) + 1) / len(values), label=label)
            reroute_sets[label] = set(current.index[current.rerouted.eq(1)])
        labels = list(reroute_sets)
        overlap = np.zeros((len(labels), len(labels)))
        for i, left in enumerate(labels):
            for j, right in enumerate(labels):
                union = reroute_sets[left] | reroute_sets[right]
                overlap[i, j] = (len(reroute_sets[left] & reroute_sets[right]) / len(union)
                                 if union else 1.0)
        axes[0].axvline(0, color="black", linewidth=1, linestyle="--")
        axes[0].set_xlabel("Paired TTFT delta vs KV handoff (ms)")
        axes[0].set_ylabel("CDF")
        axes[0].set_title("Per-request benefit relative to KV handoff")
        axes[0].legend(fontsize=7, ncol=2)
        image = axes[1].imshow(overlap, vmin=0, vmax=1, cmap="YlGnBu")
        axes[1].set_xticks(range(len(labels)), labels, rotation=45, ha="right")
        axes[1].set_yticks(range(len(labels)), labels)
        axes[1].set_title("Redirect-set Jaccard overlap")
        fig.colorbar(image, ax=axes[1], label="Jaccard similarity")
        fig.tight_layout()
        fig.savefig(output / "paired_delta_and_redirect_overlap.png", dpi=180,
                    bbox_inches="tight")
        plt.close(fig)
        pd.DataFrame(delta_rows).to_csv(
            root / "analysis/all_policy_comparison/paired_delta_vs_kv.csv", index=False)
    routing.to_csv(root / "analysis/all_policy_comparison/routing_summary.csv", index=False)
    gpu_table.to_csv(root / "analysis/all_policy_comparison/gpu_request_distribution.csv")


def write_report(root, workload, performance, breakdown, tail_breakdown):
    report = root / "reports/08_all_policy_comparison.md"
    best_mean = performance.loc[performance.mean_ms.idxmin()]
    best_p99 = performance.loc[performance.p99_ms.idxmin()]
    lines = [
        f"# 全routing policy比較: {workload}", "", "## 結論", "",
        f"Mean TTFT最良は **{best_mean.policy} ({best_mean.mean_ms:.1f} ms)**、"
        f"p99最良は **{best_p99.policy} ({best_p99.p99_ms:.1f} ms)** だった。",
        "Multi-candidateが常に最良かは、この2 workloadの結果を合わせて判断する必要がある。", "",
        "## TTFT分布", "",
        "| Policy | Mean | p50 | p95 | p99 | Max | Redirects |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in performance.itertuples():
        lines.append(
            f"| {row.policy} | {row.mean_ms:.1f} | {row.p50_ms:.1f} | {row.p95_ms:.1f} | "
            f"{row.p99_ms:.1f} | {row.max_ms:.1f} | {row.rerouted_n} |"
        )
    lines += ["", "![TTFT performance and CDF](../figures/all_policy_comparison/ttft_performance_and_cdf.png)",
              "", "## TTFT breakdown", "",
              "`E2E TTFT = Communication + Router wait + Scheduler queue + Prefill service + Other/staging`として集計した。"
              "右図は各policyのTTFT上位1%（最低3 requests）の成分平均で、tailの原因を示す。", "",
              "![TTFT breakdown](../figures/all_policy_comparison/ttft_breakdown.png)", "",
              "![TTFT breakdown share](../figures/all_policy_comparison/ttft_breakdown_share.png)", "",
              "## Breakdown CSV", "",
              "- `analysis/all_policy_comparison/performance_summary.csv`",
              "- `analysis/all_policy_comparison/mean_ttft_breakdown.csv`",
              "- `analysis/all_policy_comparison/tail_ttft_breakdown.csv`", ""]
    lines += ["## 追加の全手法比較図", "",
              "- [TTFT quantiles](../figures/all_policy_comparison/ttft_quantiles.png)",
              "- [Component boxplots](../figures/all_policy_comparison/ttft_component_boxplots.png)",
              "- [Arrival-window comparison](../figures/all_policy_comparison/ttft_by_arrival_window.png)",
              "- [GPU request distribution](../figures/all_policy_comparison/gpu_request_distribution.png)",
              "- [Routing behavior](../figures/all_policy_comparison/routing_behavior.png)",
              "- [Paired delta and redirect overlap](../figures/all_policy_comparison/paired_delta_and_redirect_overlap.png)", ""]
    lines += ["## 有望手法に絞った比較", "",
              "Cold migrate、KV handoff、Phase1 formula、Dynamic formula、Multi-candidateを抽出した。", "",
              "### TTFT統計", "",
              "| Policy | Mean | p50 | p95 | p99 | Max | Redirects |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    shortlist_performance = performance.set_index("policy").loc[
        [label for label in SHORTLIST if label in set(performance.policy)]]
    for label, row in shortlist_performance.iterrows():
        lines.append(f"| {label} | {row.mean_ms:.1f} | {row.p50_ms:.1f} | "
                     f"{row.p95_ms:.1f} | {row.p99_ms:.1f} | {row.max_ms:.1f} | "
                     f"{int(row.rerouted_n)} |")
    lines += ["", "### Mean TTFT breakdown", "",
              "| Policy | Communication | Router wait | Scheduler queue | Prefill service | Other/staging |",
              "|---|---:|---:|---:|---:|---:|"]
    shortlist_mean = breakdown.set_index("policy").loc[shortlist_performance.index]
    for label, row in shortlist_mean.iterrows():
        lines.append(f"| {label} | {row['Communication']:.1f} | {row['Router wait']:.1f} | "
                     f"{row['Scheduler queue']:.1f} | {row['Prefill service']:.1f} | "
                     f"{row['Other / staging']:.1f} |")
    lines += ["", "### Tail上位1% breakdown", "",
              "| Policy | Communication | Router wait | Scheduler queue | Prefill service | Other/staging |",
              "|---|---:|---:|---:|---:|---:|"]
    shortlist_tail = tail_breakdown.set_index("policy").loc[shortlist_performance.index]
    for label, row in shortlist_tail.iterrows():
        lines.append(f"| {label} | {row['Communication']:.1f} | {row['Router wait']:.1f} | "
                     f"{row['Scheduler queue']:.1f} | {row['Prefill service']:.1f} | "
                     f"{row['Other / staging']:.1f} |")
    lines += ["", "単位はすべてms。TailはTTFT最大の3 requests（300件の上位1%）の成分平均。", "",
              "- [Shortlist TTFT CDF](../figures/all_policy_comparison/shortlist_ttft_cdf.png)",
              "- [Shortlist TTFT breakdown](../figures/all_policy_comparison/shortlist_ttft_breakdown.png)", ""]
    report.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--workload-label", default="prompt6000 / 90s")
    args = parser.parse_args()
    root = args.root.resolve()
    frames = load_runs(root)
    if not frames:
        raise RuntimeError(f"No completed results found under {root / 'results'}")
    performance, breakdown, tail_breakdown = summaries(frames)
    output = root / "analysis/all_policy_comparison"
    output.mkdir(parents=True, exist_ok=True)
    performance.to_csv(output / "performance_summary.csv", index=False)
    breakdown.to_csv(output / "mean_ttft_breakdown.csv", index=False)
    tail_breakdown.to_csv(output / "tail_ttft_breakdown.csv", index=False)
    performance[performance.policy.isin(SHORTLIST)].to_csv(
        output / "shortlist_performance_summary.csv", index=False)
    breakdown[breakdown.policy.isin(SHORTLIST)].to_csv(
        output / "shortlist_mean_ttft_breakdown.csv", index=False)
    tail_breakdown[tail_breakdown.policy.isin(SHORTLIST)].to_csv(
        output / "shortlist_tail_ttft_breakdown.csv", index=False)
    make_figures(root, frames, performance, breakdown, tail_breakdown)
    write_report(root, args.workload_label, performance, breakdown, tail_breakdown)
    print(performance.to_string(index=False))


if __name__ == "__main__":
    main()
