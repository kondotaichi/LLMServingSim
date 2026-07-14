# ワークロードの到着時間を拡大・圧縮する方法

作成日: 2026-07-14

対象スクリプト:

```text
scripts/scale_workload_arrivals.py
```

このページでは、既存のJSONLワークロードについて、リクエスト内容を変えずに到着時間軸だけを拡大・圧縮する方法を説明する。

## 1. 今回使用するコマンド

リポジトリルートから次を実行する。

```bash
python3 scripts/scale_workload_arrivals.py \
  --input workloads/generated/cell_apn/prompt6000/sharegpt_300_prompt6000_reuse50.jsonl \
  --output workloads/generated/cell_apn/prompt6000_120s/sharegpt_300_prompt6000_reuse50_120s.jsonl \
  --scale 2.0
```

このコマンドは、約60秒の到着ウィンドウを2倍に伸ばし、約120秒のワークロードを生成する。

## 2. 各引数の意味

### `--input`

変換元となるJSONLワークロードを指定する。

```text
workloads/generated/cell_apn/prompt6000/sharegpt_300_prompt6000_reuse50.jsonl
```

今回の入力は、Prompt 6000、300リクエスト、Prefix再利用率約50%の60秒版ワークロードである。

### `--output`

変換後のJSONLを書き出す場所を指定する。

```text
workloads/generated/cell_apn/prompt6000_120s/sharegpt_300_prompt6000_reuse50_120s.jsonl
```

親ディレクトリが存在しない場合はスクリプトが自動的に作成する。すでに同名ファイルが存在する場合は確認なしで上書きされるため注意する。

### `--scale`

先頭リクエストを基準とした到着時刻の倍率を指定する。

```text
--scale 2.0
```

代表的な指定は次の通りである。

| 指定値 | 動作 | 60秒ワークロードに適用した場合 |
|---:|---|---:|
| `0.5` | 到着間隔を半分に圧縮 | 約30秒 |
| `1.0` | 到着間隔を維持 | 約60秒 |
| `1.5` | 到着間隔を1.5倍に拡大 | 約90秒 |
| `2.0` | 到着間隔を2倍に拡大 | 約120秒 |

`--scale` には0より大きい値を指定する。0以下を指定するとエラーになる。

## 3. 到着時刻の計算方法

スクリプトは、ワークロード内で最も早い `arrival_time_ns` を基準時刻とする。

```text
base_arrival = min(arrival_time_ns)
```

各リクエストの新しい到着時刻は次の式で計算される。

```text
new_arrival = base_arrival
              + round((old_arrival - base_arrival) * scale)
```

例えば、最初のリクエストから10秒後に到着していたリクエストへ `--scale 2.0` を適用すると、最初のリクエストから20秒後へ移動する。

```text
変換前: 0秒、10秒、30秒、60秒
変換後: 0秒、20秒、60秒、120秒
```

絶対時刻を単純に2倍するのではなく、最初の到着時刻からの差だけを2倍する。そのため、ワークロードの基準時刻自体は変わらない。

## 4. `request_send_time_ns` の扱い

`arrival_time_ns` を移動した量と同じだけ `request_send_time_ns` も移動する。

```text
arrival_shift = new_arrival - old_arrival
new_request_send_time = old_request_send_time + arrival_shift
```

これにより、次の通信区間が変換前後で維持される。

```text
arrival_time_ns - request_send_time_ns
```

つまり、ユーザからGPUまでの既存のuplink遅延を変更せず、リクエスト全体を時間軸上で前後へ移動する。

`request_send_time_ns` が存在しない行については、`arrival_time_ns` だけが更新される。

## 5. 変更されないもの

このスクリプトは、基本的に次の内容を変更しない。

- リクエスト数
- request ID
- 入力トークン数
- 出力トークン数
- Prompt内容
- Prefix再利用量・再利用率
- ユーザID
- ユーザ座標
- 最寄りGPU・セルの割り当て
- リクエストの並び順
- その他のJSONフィールド

したがって、60秒版と120秒版の比較では、到着率の違いを中心に評価できる。

