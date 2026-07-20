#!/usr/bin/env python3
"""Analyze post-routing TTFT component dominance across all experiments."""

import json
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


REPO_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENTS_ROOT = REPO_ROOT / "experiments"
OUTPUT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = OUTPUT_ROOT / "analysis/post_routing"
FIGURE_DIR = OUTPUT_ROOT / "figures/post_routing"
POLICIES = {"NEAREST_KV", "NEAREST_MIGRATE", "NEAREST_MIGRATE_KV"}
TARGETS = [
    "router_queue_ms",
    "scheduler_queue_ms",
    "compute_prefill_ms",
    "e2e_ttft_ms",
]

COMMON_GROUPS = {
    "request_demand": ["input_tokens", "output_tokens_actual", "minimum_prefill_chunks"],
    "nominal_reuse": ["nominal_reuse_tokens", "nominal_reuse_ratio"],
    "realized_reuse": [
        "reuse_tokens_realized", "reuse_preservation_ratio", "reuse_lost",
        "realized_uncached_tokens",
    ],
    "offered_load": [
        "request_rate_rps", "offered_uncached_tokens_per_s",
        "arrival_offset_s", "interarrival_ms",
        "global_arrivals_1s", "global_arrivals_5s",
    ],
    "home_cell_load": [
        "home_arrivals_1s", "home_arrivals_5s", "home_workload_share",
    ],
    "policy": ["policy"],
    "routing_result": ["rerouted"],
    "kv_movement": ["kv_moved", "migration_tokens_realized"],
}

STATE_GROUPS = {
    **COMMON_GROUPS,
    "router_initial_state": [
        "router_initial_waiting_reqs", "router_initial_running_reqs",
        "router_initial_required_kv_bytes",
        "router_initial_available_kv_bytes",
        "router_initial_projected_active_kv_bytes",
        "router_initial_capacity_pressure", "router_initial_slot_pressure",
        "router_initial_admissible_candidate_count",
        "router_initial_total_waiting_reqs", "router_initial_max_waiting_reqs",
        "router_initial_total_running_reqs", "router_initial_max_running_reqs",
        "router_initial_min_available_kv_bytes",
        "router_initial_max_available_kv_bytes",
        "router_initial_min_capacity_pressure",
        "router_initial_max_capacity_pressure",
    ],
    "router_decision_state": [
        "router_decision_waiting_reqs", "router_decision_running_reqs",
        "router_decision_available_kv_bytes",
        "router_decision_projected_active_kv_bytes",
        "router_decision_capacity_pressure", "router_decision_slot_pressure",
        "router_capacity_retry_count",
    ],
    "scheduler_state": [
        "scheduler_waiting_reqs_at_first_schedule",
        "scheduler_running_reqs_at_first_schedule",
        "scheduler_running_decode_reqs_at_first_schedule",
        "scheduler_scheduled_prefill_tokens",
        "scheduler_scheduled_decode_tokens",
        "scheduler_batch_num_seqs",
        "scheduler_prefill_tokens_ahead",
    ],
}
CATEGORICAL = {"policy"}


def scenario_id(path):
    relative = path.relative_to(EXPERIMENTS_ROOT)
    parts = list(relative.parts)
    results_index = parts.index("results")
    experiment = parts[0]
    suffix = parts[results_index + 1:-2]
    return "/".join([experiment, *suffix])


def load_runs():
    records = []
    for path in sorted(EXPERIMENTS_ROOT.rglob("requests.csv")):
        policy = path.parent.name
        if policy not in POLICIES:
            continue
        frame = pd.read_csv(path)
        if len(frame) != 300 or "e2e_ttft_ns" not in frame:
            continue
        records.append({
            "path": path,
            "scenario_id": scenario_id(path),
            "policy": policy,
            "frame": frame,
            "schema_columns": len(frame.columns),
            "has_scheduler_state": "scheduler_prefill_tokens_ahead" in frame,
            "has_router_state": "router_initial_capacity_pressure" in frame,
        })
    return records


