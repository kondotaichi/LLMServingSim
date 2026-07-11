"""Build a user_frequency.csv giving an exact 2:1 request-probability split
by nearest-GPU assignment (GPU0 : GPU1), from a dry-run geographic users CSV.

Usage:
    python3 make_user_frequency.py <dryrun_users.csv> <output_freq.csv> [--ratio 2:1]

Each user in the "closer to GPU0" group gets weight (ratio_num / n0), each
user in the "closer to GPU1" group gets weight (ratio_den / n1), so that
sum(group0) : sum(group1) == ratio_num : ratio_den exactly, regardless of
how many users happen to fall in each group.
"""
import argparse
import csv
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("users_csv")
    p.add_argument("output_csv")
    p.add_argument("--ratio", default="2:1", help="GPU0:GPU1 request-probability ratio")
    p.add_argument("--gpu0-id", type=int, default=0)
    p.add_argument("--gpu1-id", type=int, default=1)
    args = p.parse_args()

    ratio_num, ratio_den = (float(x) for x in args.ratio.split(":"))

    rows = list(csv.DictReader(open(args.users_csv)))
    group0 = [r for r in rows if int(r["assigned_gpu_id"]) == args.gpu0_id]
    group1 = [r for r in rows if int(r["assigned_gpu_id"]) == args.gpu1_id]
    if not group0 or not group1:
        raise ValueError(
            f"Need at least one user nearest to each GPU to build a {args.ratio} split "
            f"(got {len(group0)} nearest GPU{args.gpu0_id}, {len(group1)} nearest GPU{args.gpu1_id})."
        )

    w0 = ratio_num / len(group0)
    w1 = ratio_den / len(group1)

    out_path = Path(args.output_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "request_weight"])
        for r in rows:
            gid = int(r["assigned_gpu_id"])
            w = w0 if gid == args.gpu0_id else w1
            writer.writerow([r["user_id"], w])

    print(f"GPU{args.gpu0_id}-nearest users: {len(group0)} (weight {w0:.6f} each, "
          f"group sum {w0 * len(group0):.4f})")
    print(f"GPU{args.gpu1_id}-nearest users: {len(group1)} (weight {w1:.6f} each, "
          f"group sum {w1 * len(group1):.4f})")
    print(f"Wrote {len(rows)} rows -> {out_path}")


if __name__ == "__main__":
    main()
