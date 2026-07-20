#!/usr/bin/env python3
"""Compare redirected requests with matching requests in the wait-local run."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
EXPERIMENTS_DIR = EXPERIMENT_DIR.parent
WINDOW_RUNS = {
    60: EXPERIMENTS_DIR / "2026-07-13_prompt6000_three_policy",
    90: EXPERIMENT_DIR,
    120: EXPERIMENTS_DIR / "2026-07-14_prompt6000_120s_three_policy",
}
POLICIES = {
    "B": "NEAREST_MIGRATE",
    "C": "NEAREST_MIGRATE_KV",
}
COLORS = {"B": "#d18120", "C": "#31866f"}


def load_requests(experiment_dir, policy):
    path = experiment_dir / "results" / policy / "requests.csv"
    return pd.read_csv(path).set_index("request id").sort_index()


def queue_wait_ms(frame):
    return (
        frame["e2e_ttft_ns"]
        - frame["prefill_service_ns"]
        - frame["communication_latency_ns"]
    ) / 1e6


def paired_redirects(experiment_dir, short, policy):
    local = load_requests(experiment_dir, "NEAREST_KV")
    routed = load_requests(experiment_dir, policy)
    routed = routed[routed["rerouted"].astype(bool)].copy()
    local = local.loc[routed.index]

    first_send = load_requests(experiment_dir, "NEAREST_KV")["request_send_time_ns"].min()
    result = pd.DataFrame(index=routed.index)
    result.index.name = "request_id"
    result["policy"] = short
    result["send_time_s"] = (routed["request_send_time_ns"] - first_send) / 1e9
    result["home_gpu"] = routed["nearest_gpu_id"].astype(int)
    result["target_gpu"] = routed["gpu_id"].astype(int)
    result["capacity_reason"] = routed["redirect_capacity_reason"]
    result["capacity_running_reqs"] = routed["capacity_running_reqs"]
    result["capacity_max_num_seqs"] = routed["capacity_max_num_seqs"]
    result["capacity_required_kv_mib"] = routed["capacity_required_kv_bytes"] / 2**20
    result["capacity_available_kv_mib"] = routed["capacity_available_kv_bytes"] / 2**20
    result["local_ttft_ms"] = local["e2e_ttft_ns"] / 1e6
    result["local_queue_ms"] = queue_wait_ms(local)
    result["local_prefill_ms"] = local["prefill_service_ns"] / 1e6
    result["redirect_ttft_ms"] = routed["e2e_ttft_ns"] / 1e6
    result["redirect_queue_ms"] = queue_wait_ms(routed)
    result["redirect_prefill_ms"] = routed["prefill_service_ns"] / 1e6
    result["kv_transfer_ms"] = routed["kv_migration_latency_ns"] / 1e6
    result["redirect_minus_local_ms"] = result["redirect_ttft_ms"] - result["local_ttft_ms"]
    result["local_faster"] = result["redirect_minus_local_ms"] > 0
    return result


def plot_90s_scatter(frames, output):
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.8), sharex=True, sharey=True)
    maximum = max(
        max(frame["local_ttft_ms"].max(), frame["redirect_ttft_ms"].max())
        for frame in frames.values()
    ) * 1.06
    for axis, (short, frame) in zip(axes, frames.items()):
        harmful = frame["local_faster"]
        axis.scatter(
            frame.loc[~harmful, "local_ttft_ms"],
            frame.loc[~harmful, "redirect_ttft_ms"],
            s=62, color=COLORS[short], alpha=0.8, label="redirect faster",
        )
        axis.scatter(
            frame.loc[harmful, "local_ttft_ms"],
            frame.loc[harmful, "redirect_ttft_ms"],
            s=82, color="#c74b3a", marker="X", label="waiting local faster",
        )
        for request_id, row in frame[harmful].iterrows():
            axis.annotate(str(request_id), (row["local_ttft_ms"], row["redirect_ttft_ms"]),
                          xytext=(5, 5), textcoords="offset points", fontsize=8.5)
        axis.plot([0, maximum], [0, maximum], linestyle="--", color="#56514b", linewidth=1.2)
        axis.set_xlim(0, maximum)
        axis.set_ylim(0, maximum)
        axis.set_title(f"{short}: {POLICIES[short]}\nlocal faster: {harmful.sum()}/{len(frame)}",
                       fontsize=14)
        axis.set_xlabel("A: wait on local GPU TTFT (ms)")
        axis.grid(True, color="#d9d2c8", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right"]].set_visible(False)
        axis.legend(frameon=False, fontsize=9.5)
    axes[0].set_ylabel("redirect TTFT (ms)")
    fig.suptitle("Prompt 6000 / 90s: redirected request vs wait-local outcome", fontsize=17)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_harmful_breakdown(frame, output):
    harmful = frame[frame["local_faster"]].sort_values("redirect_minus_local_ms", ascending=False)
    labels = [str(index) for index in harmful.index]
    positions = np.arange(len(harmful))
    width = 0.35
    fig, axis = plt.subplots(figsize=(14, 7.2))
    local_queue = harmful["local_queue_ms"].to_numpy()
    local_prefill = harmful["local_prefill_ms"].to_numpy()
    redirect_queue = harmful["redirect_queue_ms"].to_numpy()
    redirect_kv = harmful["kv_transfer_ms"].to_numpy()
    redirect_prefill = harmful["redirect_prefill_ms"].to_numpy()

    axis.bar(positions - width / 2, local_queue, width, color="#e79b37", label="local queue")
    axis.bar(positions - width / 2, local_prefill, width, bottom=local_queue,
             color="#2f5f9f", label="local prefill")
    axis.bar(positions + width / 2, redirect_queue, width, color="#e79b37",
             hatch="//", edgecolor="#faf8f4", label="redirect queue")
    axis.bar(positions + width / 2, redirect_kv, width, bottom=redirect_queue,
             color="#865bd6", label="KV transfer")
    axis.bar(positions + width / 2, redirect_prefill, width,
             bottom=redirect_queue + redirect_kv, color="#31866f", label="redirect prefill")
    axis.set_xticks(positions, labels)
    axis.set_xlabel("request ID (left bar: wait local, right bar: KV handoff)")
    axis.set_ylabel("E2E TTFT components (ms)")
    axis.set_title("90s: requests for which waiting local was faster than KV handoff", fontsize=17)
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, ncol=5, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_cross_window(summary, output):
    windows = sorted(summary)
    x = np.arange(len(windows))
    width = 0.34
    fig, axis = plt.subplots(figsize=(11.5, 6.8))
    for offset, short in [(-width / 2, "B"), (width / 2, "C")]:
        values = [summary[window][short]["local_faster_rate"] * 100 for window in windows]
        bars = axis.bar(x + offset, values, width, color=COLORS[short],
                        label=f"{short}: {POLICIES[short]}")
        for bar, window in zip(bars, windows):
            item = summary[window][short]
            axis.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.2,
                      f"{item['local_faster_count']}/{item['redirect_count']}",
                      ha="center", fontsize=10.5)
    axis.set_xticks(x, [f"{window}s" for window in windows])
    axis.set_ylim(0, 70)
    axis.set_xlabel("arrival window")
    axis.set_ylabel("redirected requests where waiting local was faster (%)")
    axis.set_title("Capacity-based redirect can lose to waiting local", fontsize=17)
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    analysis_dir = EXPERIMENT_DIR / "analysis"
    figures_dir = EXPERIMENT_DIR / "figures"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    frames_90 = {
        short: paired_redirects(EXPERIMENT_DIR, short, policy)
        for short, policy in POLICIES.items()
    }
    combined = pd.concat(frames_90.values()).sort_values(
        ["policy", "redirect_minus_local_ms"], ascending=[True, False]
    )
    combined.to_csv(analysis_dir / "redirect_vs_wait_local_90s.csv")

    summary = {}
    for window, experiment_dir in WINDOW_RUNS.items():
        summary[window] = {}
        for short, policy in POLICIES.items():
            frame = paired_redirects(experiment_dir, short, policy)
            harmful = frame[frame["local_faster"]]
            summary[window][short] = {
                "redirect_count": int(len(frame)),
                "local_faster_count": int(len(harmful)),
                "local_faster_rate": float(len(harmful) / len(frame)),
                "redirect_faster_count": int((~frame["local_faster"]).sum()),
                "mean_redirect_minus_local_ms": float(frame["redirect_minus_local_ms"].mean()),
                "harmful_mean_redirect_minus_local_ms": float(
                    harmful["redirect_minus_local_ms"].mean()
                ) if len(harmful) else 0.0,
            }
    with (analysis_dir / "redirect_vs_wait_local_summary.json").open("w") as file:
        json.dump(summary, file, indent=2)

    plot_90s_scatter(frames_90, figures_dir / "redirect_vs_wait_local_90s_scatter.png")
    plot_harmful_breakdown(
        frames_90["C"], figures_dir / "harmful_kv_redirect_90s_breakdown.png"
    )
    plot_cross_window(summary, figures_dir / "harmful_redirect_rate_by_window.png")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