def rolling_features(frame):
    arrivals = frame["request_send_time_ns"].astype(float).to_numpy()
    home = frame["nearest_gpu_id"].fillna(frame["gpu_id"]).astype(int).to_numpy()
    order = np.argsort(arrivals, kind="stable")
    result = pd.DataFrame(index=frame.index)
    result["arrival_offset_s"] = (arrivals - arrivals.min()) / 1e9
    sorted_arrivals = arrivals[order]
    interarrival = np.diff(sorted_arrivals, prepend=sorted_arrivals[0]) / 1e6
    result.loc[frame.index[order], "interarrival_ms"] = interarrival
    for seconds in (1, 5):
        global_counts = np.zeros(len(frame), dtype=int)
        home_counts = np.zeros(len(frame), dtype=int)
        left = 0
        for position, row_index in enumerate(order):
            while sorted_arrivals[left] < sorted_arrivals[position] - seconds * 1e9:
                left += 1
            prior_positions = order[left:position]
            global_counts[row_index] = len(prior_positions)
            home_counts[row_index] = int(np.sum(home[prior_positions] == home[row_index]))
        result[f"global_arrivals_{seconds}s"] = global_counts
        result[f"home_arrivals_{seconds}s"] = home_counts
    shares = pd.Series(home).value_counts(normalize=True)
    result["home_workload_share"] = [shares[value] for value in home]
    return result


def build_dataset(records):
    nominal_by_scenario = {}
    for record in records:
        frame = record["frame"]
        values = pd.Series(
            frame["reuse_prefix_toks"].to_numpy(),
            index=frame["request id"].astype(int),
        )
        current = nominal_by_scenario.get(record["scenario_id"])
        nominal_by_scenario[record["scenario_id"]] = (
            values if current is None else pd.concat([current, values], axis=1).max(axis=1)
        )

    outputs = []
    inventory = []
    for record in records:
        frame = record["frame"].copy()
        request_ids = frame["request id"].astype(int)
        nominal = request_ids.map(nominal_by_scenario[record["scenario_id"]]).fillna(0).astype(float)
        rolling = rolling_features(frame)
        send = frame["request_send_time_ns"].astype(float)
        duration_s = max((send.max() - send.min()) / 1e9, 1e-9)
        rate = (len(frame) - 1) / duration_s
        communication = frame["communication_latency_ns"].astype(float)
        scheduler = frame["queueing_before_ttft_ns"].astype(float)
        compute = frame["prefill_service_ns"].astype(float)
        migration = frame["kv_migration_latency_ns"].astype(float)
        e2e = frame["e2e_ttft_ns"].astype(float)
        router = (e2e - scheduler - compute - communication).clip(lower=0)
        realized_reuse = frame["reuse_prefix_toks"].astype(float)
        input_tokens = frame["input"].astype(float)

        output = pd.DataFrame({
            "scenario_id": record["scenario_id"],
            "policy": record["policy"],
            "request_id": request_ids,
            "input_tokens": input_tokens,
            "output_tokens_actual": frame["output"].astype(float),
            "minimum_prefill_chunks": np.ceil((input_tokens - nominal).clip(lower=0) / 2048),
            "nominal_reuse_tokens": nominal,
            "nominal_reuse_ratio": nominal / input_tokens.replace(0, np.nan),
            "reuse_tokens_realized": realized_reuse,
            "reuse_preservation_ratio": np.where(nominal > 0, realized_reuse / nominal, 0.0),
            "reuse_lost": ((nominal > 0) & (realized_reuse == 0)).astype(int),
            "realized_uncached_tokens": (input_tokens - realized_reuse).clip(lower=0),
            "request_rate_rps": rate,
            "offered_uncached_tokens_per_s": (input_tokens - nominal).clip(lower=0) * rate,
            "rerouted": frame["rerouted"].astype(int),
            "kv_moved": ((migration > 0) & (frame["kv_migration_bytes"].astype(float) > 0)).astype(int),
            "migration_tokens_realized": frame["kv_migration_tokens"].astype(float),
            "router_queue_ms": router / 1e6,
            "scheduler_queue_ms": scheduler / 1e6,
            "kv_transfer_ms": migration / 1e6,
            "compute_prefill_ms": compute / 1e6,
            "other_communication_ms": (communication - migration).clip(lower=0) / 1e6,
            "e2e_ttft_ms": e2e / 1e6,
            "has_scheduler_state": int(record["has_scheduler_state"]),
            "has_router_state": int(record["has_router_state"]),
        })
        for column in rolling:
            output[column] = rolling[column].to_numpy()
        state_columns = [
            column
            for group in ("router_initial_state", "router_decision_state", "scheduler_state")
            for column in STATE_GROUPS[group]
        ]
        for column in state_columns:
            output[column] = frame[column] if column in frame else np.nan
        reconstruction = output[[
            "router_queue_ms", "scheduler_queue_ms", "kv_transfer_ms",
            "compute_prefill_ms", "other_communication_ms",
        ]].sum(axis=1)
        output["reconstruction_error_ms"] = reconstruction - output.e2e_ttft_ms
        outputs.append(output)
        inventory.append({
            "scenario_id": record["scenario_id"],
            "policy": record["policy"],
            "path": str(record["path"].relative_to(REPO_ROOT)),
            "n": len(frame),
            "schema_columns": record["schema_columns"],
            "has_scheduler_state": record["has_scheduler_state"],
            "has_router_state": record["has_router_state"],
            "input_tokens": int(input_tokens.iloc[0]),
            "nominal_reuse_ratio": float((nominal / input_tokens).median()),
            "request_rate_rps": rate,
            "rerouted_n": int(frame.rerouted.sum()),
            "kv_moved_n": int(((migration > 0) & (frame["kv_migration_bytes"] > 0)).sum()),
        })
    return pd.concat(outputs, ignore_index=True), pd.DataFrame(inventory)


