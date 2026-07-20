#!/usr/bin/env python3
"""Export auditable arithmetic traces for representative TTFT predictions."""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "analysis/post_routing/canonical_requests.csv"
MODEL = ROOT / "models/ttft_formula/ttft_formula.joblib"
OUTPUT_DIR = ROOT / "analysis/ttft_formula/examples"


def select_examples(dataset):
    rows = []
    specifications = [
        ("low_load", 4000, 0, 0.35, 10),
        ("cache_reuse", 6000, 2992, 0.60, 9),
        ("capacity_boundary", 6000, 0, 0.98, 5),
    ]
    for name, tokens, cache, pressure, admissible in specifications:
        candidates = dataset[
            (dataset.input_tokens == tokens)
            & (dataset.home_cached_prefix_tokens == cache)
        ].copy()
        candidates["selection_distance"] = (
            (candidates.router_initial_capacity_pressure - pressure).abs()
            + 0.15 * (
                candidates.router_initial_admissible_candidate_count - admissible
            ).abs()
        )
        row = candidates.sort_values("selection_distance").iloc[0].copy()
        row["case"] = name
        rows.append(row)
    overloaded = dataset[
        dataset.router_initial_admissible_candidate_count == 0
    ].sort_values("router_initial_capacity_pressure", ascending=False).iloc[0].copy()
    overloaded["case"] = "no_admissible_gpu"
    rows.append(overloaded)
    return pd.DataFrame(rows).reset_index(drop=True)


def transformed_contributions(model, transformed, key, case_names, feature_names):
    estimator = model[key]
    coefficients = estimator.coef_[0] if key == "route_event" else estimator.coef_
    intercept = (
        float(estimator.intercept_[0]) if key == "route_event"
        else float(estimator.intercept_)
    )
    rows = []
    for case_index, case in enumerate(case_names):
        rows.append({
            "case": case,
            "term": "intercept",
            "transformed_value": 1.0,
            "coefficient": intercept,
            "contribution": intercept,
        })
        for name, value, coefficient in zip(
            feature_names, transformed[case_index], coefficients
        ):
            rows.append({
                "case": case,
                "term": name,
                "transformed_value": float(value),
                "coefficient": float(coefficient),
                "contribution": float(value * coefficient),
            })
    return pd.DataFrame(rows)


def tail_tree_trace(model, transformed, case_names, feature_names):
    estimator = model["route_tail"]
    initial = float(np.ravel(estimator.init_.constant_)[0])
    rows = []
    for case_index, case in enumerate(case_names):
        vector = transformed[case_index:case_index + 1]
        for tree_index, estimator_row in enumerate(estimator.estimators_):
            tree_model = estimator_row[0]
            leaf_id = int(tree_model.apply(vector)[0])
            leaf_value = float(tree_model.tree_.value[leaf_id, 0, 0])
            path_nodes = tree_model.decision_path(vector).indices.tolist()
            conditions = []
            for node_id in path_nodes:
                feature_index = int(tree_model.tree_.feature[node_id])
                if feature_index < 0:
                    continue
                threshold = float(tree_model.tree_.threshold[node_id])
                value = float(vector[0, feature_index])
                operator = "<=" if value <= threshold else ">"
                conditions.append(
                    f"{feature_names[feature_index]}({value:.6g}) {operator} {threshold:.6g}"
                )
            rows.append({
                "case": case,
                "tree_index": tree_index,
                "initial_prediction": initial if tree_index == 0 else 0.0,
                "leaf_id": leaf_id,
                "leaf_value": leaf_value,
                "learning_rate": float(estimator.learning_rate),
                "weighted_leaf_contribution": float(
                    estimator.learning_rate * leaf_value
                ),
                "path": " AND ".join(conditions),
            })
    return pd.DataFrame(rows)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model = joblib.load(MODEL)
    dataset = pd.read_csv(DATASET)
    dataset = dataset[dataset.has_router_state == 1].copy()
    dataset["target_total_tokens"] = dataset.output_tokens_actual
    dataset["home_cached_prefix_tokens"] = dataset.nominal_reuse_tokens
    examples = select_examples(dataset)
    transformed = model["preprocessor"].transform(examples[model["features"]])
    feature_names = [
        name.split("__", 1)[1]
        for name in model["preprocessor"].get_feature_names_out()
    ]
    cases = examples["case"].tolist()

    logistic = transformed_contributions(
        model, transformed, "route_event", cases, feature_names
    )
    scheduler = transformed_contributions(
        model, transformed, "scheduler", cases, feature_names
    )
    tail = tail_tree_trace(model, transformed, cases, feature_names)

    logit = logistic.groupby("case").contribution.sum().reindex(cases)
    probability = 1 / (1 + np.exp(-logit.to_numpy()))
    tail_raw = (
        tail.groupby("case")[["initial_prediction", "weighted_leaf_contribution"]]
        .sum().sum(axis=1).reindex(cases)
    )
    tail_clipped = np.clip(tail_raw.to_numpy(), *model["route_log_bounds"])
    positive_route = np.expm1(tail_clipped)
    expected_route = probability * positive_route
    scheduler_raw = scheduler.groupby("case").contribution.sum().reindex(cases)
    scheduler_prediction = np.maximum(0, scheduler_raw.to_numpy())
    compute_intercept = float(model["compute"].intercept_)
    compute_input = examples.input_tokens.to_numpy() * model["compute"].coef_[0]
    compute_cache = (
        examples.home_cached_prefix_tokens.to_numpy() * model["compute"].coef_[1]
    )
    compute_raw = compute_intercept + compute_input + compute_cache
    compute_prediction = np.maximum(0, compute_raw)
    ttft = expected_route + scheduler_prediction + compute_prediction

    summary = examples[[
        "case", "input_tokens", "home_cached_prefix_tokens", "request_rate_rps",
        "policy", "router_initial_waiting_reqs", "router_initial_running_reqs",
        "router_initial_capacity_pressure", "router_initial_slot_pressure",
        "router_initial_admissible_candidate_count",
    ]].copy()
    summary["route_logit"] = logit.to_numpy()
    summary["route_probability"] = probability
    summary["tail_log_raw"] = tail_raw.to_numpy()
    summary["tail_log_clipped"] = tail_clipped
    summary["route_positive_ms"] = positive_route
    summary["route_expected_ms"] = expected_route
    summary["scheduler_raw_ms"] = scheduler_raw.to_numpy()
    summary["scheduler_ms"] = scheduler_prediction
    summary["compute_intercept_ms"] = compute_intercept
    summary["compute_input_contribution_ms"] = compute_input
    summary["compute_cache_contribution_ms"] = compute_cache
    summary["compute_ms"] = compute_prediction
    summary["communication_ms"] = 0.0
    summary["ttft_ms"] = ttft
    summary["observed_row_ttft_ms"] = examples.e2e_ttft_ms.to_numpy()

    summary.to_csv(OUTPUT_DIR / "example_summary.csv", index=False)
    logistic.to_csv(OUTPUT_DIR / "route_logistic_contributions.csv", index=False)
    tail.to_csv(OUTPUT_DIR / "route_tail_tree_trace.csv", index=False)
    scheduler.to_csv(OUTPUT_DIR / "scheduler_contributions.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
