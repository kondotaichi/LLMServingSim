# Chakra変換・トレースI/O高速化レポート

作成日: 2026-07-14

## 1. 結論

LLMServingSimのバッチ反復ごとに発生していたChakra converterのPythonプロセス起動と、
中間トレースファイルの重複I/Oを削減した。シミュレーションモデル、スケジューラ、
ASTRA-Sim、プロファイルDBの参照方法は変更していない。

小規模な3リクエスト比較では、wall timeが3分16.976秒から32.825秒へ短縮され、
約6.0倍高速になった。60秒・300リクエストの実験でも、保存済み旧版と新版の
`requests.csv`、`users.csv`、`gpus.csv`がbyte単位で完全一致した。

| 検証 | 新版wall time | 結果 |
|---|---:|---|
| 3リクエスト・`NEAREST_MIGRATE_KV` | 32.825秒 | 旧版3分16.976秒、約6.0倍高速、3 CSV完全一致 |
| 60秒・300リクエスト・`NEAREST_MIGRATE_KV` | 9分48.132秒 | 3 CSV完全一致、metadata条件一致 |
| 60秒・300リクエスト・`NEAREST_KV` | 21分51.537秒 | 3 CSV完全一致、metadata条件一致 |

旧60秒実験にはwall timeが保存されていないため、300リクエスト実験の高速化倍率は
算出していない。300リクエスト比較は結果不変の検証として使用した。

## 2. 変更前の処理

変更前は、スケジューラがバッチを作るたびに次の処理を行っていた。

```text
1. Pythonで各layerのトレース行を生成
2. trace.txtへ書き出す
3. trace.txtをPythonで全行読み直す
4. KV load/evict、layer番号、ヘッダを付けてtrace.txtを書き直す
5. python -m chakra.src.converter.converterをsubprocessとして起動
6. subprocessがPython moduleとprotobuf定義をimport
7. subprocessがtrace.txtを読み、Chakra .etを生成
8. ASTRA-Simが.etを読み込んでcycle数を計算
```

この処理はprefillだけでなく、decodeの各iterationでも繰り返される。長い出力を持つ
workloadではconverter subprocessが多数起動され、Python interpreter起動とmodule importの
固定費がシミュレーションのwall timeを支配していた。

## 3. 変更後の処理

### 3.1 Chakra converterのin-process化

`serving/core/graph_generator.py`から、従来と同じ
`chakra.src.converter.llm_converter.LLMConverter`クラスを直接importして`convert()`を呼ぶ。

```text
変更前: serving Python -> 新しいPython subprocess -> LLMConverter.convert()
変更後: serving Python ---------------------------> LLMConverter.convert()
```

変換アルゴリズムやprotobuf生成処理は置き換えていない。同じconverterクラスへ同じ
引数と同じ入力トレースを渡しており、削除したのはプロセス境界だけである。
これにより、バッチごとの以下の固定費がなくなった。

- Python interpreterの起動と終了
- Chakra converterとprotobuf moduleの再import
- CLI argumentの構築と解析
- 親子プロセスの生成・待機

### 3.2 中間トレースI/Oの削減

`serving/core/trace_generator.py`では、layer行を一度`StringIO`へ構築する。KV load/evict、
layer番号、ヘッダをメモリ上で付けた後、完成したトレースを1回だけファイルへ書く。

```text
変更前: write -> read -> parse -> rewrite
変更後: memory buffer -> parse -> single write
```

Chakra converterに渡す最終テキストの内容は変えていない。`.txt`を完全に廃止せず、
既存converterと`--no-cleanup-inputs`によるデバッグも維持している。

## 4. シミュレーション結果を変えない理由

今回変更したのは、同じ実行グラフを作るまでのホスト側の経路だけである。以下は変更していない。

- schedulerのbatch形成とrequest状態更新
- prefix cacheとKV memory accounting
- routingおよびKV migration latency
- profile DBのlatency lookup
- Chakra `LLMConverter`の変換ロジック
- 生成されたChakra nodeの依存関係
- ASTRA-Simのcycle-level simulation
- request、user、GPU集計

analytical backendへの置換、decode stepの集約、KV長のbucket化、layer latencyの近似などは
行っていない。このため、シミュレーション時間軸を短縮する近似ではなく、同じ計算を
少ないプロセス起動とファイルI/Oで実行する実装高速化である。

## 5. 後方互換性

新版をデフォルトにしたが、旧処理も削除していない。問題の切り分けや過去環境の再現では、
次の2オプションを追加すると完全な旧経路へ戻せる。

```bash
python -m serving \
  <OTHER_ARGUMENTS> \
  --graph-converter subprocess \
  --trace-io legacy
```

個別の切り替えも可能である。

| オプション | 新版デフォルト | 旧経路 |
|---|---|---|
| `--graph-converter` | `in-process` | `subprocess` |
| `--trace-io` | `buffered` | `legacy` |

これにより、converterのin-process化とbuffered I/Oを別々に無効化して問題箇所を特定できる。

## 6. 検証方法

### 6.1 小規模な旧・新同条件比較

同じ3リクエストに対し、旧経路を
`--graph-converter subprocess --trace-io legacy`、新版を
`--graph-converter in-process --trace-io buffered`として実行した。

| 経路 | wall time |
|---|---:|
| 旧経路 | 3分16.976秒 |
| 新経路 | 32.825秒 |
| 短縮 | 2分44.151秒 |
| 高速化 | 約6.0倍 |

3種類のCSVについて`cmp`とSHA-256を確認し、すべて一致した。

### 6.2 60秒・300リクエスト実験

保存済み旧版結果を基準に、次のworkloadを新版で再実行した。

```text
workloads/generated/cell_apn/prompt6000/sharegpt_300_prompt6000_reuse50.jsonl
```

共通条件は10 RTX 4090、入力6000 tokens、300リクエスト、Prefix再利用率約50%、
`max_num_seqs=128`、`max_num_batched_tokens=2048`、chunked prefill有効、
prefix caching有効である。

比較結果:

| Policy | `requests.csv` | `users.csv` | `gpus.csv` | metadata条件 |
|---|---|---|---|---|
| `NEAREST_MIGRATE_KV` | byte一致 | byte一致 | byte一致 | 一致 |
| `NEAREST_KV` | byte一致 | byte一致 | byte一致 | 一致 |

metadataは実行ごとに変わる出力パスとGit commit hashだけを除外して比較した。
旧結果ディレクトリは読み取り専用の比較対象とし、新版結果はコンテナ内の`/tmp`へ出力した。

## 7. 解釈と今後の方針

今回の高速化は、decode iterationが多いほど有効である。反対に、converter起動回数が少なく
ASTRA-Sim本体の計算が支配的な大規模collective構成では、高速化率が小さくなる可能性がある。

現時点では、新しいin-process converterとbuffered trace I/Oを通常経路として使用できる。
将来さらに高速化する場合も、まず今回と同様に旧経路を残し、実workloadのCSV完全一致を
確認してからデフォルトを切り替えるべきである。