def model_columns(groups):
    return list(dict.fromkeys(column for columns in groups.values() for column in columns))


def make_model(columns):
    numeric = [column for column in columns if column not in CATEGORICAL]
    categorical = [column for column in columns if column in CATEGORICAL]
    transformer = ColumnTransformer([
        ("numeric", StandardScaler(), numeric),
        ("categorical", OneHotEncoder(handle_unknown="ignore"), categorical),
    ])
    return Pipeline([("preprocess", transformer), ("model", Ridge(alpha=10.0))])


def prepare_subset(dataset, groups):
    columns = model_columns(groups)
    complete = [column for column in columns if dataset[column].notna().all()]
    subset = dataset.dropna(subset=complete + TARGETS).copy()
    variable = [column for column in complete if subset[column].nunique(dropna=False) > 1]
    filtered_groups = {
        group: [column for column in columns if column in variable]
        for group, columns in groups.items()
    }
    filtered_groups = {group: columns for group, columns in filtered_groups.items() if columns}
    return subset, filtered_groups


def fit_ablation(dataset, feature_set, groups):
    dataset, groups = prepare_subset(dataset, groups)
    full_columns = model_columns(groups)
    rows = []
    predictions = []
    scenarios = sorted(dataset.scenario_id.unique())
    for fold, scenario in enumerate(scenarios):
        train = dataset[dataset.scenario_id != scenario]
        test = dataset[dataset.scenario_id == scenario]
        if train.empty or test.empty:
            continue
        for target in TARGETS:
            model = make_model(full_columns)
            model.fit(train[full_columns], train[target])
            full_prediction = np.clip(model.predict(test[full_columns]), 0, None)
            full_mae = mean_absolute_error(test[target], full_prediction)
            predictions.append(pd.DataFrame({
                "feature_set": feature_set,
                "fold": fold,
                "test_scenario": scenario,
                "target": target,
                "actual": test[target].to_numpy(),
                "predicted": full_prediction,
            }))
            for group, removed in groups.items():
                reduced = [column for column in full_columns if column not in removed]
                if not reduced:
                    continue
                reduced_model = make_model(reduced)
                reduced_model.fit(train[reduced], train[target])
                reduced_prediction = np.clip(reduced_model.predict(test[reduced]), 0, None)
                reduced_mae = mean_absolute_error(test[target], reduced_prediction)
                full_r2 = r2_score(test[target], full_prediction)
                reduced_r2 = r2_score(test[target], reduced_prediction)
                rows.append({
                    "feature_set": feature_set,
                    "fold": fold,
                    "test_scenario": scenario,
                    "target": target,
                    "group": group,
                    "n_test": len(test),
                    "full_mae_ms": full_mae,
                    "ablated_mae_ms": reduced_mae,
                    "mae_increase_ms": reduced_mae - full_mae,
                    "full_r2": full_r2,
                    "partial_r2": full_r2 - reduced_r2,
                })
    return pd.DataFrame(rows), pd.concat(predictions, ignore_index=True)


def coefficients(dataset, feature_set, groups):
    dataset, groups = prepare_subset(dataset, groups)
    columns = model_columns(groups)
    rows = []
    for target in TARGETS:
        model = make_model(columns)
        model.fit(dataset[columns], dataset[target])
        names = model.named_steps["preprocess"].get_feature_names_out()
        values = model.named_steps["model"].coef_
        for name, value in zip(names, values):
            feature = name.split("__", 1)[1]
            group = next(
                group for group, members in groups.items()
                if feature in members or any(feature.startswith(member + "_") for member in members)
            )
            rows.append({
                "feature_set": feature_set,
                "target": target,
                "group": group,
                "feature": feature,
                "coefficient_ms_per_sd": value,
                "absolute_coefficient": abs(value),
            })
    return pd.DataFrame(rows)


