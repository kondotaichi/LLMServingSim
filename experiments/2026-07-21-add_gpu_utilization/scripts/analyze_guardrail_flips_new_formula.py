#!/usr/bin/env python3
"""Flip-sensitivity check for guardrail_new_formula vs pressure, mirroring
analyze_guardrail_flips.py's method but on the item-13 log1p formula
(evaluate_guardrail_new_formula.py output). Every prior "X beats pressure"
result in MODEL_ITERATION_HISTORY.md (stage2_expanded at n=5/6, guardrail at
n=18 on the old formula) turned out to hinge on 1-3 folds, so this check is
run before trusting the new-formula guardrail number.
"""

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "analysis" / "guardrail_new_formula_by_request.csv"
ACTUALS = ROOT / "analysis" / "counterfactual_candidate_actuals_new_formula.csv"
ANALYSIS = ROOT / "analysis"


def main():
    detail = pd.read_csv(DETAIL)
    actuals = pd.read_csv(ACTUALS)
    complete = actuals[actuals.counterfactual_complete]

    pressure = detail[detail.variant == "pressure"].set_index("request_id")
    guard = detail[detail.variant == "guardrail_new_formula"].set_index("request_id")
    formula = detail[detail.variant == "formula_new_formula_only"].set_index("request_id")

    rows = []
    for rid in pressure.index:
        group = complete[complete.request_id == rid].sort_values("candidate_capacity_pressure")
        top2 = group.head(2)
        pressure_top2_gap = float(
            top2.candidate_capacity_pressure.iloc[1] - top2.candidate_capacity_pressure.iloc[0]
        )
        lam = guard.loc[rid, "lambda_used"]
        rows.append(dict(
            request_id=rid,
            flipped=bool(pressure.loc[rid, "chosen_gpu"] != guard.loc[rid, "chosen_gpu"]),
            pressure_gpu=int(pressure.loc[rid, "chosen_gpu"]),
            guardrail_gpu=int(guard.loc[rid, "chosen_gpu"]),
            formula_gpu=int(formula.loc[rid, "chosen_gpu"]),
            lambda_used=lam,
            pressure_top2_gap=pressure_top2_gap,
            lambda_x_gap_ms=lam * pressure_top2_gap,
            pressure_regret_ms=pressure.loc[rid, "regret_ms"],
            guardrail_regret_ms=guard.loc[rid, "regret_ms"],
            formula_regret_ms=formula.loc[rid, "regret_ms"],
            regret_delta_guardrail_minus_pressure=(
                guard.loc[rid, "regret_ms"] - pressure.loc[rid, "regret_ms"]
            ),
        ))
    flips = pd.DataFrame(rows).sort_values(
        "regret_delta_guardrail_minus_pressure"
    )
    flips.to_csv(ANALYSIS / "guardrail_flip_analysis_new_formula.csv", index=False)

    print(flips[[
        "request_id", "flipped", "pressure_gpu", "guardrail_gpu", "formula_gpu",
        "pressure_top2_gap", "lambda_x_gap_ms", "regret_delta_guardrail_minus_pressure",
    ]].to_string(index=False))

    flipped_ids = flips[flips.flipped].request_id.tolist()
    print(f"\nFlipped requests: {flipped_ids}")

    def summarize(exclude):
        p = pressure.drop(index=exclude, errors="ignore")
        g = guard.drop(index=exclude, errors="ignore")
        return dict(
            n=len(p),
            pressure_mean_regret_ms=p.regret_ms.mean(),
            guardrail_mean_regret_ms=g.regret_ms.mean(),
            pressure_top1=p.top1_correct.mean(),
            guardrail_top1=g.top1_correct.mean(),
        )

    print("\nSensitivity: aggregate metrics as flipped requests are excluded one at a time")
    excluded = []
    sensitivity_rows = [dict(excluded="(none)", **summarize([]))]
    for rid in flipped_ids:
        excluded.append(rid)
        sensitivity_rows.append(dict(excluded=str(excluded), **summarize(list(excluded))))
    sensitivity = pd.DataFrame(sensitivity_rows)
    sensitivity.to_csv(ANALYSIS / "guardrail_flip_sensitivity_new_formula.csv", index=False)
    print(sensitivity.to_string(index=False))


if __name__ == "__main__":
    main()
