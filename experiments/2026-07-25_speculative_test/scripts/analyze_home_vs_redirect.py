#!/usr/bin/env python3
"""Home-vs-redirect decision accuracy for speculative KV migration.

For each of the 18 baseline redirect decisions
(experiments/2026-07-21-add_gpu_utilization), this compares:

  - actual_redirect_ttft_ms: what really happened (the request was
    redirected, per the baseline five-policy run)
  - actual_home_ttft_ms: what would have happened had the router instead
    waited at the home GPU (from the new
    --counterfactual-force-local-request-id simulations, one per request)
  - predicted_local_ttft_ms: what the router's OWN formula predicted the
    local wait would be at decision time (oneshot_predicted_local_ttft_ns,
    already logged in the baseline run -- this is the number that actually
    drove the redirect decision)

This directly answers the question the ranking-only evaluation
(2026-07-25_speculative_test/reports/report.md section 1) could not:
was redirecting actually the right call, and was the router's local-wait
prediction (not just the redirect-target prediction) trustworthy?
"""

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
THIS_DIR = Path(__file__).resolve().parents[1]
BASELINE_REQUESTS = (
    REPO_ROOT / "experiments/2026-07-21-add_gpu_utilization/results/"
    "NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE/requests.csv"
)
ANALYSIS = THIS_DIR / "analysis"
ANALYSIS.mkdir(parents=True, exist_ok=True)

REQUEST_IDS = [49, 51, 67, 82, 84, 90, 154, 156, 161, 165,
               173, 175, 197, 203, 205, 263, 265, 267]


def main():
    baseline = pd.read_csv(BASELINE_REQUESTS)

    rows = []
    for rid in REQUEST_IDS:
        base_row = baseline[baseline["request id"] == rid].iloc[0]
        force_local_path = THIS_DIR / "results" / f"request{rid}_force_local" / "requests.csv"
        force_local = pd.read_csv(force_local_path)
        home_row = force_local[force_local["request id"] == rid].iloc[0]

        if int(home_row.rerouted) != 0:
            raise RuntimeError(
                f"request {rid}: force-local run still shows rerouted=1 "
                f"(gpu_id={home_row.gpu_id}, nearest_gpu_id={home_row.nearest_gpu_id}); "
                "counterfactual_force_local_request_id did not take effect."
            )

        actual_redirect_ttft_ms = base_row.e2e_ttft_ns / 1e6
        actual_home_ttft_ms = home_row.e2e_ttft_ns / 1e6
        predicted_local_ttft_ms = base_row.oneshot_predicted_local_ttft_ns / 1e6
        predicted_redirect_ttft_ms = base_row.oneshot_predicted_redirect_ttft_ns / 1e6

        rows.append(dict(
            request_id=rid,
            home_gpu_id=int(home_row.gpu_id),
            redirect_gpu_id=int(base_row.gpu_id),
            actual_home_ttft_ms=actual_home_ttft_ms,
            actual_redirect_ttft_ms=actual_redirect_ttft_ms,
            # Positive = redirecting was actually the right call (home would
            # have been slower); negative = redirecting made it worse.
            redirect_benefit_ms=actual_home_ttft_ms - actual_redirect_ttft_ms,
            redirect_was_correct_decision=bool(
                actual_redirect_ttft_ms <= actual_home_ttft_ms
            ),
            predicted_local_ttft_ms=predicted_local_ttft_ms,
            predicted_redirect_ttft_ms=predicted_redirect_ttft_ms,
            # How wrong was the router's own local-wait prediction, in the
            # direction that matters: predicting local is far worse than it
            # actually would have been is what causes an unnecessary redirect.
            local_prediction_error_ms=predicted_local_ttft_ms - actual_home_ttft_ms,
            oneshot_decision_reason=base_row.oneshot_decision_reason,
        ))

    detail = pd.DataFrame(rows).sort_values("request_id")
    detail.to_csv(ANALYSIS / "home_vs_redirect_by_request.csv", index=False)

    n = len(detail)
    n_correct = detail.redirect_was_correct_decision.sum()
    print(f"n={n} redirect decisions")
    print(f"Redirecting was actually the better (or equal) choice: {n_correct}/{n} "
          f"({100 * n_correct / n:.1f}%)")
    print(f"Redirecting made things WORSE than waiting at home: {n - n_correct}/{n} "
          f"({100 * (n - n_correct) / n:.1f}%)")
    print()
    print("Redirect benefit (actual_home - actual_redirect), ms:")
    print(detail.redirect_benefit_ms.describe().to_string())
    print()
    print("Router's local-wait prediction error (predicted_local - actual_home), ms:")
    print(detail.local_prediction_error_ms.describe().to_string())
    print()

    cols = ["request_id", "actual_home_ttft_ms", "actual_redirect_ttft_ms",
            "redirect_benefit_ms", "redirect_was_correct_decision",
            "predicted_local_ttft_ms", "local_prediction_error_ms",
            "oneshot_decision_reason"]
    print(detail[cols].to_string(index=False))

    summary = dict(
        n=n,
        redirect_correct_count=int(n_correct),
        redirect_correct_rate=n_correct / n,
        redirect_wrong_count=int(n - n_correct),
        mean_redirect_benefit_ms=float(detail.redirect_benefit_ms.mean()),
        median_redirect_benefit_ms=float(detail.redirect_benefit_ms.median()),
        worst_redirect_harm_ms=float(-detail.redirect_benefit_ms.min())
        if (detail.redirect_benefit_ms < 0).any() else 0.0,
        mean_local_prediction_error_ms=float(detail.local_prediction_error_ms.mean()),
        median_local_prediction_error_ms=float(detail.local_prediction_error_ms.median()),
    )
    pd.Series(summary).to_csv(ANALYSIS / "home_vs_redirect_summary.csv")
    print()
    print("Summary:", summary)


if __name__ == "__main__":
    main()
