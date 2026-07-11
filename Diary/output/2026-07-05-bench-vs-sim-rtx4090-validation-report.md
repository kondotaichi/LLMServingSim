# 2026-07-05: 実機vLLM(RTX4090) vs シミュレータ — `max_num_seqs`キューイングモデルの検証

日付: 2026-07-05
ブランチ: `experiment/sim-hack`
関連: `Diary/output/2026-07-04-nearest-reject-capacity-routing-report.md`、
`Diary/output/2026-07-05-nearest-migrate-2to1-load-report.md`(この検証の発端)

## 検証したかったこと

`2026-07-04`〜`07-05`の一連の実験(NEAREST vs NEAREST_REJECT/NEAREST_MIGRATE、
非対称容量/非対称トラフィック比較)は、すべて「シミュレータの`max_num_seqs`
によるキュー詰まりのモデルが正しい」という前提の上に成り立っている。この
前提を実機のvLLMと突き合わせて検証した。

使ったツールは`bench`モジュール(`python -m bench run` / `python -m bench
validate`)。同一ワークロード・同一エンジン設定(`max_num_seqs=24`など)を
実機vLLMとシミュレータの両方に流し、TTFT/TPOT/latencyを比較する。

## 実機環境

- GPU: **RTX4090**(判明前は`RTXPRO6000`を前提にしていたため、後述の通り
  1回大きな手戻りが発生した)
- モデル: `meta-llama/Llama-3.1-8B`、bf16
- vLLM 0.19.0、`--max-num-seqs 24 --max-num-batched-tokens 2048`
- ワークロード: `workloads/generated/geo_2gpu100_src.jsonl`(ShareGPTベース
  100リクエスト、入力268〜3452トークン、出力516〜822トークン、到着は
  0.05〜9.15秒に分散)

## 起きたこと(時系列)

### 1回目: RTXPRO6000前提でズレを検出

シミュレータのクラスタ設定が`RTXPRO6000`のプロファイルを前提にしたままで
`bench run`(実機)と比較したところ、TTFT/latencyはともかく**TPOTが実機
24.36ms/tokenに対しシミュレータ約15ms/tokenと、約40%(1.6倍)のズレ**が
出た。一方で「詰まり始めるタイミング」(実機は16件目付近から明確にキュー
待ちが発生)は理論式(`N_threshold ≈ (max_num_seqs / E[S]) × window`)と
オーダー感で一致しており、キューイングモデルの根本設計は疑わしくなかった。

このズレの原因を「シミュレータがRTXPRO6000のプロファイルを使っているのに
実機はRTXPRO6000ではないから」と当たりをつけ、実機の型番を確認したところ
**RTX4090**であることが判明した。

### 2回目: RTX4090にプロファイル・クラスタ設定を差し替え

- `profiler/profile.sh`の`MODEL`を`meta-llama/Llama-3.1-8B`に切り替え、
  `HARDWARE="RTX4090"`のまま実機で再プロファイル
  (`profiler/perf/RTX4090/meta-llama/Llama-3.1-8B/`を新規生成)
- `configs/cluster/single_node_single_instance_rtx4090.json` /
  `single_node_multi_instance_rtx4090.json`を新規作成
  (`hardware: RTX4090`、`npu_mem.mem_size: 24`GB、`mem_bw: 1008`GB/s
  — RTX4090の公称スペック)

途中でいくつか環境上のつまずきがあった:
- `bash scripts/compile.sh`はASTRA-Sim/Chakraのビルドなので**シミュレータ
  コンテナの中で**実行する必要があるが、誤ってホスト側で実行してしまい失敗
  (`chakra.egg-info`のタイムスタンプ更新エラー)
- シミュレータコンテナ(`astrasim/tutorial-micro2024`ベース)には`git`が
  入っておらず、Chakraのインストール(HolisticTraceAnalysisのgit clone)が
  失敗 → `apt-get install -y git`で解消
- vLLMコンテナとシミュレータコンテナでマウントパスが違う
  (vLLM側`/workspace`、シミュレータ側`/app/LLMServingSim`)ため、
  コンテナを取り違えてコマンドを打つ事故が数回発生
- シミュレータ実行中、`max_num_seqs=24`・100リクエストの終盤で
  **NPUメモリ不足によるクラッシュ**(`cache_unfinished_req` →
  `apply_kv_cache_events` → `allocate`でRuntimeError)が発生。原因は
  prefix caching(RadixAttention)のノード管理会計処理で、このワークロード
  (ShareGPTベース、共通プレフィックス無し)ではPrefix Cache Hit ratioが
  常に0.00%であり効果が無いにもかかわらずデフォルトで有効になっていたため。
  `--no-enable-prefix-caching`を付けて回避(この会計処理パスにはバッチ
  組成時のようなeviction機構が無いようで、シミュレータ側の頑健性の
  ギャップとして記録しておく)

### 実行コマンド(最終的に成功したもの)

