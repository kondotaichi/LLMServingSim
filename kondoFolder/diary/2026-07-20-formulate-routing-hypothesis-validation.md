# 2026-07-20 Formulate routingの予測改善とtail原因検証

## 1. 本日の目的

Phase 1のcapacity-boundary実験が完了したため、その結果を使ってTTFT予測modelを再学習し、
`NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE`が誤ってrequestをlocal GPUに残す問題を改善することを
目的とした。

検証した主な問いは以下である。

1. Phase 1の追加dataでTTFT point predictionの精度を上げられるか。
2. 旧formulate版がなぜlocalを選んでいたのか。
3. 予測modelを改善すると実際のE2E TTFTも改善するか。
4. なぜ単純なKV handoffが依然として強いのか。
5. Formulate版のtailを生んでいるのは予測誤差か、routing state machineか。
6. `target_not_admissible`時の再評価とmulti-candidate化でtailを改善できるか。

## 2. Phase 1 dataの再集約

### 意図

旧TTFT formulaはPhase 1が未完了の時点の6,000 requests、7 scenariosで学習されていた。
完了したPhase 1の全runを取り込み、scenario-held-outで再評価した。

### 実施内容

- 45 runs
- 15 scenarios
- 13,500 requests
- Policy: `NEAREST_KV`、`NEAREST_MIGRATE`、`NEAREST_MIGRATE_KV`
- Input tokens、request rate、reuse ratio、seedの異なるcapacity regimeを含む

Post-routing datasetとTTFT formula artifactを再生成した。Bootstrap処理がPandas filteringを
内側loopで繰り返していたため非常に遅く、scenario meanのNumPy samplingへ変更した。

### 再学習結果

| Metric | 旧model | Phase 1再学習 |
|---|---:|---:|
| Router queue MAE | 989.6 ms | 630.2 ms |
| TTFT MAE | 1,067.5 ms | 716.1 ms |
| TTFT R² | 0.356 | 0.467 |
| Queue ROC-AUC | 0.9964 | 0.99995 |
| Queue PR-AUC | 0.9754 | 0.99971 |

Point predictionは改善したが、`input6000_rate5p0_reuse00_seed1`のような高負荷long-tailでは
依然として数秒の過小予測が残った。

## 3. 旧formulate版の誤local判断の原因

旧実験のrequest CSVを調べると、`local_within_margin_and_deadline`でlocalを選んだrequestが
9件あった。予測待ち時間は0.14〜0.64秒だったが、実際のRouter waitは1.4〜11.4秒だった。

当時の計算は以下であった。

```text
predicted local route wait
  = P(router wait occurs) * E(wait | wait occurs)
```

しかしformulate policyが呼ばれる時点で、Home GPUが収容不能であることはすでに観測済みである。
その条件下で待ち発生確率をもう一度掛けると、deadline判断に使う時間が系統的に小さくなる。

## 4. Point predictionとrouting判断用予測の分離

### 仮説

TTFTの中央値付近を当てるpoint modelと、false-localを避けるhard deadline判断は、同じ値を
使うべきではない。False-localは数秒〜数十秒の損失になる一方、不要なredirectの損失は通常
数十〜数百msであり、損失が非対称である。

### 変更

Scenario-held-outの正値router wait residualの90 percentileを使って、routing判断用の上側予測を追加した。

```text
route_upper_ms = route_positive_point_ms + 10,311.69 ms
```

- Point estimateは従来の診断列とTTFT推定用に保持
- Local/redirectと1秒deadlineの判断には上側予測を使用
- OOF positive-wait coverage: 89.96%
- OOFで実待ち1秒超を1秒以下と判定した件数: 0
- 旧実験の誤local 9件はすべてredirect判定に変化

この補正は強く保守的であり、requestごとの予測精度を上げたというより、誤localの損失を
避けるためのrisk boundである。

## 5. 実行環境での試行錯誤

Prompt 6000 / reuse 50% / 90sとInput 10000 / reuse 50% / 180sを2並列で実行しようとした。
既存結果を上書きしないよう、`PHASE1_MODEL20260720`という別result名を使った。

実行時に以下の問題が順に発生した。

1. ホスト環境に`python`がなく、`python3`へ変更。
2. Protobuf gencode 7.35.0とruntime 6.33.5のversion checkでconverter importが停止。
3. Protobufが提供する`TEMPORARILY_DISABLE_PROTOBUF_VERSION_CHECK=true`でserialize/deserializeの
   compatibility checkを行い、converter importを確認。
4. ホストmacOSからLinux向けASTRA-Sim binaryを起動し、`Exec format error`。
5. Repositoryの`llmservingsim-sim:local` Docker imageへ切り替え。
6. amd64 imageをarm64 Mac上でemulationしたため実行は通常より遅かったが、simulationは完了。

その後、Input 10000側はユーザ指示により途中で停止し、90s条件だけを先に解析した。

## 6. 90s workloadでの実性能検証

