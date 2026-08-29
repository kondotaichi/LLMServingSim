# Level 1 Remote vLLM Replay Client 要件定義書

## 1. 目的

本機能は、このMacからInternet/WAN経由でAzure上のセルフホストvLLM serverへ
Hongo workloadをopen-loop replayし、clientから見たE2E TTFT、completion latency、
throughput、failureをrequest単位で記録することを目的とする。

主比較の対象は「このMacのclient→WAN→Azure vLLM→WAN→このMacのclient」の系全体である。
Azure VM内でworkloadを生成またはreplayしない。Azure VMにrepositoryのcloneを必須としない。

## 2. Level 1の範囲

### 2.1 実装対象

- 外部のOpenAI-compatible vLLM endpointへのstreaming request送信
- JSONL workloadのopen-loop replay
- requestごとの送信予定、実送信、初回token受信、完了時刻の記録
- E2E TTFT、completion latency、TPOT、launch lagの算出
- request throughput、token throughput、latency percentile、failureの集計
- reproducibility metadataとraw request resultの保存
- smoke runとfull runの分離

### 2.2 対象外

- vLLM内部のrequest単位queue time、prefill time、scheduler eventの取得
- Prometheus、DCGM、`nvidia-smi`などAzure server側telemetryの収集
- MacとAzure VMのwall clockを使った片道network latencyの推定
- Azure VMのprovisioning、vLLM起動、model download、firewall設定
- H100 NVLのGPU数を8枚より減らすscale-down sensitivity実験
- Router内部queueの取得
- Simulatorの実行またはSimulator結果との自動比較
- 自動retryによる成功率の底上

server側breakdownはLevel 2以降の要件とする。

## 3. 実行構成

```text
Mac
  Hongo workload JSONL
  remote replay client
  monotonic clock / result writer
       |
       | HTTPS streaming request over Internet/WAN
       v
Azure cloud environment
  experiment router (single public endpoint)
       |
       +-- Standard_NC40ads_H100_v5 replica 0 (H100 NVL 94 GB x1, TP=1)
       +-- Standard_NC40ads_H100_v5 replica 1 (H100 NVL 94 GB x1, TP=1)
       +-- ...
       +-- Standard_NC40ads_H100_v5 replica 7 (H100 NVL 94 GB x1, TP=1)
```

### 3.1 Primary cloud構成

Level 1のPrimary runでは次のhardware/server構成を固定する。

| 項目 | 固定値 |
|---|---|
| Cloud | Microsoft Azure |
| VM SKU | `Standard_NC40ads_H100_v5` |
| VM数 | 8 |
| GPU | NVIDIA H100 NVL 94 GB |
| GPU数 | 1 GPU/VM、合計8 GPU |
| 合計GPU VRAM | 752 GB |
| vLLM replica数 | 8 |
| vLLM tensor parallel size | 各replicaで1 |
| Model | Meta Llama 3.1 8B |
| Weight dtype | `bfloat16` |
| Serving endpoint | routerが提供する単一のOpenAI-compatible endpoint |

8 GPUを1つのvLLM processでTP=8として動作させない。各VMで1 GPUを使う独立したvLLM serverを
1つ起動し、計8 replicaとする。clientは各replicaへ直接送信せず、単一のrouter endpointへ送信する。
Router policyは本計測前に固定し、metadataに保存する。Level 1 Primaryの候補は
`least-active-requests`とし、同率時はreplica IDで決定する。
routerはresponse headerにbackendのreplica IDを付与し、clientがrequestごとの送信先を記録できるようにする。

H100 NVLとRTX 4090の計算性能、memory bandwidth、VRAM、interconnectの差は、
クラウド集中環境とローカル分散環境のsystem差の一部としてそのまま含める。
GPUメモリ容量や計算性能を揃えるためのworkload rate補正は行わない。

H100 NVLのGPU数を8枚から減らした場合の性能・コスト変化は後続の
scale-down sensitivity実験で扱い、Level 1 Primary runに混ぜない。

Level 1で観測するE2E TTFTは次の要素を分離せず含む。