def bootstrap_importance(ablation, repetitions=2000):
    rng = np.random.default_rng(20260719)
    rows = []
    for keys, frame in ablation.groupby(["feature_set", "target", "group"]):
        scenarios = frame.test_scenario.unique()
        values = []
        for _ in range(repetitions):
            sampled = rng.choice(scenarios, size=len(scenarios), replace=True)
            values.append(np.mean([
                frame.loc[frame.test_scenario == scenario, "mae_increase_ms"].mean()
                for scenario in sampled
            ]))
        rows.append({
            "feature_set": keys[0],
            "target": keys[1],
            "group": keys[2],
            "mean_mae_increase_ms": frame.mae_increase_ms.mean(),
            "ci95_low_ms": np.quantile(values, 0.025),
            "ci95_high_ms": np.quantile(values, 0.975),
            "mean_partial_r2": frame.partial_r2.mean(),
            "n_scenarios": len(scenarios),
        })
    return pd.DataFrame(rows)


def component_shares(dataset):
    components = [
        "router_queue_ms", "scheduler_queue_ms", "kv_transfer_ms",
        "compute_prefill_ms", "other_communication_ms",
    ]
    rows = []
    for (scenario, policy), frame in dataset.groupby(["scenario_id", "policy"]):
        row = {"scenario_id": scenario, "policy": policy, "n": len(frame)}
        for component in components:
            row[f"{component}_mean_ms"] = frame[component].mean()
            row[f"{component}_mean_share"] = (frame[component] / frame.e2e_ttft_ms).mean()
        row["dominant_component"] = frame[components].mean().idxmax()
        rows.append(row)
    return pd.DataFrame(rows)


def plot_importance(bootstrap):
    for feature_set in bootstrap.feature_set.unique():
        selected = bootstrap[bootstrap.feature_set == feature_set]
        pivot = selected.pivot(index="group", columns="target", values="mean_mae_increase_ms").fillna(0)
        pivot = pivot.reindex(columns=TARGETS)
        fig, axis = plt.subplots(figsize=(11, max(5, 0.55 * len(pivot))))
        values = np.sign(pivot) * np.log10(np.abs(pivot) + 1)
        image = axis.imshow(values, aspect="auto", cmap="RdBu_r", vmin=-4, vmax=4)
        axis.set_xticks(range(len(TARGETS)), [target.replace("_ms", "") for target in TARGETS], rotation=20, ha="right")
        axis.set_yticks(range(len(pivot)), pivot.index)
        for row in range(len(pivot)):
            for column in range(len(pivot.columns)):
                axis.text(column, row, f"{pivot.iloc[row, column]:.1f}", ha="center", va="center", fontsize=8)
        fig.colorbar(image, ax=axis, label="signed log10(MAE increase + 1 ms)")
        axis.set_title(f"Post-routing group ablation importance: {feature_set}")
        fig.tight_layout()
        fig.savefig(FIGURE_DIR / f"group_ablation_{feature_set}.png", dpi=180,
                    bbox_inches="tight", facecolor="#faf8f4")
        plt.close(fig)