## 6. なぜ60秒版から直接120秒版を作るのか

120秒版は、90秒版へさらに倍率を掛けるのではなく、元の60秒版へ `--scale 2.0` を適用して作ることを推奨する。

```text
推奨: 60秒版 × 2.0 = 120秒版
非推奨: 60秒版 × 1.5 = 90秒版、90秒版 × 1.333... = 120秒版
```

複数回変換すると、各段階の `round()` による丸め誤差が累積する可能性がある。すべての感度実験を同じ元ワークロードから生成すると、比較条件も分かりやすい。

## 7. 実行時の出力

正常に完了すると、次の情報が表示される。

```text
requests: 300
first arrival: <先頭到着時刻> ns
last arrival: <最終到着時刻> ns
arrival span: <到着ウィンドウ> s
workloads/generated/cell_apn/prompt6000_120s/sharegpt_300_prompt6000_reuse50_120s.jsonl
```

今回確認すべき点は次の2つである。

- `requests: 300` になっていること
- `arrival span` が約120秒になっていること

元ワークロードのspanが厳密に60.000000秒でない場合、出力も厳密に120.000000秒にはならない。元spanの2倍になっていれば正常である。

## 8. 生成後の確認

### リクエスト行数

JSONLは1リクエスト1行なので、次で300行あることを確認できる。

```bash
wc -l workloads/generated/cell_apn/prompt6000_120s/sharegpt_300_prompt6000_reuse50_120s.jsonl
```

期待値:

```text
300 workloads/generated/cell_apn/prompt6000_120s/sharegpt_300_prompt6000_reuse50_120s.jsonl
```

### 生成をもう一度検証する

同じ出力パスへ再実行するとファイルを上書きするため、検証だけを目的として再実行する必要はない。最初の実行時に表示される `requests` と `arrival span` を保存するのが安全である。

## 9. 120秒化による負荷の変化

300リクエストを60秒と120秒へ投入した場合、単純平均の投入率は次のようになる。

```text
60秒版:  300 / 60  = 5.0 req/s
120秒版: 300 / 120 = 2.5 req/s
```

リクエスト内容と地域別の到着比率は維持されるが、単位時間あたりの到着数は半分になる。このため、一般には次が期待される。

- GPU同時実行数の低下
- KV cache圧迫の緩和
- router待ち・scheduler待ちの減少
- メモリ容量を理由とするredirect件数の減少
- `NEAREST_MIGRATE` と `NEAREST_MIGRATE_KV` の差の縮小

ただし、地域負荷の偏りはそのままなので、特定セルのhotspotが完全になくなるとは限らない。

## 10. 生成したワークロードの使用例

生成後は `python -m serving` の `--dataset` に120秒版を指定する。

```bash
python -m serving \
  --cluster-config configs/cluster/ten_node_rtx4090_apn.json \
  --dataset workloads/generated/cell_apn/prompt6000_120s/sharegpt_300_prompt6000_reuse50_120s.jsonl \
  --num-reqs 300 \
  --request-routing-policy NEAREST_MIGRATE_KV \
  --dtype bfloat16 \
  --kv-cache-dtype auto \
  --max-num-seqs 128 \
  --max-num-batched-tokens 2048 \
  --enable-chunked-prefill \
  --enable-prefix-caching
```

実際の3方式比較では、ネットワーク、地理情報、出力先などの引数も60秒版・90秒版と同じ値に揃える。

## 11. 注意点

- 出力ファイルは確認なしで上書きされる。
- このスクリプトはtop-levelの `arrival_time_ns` を持つJSONLを前提とする。
- 到着時刻以外のthink timeや `tool_duration_ns` は倍率変更しない。
- Agentic session内のサブリクエスト間隔を伸縮する用途には、そのままでは不十分である。
- 60秒版と120秒版を比較するときは、同じ3方式、同じcluster config、同じseed、同じtoken設定を使用する。
- `--scale 2.0` はシミュレーション処理時間を2倍にする指定ではない。変更するのはシミュレーション内の到着時刻であり、実際のwall-clock実行時間は負荷や反復回数によって変わる。
