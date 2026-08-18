# Azure比較実験用ワークロード引き継ぎ仕様

## 1. 正本

Azure比較で使用するワークロードの正本は、次の2000件版3ファイルとする。
300件版やPP=2用にinstance割当を書き換えた派生版は使用しない。

```text
experiments/2026-08-09-test-some-workload/workloads/full/hongo_peak_2x_seed1.jsonl
experiments/2026-08-09-test-some-workload/workloads/full/hongo_peak_5x_seed1.jsonl
experiments/2026-08-09-test-some-workload/workloads/full/hongo_peak_10x_seed1.jsonl
```

これらはGit管理されていない生成物である。したがって、リポジトリをcloneするだけではAzure側へ
渡らない。Azure側で実験を始める前に、3ファイルをAzure Blob Storageへ別送し、下表のSHA-256を照合する。
照合できないファイルで本計測を開始してはならない。

| 負荷 | 件数 | Target rate | Timeline | Size | SHA-256 |
|---|---:|---:|---:|---:|---|
| Peak 2x | 2000 | 18 req/s | 111.111 s | 44,326,813 bytes | `5f9c875341cdfc60743b7318a74bc25c132efc691a028dc4d6d9622a628f7107` |
| Peak 5x | 2000 | 45 req/s | 44.444 s | 44,325,749 bytes | `8e6c33d7ec88c262f6ce494641aa15ce12c8182334360c2b4ecec4b5df74cdd8` |
| Peak 10x | 2000 | 90 req/s | 22.222 s | 44,326,685 bytes | `f37f287d0e6a196eff9708cc1a5a8bfc41dd78e9d7b3313684888f0f242226e6` |

実測の最初と最後の`request_send_time_ns`の差から求めたrateは、それぞれ約18.019、
45.048、90.097 req/sである。表のTarget rateとの差はPoisson到着の有限標本による。

## 2. 全負荷点で固定される内容

3ファイルは同じ2000リクエストを持ち、負荷倍率で変更するのは送信・到着timelineだけである。

| 指標 | 値 |
|---|---:|
| Requests | 2000 |
| Unique sessions/users | 1129 |
| Return-visit requests | 871 |
| Return-visit rate | 43.55% |
| Input tokens: total / mean / p50 / p95 / range | 8,145,810 / 4,072.905 / 3,659 / 6,459.5 / 3,000–7,952 |
| Output tokens: total / mean / p50 / p95 / range | 743,544 / 371.772 / 352 / 767 / 1–2,305 |
| Physical GPU assignment | 12 IDs、各137–198 requests |
| Arrival process | Poisson型open-loop arrival |
| Seed | 1（地理配置seedは20260801） |

入力・出力token ID、セッション、ユーザー座標、GPU割当、prefix再利用候補長は全負荷点で同一である。
Hongo workloadの詳細な生成前提は
`experiments/2026-08-09-test-some-workload/report/hongo_workload_detail.md`を参照する。

## 3. Replayで使うfield

JSONLは1行1requestである。Azure replayで最低限使用するfieldは次のとおり。

| Field | 用途 |
|---|---|
| `request_id` | 結果との一対一対応 |
| `session_id`, `user_id` | 再訪とsession affinityの追跡 |
| `input_tok_ids`, `input_toks` | Prompt内容と要求input長 |
| `output_tok_ids`, `output_toks` | Controlled modeの要求出力長 |
| `request_send_time_ns` | Open-loopの送信予定時刻 |
| `arrival_time_ns` | 元シミュレーションで通信遅延を含む到着時刻 |
| `reuse_prefix_toks` | 再利用可能prefix長 |
| `assigned_instance_id` | 12 physical GPUを前提にした元割当（AzureのTP=8 backendでは使用しない） |
| `communication_latency_ns` | WAN込み比較を別算する場合の元通信遅延 |

Azureのservice-only比較では、run開始を0として`request_send_time_ns`をreplayする。
`arrival_time_ns`と`communication_latency_ns`をserver送信時刻へ二重加算しない。
WAN込みの感度分析では、service-only結果へ通信遅延を分離して加える。

## 4. 再現元

生成ロジックは次の順に参照する。

```text
experiments/2026-08-09-test-some-workload/scripts/build_hongo_workloads.py
experiments/2026-08-01_hongo_workload/scripts/prepare_hongo_workload.py
```

再生成にはShareGPT由来の入力データ、tokenizer revision、依存パッケージが必要になるため、
Azure側では原則として正本JSONLを転送してhash固定する。再生成は欠損時の代替ではなく、別workloadを
作る操作として扱い、元のhashと一致しなければ同一条件の比較に含めない。

## 5. Azure Blob Storage経由の引き継ぎ手順

ローカル側では、AzCopyで`azcopy login`を実行し、対象containerへの権限を確認してから次を実行する。
`<BLOB_PREFIX_URL>`は`https://<account>.blob.core.windows.net/<container>/<prefix>`形式へ置き換える。
この処理は正本を検査してから3ファイルとmanifestをuploadする。

```bash
experiments/2026-08-17_azure_selfhost_vs_local/scripts/upload_workloads.sh <BLOB_PREFIX_URL>
```

Azure VM側ではrepositoryをcloneした後、同じBlob prefix URLを指定する。

```bash
experiments/2026-08-17_azure_selfhost_vs_local/scripts/download_workloads.sh <BLOB_PREFIX_URL>
```

download後の配置先は次のとおりであり、script内で件数、session、順序、全負荷間の内容一致、
ファイルサイズ、SHA-256を検査する。

```text
experiments/2026-08-17_azure_selfhost_vs_local/workloads/hongo/
```

## 6. Azure側で本計測前に行う検査

1. 3ファイルのSHA-256が本書と一致すること。
2. 各ファイルが2000行、1129 session、1129 userであること。
3. `request_id`が0–1999で重複・欠損しないこと。
4. 3負荷間でtoken ID、token数、session、user、元GPU割当が一致すること。
5. `request_send_time_ns`が単調非減少で、replayがopen-loopであること。
6. 全2000件が送信・完了したこと。timeoutを成功扱いにしないこと。
7. Load generatorのlaunch lagを保存し、client飽和runを除外できること。

## 7. 解釈上の注意

- 43.55%は「2000件のうち、同一sessionの2回目以降」の比率であり、cache hit率ではない。
- `reuse_prefix_toks`は再利用可能量であり、実際のhitやmigration量はcache状態とroutingに依存する。
- `assigned_instance_id`はローカル12-GPU配置用のfieldであり、Azureの単一TP=8 backendでは送信先の選択に使用しない。
  このtopology差を結果で明記する。
- Peak 2x/5x/10xはrequest数を増やす条件ではない。同じ2000件のtimeline圧縮である。