```text
request upload / connection / TLS / WAN
+ Azure側queueing
+ prefill and first-token generation
+ first-token response over WAN
```

## 4. 入力

### 4.1 Workload

正本は`experiments/2026-08-01_hongo_workload/workloads/`にあるHongo Peak
1x〜10x、seed 1の300-request workloadとする。Azure runでは同じ到着間隔を
2周期連結し、各倍率600 requestとして使用する。

```text
experiments/2026-08-17_azure_selfhost_vs_local/workloads/hongo_repeat600/hongo_peak_{1..10}x_repeat600_seed1.jsonl
```

runnerは実行前にmanifestを使って件数、size、SHA-256、request orderを検査する。

Peak 1x〜10xの`request_send_time_ns`は元のHongo workloadのまま使う。
GPU数、VRAM、クラウドGPUの性能に合わせてarrival timelineを伸縮しない。

2周期目は実vLLMで有効なtoken ID範囲を維持するため、input token列を1 token
巡回移動する。token数は変えず、周期をまたぐ意図しないprefix cache hitを防ぐ。

### 4.2 使用するworkload field

| Field | 必須 | 用途 |
|---|---|---|
| `request_id` | Yes | resultとの一対一対応 |
| `session_id` | Yes | session単位の後続分析 |
| `user_id` | Yes | user単位の後続分析 |
| `input_tok_ids` | Yes | vLLMへ送るprompt |
| `input_toks` | Yes | 要求input token数の検証 |
| `output_toks` | Yes | Controlled modeの要求生成長 |
| `request_send_time_ns` | Yes | open-loop送信予定 |
| `assigned_instance_id` | No | 結果に保存するが、送信先選択には使用しない |
| `communication_latency_ns` | No | 参考値として保存するが、送信時刻に加算しない |

### 4.3 Endpointとcredential

- Endpoint URLはCLI argumentまたはenvironment variableから受け取る。
- API keyはenvironment variableからのみ受け取る。
- API keyをCLI argument、result、metadata、log、exception messageへ出力しない。
- HTTPSを原則とする。TLS verification無効化は通常runでは許可しない。

## 5. Request仕様

### 5.1 API

- vLLMのOpenAI-compatible Completions APIを使用する。
- `stream=true`とする。
- `input_tok_ids`をtoken ID promptとして直接送信することを第一候補とする。
- 対象vLLM endpointがtoken ID promptを受理しない場合、full runを開始せずsmokeを失敗とする。
- requestにworkloadの`request_id`を含むclient request IDを付与できる構成にする。

### 5.2 Generation parameter

| Parameter | 値 |
|---|---|
| model | CLIで固定 |
| temperature | `0` |
| top_p | `1` |
| max_tokens | requestごとの`output_toks` |
| ignore_eos | `true` |
| stream | `true` |
| stream usage | 有効 |

Controlled modeでは`actual_output_tokens == output_toks`を成功条件とする。
vLLMがstream最終eventでusageを返す構成を使う。usageが得られない場合はfull runを開始しない。

### 5.3 Connectionの扱い

- 非同期HTTP clientとconnection poolを使用する。
- requestごとに新しいclient processを起動しない。
- keep-aliveを有効にする。
- 本計測前に明示的なwarm-up requestを実行し、本計測resultに含めない。
- connection pool size、timeout、client concurrency上限をmetadataに保存する。

## 6. Open-loop replay

### 6.1 Scheduling

各requestの送信予定は次で計算する。

```text
relative_send_ns = request.request_send_time_ns - first_request.request_send_time_ns
scheduled_send_monotonic_ns = run_start_monotonic_ns + relative_send_ns
```

runnerは前requestの完了を待たず、各requestをその送信予定時刻に起動する。
client側のconcurrency limitに達した場合もrequestを破棄せず、遅延を`launch_lag_ns`として記録する。

### 6.2 Retryとtimeout