### 条件

- Prompt: 6,000 tokens
- Prefix reuse: 約50%
- 300 requests / 90s arrival window
- 10 × RTX 4090 model
- `max_num_seqs=128`
- `max_num_batched_tokens=2048`
- Redirect margin: 200 ms
- Local wait deadline: 1 s
- Target reservation: enabled

### 結果

| Policy | Mean | p50 | p95 | p99 | Redirects |
|---|---:|---:|---:|---:|---:|
| NEAREST_KV | 2,004.1 ms | 410.2 ms | 14,021.0 ms | 19,958.1 ms | 0 |
| NEAREST_MIGRATE | 518.5 ms | 412.1 ms | 917.9 ms | 2,369.1 ms | 24 |
| NEAREST_MIGRATE_KV | **487.1 ms** | 410.6 ms | **785.6 ms** | **1,338.6 ms** | 24 |
| 旧formulate | 646.3 ms | 410.2 ms | 931.7 ms | 7,865.9 ms | 16 |
| Phase 1 formulate | 542.1 ms | 409.8 ms | 904.7 ms | 2,085.3 ms | 20 |

Phase 1 formulateは旧formulateよりmeanで16.1%改善し、p99を7.87秒から2.09秒へ短縮した。
一方、単純な`NEAREST_MIGRATE_KV`よもmeanで55.0 ms遅く、tailも悪かった。

### 予測精度

Capacity判断が必要だった27 requestsで評価した。

| Target | Metric |
|---|---:|
| Local point TTFT vs paired NEAREST_KV | MAE 5,134.6 ms |
| Local point bias | -2,627.8 ms |
| Local upper prediction coverage | 81.5% |
| Redirect TTFT vs realized redirect | MAE 123.2 ms |
| Redirect bias | -54.1 ms |

Redirect予測は比較的良好だったが、local point predictionは90s long-tailをまだ大きく過小予測した。
性能改善の主因はpoint modelが十分正確になったことではなく、上側予測で危険なlocal選択を
回避したことだと判断した。

## 7. Phase 1 formulateのtail原因

Tail requestを個別に調べると、p99上余3件はすべて`target_not_admissible`経路だった。

| Request | Phase 1 formulate | KV handoff | Router wait | 予測local wait |
|---:|---:|---:|---:|---:|
| 161 | 7,039.8 ms | 727.5 ms | 6,591.5 ms | 19,207.5 ms |
| 175 | 4,897.7 ms | 1,434.3 ms | 4,456.2 ms | 18,704.1 ms |
| 126 | 4,643.4 ms | 1,060.2 ms | 4,212.4 ms | 11,719.5 ms |

この3件ではmodel自体は「localは危険」と正しく予測していた。しかしsecond-nearest GPUが
初回snapshotで収容不能だったため、one-shot state machineはrequestをlocalに固定した。その後targetが
空いても再評価されなかった。

```text
Home blocked + target blocked at first snapshot
  -> selected_route = local
  -> only Home is checked afterward
  -> several seconds of Router capacity wait
```

したがって、今回のtailの最大要因は予測誤差ではなく、初回判断を取り消せないone-shot制御だった。

### Input 10000 / reuse 50% / 180sでの追加確認

一旦停止したと考えていたInput 10000側も、停止指示の前にsimulation自体は300 requestsまで
完了し、CSVも保存されていた。そのため追加解析した。

| Policy | Mean | p95 | p99 | Redirects |
|---|---:|---:|---:|---:|
| NEAREST_KV | 3,046.3 ms | 18,954.1 ms | 25,183.2 ms | 0 |
| NEAREST_MIGRATE | 967.5 ms | 1,907.9 ms | 4,711.8 ms | 33 |
| NEAREST_MIGRATE_KV | **874.4 ms** | 1,394.8 ms | **2,639.3 ms** | 28 |
| 旧formulate | 1,104.8 ms | 1,716.3 ms | 10,739.8 ms | 20 |
| Phase 1 formulate | 881.4 ms | **1,375.5 ms** | 3,328.7 ms | 26 |

Phase 1 formulateは旧formulateよりmeanで20.2%改善し、単純KV handoffとのmean差は+7.0 msまで
縮まった。p95はKV handoffも19.4 ms良かったが、p99は689.4 ms悪かった。

Capacity判断36件に対し、local point prediction MAEは7,761.8 ms、biasは-6,749.6 ms、上側予測の
coverageは72.2%だった。Redirect prediction MAEは115.1 ms、biasは-4.8 msであり、90s条件と同じく
redirect側は良好、local long-tailは過小予測という構造だった。

P99上余3件は全て`target_not_admissible`であり、Router waitは5.61秒、3.24秒、3.06秒だった。
よって90sで見つけたone-shot固定のtail問題はInput 10000 / 180sでも再現した。

## 8. 単純KV handoffが強い理由

