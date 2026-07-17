#!/usr/bin/env python3
"""Analyze redirect destination concentration and its scheduler-queue impact."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
POLICIES = {"B": "NEAREST_MIGRATE", "C": "NEAREST_MIGRATE_KV"}
COLORS = {"B": "#d18120", "C": "#31866f"}


def load(policy):
    path = EXPERIMENT_DIR / "results" / policy / "requests.csv"
    frame = pd.read_csv(path).sort_values("gpu_arrival_time_ns")
    frame["scheduler_queue_ms"] = frame["queueing_before_ttft_ns"] / 1e6
    return frame


def annotate_overlap(frame):
    redirected = frame[frame["rerouted"].astype(bool)].copy()
    prior_redirects = []
    prior_all = []
    for _, request in redirected.iterrows():
        target = request["gpu_id"]
        arrival = request["gpu_arrival_time_ns"]
        active_redirects = redirected[
            (redirected["gpu_id"] == target)
            & (redirected["gpu_arrival_time_ns"] < arrival)
            & (redirected["request_end_time_ns"] > arrival)
        ]
        active_all = frame[
            (frame["gpu_id"] == target)
            & (frame["gpu_arrival_time_ns"] < arrival)
            & (frame["request_end_time_ns"] > arrival)
        ]
        prior_redirects.append(len(active_redirects))
        prior_all.append(len(active_all))
    redirected["prior_active_redirects_at_target"] = prior_redirects
    redirected["prior_active_requests_at_target"] = prior_all
    first_send = frame["request_send_time_ns"].min()
    redirected["send_time_s"] = (redirected["request_send_time_ns"] - first_send) / 1e9
    redirected["gpu_arrival_time_s"] = (
        redirected["gpu_arrival_time_ns"] - first_send
    ) / 1e9
    return redirected


def max_window_count(frame, seconds):
    times = np.sort(frame["gpu_arrival_time_ns"].to_numpy() / 1e9)
    if not len(times):
        return 0
    return max(int(((times >= start) & (times < start + seconds)).sum()) for start in times)


def summarize(frame, redirected):
    counts = redirected["gpu_id"].astype(int).value_counts().sort_index()
    shares = counts / len(redirected)
    target_queue = redirected.groupby("gpu_id")["scheduler_queue_ms"].agg(
        ["count", "sum", "mean", "median", "max"]
    )
    top_target = int(counts.idxmax())
    top_target_redirected = redirected[redirected["gpu_id"] == top_target]
    all_scheduler_queue_ms = frame["scheduler_queue_ms"].sum()
    redirect_scheduler_queue_ms = redirected["scheduler_queue_ms"].sum()
    top_target_queue_ms = top_target_redirected["scheduler_queue_ms"].sum()
    return {
        "redirect_count": int(len(redirected)),
        "destination_counts": {str(int(k)): int(v) for k, v in counts.items()},
        "top_target_gpu": top_target,
        "top_target_count": int(counts.max()),
        "top_target_share": float(counts.max() / len(redirected)),
        "destination_hhi": float((shares**2).sum()),
        "max_redirect_arrivals_in_1s": max_window_count(redirected, 1),
        "max_redirect_arrivals_in_2s": max_window_count(redirected, 2),
        "max_redirect_arrivals_in_5s": max_window_count(redirected, 5),
        "redirect_scheduler_queue_total_ms": float(redirect_scheduler_queue_ms),
        "redirect_scheduler_queue_mean_ms": float(redirected["scheduler_queue_ms"].mean()),
        "top_target_redirect_queue_total_ms": float(top_target_queue_ms),
        "top_target_share_of_redirect_queue": float(top_target_queue_ms / redirect_scheduler_queue_ms),
        "redirect_share_of_all_scheduler_queue": float(
            redirect_scheduler_queue_ms / all_scheduler_queue_ms
        ),
        "top_target_redirect_share_of_all_scheduler_queue": float(
            top_target_queue_ms / all_scheduler_queue_ms
        ),
        "scheduler_queue_correlation_with_prior_active_redirects": float(
            redirected["scheduler_queue_ms"].corr(
                redirected["prior_active_redirects_at_target"]
            )
        ),
        "scheduler_queue_correlation_with_prior_active_requests": float(
            redirected["scheduler_queue_ms"].corr(
                redirected["prior_active_requests_at_target"]
            )
        ),
        "by_target": {
            str(int(gpu)): {
                key: (int(value) if key == "count" else float(value))
                for key, value in row.items()
            }
            for gpu, row in target_queue.to_dict("index").items()
        },
    }


def plot_destinations(frames, output):
    gpu_ids = np.arange(10)
    width = 0.36
    fig, axis = plt.subplots(figsize=(12.5, 6.5))
    for offset, (short, frame) in zip((-width / 2, width / 2), frames.items()):
        counts = frame["gpu_id"].astype(int).value_counts().reindex(gpu_ids, fill_value=0)
        axis.bar(gpu_ids + offset, counts, width, color=COLORS[short],
                 label=f"{short}: {POLICIES[short]}")
    axis.set_xticks(gpu_ids)
    axis.set_xlabel("redirect destination GPU")
    axis.set_ylabel("redirected requests")
    axis.set_title("Prompt 6000 / 90s: redirect destination concentration", fontsize=17)
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_timeline(frame, output):
    fig, axis = plt.subplots(figsize=(14, 7.2))
    queue = frame["scheduler_queue_ms"]
    sizes = 45 + 2.2 * np.sqrt(queue.clip(lower=0)) * 10
    scatter = axis.scatter(
        frame["gpu_arrival_time_s"], frame["gpu_id"].astype(int),
        s=sizes, c=queue, cmap="magma_r", edgecolor="#faf8f4", linewidth=0.8,
    )
    for request_id, row in frame.nlargest(5, "scheduler_queue_ms").iterrows():
        axis.annotate(
            f"req {int(row['request id'])}\n{row['scheduler_queue_ms']:.0f} ms",
            (row["gpu_arrival_time_s"], int(row["gpu_id"])),
            xytext=(6, 7), textcoords="offset points", fontsize=8.5,
        )
    axis.axvspan(77.0, 79.0, color="#c74b3a", alpha=0.10, label="GPU 5 burst")
    axis.set_yticks(range(10))
    axis.set_xlabel("arrival at destination relative to first send (s)")
    axis.set_ylabel("redirect destination GPU")
    axis.set_title("C: redirect arrivals and scheduler queue at destination", fontsize=17)
    axis.grid(True, color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    axis.legend(frameon=False, loc="upper left")
    colorbar = fig.colorbar(scatter, ax=axis, pad=0.02)
    colorbar.set_label("scheduler queue (ms)")
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_queue_by_target(frame, output):
    groups = []
    labels = []
    counts = frame["gpu_id"].astype(int).value_counts().sort_index()
    for gpu in counts.index:
        groups.append(frame.loc[frame["gpu_id"] == gpu, "scheduler_queue_ms"].to_numpy())
        labels.append(f"GPU {gpu}\n(n={counts[gpu]})")
    fig, axis = plt.subplots(figsize=(12, 6.8))
    boxes = axis.boxplot(groups, tick_labels=labels, patch_artist=True, showmeans=True,
                         meanprops={"marker": "D", "markerfacecolor": "white",
                                    "markeredgecolor": "#262421", "markersize": 5})
    for box in boxes["boxes"]:
        box.set_facecolor(COLORS["C"])
        box.set_alpha(0.82)
    axis.set_ylabel("scheduler queue (ms)")
    axis.set_title("C: scheduler queue of redirected requests by destination", fontsize=17)
    axis.grid(axis="y", color="#d9d2c8", linewidth=0.8)
    axis.set_axisbelow(True)
    axis.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output, dpi=180, bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    analysis_dir = EXPERIMENT_DIR / "analysis"
    figures_dir = EXPERIMENT_DIR / "figures"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    full = {short: load(policy) for short, policy in POLICIES.items()}
    redirected = {short: annotate_overlap(frame) for short, frame in full.items()}
    summary = {
        short: summarize(full[short], redirected[short])
        for short in POLICIES
    }
    with (analysis_dir / "redirect_concentration_summary.json").open("w") as file:
        json.dump(summary, file, indent=2)
    request_rows = []
    for short, frame in redirected.items():
        labeled = frame.copy()
        labeled.insert(0, "policy", short)
        request_rows.append(labeled)
    pd.concat(request_rows, ignore_index=True).to_csv(
        analysis_dir / "redirect_concentration_requests.csv", index=False
    )

    plot_destinations(redirected, figures_dir / "redirect_destination_concentration.png")
    plot_timeline(redirected["C"], figures_dir / "redirect_concentration_c_timeline.png")
    plot_queue_by_target(redirected["C"], figures_dir / "redirect_queue_by_target_c.png")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