- Primary runでは自動retryを行わない。
- connect timeout、first-token timeout、overall timeoutを別々に設定可能とする。
- timeoutとHTTP errorは失敗requestとしてresultへ残す。
- timeout後も他requestのreplayは継続する。
- Ctrl-C受信時は送信済みrequestの結果を可能な限りwriteし、runを`interrupted`とする。

## 7. 時刻とmetric定義

### 7.1 Clock

- duration計測にはMac上の同一のmonotonic nanosecond clockを使用する。
- runの人間向け開始・終了時刻はUTC wall clockで別途保存する。
- MacとAzure VMのwall clockの差をrequest latencyの計算に使用しない。

### 7.2 Event

```text
t_scheduled : workloadから求めた送信予定時刻
t_send      : HTTP request送信処理を開始した時刻
t_first     : 最初の空でない生成token/contentを受信した時刻
t_end       : streamの正常終了を受信した時刻
```

HTTP header、keep-alive event、role-only chunk、usage-only chunk、空contentを`t_first`としない。

### 7.3 Metric

```text
launch_lag_ns         = t_send - t_scheduled
e2e_ttft_ns           = t_first - t_send
completion_latency_ns = t_end - t_send
tpot_ns               = (t_end - t_first) / max(actual_output_tokens - 1, 1)
```

`t_first`を取得できない失敗requestのTTFTはnullとし、0にしない。

## 8. CLI要件

runnerは少なくとも次のargumentを持つ。具体的なmodule/file名は実装時に固定する。

| Flag | 必須 | 説明 |
|---|---|---|
| `--workload` | Yes | 入力JSONL |
| `--endpoint` | Yes | vLLM API base URL |
| `--model` | Yes | serverが提供するmodel name |
| `--output-dir` | Yes | run artifactの出力先 |
| `--run-id` | No | 未指定時はUTC timestampから生成 |
| `--max-requests` | No | smoke用の先頭request数 |
| `--max-client-concurrency` | No | client側同時実行上限 |
| `--connect-timeout` | No | connection timeout |
| `--first-token-timeout` | No | first token timeout |
| `--request-timeout` | No | request全体のtimeout |
| `--warmup-requests` | No | 本計測前のwarm-up数 |
| `--server-gpu-model` | No | metadata用。Primaryは`NVIDIA H100 NVL 94GB` |
| `--server-gpu-count` | No | metadata用。Primaryはcluster合計`8` |
| `--server-vm-sku` | No | metadata用。Primaryは`Standard_NC40ads_H100_v5` |
| `--server-replica-count` | No | metadata用。Primaryは`8` |
| `--tensor-parallel-size` | No | metadata用。Primaryは各replicaで`1` |
| `--router-policy` | No | metadata用。Primary候補は`least-active-requests` |

API keyのenvironment variable名は`HONGO_API_KEY`とする。

## 9. Artifact要件

### 9.1 Directory layout

```text
experiments/2026-08-17_azure_selfhost_vs_local/results/remote/<run_id>/
  metadata.json
  requests.jsonl
  summary.json
  run.log
```

`results/`はGit管理対象外とする。

### 9.2 `requests.jsonl`

1行1requestとし、少なくとも次を保存する。

```text
run_id
request_id
session_id
user_id
assigned_instance_id
scheduled_send_ns
actual_send_ns
launch_lag_ns
first_token_ns
completion_ns
e2e_ttft_ns
completion_latency_ns
tpot_ns
requested_input_tokens
actual_input_tokens
requested_output_tokens
actual_output_tokens
http_status
finish_reason
success
error_type
error_message
replica_id
```

nanosecond event値はrun開始monotonic時刻からの相対値として保存し、異なるhostのwall clockと誤認させない。
`error_message`はcredential、prompt、response本文を含めず、長さを制限する。

### 9.3 `metadata.json`

- run ID、status、start/end UTC
- Git commitとdirty state
- workload path、SHA-256、request数
- endpointのscheme/host/port（credentialとqueryは除外）
- model name
- server VM SKU/count、GPU model/count、GPU当たりのVRAM、replica数、replicaごとのtensor parallel size
- routerのsoftware/version、routing policy、backend一覧
- generation parameter
- timeoutとclient concurrency設定
- warm-up条件
- Pythonと主要dependency version
- MacのOS、CPU、network interfaceの識別に必要な非機密情報
- runnerのCLI（API keyは除外）

