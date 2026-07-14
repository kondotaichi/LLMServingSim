#!/usr/bin/env python3
"""Request-paired analysis for the prompt-6000, 120-second experiment."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
RUNS = {
    "NEAREST_KV": EXPERIMENT_DIR / "results/NEAREST_KV/requests.csv",
    "NEAREST_MIGRATE": EXPERIMENT_DIR / "results/NEAREST_MIGRATE/requests.csv",
    "NEAREST_MIGRATE_KV": EXPERIMENT_DIR / "results/NEAREST_MIGRATE_KV/requests.csv",
}
COLORS = {
    "NEAREST_KV": "#2f5f9f",
    "NEAREST_MIGRATE": "#d18120",
    "NEAREST_MIGRATE_KV": "#31866f",
}
SHORT = {
    "NEAREST_KV": "A",
    "NEAREST_MIGRATE": "B",
    "NEAREST_MIGRATE_KV": "C",
}
ARRIVAL_BINS = [0, 20, 40, 60, 80, 100, 121]
ARRIVAL_LABELS = ["0-20", "20-40", "40-60", "60-80", "80-100", "100-120"]


def load_runs():
    return {
        name: pd.read_csv(path).set_index("request id").sort_index()
        for name, path in RUNS.items()
    }


def validate(runs):
    baseline = runs["NEAREST_KV"]
    for name, frame in runs.items():
        if not baseline.index.equals(frame.index):
            raise ValueError(f"request ID mismatch: {name}")
        for column in ("input", "output", "user_id", "nearest_gpu_id", "request_send_time_ns"):
            left = baseline[column].to_numpy()
            right = frame[column].to_numpy()
            if not np.array_equal(left, right):
                raise ValueError(f"workload mismatch in {column}: {name}")


def paired_frame(runs):
    baseline = runs["NEAREST_KV"]
    result = pd.DataFrame(index=baseline.index)
    result["output_toks"] = baseline["output"]
    result["user_id"] = baseline["user_id"]
    result["home_gpu"] = baseline["nearest_gpu_id"]
    first_send = baseline["request_send_time_ns"].min()
    result["send_time_s"] = (baseline["request_send_time_ns"] - first_send) / 1e9

    for name, frame in runs.items():
        short = SHORT[name]
        result[f"{short}_target_gpu"] = frame["gpu_id"]
        result[f"{short}_rerouted"] = frame["rerouted"].astype(int)
        result[f"{short}_e2e_ttft_ms"] = frame["e2e_ttft_ns"] / 1e6
        result[f"{short}_completion_ms"] = frame["request_completion_latency_ns"] / 1e6
        result[f"{short}_prefill_ms"] = frame["prefill_service_ns"] / 1e6
        result[f"{short}_queue_wait_ms"] = (
            frame["e2e_ttft_ns"]
            - frame["prefill_service_ns"]
            - frame["communication_latency_ns"]
        ) / 1e6

    result["delta_B_minus_A_ttft_ms"] = result["B_e2e_ttft_ms"] - result["A_e2e_ttft_ms"]
    result["delta_C_minus_A_ttft_ms"] = result["C_e2e_ttft_ms"] - result["A_e2e_ttft_ms"]
    result["delta_C_minus_B_ttft_ms"] = result["C_e2e_ttft_ms"] - result["B_e2e_ttft_ms"]
    result["delta_C_minus_B_completion_ms"] = result["C_completion_ms"] - result["B_completion_ms"]
    result["redirect_group"] = "neither"
    result.loc[(result.B_rerouted == 1) & (result.C_rerouted == 1), "redirect_group"] = "both"
    result.loc[(result.B_rerouted == 1) & (result.C_rerouted == 0), "redirect_group"] = "B only"
    result.loc[(result.B_rerouted == 0) & (result.C_rerouted == 1), "redirect_group"] = "C only"
    return result


def summarize(runs, paired):
    summary = {"policies": {}, "redirect_overlap": paired.redirect_group.value_counts().to_dict()}
    for name, frame in runs.items():
        start = frame["arrival"].min()
        end = frame["end_time"].max()
        makespan_s = (end - start) / 1e9
        user_means = frame.groupby("user_id")["e2e_ttft_ns"].mean() / 1e6
        summary["policies"][name] = {
            "requests": int(len(frame)),
            "redirects": int(frame["rerouted"].sum()),
            "e2e_ttft_mean_ms": float(frame["e2e_ttft_ns"].mean() / 1e6),
            "e2e_ttft_p50_ms": float(frame["e2e_ttft_ns"].quantile(0.50) / 1e6),
            "e2e_ttft_p95_ms": float(frame["e2e_ttft_ns"].quantile(0.95) / 1e6),
            "e2e_ttft_max_ms": float(frame["e2e_ttft_ns"].max() / 1e6),
            "completion_mean_ms": float(frame["request_completion_latency_ns"].mean() / 1e6),
            "completion_p50_ms": float(frame["request_completion_latency_ns"].quantile(0.50) / 1e6),
            "completion_p95_ms": float(frame["request_completion_latency_ns"].quantile(0.95) / 1e6),
            "completion_max_ms": float(frame["request_completion_latency_ns"].max() / 1e6),
            "tpot_mean_ms": float(frame["TPOT"].mean() / 1e6),
            "makespan_s": float(makespan_s),
            "request_throughput": float(len(frame) / makespan_s),
            "output_token_throughput": float(frame["output"].sum() / makespan_s),
            "prefix_hit_rate": float(frame["reuse_prefix_toks"].sum() / frame["input"].sum()),
            "user_mean_ttft_cv": float(user_means.std(ddof=0) / user_means.mean()),
        }

    both = paired[paired.redirect_group == "both"]
    summary["paired"] = {
        "C_vs_B_all_ttft_win_rate": float((paired.delta_C_minus_B_ttft_ms < 0).mean()),
        "C_vs_B_all_completion_win_rate": float((paired.delta_C_minus_B_completion_ms < 0).mean()),
        "C_vs_B_both_redirect_count": int(len(both)),
        "C_vs_B_both_redirect_ttft_win_rate": float((both.delta_C_minus_B_ttft_ms < 0).mean()),
        "C_vs_B_both_redirect_mean_delta_ms": float(both.delta_C_minus_B_ttft_ms.mean()),
        "C_vs_B_both_redirect_median_delta_ms": float(both.delta_C_minus_B_ttft_ms.median()),
    }

    arrival_frame = paired.assign(
        time_bin=pd.cut(
            paired.send_time_s,
            bins=ARRIVAL_BINS,
            labels=ARRIVAL_LABELS,
            include_lowest=True,
        )
    )
    summary["arrival_bins"] = {}
    for label, group in arrival_frame.groupby("time_bin", observed=True):
        summary["arrival_bins"][str(label)] = {
            "requests": int(len(group)),
            "A_mean_ttft_ms": float(group.A_e2e_ttft_ms.mean()),
            "B_mean_ttft_ms": float(group.B_e2e_ttft_ms.mean()),
            "C_mean_ttft_ms": float(group.C_e2e_ttft_ms.mean()),
            "B_redirects": int(group.B_rerouted.sum()),
            "C_redirects": int(group.C_rerouted.sum()),
        }

    summary["home_gpus"] = {}
    for gpu_id, group in paired.groupby("home_gpu"):
        summary["home_gpus"][str(int(gpu_id))] = {
            "requests": int(len(group)),
            "A_mean_ttft_ms": float(group.A_e2e_ttft_ms.mean()),
            "B_mean_ttft_ms": float(group.B_e2e_ttft_ms.mean()),
            "C_mean_ttft_ms": float(group.C_e2e_ttft_ms.mean()),
            "B_redirects": int(group.B_rerouted.sum()),
            "C_redirects": int(group.C_rerouted.sum()),
        }

    summary["redirect_groups"] = {}
    for label, group in paired.groupby("redirect_group"):
        summary["redirect_groups"][label] = {
            "requests": int(len(group)),
            "A_mean_ttft_ms": float(group.A_e2e_ttft_ms.mean()),
            "B_mean_ttft_ms": float(group.B_e2e_ttft_ms.mean()),
            "C_mean_ttft_ms": float(group.C_e2e_ttft_ms.mean()),
        }
    return summary


def plot_paired_delta(paired, output):
    fig, axis = plt.subplots(figsize=(13.5, 6.8))
    series = [
        ("B - A", paired.delta_B_minus_A_ttft_ms, COLORS["NEAREST_MIGRATE"]),
        ("C - A", paired.delta_C_minus_A_ttft_ms, COLORS["NEAREST_MIGRATE_KV"]),
        ("C - B", paired.delta_C_minus_B_ttft_ms, "#865bd6"),
    ]
    for label, values, color in series:
        ordered = np.sort(values.to_numpy())
        probability = np.arange(1, len(ordered) + 1) / len(ordered)
        axis.step(ordered, probability, where="post", linewidth=2.3, color=color, label=label)
    axis.axvline(0, color="#333333", linestyle="--", linewidth=1.2)
    axis.set_xlabel("paired E2E TTFT delta (ms); negative means first policy is faster", fontsize=11.5)
    axis.set_ylabel("cumulative probability", fontsize=11.5)
    axis.set_title("Prompt 6000 / 120s: request-paired E2E TTFT delta CDF", fontsize=16, pad=14)
    axis.grid(True, color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(fontsize=11)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_arrival_bins(paired, output):
    grouped = paired.assign(time_bin=pd.cut(paired.send_time_s, bins=ARRIVAL_BINS, labels=ARRIVAL_LABELS,
                                            include_lowest=True)).groupby("time_bin", observed=True)
    fig, axis = plt.subplots(figsize=(13.5, 6.8))
    for name in RUNS:
        short = SHORT[name]
        means = grouped[f"{short}_e2e_ttft_ms"].mean().reindex(ARRIVAL_LABELS)
        axis.plot(ARRIVAL_LABELS, means, marker="o", linewidth=2.4, color=COLORS[name], label=name)
    axis.set_xlabel("request send-time bin (s)", fontsize=11.5)
    axis.set_ylabel("mean E2E TTFT (ms)", fontsize=11.5)
    axis.set_title("Prompt 6000 / 120s: E2E TTFT over the arrival window", fontsize=16, pad=14)
    axis.grid(True, color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(fontsize=10.5)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_home_gpu(paired, output):
    means = paired.groupby("home_gpu")[["A_e2e_ttft_ms", "B_e2e_ttft_ms", "C_e2e_ttft_ms"]].mean()
    gpu_ids = means.index.to_numpy()
    width = 0.25
    fig, axis = plt.subplots(figsize=(14, 6.8))
    for offset, name in zip((-width, 0, width), RUNS):
        short = SHORT[name]
        axis.bar(gpu_ids + offset, means[f"{short}_e2e_ttft_ms"], width=width,
                 color=COLORS[name], label=name)
    axis.set_yscale("log")
    axis.set_xticks(gpu_ids)
    axis.set_xlabel("home GPU / cell ID", fontsize=11.5)
    axis.set_ylabel("mean E2E TTFT (ms, log scale)", fontsize=11.5)
    axis.set_title("Prompt 6000 / 120s: mean E2E TTFT by original home cell", fontsize=16, pad=14)
    axis.grid(axis="y", which="both", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(fontsize=10.5)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_redirect_overlap(paired, output):
    order = ["both", "B only", "C only", "neither"]
    counts = paired.redirect_group.value_counts().reindex(order).fillna(0)
    colors = ["#865bd6", COLORS["NEAREST_MIGRATE"], COLORS["NEAREST_MIGRATE_KV"], "#9b9892"]
    fig, axis = plt.subplots(figsize=(10.5, 6.2))
    bars = axis.bar(order, counts, color=colors)
    for bar, count in zip(bars, counts):
        axis.text(bar.get_x() + bar.get_width() / 2, count + 4, str(int(count)),
                  ha="center", fontweight="bold", fontsize=12)
    axis.set_ylim(0, max(counts) * 1.13)
    axis.set_ylabel("requests", fontsize=11.5)
    axis.set_title("Redirect-decision overlap: B vs C", fontsize=16, pad=14)
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    runs = load_runs()
    validate(runs)
    paired = paired_frame(runs)
    summary = summarize(runs, paired)

    analysis_dir = EXPERIMENT_DIR / "analysis"
    image_dir = EXPERIMENT_DIR / "figures"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    paired.to_csv(analysis_dir / "paired_requests.csv")
    with (analysis_dir / "summary.json").open("w") as file:
        json.dump(summary, file, indent=2)

    prefix = "three_policy"
    plot_paired_delta(paired, image_dir / f"{prefix}_paired_delta_cdf.png")
    plot_arrival_bins(paired, image_dir / f"{prefix}_arrival_bins.png")
    plot_home_gpu(paired, image_dir / f"{prefix}_home_gpu_ttft.png")
    plot_redirect_overlap(paired, image_dir / f"{prefix}_redirect_overlap.png")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
