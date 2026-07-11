import json
from pathlib import Path

src = Path("workloads/generated/geo_2gpu100_kv_workload.jsonl")
out = Path("workloads/generated/geo_2gpu100_kv_workload_1to2_n50.jsonl")

target = {0: 17, 1: 33}
counts = {0: 0, 1: 0}

selected = []

with open(src, encoding="utf-8") as f:
    for line in f:
        row = json.loads(line)

        gpu = int(row["assigned_instance_id"])

        if counts[gpu] < target[gpu]:
            selected.append(row)
            counts[gpu] += 1

        if counts == target:
            break

selected.sort(key=lambda x: int(x["arrival_time_ns"]))

with open(out, "w", encoding="utf-8") as f:
    for i, row in enumerate(selected):
        row["request_id"] = i
        json.dump(row, f, ensure_ascii=False)
        f.write("\n")

print(counts)
print("total =", len(selected))
print("saved to", out)