### 9.4 `summary.json`

- requested/sent/completed/successful/failed request数
- E2E TTFTのmean、p50、p95、p99、max
- TPOTのmean、p50、p95、p99
- completion latencyのmean、p50、p95、p99、max
- launch lagのp50、p95、p99、max
- request throughput
- prompt tokens/s、generation tokens/s
- HTTP status、timeout、error typeごとの件数
- output token mismatch数
- run acceptance判定と理由

percentileは成功requestのみで計算する。failureをlatency 0として集計しない。

## 10. Loggingと表示

- progressは定期的に表示するが、requestごとの全responseをstdoutへ出力しない。
- prompt token ID、prompt本文、生成本文を通常logに保存しない。
- errorはrequest ID、error type、HTTP status、打ち切ったmessageだけを記録する。
- resultはrequest完了ごとに追記し、run途中のprocess crash時も可能な限り回収できるようにする。

## 11. Smoke test

full runの前に10 requests以下のsmoke runを実行し、次を確認する。

1. Endpointに接続できる。
2. Model nameが一致する。
3. Token ID promptが受理される。
4. Streaming chunkから最初の生成contentを検出できる。
5. Final usageからinput/output token数を取得できる。
6. `ignore_eos=true`で要求output token数とactual output token数が一致する。
7. Resultにcredential、prompt、response本文が含まれない。
8. Warm-up requestが本計測のsummaryに入らない。

いずれかが失敗した場合、full runを開始しない。

## 12. Run acceptance criteria

次のいずれかに該当するrunは主性能結果として採用しない。Raw resultは削除せず、無効理由を保存する。

- workloadのSHA-256がmanifestと一致しない
- 対象requestの一部が送信されていない
- client launch lag p99が事前に固定した閾値を超える
- Controlled modeでoutput token mismatchが1件以上ある
- model name、generation parameter、endpointが計画と異なる
- `Standard_NC40ads_H100_v5`×8 VM、H100 NVL 94 GB×8、8 vLLM replicaでない
- いずれかのreplicaのtensor parallel sizeが1でない、またはrouter policyが計画と異なる
- `requests.jsonl`または`metadata.json`が欠損している
- runner自体がcrashまたはinterruptedで終了した

server saturationによるHTTP error、timeout、failureはそれ自体をrun無効の理由としない。
これらは対象systemの結果として保存する。

launch lag閾値の数値はfull run実行前にsmoke/pilotを基に固定し、結果を見て後から変更しない。

## 13. 非機能要件

- Pythonで実装し、このMacから実行できる。
- 2000 requestsのopen-loop replay中にclientが同期I/Oで送信scheduleを停めない。
- 出力はrequest完了ごとに段階的に永続化する。
- 同じworkload、CLI、endpoint、software versionから比較可能なartifactを作る。
- API keyやその他のsecretをGit、artifact、stdoutへ残さない。
- output directoryが既に存在する場合は無言でoverwriteしない。

## 14. 実装完了条件

Level 1は次のすべてを満たした時点で完了とする。

1. CLIでworkload、endpoint、model、output directoryを指定できる。
2. 10-request smoke runが受入条件を満たす。
3. Peak 2xの2000 requestsをopen-loopで送信できる。
4. request-level artifactからE2E TTFT、completion latency、TPOT、launch lagを再計算できる。
5. summaryとraw request数が一致する。
6. failure、timeout、token mismatchを成功requestと混同しない。
7. artifactにsecret、prompt、response本文が含まれない。
8. validatorと最小のlocal/mock testを通過する。

## 15. Level 2への拡張点

Level 1のrequest ID、run ID、event schemaは、後からserver側telemetryを追加できる形にする。
Level 2ではvLLM Prometheus metrics、waiting/running request数、queue time histogram、GPU telemetryを
同run IDのartifactとして収集する。Level 1のE2E metric定義は変更しない。