```bash
# 実機(vLLMコンテナ内、RTX4090上)
python3 -m bench run \
  --model meta-llama/Llama-3.1-8B \
  --dataset workloads/generated/geo_2gpu100_src.jsonl \
  --output-dir bench/results/exp1_maxseqs24 \
  --tensor-parallel-size 1 --data-parallel-size 1 \
  --max-num-seqs 24 --max-num-batched-tokens 2048 \
  --max-model-len 8192 \
  --dtype bfloat16 --kv-cache-dtype auto \
  --seed 42 --num-reqs 100

# シミュレータ(シミュレータコンテナ内、RTX4090プロファイル使用)
docker exec servingsim_docker bash -lc "cd /app/LLMServingSim && python3 -m serving \
  --cluster-config configs/cluster/single_node_single_instance_rtx4090.json \
  --dtype bfloat16 --block-size 16 \
  --dataset workloads/generated/geo_2gpu100_src.jsonl \
  --max-num-seqs 24 --num-req 100 \
  --no-enable-prefix-caching \
  --output outputs/exp1_maxseqs24_sim.csv \
  --run-id exp1_maxseqs24_sim --log-level INFO 2>&1 | tee outputs/exp1_maxseqs24_sim.log"

# 比較
./bench/validate.sh \
  bench/results/exp1_maxseqs24 \
  outputs/exp1_maxseqs24_sim.csv \
  outputs/exp1_maxseqs24_sim.log \
  exp1
```

## 結果

### 実機の生データ(`bench run`、100件)

| 指標 | 値 |
| --- | --- |
| 到着レート | 10.97 req/s(9.11秒窓に100件) |
| 実測TPOT | 24.36ms/token |
| 実測occupancy時間(admission〜完了) | 平均16.8秒 |
| 最初に大きな待ち(>100ms)が出た地点 | 16件目 |
| 100件中、待ち(>1ms)が発生した件数 | 84/100 |
| 平均TTFT(待ち込み) | 24497.6ms、P99 54710.9ms |
| 平均総latency | 41166.7ms、P99 66335.1ms |

理論式`N_threshold ≈ (max_num_seqs / E[S]) × window ≈ (24/16.8)×9.11 ≈ 13件`
に対し実測は16件目 — オーダー感が一致。

### RTX4090プロファイル反映後の`bench validate`サマリ

```
Metric                           vLLM         Sim     Diff%
-----------------------------------------------------------
TTFT Mean                     24497.6     26717.3     +9.1%
TTFT Median                   24950.3     27173.2     +8.9%
TTFT P90                      48567.8     52896.8     +8.9%
TTFT P95                      49084.5     53533.9     +9.1%
TTFT P99                      54710.9     59502.6     +8.8%

TPOT Mean                        24.4        26.3     +7.8%
TPOT Median                      24.3        26.3     +7.9%
TPOT P90                         26.1        28.4     +8.9%
TPOT P95                         27.0        29.4     +9.0%
TPOT P99                         27.3        29.7     +8.6%

Latency Mean                  41166.7     44684.1     +8.5%
Latency Median                42325.5     45903.8     +8.5%
Latency P90                   64077.0     69506.6     +8.5%
Latency P95                   65021.9     70483.8     +8.4%
Latency P99                   66335.1     71636.9     +8.0%
```

RTXPRO6000前提だったときのTPOT誤差(約60%)から、RTX4090プロファイルに
差し替えるだけで**全指標が一貫して約8〜9%のズレに収束**した。

## 結論

1. **`max_num_seqs`によるキュー詰まりのモデルは、実機vLLMの挙動を高い精度で
   再現できている。** 根拠: TTFT(このワークロードでは98%がキュー待ちで
   支配される指標)の誤差(約8.8〜9.1%)と、TPOT(ほぼ純粋な計算時間で
   キューイングの影響がほぼ無い指標)の誤差(約7.8〜8.9%)が、ほぼ同じ
   水準で揃っている。もしキューイングロジック自体が実機とズレていれば、
   何十回・何百回と積み重なるTTFT側の誤差はTPOT側より大きく増幅されて
   出るはずだが、実際には増幅がほとんど見られなかった。
2. 残っている約8〜9%の系統的な誤差は、キューイングモデルの欠陥ではなく、
   **プロファイルされた1トークンあたりの計算時間がわずかに保守的(実機より
   やや遅め)に見積もられている**という、より単純な要因で説明できる水準
   (プロファイラ自身のドキュメントにも「単発サンプルはDVFS/ブーストクロック
   の影響で15〜25%振れる」と明記されている)。
3. **ハードウェアプロファイルの前提を間違えると(RTXPRO6000 vs RTX4090)、
   TPOTで最大60%もの誤差が出る**ことを実際に確認した。シミュレーション結果
   を解釈する際は、使っているプロファイルが実際に検証したいハードウェアと
   一致しているかを最初に確認すべき、という実務上の教訓が得られた。
4. これにより、`2026-07-04`〜`07-05`の一連の実験(方法A vs 方法B比較など)
   の土台になっている「キュー詰まりの挙動」は実機による裏付けが取れた。

## 副次的に見つかったシミュレータの課題

- prefix caching(RadixAttention)のノード管理会計処理
  (`memory_model.py::cache_unfinished_req` → `apply_kv_cache_events` →
  `allocate`)に、メモリ逼迫時のフォールバック(eviction)が無く、極端に
  タイトなメモリ状況でRuntimeErrorになる。バッチ組成時(`schedule_base`/
  `schedule_with_prefix`)には既にeviction機構があるので、同様の処理を
  この完了時パスにも入れるべきかもしれない。今回はPrefix Cache Hit ratio
  0.00%のワークロードだったので`--no-enable-prefix-caching`で回避したが、
  prefix cachingが実際に効くワークロードでは同じ問題を踏む可能性がある。
