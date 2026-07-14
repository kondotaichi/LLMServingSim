#!/usr/bin/env python3
"""Summarize home-cell to destination-GPU routing flows for one experiment."""

import csv
from collections import Counter
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
POLICIES = ["NEAREST_KV", "NEAREST_MIGRATE", "NEAREST_MIGRATE_KV"]


def read_rows(policy):
    path = EXPERIMENT_DIR / "results" / policy / "requests.csv"
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def as_gpu_id(value):
    return int(float(value))


def main():
    rows_by_policy = {policy: read_rows(policy) for policy in POLICIES}
    home_ids = sorted({
        as_gpu_id(row["nearest_gpu_id"])
        for rows in rows_by_policy.values()
        for row in rows
    })

    flow_output = EXPERIMENT_DIR / "analysis" / "routing_flows.csv"
    table_output = EXPERIMENT_DIR / "analysis" / "routing_flows.md"
    flow_output.parent.mkdir(parents=True, exist_ok=True)

    summaries = {}
    with flow_output.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            "policy", "home_gpu_id", "destination_gpu_id", "redirected",
            "request_count", "home_request_count", "share_of_home_requests",
        ])
        for policy in POLICIES:
            rows = rows_by_policy[policy]
            for home in home_ids:
                home_rows = [
                    row for row in rows
                    if as_gpu_id(row["nearest_gpu_id"]) == home
                ]
                destinations = Counter(as_gpu_id(row["gpu_id"]) for row in home_rows)
                redirected = sum(
                    count for destination, count in destinations.items()
                    if destination != home
                )
                summaries[(policy, home)] = (destinations, len(home_rows), redirected)
                for destination, count in sorted(destinations.items()):
                    writer.writerow([
                        policy, home, destination, int(destination != home), count,
                        len(home_rows), f"{count / len(home_rows):.6f}",
                    ])

    if "120s" in EXPERIMENT_DIR.name:
        title_window = "120 seconds"
    elif "90s" in EXPERIMENT_DIR.name:
        title_window = "90 seconds"
    else:
        title_window = "60 seconds"
    lines = [
        f"# Routing flow summary ({title_window})",
        "",
        "Home GPU is nearest_gpu_id; destinations are the actual gpu_id values.",
        "A destination equal to the home GPU means that the request was not redirected.",
        "",
        "| Home GPU | Requests | A destinations | A redirect | B destinations | B redirect | C destinations | C redirect |",
        "|---:|---:|---|---:|---|---:|---|---:|",
    ]
    for home in home_ids:
        cells = [str(home)]
        request_count = summaries[(POLICIES[0], home)][1]
        cells.append(str(request_count))
        for policy in POLICIES:
            destinations, total, redirected = summaries[(policy, home)]
            destination_text = ", ".join(
                f"GPU {destination}: {count}"
                for destination, count in sorted(destinations.items())
            )
            cells.extend([
                destination_text,
                f"{redirected}/{total} ({redirected / total:.1%})",
            ])
        lines.append("| " + " | ".join(cells) + " |")

    lines.extend([
        "",
        "## Policy totals",
        "",
        "| Policy | Requests | Redirected | Redirect rate |",
        "|---|---:|---:|---:|",
    ])
    for policy in POLICIES:
        rows = rows_by_policy[policy]
        redirected = sum(
            as_gpu_id(row["nearest_gpu_id"]) != as_gpu_id(row["gpu_id"])
            for row in rows
        )
        lines.append(
            f"| {policy} | {len(rows)} | {redirected} | {redirected / len(rows):.1%} |"
        )
    table_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(table_output)
    print(flow_output)


if __name__ == "__main__":
    main()