`NEAREST_MIGRATE_KV`はHomeが収容不能ならtargetを再確認し続け、targetが空いた時点でhandoffできる。
今回の条件では、不要なKV transferの損失よも誤localで数秒待つ損失の方がはるかに大きい。
そのため、modelを使わない単純な動的handoff ruleが最も良い結果になった。

## 9. Dynamic再評価とMulti-candidate化

Tail原因を受け、以下の仮説を追加検証することにした。

### Variant A: Dynamic

Policy: `NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE`

- `target_not_admissible`時にlocalを確定しない
- `undecided`状態でHomeとsecond-nearest targetを再評価
- Homeが先に空けばlocal
- Targetが先に空き、予測上redirectが有利またはdeadline超過ならKV handoff
- Redirect決定時にtarget KV/slotをatomic reservation

### Variant B: Multi-candidate

Policy: `NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE`

- Dynamicの全動作を含む
- Second-nearestに限定せず、Home以外の全prefill GPUのcapacityを評価
- 収容可能なGPUのうち、予測redirect TTFTが最小の候補を選択
- 今回の固定APN propagation条件に限定。Workloadに3番目以降の実距離がないため、
  distance-proportional networkでは使用しない

### テスト

Fake scheduler/memoryを使うunit testを追加した。

1. Homeとtargetの両方がblockedならrouteを確定しない。
2. 次の再評価でtargetが空いたらKV handoffする。
3. Multi-candidateはsecond-nearestがblockedでも3番目のGPUが空いていればそちらを選ぶ。
4. 既存one-shotとformula evaluatorのregression testを維持する。

10 testsはすべて通過した。

## 10. Dynamic / Multi-candidateの90s simulation

DynamicとMulti-candidateを同じ90s workloadで2並列実行した。

<!-- DYNAMIC_RESULTS_START -->
| Policy | Mean | p95 | p99 | Redirects | Mean Router wait | Max Router wait |
|---|---:|---:|---:|---:|---:|---:|
| 単純KV handoff | 487.1 ms | 785.6 ms | 1,338.6 ms | 24 | 5.3 ms | 687.5 ms |
| Current formulate | 542.1 ms | 904.7 ms | 2,085.3 ms | 20 | 63.9 ms | 6,591.5 ms |
| Dynamic | 487.1 ms | 785.6 ms | 1,338.6 ms | 24 | 5.1 ms | 689.1 ms |
| Multi-candidate | **471.7 ms** | **747.8 ms** | **932.6 ms** | 18 | **0 ms** | **0 ms** |

Dynamicは単純KV handoffとmean、p50、p95、p99が完全に一致した。`target_not_admissible`にlocalを
固定せず、second-nearestが空いた時点でhandoffすることで、Current formulateの6.59秒のtailを解消した。
1 requestは待機中にHomeが先に収容可能となり、localで処理された。

Multi-candidateは全requestのRouter waitを0にし、redirect数も18件へ減らした。Redirect先は9 GPUへ
分散し、単純KV handoffに対してmeanを3.2%、p99を30.3%改善した。この結果は、以下の3仮説を支持する。

1. `target_not_admissible`時のlocal固定は有害だった。
2. Homeとtargetの動的再評価で単純KV handoffのtail耐性を回復できた。
3. Second-nearestに限定せず収容可能なGPUを探すと、待ちとredirect先集中をさらに減らせた。
<!-- DYNAMIC_RESULTS_END -->

## 11. 現時点の結論

1. Phase 1全dataでpoint predictionは改善した。
2. False-localは確率重み付き平均でdeadlineを判定したことで発生していた。
3. 上側予測で旧誤localは解消し、旧formulateよも実性能が改善した。
4. それでも単純KV handoffが最も良かった。
5. Phase 1 formulateの残ったtailは、modelが危険を見抜けなかったのではなく、
   `target_not_admissible`をlocalに固定するstate machineによって発生した。
6. Dynamicは単純KV handoffと同等まで回復し、Multi-candidateはそれを上回った。
7. 現時点の最有力はMulti-candidateだが、固定APN条件の1 workloadであり、seedと負荷条件の追加検証が必要である。

## 12. 主な成果物

- `experiments/2026-07-16_ttft_component_regression/analysis/ttft_formula/`
- `experiments/2026-07-16_ttft_component_regression/reports/ttft_formula.md`
- `experiments/2026-07-14_prompt6000_90s_three_policy_良結果/results/NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE_M200MS_D1S_PHASE1_MODEL20260720/`
- `experiments/2026-07-14_prompt6000_90s_three_policy_良結果/reports/06_formula_phase1_model_analysis.md`
- `experiments/2026-07-14_prompt6000_90s_three_policy_良結果/figures/formula_phase1_model/`
- `experiments/2026-07-14_prompt6000_90s_three_policy_良結果/scripts/analyze_formula_phase1_model.py`
- `serving/core/ttft_formula.py`
- `serving/core/router.py`
- `tests/test_second_ttft_reserve_router.py`
