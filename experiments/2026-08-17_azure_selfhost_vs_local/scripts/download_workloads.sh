#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 https://account.blob.core.windows.net/container/prefix" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
experiment_dir="$repo_root/experiments/2026-08-17_azure_selfhost_vs_local"
manifest="$experiment_dir/workloads/manifests/hongo_seed1.csv"
destination="$experiment_dir/workloads/hongo"
blob_prefix="${1%/}"

command -v azcopy >/dev/null || {
  echo "AzCopy is required" >&2
  exit 1
}

mkdir -p "$destination"
while IFS=, read -r load filename source_path object_key requests unique_sessions return_visit_rate target_rate_rps timeline_s size_bytes sha256; do
  if [[ "$load" == "load" ]]; then
    continue
  fi
  azcopy copy "$blob_prefix/$object_key" "$destination/$filename"
done < "$manifest"

python3 "$experiment_dir/scripts/validate_workloads.py" \
  --manifest "$manifest" \
  --directory "$destination"
echo "Downloaded and verified Hongo workloads in $destination"
