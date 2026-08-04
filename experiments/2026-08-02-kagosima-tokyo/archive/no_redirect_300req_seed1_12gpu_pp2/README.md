# No-redirect baseline archive

This archive contains the three completed heavy-load runs with 300 requests,
seed 1, 12 physical GPUs, and PP=2 (6 logical instances).

## Arms

- `all_tokyo`: 12 Tokyo GPUs, proactive KV prewarm enabled
- `kg_pp2`: 6 Tokyo GPUs and 6 Kagoshima GPUs
- `kg_pp2_spec`: 6 Tokyo GPUs and 6 Kagoshima GPUs, proactive KV prewarm enabled

## Interpretation

All three runs recorded zero rerouted requests. The two Kagoshima-Tokyo runs
produced identical request-level results, with zero proactive KV prewarm hits
and zero speculative KV activity. These runs therefore serve as no-redirect
baselines and do not evaluate the proposed proactive KV prewarm method.

The archived summary reported mean TTFTs of 1851.8 ms for All-Tokyo and
1754.2 ms for both Kagoshima-Tokyo arms. The estimated electricity cost was
0.7234 yen for All-Tokyo and 0.5961 yen for both Kagoshima-Tokyo arms. These
figures should not be interpreted as evidence of a prewarm benefit.