def plot_component_shares(shares):
    components = [
        "router_queue_ms", "scheduler_queue_ms", "kv_transfer_ms",
        "compute_prefill_ms", "other_communication_ms",
    ]
    aggregate = shares.groupby("policy")[[f"{c}_mean_share" for c in components]].mean()
    fig, axis = plt.subplots(figsize=(11, 6.5))
    bottom = np.zeros(len(aggregate))
    colors = ["#c84f3d", "#e89c32", "#8456d8", "#358a73", "#4b79b8"]
    for component, color in zip(components, colors):
        values = aggregate[f"{component}_mean_share"].to_numpy()
        axis.bar(aggregate.index, values, bottom=bottom,
                 label=component.replace("_ms", ""), color=color)
        bottom += values
    axis.set_ylabel("Mean request-level E2E share")
    axis.set_title("TTFT component share across all experiments")
    axis.legend(ncol=3)
    axis.grid(axis="y", color="#ded8ce")
    axis.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "component_share_all_experiments.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_importance_intervals(bootstrap):
    targets = ["router_queue_ms", "scheduler_queue_ms", "compute_prefill_ms"]
    fig, axes = plt.subplots(1, len(targets), figsize=(16, 7), sharex=False)
    for axis, target in zip(axes, targets):
        frame = bootstrap[
            (bootstrap.feature_set == "state") & (bootstrap.target == target)
        ].sort_values("mean_mae_increase_ms").tail(8)
        means = frame.mean_mae_increase_ms.to_numpy()
        lower = means - frame.ci95_low_ms.to_numpy()
        upper = frame.ci95_high_ms.to_numpy() - means
        colors = np.where(frame.ci95_low_ms > 0, "#26734d", "#9b9b9b")
        axis.barh(frame.group, means, color=colors, alpha=0.9)
        axis.errorbar(means, range(len(frame)), xerr=[lower, upper], fmt="none",
                      ecolor="#202020", capsize=3, linewidth=1)
        axis.axvline(0, color="#202020", linewidth=0.8)
        axis.set_title(target.replace("_ms", ""))
        axis.set_xlabel("Held-out MAE increase (ms)\n95% scenario-bootstrap CI")
        axis.grid(axis="x", color="#ded8ce")
        axis.set_axisbelow(True)
    fig.suptitle("Input-group importance with newly logged router/scheduler state", fontsize=14)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "state_importance_with_ci.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def plot_component_regimes(dataset):
    components = [
        "router_queue_ms", "scheduler_queue_ms", "kv_transfer_ms",
        "compute_prefill_ms",
    ]
    aggregate = dataset.groupby("input_tokens")[components].mean().sort_index()
    fig, axis = plt.subplots(figsize=(10, 6))
    colors = ["#c84f3d", "#e89c32", "#8456d8", "#358a73"]
    for component, color in zip(components, colors):
        axis.plot(aggregate.index, aggregate[component], marker="o", linewidth=2.2,
                  label=component.replace("_ms", ""), color=color)
    axis.set_yscale("symlog", linthresh=10)
    axis.set_xlabel("Input tokens")
    axis.set_ylabel("Mean component latency (ms, symlog scale)")
    axis.set_title("TTFT bottleneck changes with input length")
    axis.grid(color="#ded8ce")
    axis.legend()
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "component_regime_by_input.png", dpi=180,
                bbox_inches="tight", facecolor="#faf8f4")
    plt.close(fig)


def main():
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    records = load_runs()
    dataset, inventory = build_dataset(records)
    inventory.to_csv(ANALYSIS_DIR / "run_inventory.csv", index=False)
    dataset.to_csv(ANALYSIS_DIR / "canonical_requests.csv", index=False)
    shares = component_shares(dataset)
    shares.to_csv(ANALYSIS_DIR / "component_shares.csv", index=False)

    common_ablation, common_predictions = fit_ablation(dataset, "common", COMMON_GROUPS)
    state_dataset = dataset[dataset.has_scheduler_state == 1].copy()
    state_ablation, state_predictions = fit_ablation(state_dataset, "state", STATE_GROUPS)
    ablation = pd.concat([common_ablation, state_ablation], ignore_index=True)
    predictions = pd.concat([common_predictions, state_predictions], ignore_index=True)
    ablation.to_csv(ANALYSIS_DIR / "group_ablation_importance.csv", index=False)
    predictions.to_csv(ANALYSIS_DIR / "oof_predictions.csv", index=False)
    bootstrap = bootstrap_importance(ablation)
    bootstrap.to_csv(ANALYSIS_DIR / "bootstrap_importance.csv", index=False)
    coefficient_frame = pd.concat([
        coefficients(dataset, "common", COMMON_GROUPS),
        coefficients(state_dataset, "state", STATE_GROUPS),
    ], ignore_index=True)
    coefficient_frame.to_csv(ANALYSIS_DIR / "standardized_coefficients.csv", index=False)

    metrics = []
    for keys, frame in predictions.groupby(["feature_set", "target"]):
        metrics.append({
            "feature_set": keys[0],
            "target": keys[1],
            "n": len(frame),
            "mae_ms": mean_absolute_error(frame.actual, frame.predicted),
            "r2": r2_score(frame.actual, frame.predicted),
        })
    pd.DataFrame(metrics).to_csv(ANALYSIS_DIR / "model_metrics.csv", index=False)
    plot_importance(bootstrap)
    plot_component_shares(shares)
    plot_importance_intervals(bootstrap)
    plot_component_regimes(dataset)
    summary = {
        "runs": len(inventory),
        "requests": len(dataset),
        "scenarios": int(dataset.scenario_id.nunique()),
        "state_runs": int(inventory.has_scheduler_state.sum()),
        "router_state_runs": int(inventory.has_router_state.sum()),
        "max_reconstruction_error_ms": float(dataset.reconstruction_error_ms.abs().max()),
    }
    (ANALYSIS_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    print("\nTop common importance")
    print(bootstrap[bootstrap.feature_set == "common"].sort_values(
        ["target", "mean_mae_increase_ms"], ascending=[True, False]
    ).groupby("target").head(4).to_string(index=False))


if __name__ == "__main__":
    main()
