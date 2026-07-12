"""CLI dispatch for workload generators.

Usage:
    python -m workloads.generators sharegpt --model <hf-id> --num-reqs 300 --sps 10 \
        --source <path-or-hf-id> --output workloads/sharegpt-<model>-<n>-sps<r>.jsonl
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="workloads.generators")
    sub = parser.add_subparsers(dest="generator", required=True)

    sg = sub.add_parser("sharegpt", help="ShareGPT -> LLMServingSim JSONL")
    from workloads.generators.sharegpt import register_args as sg_register
    sg_register(sg)

    bg = sub.add_parser(
        "burstgpt",
        help="BurstGPT weekly_minute_trace.csv -> LLMServingSim JSONL",
    )
    from workloads.generators.burstgpt import register_args as bg_register
    bg_register(bg)

    gg = sub.add_parser(
        "geographic",
        help="Existing JSONL -> geographically-distributed UE/GPU workload (Phase 1)",
    )
    from workloads.generators.geographic import register_args as gg_register
    gg_register(gg)

    rr = sub.add_parser(
        "regional-ratio",
        help="Flat JSONL -> regional workload using 10-minute load ratios",
    )
    from workloads.generators.regional_ratio import register_args as rr_register
    rr_register(rr)

    ca = sub.add_parser(
        "cell-apn",
        help="regional-ratio output -> fixed 3-4-3 GPU grid + Voronoi-stratified users + KV reuse (10cell_apn spec)",
    )
    from workloads.generators.cell_apn import register_args as ca_register
    ca_register(ca)

    args = parser.parse_args()

    if args.generator == "sharegpt":
        from workloads.generators.sharegpt import run
        return run(args)

    if args.generator == "burstgpt":
        from workloads.generators.burstgpt import run
        return run(args)

    if args.generator == "geographic":
        from workloads.generators.geographic import run
        return run(args)

    if args.generator == "regional-ratio":
        from workloads.generators.regional_ratio import run
        return run(args)

    if args.generator == "cell-apn":
        from workloads.generators.cell_apn import run
        return run(args)

    parser.error(f"Unknown generator: {args.generator}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
