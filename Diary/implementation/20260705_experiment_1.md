# GPUリダイレクト型TTFTシミュレータ 要件定義書

**文書バージョン:** 1.0  
**作成日:** 2026-07-05  
**実装対象:** Pythonによる離散イベントシミュレータ  
**主用途:** Claude Codeへの実装依頼、実験条件の共有、受け入れ試験

---

## 1. 文書の目的

本書は、2台のGPUサーバと多数のUEが存在する地理的環境において、最寄りGPUのキューで一定時間待機した後に別GPUへリダイレクトする方式を評価するための、離散イベントシミュレータの要件を定義する。

評価対象は、最寄りGPUで待ち続ける方式、待たずに即時リダイレクトする方式、一定時間待ってからリダイレクトする方式である。キュー待機上限をパラメータ \(\tau\) として統一的に扱い、TTFT、キュー待ち時間、リダイレクト率、GPU利用率などを比較する。

本バージョンでは、KVキャッシュの転送、APNによる実際のGPU間転送時間、無線区間の帯域競合、continuous batchingは考慮しない。ただし、将来これらを追加できるように、ネットワークモデル、GPU間リンクモデル、処理時間モデル、リダイレクト方式を交換可能な構造にする。

---

## 2. 背景と検証仮説

### 2.1 背景

地理的に最も近いGPUへ常にリクエストを送る方式では、特定地域にUEが集中すると、一方のGPUに長いキューが形成される一方で、別地域のGPUには空き計算資源が残る可能性がある。

このとき、UEが最寄りGPUで待ち続けるよりも、一定時間後に別GPUへアクセスし直した方が、追加の伝搬時間を含めてもTTFTを短縮できる場合がある。

### 2.2 検証仮説

> 多数のUEと複数GPUが存在する環境で、一方の地域にリクエストが偏った場合、最寄りGPUで発生する追加キュー待ち時間が遠隔GPUへの追加伝搬時間を上回れば、リダイレクトによってTTFTを短縮できる。

### 2.3 本シミュレータで比較する方式

1. **Wait-only**: \(\tau=\infty\)。最寄りGPUで処理されるまで待機し、リダイレクトしない。
2. **Immediate redirect**: \(\tau=0\)。最寄りGPUへ到着した時点で直ちに処理開始できなければ、即時リダイレクトする。
3. **Timeout redirect**: \(0<\tau<\infty\)。最寄りGPUのキューで最大 \(\tau\) 待ち、処理開始されなければリダイレクトする。

---

## 3. スコープ

### 3.1 実装対象

- 10 km四方の地理空間
- 2台のGPUサーバ
- 設定可能な数のUE。既定値100
- 全UEによる同時刻送信
- 左右2地域に2:1でUEを配置し、各地域内では一様分布
- 地理的距離から最寄りGPUを決定
- 伝搬時間のみを考慮するネットワークモデル
- FCFS、同時処理数1のGPU処理モデル
- プロンプト長に依存する処理時間
- \(\tau=0\)、有限値、\(\infty\)のリダイレクト制御
- UE差し戻し方式とGPU間転送方式
- リクエスト単位ログ、GPUイベントログ、集計CSV、可視化
- 複数seedおよび複数 \(\tau\) の一括実行
- 自動テスト

### 3.2 本バージョンの対象外

- KVキャッシュの生成量および転送時間
- APNの実測レイテンシ、帯域、輻輳
- GPU間転送データ量
- RANスケジューリング、パケットロス、再送、ジッタ
- プロンプトサイズに起因するシリアライゼーション時間
- vLLMのcontinuous batching
- prefillとdecodeの同時実行
- 複数トークン生成中のGPU占有
- 3台以上のGPU
- リダイレクト後の再リダイレクト
- キュー待ち時間の将来予測
- ns-3との接続

---

## 4. 基本シナリオ

### 4.1 地理空間

シミュレーション領域は横10 km、縦10 kmの矩形とする。

- 左地域: \(0 \le x < 5\), \(0 \le y \le 10\)
- 右地域: \(5 \le x \le 10\), \(0 \le y \le 10\)

GPUの既定位置は次の通りとする。

| GPU | x座標 | y座標 |
|---|---:|---:|
| gpu_a | 2.5 km | 5.0 km |
| gpu_b | 7.5 km | 5.0 km |

### 4.2 UE配置

UEは左右の地域に2:1の人数比で配置する。各地域内では独立な一様分布とする。

総UE数を \(N\) とし、地域重みを左2、右1とする。人数が整数にならない場合は最大剰余法で割り当て、合計が必ず \(N\) になるようにする。

既定値 \(N=100\) の場合は次の人数とする。

- 左地域: 67 UE
- 右地域: 33 UE

座標は次の分布から生成する。

\[
x_i \sim U(x_{min,r}, x_{max,r})
\]

\[
y_i \sim U(y_{min,r}, y_{max,r})
\]

ここで \(r\) は割り当てられた地域である。

UEの最寄りGPUは、地域ラベルを直接利用せず、実際のユークリッド距離から再計算する。

\[
home(i)=\arg\min_j d(i,j)
\]

\[
d(i,j)=\sqrt{(x_i-x_j)^2+(y_i-y_j)^2}
\]

既定の対称配置では、左地域のUEはgpu_a、右地域のUEはgpu_bを最寄りGPUとすることが期待される。実行時には、生成地域と最寄りGPUが一致しないUEが存在した場合に警告を出す。

### 4.3 リクエスト送信

すべてのUEは同一時刻に1件のリクエストを送信する。

\[
t_i^{send}=0
\]

送信時刻は設定可能とするが、同一実験内では全UEで同じ値を使用する。

UEごとのGPU到着時刻は、UEとGPU間の距離差によりばらつく。

---

## 5. プロンプトワークロード

### 5.1 プロンプト長分布

プロンプト長と、その長さを送信するUEの比率を設定ファイルで指定できるようにする。

例:

```yaml
workload:
  output_tokens: 1
  prompt_distribution:
    assignment_mode: exact_ratio
    entries:
      - prompt_tokens: 128
        ratio: 0.20
      - prompt_tokens: 512
        ratio: 0.40
      - prompt_tokens: 1024
        ratio: 0.25
      - prompt_tokens: 2048
        ratio: 0.10
      - prompt_tokens: 4096
        ratio: 0.05
```

要件は次の通りとする。

- ratioの合計は、浮動小数点誤差を許容した上で1.0でなければならない。
- `assignment_mode: exact_ratio`では最大剰余法を用い、各長さの件数合計をUE数と一致させる。
- 割り当てたプロンプト長の一覧はseedを用いてシャッフルし、UEへランダムに割り当てる。
- 同一seedでは、同じUE座標と同じプロンプト長割り当てを再現する。
- 異なる \(\tau\) を比較するときは、同一seedのワークロードを共通利用する。

### 5.2 出力トークン数

MVPでは `output_tokens: 1` に固定する。

GPUの処理完了時刻は、最初のトークンが生成された時刻と一致する。このため、GPUは最初のトークン生成時に当該リクエストの処理を終了し、次のリクエストを開始できるものとする。

### 5.3 プロンプト長が影響する項目

プロンプト長はGPU処理時間に影響する。

プロンプト長はネットワーク時間には影響しない。プロンプト送信データは伝送帯域に対して十分小さく、シリアライゼーション時間を無視できると仮定する。

---

## 6. ネットワークモデル

### 6.1 基本方針

UEとGPU間の通信時間は、地理的距離に基づく伝搬時間のみとする。

片道伝搬時間は次式で計算する。

\[
T_{i,j}^{prop}=\frac{d(i,j)\cdot k_{path}}{v}
\]

- \(d(i,j)\): UE \(i\) とGPU \(j\) のユークリッド距離 [km]
- \(v\): 伝搬速度 [km/s]
- \(k_{path}\): 経路伸長係数

既定値は次の通りとする。

```yaml
network:
  model: propagation_only
  propagation_speed_km_per_s: 200000
  path_stretch_factor: 1.0
  symmetric: true
  ignore_serialization_delay: true
```

上りと下りの時間は対称とする。

\[
T_{UE\rightarrow GPU}=T_{GPU\rightarrow UE}
\]

### 6.2 考慮しない遅延

以下の要素は0とする。

- プロンプトサイズ÷帯域による送信時間
- RAN処理時間
- コアネットワーク処理時間
- ルータおよびスイッチ処理時間
- ネットワークキュー
- ジッタ
- パケットロスおよび再送
- UE側のリダイレクト処理時間

### 6.3 GPU間リンク

MVPではGPU間転送時間を0とする。

ただし、将来APNレイテンシ、APN帯域、プロンプト転送、KVキャッシュ転送を追加できるように、GPU間リンクモデルのインターフェースと設定項目を用意する。

```yaml
gpu_link:
  enabled: false
  model: zero_cost
  one_way_latency_ms: 0
  bandwidth_gbps: null
  transfer_prompt: false
  transfer_kv_cache: false
  kv_cache_size_bytes: 0
```

`gpu_link.enabled: false`または`model: zero_cost`の場合、GPU間転送時間は必ず0とする。

将来の拡張式は次を想定するが、MVPでは実装結果に影響させない。

\[
T_{h,r}^{gpu-link}=L_{APN}+\frac{S_{prompt}+S_{KV}}{B_{APN}}
\]

---

## 7. GPU処理モデル

### 7.1 GPU数とキュー

- GPU数は2台とする。
- 各GPUは独立したFCFSキューを持つ。
- 各GPUは同時に1リクエストだけ処理する。
- 処理中のリクエストに対するプリエンプションは行わない。
- リダイレクトできるのは、home GPUのキューで待機中のリクエストだけとする。
- 処理開始後はリダイレクトしない。

### 7.2 処理時間

GPU処理時間は、GPUがリクエストの処理を開始してから最初のトークンを生成するまでの時間とする。

以下のモードを実装する。
ただしこれはLLMServingSimで可能なことを考慮していないため、このGPUの処理時間についてはLLMServingSimで考慮可能なら以下のことは考慮しなくて良い。

#### fixed

\[
C_i=C_{fixed}
\]

```yaml
service_time:
  mode: fixed
  fixed_ms: 500
```

#### linear

\[
C_i=a+bL_i
\]

```yaml
service_time:
  mode: linear
  intercept_ms: 100
  ms_per_prompt_token: 0.5
```

#### lookup_table

```yaml
service_time:
  mode: lookup_table
  interpolation: linear
  values:
    128: 120
    512: 250
    1024: 430
    2048: 780
    4096: 1450
```

要件は次の通りとする。

- `lookup_table`に完全一致するトークン長があれば、その値を使用する。
- 未登録のトークン長は、設定に応じて線形補間または最近傍値を使用する。
- 範囲外の値は、端点値を使用するかエラーにするかを設定可能にする。既定は端点値とする。
- 全処理時間は0以上でなければならない。
- MVPでは2台のGPUに同一の処理時間モデルを適用する。

---

## 8. リダイレクト制御

### 8.1 タイムアウトパラメータ

home GPUへの到着後、直ちに処理開始できないリクエストはhome GPUのFCFSキューに入る。キューへ入った時刻からの経過時間が \(\tau\) に到達した時点で、まだ処理開始されていなければリダイレクトする。

\[
t_i^{timeout}=t_i^{home-arrival}+\tau
\]

- \(\tau=0\): GPUが使用中ならキューに残さず即時リダイレクト
- \(0<\tau<\infty\): 最大 \(\tau\) 待機
- \(\tau=\infty\): タイムアウトイベントを作成しない

### 8.2 タイムアウト時の判定

タイムアウトイベント発生時にリクエスト状態を確認する。

- `QUEUED_AT_HOME`: homeキューから削除し、リダイレクトを開始する。
- `RUNNING_AT_HOME`: タイムアウトイベントを無視する。
- `COMPLETED`: タイムアウトイベントを無視する。
- それ以外: 不正な状態としてエラーまたは警告を記録する。

処理開始時刻とタイムアウト時刻が同一の場合は、処理開始を優先し、リダイレクトしない。

したがって、実際のhome待ち時間が \(W_{home}\) の場合、次の条件とする。

\[
W_{home}\le\tau \Rightarrow home GPUで処理
\]

\[
W_{home}>\tau \Rightarrow リダイレクト
\]

### 8.3 リダイレクト回数

- 最大リダイレクト回数は1回とする。
- リダイレクト後のremote GPUでは、処理開始まで無期限に待つ。
- remote GPUで再度タイムアウトさせない。
- 2台構成のため、remote GPUはhome GPUではない方のGPUとする。

---

## 9. リダイレクト方式

### 9.1 設定

```yaml
redirect:
  enabled: true
  timeout_ms: 100
  max_redirects: 1
  mode: ue_retry
  destination_policy: other_gpu
```

対応モードは次の2種類とする。

- `ue_retry`: home GPUからUEへ差し戻し、UEがremote GPUへ再送する。既定モード。
- `gpu_forward`: home GPUからremote GPUへ直接転送する。

### 9.2 UE差し戻し方式 `ue_retry`

タイムアウト時の経路は次の通りとする。

```text
UE → home GPU → UE → remote GPU → UE
```

処理手順:

1. home GPUのキューからリクエストを削除する。
2. home GPUからUEへリダイレクト応答を送信する。
3. UEがリダイレクト応答を受信する。
4. UEは追加処理時間なしで同じリクエストをremote GPUへ再送する。
5. remote GPUへ到着後、空いていれば即時処理、使用中ならFCFSキューへ入る。
6. remote GPUで最初のトークン生成後、UEへ返送する。

リダイレクトされたリクエストのTTFTは次式とする。

\[
TTFT_i=
T_{i,h}
+W_{i,h}
+T_{h,i}
+T_{i,r}
+W_{i,r}
+C_{i,r}
+T_{r,i}
\]

リダイレクトが発生した場合、通常 \(W_{i,h}=\tau\) である。

通信が対称なら次のように表せる。

\[
TTFT_i=2T_{i,h}+\tau+2T_{i,r}+W_{i,r}+C_{i,r}
\]

### 9.3 GPU間転送方式 `gpu_forward`

タイムアウト時の経路は次の通りとする。

```text
UE → home GPU → remote GPU → UE
```

MVPではGPU間転送時間を0とする。

\[
TTFT_i=
T_{i,h}
+W_{i,h}
+T_{h,r}^{gpu-link}
+W_{i,r}
+C_{i,r}
+T_{r,i}
\]

MVPでは、

\[
T_{h,r}^{gpu-link}=0
\]

とする。

`gpu_forward`では、home GPUからUEへのリダイレクト応答イベントおよびUEからremote GPUへの再送イベントを発生させない。

### 9.4 リダイレクトなし

home GPUで処理されたリクエストのTTFTは次式とする。

\[
TTFT_i=T_{i,h}+W_{i,h}+C_{i,h}+T_{h,i}
\]

---

## 10. リクエスト状態

リクエストは少なくとも次の状態を持つ。

```text
CREATED
IN_TRANSIT_TO_HOME
QUEUED_AT_HOME
RUNNING_AT_HOME
REDIRECT_RETURNING_TO_UE
REDIRECT_RECEIVED_BY_UE
IN_TRANSIT_TO_REMOTE
IN_TRANSIT_GPU_TO_GPU
QUEUED_AT_REMOTE
RUNNING_AT_REMOTE
FIRST_TOKEN_GENERATED
COMPLETED
```

状態遷移はモードごとに次の通りとする。

### 10.1 home GPUで処理

```text
CREATED
→ IN_TRANSIT_TO_HOME
→ QUEUED_AT_HOME または RUNNING_AT_HOME
→ RUNNING_AT_HOME
→ FIRST_TOKEN_GENERATED
→ COMPLETED
```

### 10.2 `ue_retry`

```text
CREATED
→ IN_TRANSIT_TO_HOME
→ QUEUED_AT_HOME
→ REDIRECT_RETURNING_TO_UE
→ REDIRECT_RECEIVED_BY_UE
→ IN_TRANSIT_TO_REMOTE
→ QUEUED_AT_REMOTE または RUNNING_AT_REMOTE
→ RUNNING_AT_REMOTE
→ FIRST_TOKEN_GENERATED
→ COMPLETED
```

### 10.3 `gpu_forward`

```text
CREATED
→ IN_TRANSIT_TO_HOME
→ QUEUED_AT_HOME
→ IN_TRANSIT_GPU_TO_GPU
→ QUEUED_AT_REMOTE または RUNNING_AT_REMOTE
→ RUNNING_AT_REMOTE
→ FIRST_TOKEN_GENERATED
→ COMPLETED
```

各リクエストは、同時に複数のGPUキューまたは複数の実行状態に存在してはならない。

---

## 11. 離散イベント

### 11.1 必須イベント

```text
UE_SEND
ARRIVE_HOME_GPU
HOME_QUEUE_TIMEOUT
REDIRECT_RESPONSE_ARRIVE_UE
ARRIVE_REMOTE_GPU
ARRIVE_REMOTE_FROM_GPU
GPU_PROCESS_START
GPU_FIRST_TOKEN_GENERATED
FIRST_TOKEN_ARRIVE_UE
```

### 11.2 イベントキュー

- `heapq`等の時刻順優先度付きキューを用いる。
- イベントは `(timestamp, priority, sequence_number, event)` で一意に順序付けする。
- `sequence_number`は同一時刻・同一優先度の決定性を保証する単調増加値とする。
- 浮動小数点時刻を使用する場合は、比較誤差に注意し、テストでは許容誤差を定義する。

### 11.3 同一時刻の優先順位

同一時刻では次の順序で処理する。

1. `GPU_FIRST_TOKEN_GENERATED`
2. `GPU_PROCESS_START`
3. `ARRIVE_HOME_GPU`
4. `ARRIVE_REMOTE_GPU`
5. `ARRIVE_REMOTE_FROM_GPU`
6. `HOME_QUEUE_TIMEOUT`
7. `REDIRECT_RESPONSE_ARRIVE_UE`
8. `UE_SEND`
9. `FIRST_TOKEN_ARRIVE_UE`

`GPU_FIRST_TOKEN_GENERATED`によりGPUが空いた場合は、同じ時刻にキュー先頭の`GPU_PROCESS_START`を実行できるようにする。これにより、処理開始時刻とタイムアウト時刻が同一の場合は処理開始が優先される。

### 11.4 stale event対策

タイムアウトや処理開始イベントが無効になった場合でも、優先度付きキューから物理的に削除する必要はない。イベント実行時にリクエスト状態、対象GPU、イベント世代番号を確認し、古いイベントを無視できるようにする。

---

## 12. 詳細処理仕様

### 12.1 UE送信

1. UEごとにhome GPUを決定済みであることを確認する。
2. UEからhome GPUへの片道伝搬時間を計算する。
3. `ARRIVE_HOME_GPU`を \(t^{send}+T_{i,h}\) に登録する。

### 12.2 home GPU到着

GPUがアイドルかつキューが空の場合:

- 直ちに`RUNNING_AT_HOME`へ遷移する。
- `GPU_PROCESS_START`を同時刻に処理する。

GPUがビジーまたはキューに先行リクエストがある場合:

- FCFSキュー末尾へ追加する。
- `QUEUED_AT_HOME`へ遷移する。
- \(\tau=0\)の場合は同時刻に`HOME_QUEUE_TIMEOUT`を登録する。
- 有限の \(\tau>0\) の場合は到着時刻+\(\tau\)に登録する。
- \(\tau=\infty\)の場合は登録しない。

### 12.3 homeキュータイムアウト

1. 状態が`QUEUED_AT_HOME`であることを確認する。
2. home GPUのキューから当該リクエストを削除する。
3. 二重処理防止用の世代番号を更新する。
4. `ue_retry`または`gpu_forward`に応じたイベントを登録する。

### 12.4 remote GPU到着

- remote GPUがアイドルかつキューが空なら即時処理する。
- それ以外はremote GPUのFCFSキュー末尾へ追加する。
- remoteキューではタイムアウトイベントを登録しない。

### 12.5 GPU処理完了

1. 最初のトークン生成時刻を記録する。
2. GPUをアイドルにする。
3. 最終GPUからUEへの片道伝搬時間を計算する。
4. `FIRST_TOKEN_ARRIVE_UE`を登録する。
5. 同じGPUのキューが空でなければ、同時刻にキュー先頭の処理を開始する。

### 12.6 UEへの最初のトークン到着

- リクエストを`COMPLETED`にする。
- `ttft_ms = first_token_arrival_time_ms - send_time_ms`を計算する。
- TTFTブレイクダウンの合計とイベント時刻差が一致することを検証する。

---

## 13. 設定ファイル完全例

```yaml
experiment:
  name: phase1_timeout_redirect
  output_dir: results/phase1_timeout_redirect

simulation:
  seed: 42
  num_ues: 100
  send_time_ms: 0
  repetitions: 30
  time_epsilon_ms: 1.0e-9

area:
  width_km: 10.0
  height_km: 10.0

regions:
  - id: left
    x_min_km: 0.0
    x_max_km: 5.0
    y_min_km: 0.0
    y_max_km: 10.0
    ue_weight: 2
    expected_home_gpu_id: gpu_a
  - id: right
    x_min_km: 5.0
    x_max_km: 10.0
    y_min_km: 0.0
    y_max_km: 10.0
    ue_weight: 1
    expected_home_gpu_id: gpu_b

ue_placement:
  mode: regional_uniform
  allocation_method: largest_remainder
  validate_expected_home_gpu: true

gpus:
  - id: gpu_a
    x_km: 2.5
    y_km: 5.0
  - id: gpu_b
    x_km: 7.5
    y_km: 5.0

network:
  model: propagation_only
  propagation_speed_km_per_s: 200000
  path_stretch_factor: 1.0
  symmetric: true
  ignore_serialization_delay: true

workload:
  output_tokens: 1
  prompt_distribution:
    assignment_mode: exact_ratio
    entries:
      - prompt_tokens: 128
        ratio: 0.20
      - prompt_tokens: 512
        ratio: 0.40
      - prompt_tokens: 1024
        ratio: 0.25
      - prompt_tokens: 2048
        ratio: 0.10
      - prompt_tokens: 4096
        ratio: 0.05

service_time:
  mode: lookup_table
  interpolation: linear
  out_of_range: clamp
  values:
    128: 120
    512: 250
    1024: 430
    2048: 780
    4096: 1450

redirect:
  enabled: true
  timeout_ms: 100
  max_redirects: 1
  mode: ue_retry
  destination_policy: other_gpu

gpu_link:
  enabled: false
  model: zero_cost
  one_way_latency_ms: 0
  bandwidth_gbps: null
  transfer_prompt: false
  transfer_kv_cache: false
  kv_cache_size_bytes: 0

sweep:
  timeout_ms:
    - 0
    - 10
    - 25
    - 50
    - 100
    - 200
    - 500
    - infinity
  redirect_modes:
    - ue_retry
    - gpu_forward

plotting:
  enabled: true
  formats:
    - png
  dpi: 160
```

---

## 14. CLI要件

### 14.1 単一実行

```text
python -m simulator run --config configs/base.yaml
```

CLI上書き例:

```text
python -m simulator run \
  --config configs/base.yaml \
  --seed 42 \
  --timeout-ms 100 \
  --redirect-mode ue_retry
```

### 14.2 スイープ実行

```text
python -m simulator sweep \
  --config configs/base.yaml \
  --timeout-ms 0 10 25 50 100 200 500 infinity \
  --redirect-modes ue_retry gpu_forward
```

### 14.3 検証のみ

```text
python -m simulator validate-config --config configs/base.yaml
```

### 14.4 期待動作

- 不正な設定は実行前に明確なエラーメッセージを出す。
- `infinity`, `inf`, `none`のうち、少なくとも`infinity`を無限待機として受理する。
- 実行に使用した最終設定を`resolved_config.yaml`として保存する。
- seedごとにワークロードを`workload.csv`へ保存し、異なる \(\tau\) で再利用する。

---

## 15. 出力ディレクトリ

推奨構成:

```text
results/
└── phase1_timeout_redirect/
    ├── resolved_config.yaml
    ├── sweep_summary.csv
    ├── prompt_summary.csv
    ├── region_summary.csv
    ├── figures/
    │   ├── ttft_ecdf.png
    │   ├── ttft_percentiles_vs_timeout.png
    │   ├── ttft_breakdown.png
    │   ├── redirect_rate_vs_timeout.png
    │   ├── gpu_utilization_vs_timeout.png
    │   ├── ue_map.png
    │   └── arrival_time_distribution.png
    └── runs/
        └── seed_000042/
            ├── workload.csv
            ├── timeout_0_ue_retry/
            │   ├── requests.csv
            │   ├── gpu_events.csv
            │   └── summary.csv
            ├── timeout_100_ue_retry/
            └── timeout_infinity_ue_retry/
```

---

## 16. リクエスト単位ログ

`requests.csv`は、1リクエストにつき1行とし、少なくとも次の列を含む。

```text
seed
request_id
ue_id
region_id
ue_x_km
ue_y_km
prompt_tokens
output_tokens
home_gpu_id
remote_gpu_id
final_gpu_id
redirect_mode
redirected
redirect_count
send_time_ms
home_arrival_time_ms
home_queue_enter_time_ms
home_queue_exit_time_ms
timeout_time_ms
redirect_response_arrival_ue_ms
remote_send_time_ms
remote_arrival_time_ms
remote_queue_enter_time_ms
remote_queue_exit_time_ms
process_start_time_ms
first_token_generated_time_ms
first_token_arrival_time_ms
ue_to_home_distance_km
ue_to_remote_distance_km
ue_to_home_propagation_ms
home_queue_wait_ms
home_to_ue_redirect_propagation_ms
ue_to_remote_propagation_ms
gpu_to_gpu_transfer_ms
remote_queue_wait_ms
compute_ms
final_gpu_to_ue_propagation_ms
network_total_ms
queue_total_ms
ttft_ms
```

未使用の時刻列は空欄とし、時間成分列は0とする。

### 16.1 ブレイクダウン整合性

home GPUで処理した場合:

\[
TTFT=
T_{ue\rightarrow home}
+W_{home}
+C
+T_{final\rightarrow ue}
\]

`ue_retry`でリダイレクトした場合:

\[
TTFT=
T_{ue\rightarrow home}
+W_{home}
+T_{home\rightarrow ue}
+T_{ue\rightarrow remote}
+W_{remote}
+C
+T_{remote\rightarrow ue}
\]

`gpu_forward`でリダイレクトした場合:

\[
TTFT=
T_{ue\rightarrow home}
+W_{home}
+T_{gpu\rightarrow gpu}
+W_{remote}
+C
+T_{remote\rightarrow ue}
\]

すべてのリクエストで、成分合計と`ttft_ms`の絶対差が設定された許容誤差以下でなければならない。

---

## 17. GPUイベントログ

`gpu_events.csv`はGPU状態が変化するたびに1行を出力し、少なくとも次の列を含む。

```text
seed
timestamp_ms
gpu_id
event_type
request_id
request_state
queue_length_before
queue_length_after
busy_before
busy_after
current_request_id
```

`event_type`例:

```text
ARRIVAL
ENQUEUE
DEQUEUE_FOR_START
START
TIMEOUT_REMOVE
FIRST_TOKEN_GENERATED
IDLE
```

---

## 18. 集計指標

### 18.1 実行単位の`summary.csv`

```text
seed
timeout_ms
redirect_mode
num_requests
num_left_region
num_right_region
num_home_gpu_a
num_home_gpu_b
redirect_count
redirect_rate
mean_ttft_ms
p50_ttft_ms
p90_ttft_ms
p95_ttft_ms
p99_ttft_ms
max_ttft_ms
mean_home_queue_wait_ms
mean_remote_queue_wait_ms
mean_total_queue_wait_ms
mean_network_total_ms
mean_compute_ms
gpu_a_busy_time_ms
gpu_b_busy_time_ms
gpu_a_utilization
gpu_b_utilization
server_makespan_ms
end_to_end_makespan_ms
```

### 18.2 GPU利用率

GPU利用率は次式とする。

\[
U_j=\frac{GPU_jのbusy時間}{server\_makespan}
\]

`server_makespan`はシミュレーション開始時刻から最後の`GPU_FIRST_TOKEN_GENERATED`までとする。

`end_to_end_makespan`はシミュレーション開始時刻から最後の`FIRST_TOKEN_ARRIVE_UE`までとする。

### 18.3 プロンプト長別集計

`prompt_summary.csv`:

```text
seed
timeout_ms
redirect_mode
prompt_tokens
request_count
redirect_count
redirect_rate
mean_ttft_ms
p50_ttft_ms
p95_ttft_ms
mean_home_queue_wait_ms
mean_remote_queue_wait_ms
```

### 18.4 地域別集計

`region_summary.csv`:

```text
seed
timeout_ms
redirect_mode
region_id
request_count
redirect_count
redirect_rate
mean_ttft_ms
p50_ttft_ms
p95_ttft_ms
p99_ttft_ms
```

### 18.5 複数seed集計

複数seedの結果について、平均、中央値、95% bootstrap信頼区間を計算できるようにする。少なくとも次の指標を対象とする。

- mean TTFT
- P50 TTFT
- P95 TTFT
- P99 TTFT
- redirect rate
- GPU utilization

---

## 19. 可視化要件

最低限、次の図を生成する。

1. **TTFT ECDF**: \(\tau\) または方式ごとの比較
2. **TTFTヒストグラムまたは密度分布**: 方式ごとの分布
3. **TTFT percentile vs timeout**: P50、P95、P99
4. **TTFTブレイクダウン**: network、home queue、redirect path、remote queue、compute
5. **GPUキュー長時系列**: gpu_a、gpu_b
6. **リダイレクト率 vs timeout**
7. **GPU利用率 vs timeout**
8. **UE配置図**: UE、GPU、左右地域境界、home GPU別表示
9. **home GPU到着時刻分布**
10. **プロンプト長別TTFT**
11. **地域別TTFT ECDFまたはP95**

可視化の要件:

- 軸名、単位、凡例、タイトルを明示する。
- \(\tau=\infty\) は文字列`infinity`または`wait-only`として表示する。
- ECDFは左上に位置する方式ほど良いことが読み取れる形式にする。
- ブレイクダウンでは、`ue_retry`のhome→UEとUE→remoteを区別できるようにする。
- 平均だけでなく、P95を主指標として表示できるようにする。
- 全UE、左地域、右地域を分けて表示できるようにする。

---

## 20. ディレクトリおよびモジュール構成

```text
gpu_redirect_sim/
├── README.md
├── pyproject.toml
├── configs/
│   ├── base.yaml
│   └── sweep.yaml
├── simulator/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── engine.py
│   ├── events.py
│   ├── entities.py
│   ├── geometry.py
│   ├── workload.py
│   ├── network.py
│   ├── gpu_link.py
│   ├── service_time.py
│   ├── redirect_policy.py
│   ├── metrics.py
│   ├── plotting.py
│   └── io_utils.py
├── scripts/
│   ├── run_single.py
│   └── run_sweep.py
├── tests/
│   ├── test_config.py
│   ├── test_regional_placement.py
│   ├── test_prompt_assignment.py
│   ├── test_propagation.py
│   ├── test_no_queue.py
│   ├── test_timeout_zero.py
│   ├── test_timeout_infinity.py
│   ├── test_timeout_during_queue.py
│   ├── test_timeout_after_start.py
│   ├── test_same_timestamp_priority.py
│   ├── test_ue_retry_path.py
│   ├── test_gpu_forward_path.py
│   ├── test_no_double_processing.py
│   ├── test_breakdown_consistency.py
│   └── test_reproducibility.py
└── results/
```

設計上、以下を抽象インターフェースとして分離する。

- `NetworkModel`
- `GpuLinkModel`
- `ServiceTimeModel`
- `RedirectPolicy`
- `WorkloadGenerator`

将来のns-3連携、APNモデル、KVキャッシュ転送、複数GPU選択を追加するときに、イベントエンジン全体を書き換えずに済む構造とする。

---

## 21. 設定検証要件

実行前に次を検証する。

- GPU数がMVPでは2であること
- GPU IDが一意であること
- GPU座標が領域内であること
- 地域矩形が領域内であること
- 地域重みが正の数であること
- UE数が1以上であること
- prompt ratioの合計が1であること
- prompt tokensが正の整数であること
- output tokensが1であること
- propagation speedが正であること
- path stretch factorが1以上であること
- service timeが非負であること
- max_redirectsがMVPでは1であること
- redirect modeが`ue_retry`または`gpu_forward`であること
- timeoutが0以上またはinfinityであること
- `gpu_forward`でGPUリンクが無効な場合、転送時間が0になること

設定エラーには、問題のパスと期待値を含むメッセージを出す。

---

## 22. 再現性要件

- 乱数生成には単一のseedから派生した明示的な乱数生成器を使用する。
- グローバル乱数状態に依存しない。
- 同一設定・同一seedでは、UE座標、地域人数、プロンプト割り当て、イベント順序、出力CSVが一致する。
- \(\tau\) やリダイレクトモードを変更しても、同一seedのワークロード自体は変化しない。
- 結果ディレクトリに使用コードのバージョン、Pythonバージョン、依存パッケージ情報を保存できることが望ましい。

---

## 23. 必須テスト

### 23.1 地域配置

- 100 UE、重み2:1で左67、右33になること。
- 各UE座標が割り当て地域の矩形内にあること。
- 既定GPU配置では左UEのhomeがgpu_a、右UEのhomeがgpu_bになること。

### 23.2 プロンプト割り当て

- 比率に基づく件数の合計がUE数と一致すること。
- 同じseedで同じ割り当てになること。
- プロンプト長が異なっても、同じ距離ならネットワーク時間が同じであること。

### 23.3 伝搬時間

- 距離が2倍なら伝搬時間も2倍になること。
- 往復時間が片道時間の2倍になること。
- path stretch factorが正しく反映されること。

### 23.4 キューなし

GPUがアイドルなら、\(\tau=0\)でもリダイレクトされず、即時処理されること。

### 23.5 \(\tau=0\)

home GPUがビジーなら、homeキューに実質的に滞在せずリダイレクトされること。

### 23.6 \(\tau=\infty\)

どれだけ待ってもリダイレクトされないこと。

### 23.7 タイムアウト前の処理開始

処理開始がタイムアウトより前なら、タイムアウトイベントを無視すること。

### 23.8 同一時刻

処理開始時刻とタイムアウト時刻が同一なら、home GPUで処理すること。

### 23.9 homeキューからの削除

タイムアウトしたリクエストがhomeキューから確実に削除され、後からhome GPUで処理されないこと。

### 23.10 UE差し戻し経路

`ue_retry`でイベント順が次になること。

```text
ARRIVE_HOME_GPU
→ HOME_QUEUE_TIMEOUT
→ REDIRECT_RESPONSE_ARRIVE_UE
→ ARRIVE_REMOTE_GPU
→ GPU_PROCESS_START
→ GPU_FIRST_TOKEN_GENERATED
→ FIRST_TOKEN_ARRIVE_UE
```

### 23.11 GPU間転送経路

`gpu_forward`では`REDIRECT_RESPONSE_ARRIVE_UE`が発生せず、GPU間転送時間が0であること。

### 23.12 二重処理防止

1リクエストにつき`GPU_FIRST_TOKEN_GENERATED`が1回だけ発生すること。

### 23.13 再リダイレクト防止

remote GPUで待機しても2回目のタイムアウトが発生しないこと。

### 23.14 TTFT整合性

各リダイレクト方式の成分合計がイベント時刻から計算したTTFTと一致すること。

### 23.15 再現性

同一seedと同一設定で、CSVの行順および数値が再現されること。

---

## 24. 非機能要件

### 24.1 実装品質

- Python 3.11以上を対象とする。
- 型ヒントを付与する。
- `ruff`または同等のlintを通す。
- `pytest`で全テストが成功する。
- 公開関数および主要クラスにdocstringを付ける。
- 例外を握りつぶさず、設定エラーと実行時不変条件違反を区別する。

### 24.2 可読性

- イベント処理、状態遷移、時間計算を分離する。
- TTFTを直接式で一括計算せず、イベント時刻から導出する。
- ブレイクダウンは記録済みイベント時刻から計算する。
- magic numberを避け、設定または定数として定義する。

### 24.3 拡張性

次の追加でコアエンジンの大幅変更を必要としないこと。

- APN実測レイテンシ
- APN帯域
- KVキャッシュサイズと転送
- GPU間直接転送コスト
- 3台以上のGPU
- remote GPU選択ポリシー
- 実測サービス時間CSV
- continuous batchingを近似する処理モデル
- ns-3から取得したUE-GPU遅延トレース

---

## 25. README要件

READMEには少なくとも次を記載する。

- 研究目的と仮説
- MVPの仮定
- UEが左右2:1、各地域一様分布であること
- ネットワーク時間が伝搬時間のみであること
- プロンプト長はネットワーク時間に影響しないこと
- \(\tau\) の意味
- `ue_retry`と`gpu_forward`の経路
- TTFT式
- インストール方法
- 単一実行方法
- sweep実行方法
- 出力ファイル説明
- グラフ説明
- テスト実行方法
- 現在の制約と将来拡張

---

## 26. 受け入れ条件

以下をすべて満たした場合にMVP実装を完了とする。

1. 100 UEを左67、右33に割り当て、各地域内で一様分布できる。
2. 各UEのhome GPUを実距離から決定できる。
3. 全UEを同時刻に送信し、距離差によりhome GPU到着時刻がばらつく。
4. ネットワーク時間は距離、伝搬速度、経路伸長係数だけから計算される。
5. プロンプト長がネットワーク時間に影響しない。
6. プロンプト長と比率を設定し、GPU処理時間に反映できる。
7. GPUはFCFS、同時処理数1で動作する。
8. \(\tau=0\)、有限値、\(\infty\)を扱える。
9. 処理開始済みのリクエストはリダイレクトされない。
10. `ue_retry`でUEへの差し戻しとremote GPUへの再送がイベントとして再現される。
11. `gpu_forward`モードを選択でき、MVPではGPU間転送時間が0になる。
12. リダイレクト後に二重処理、二重キュー登録、再リダイレクトが発生しない。
13. 同一seedのワークロードをすべての \(\tau\) で共通利用できる。
14. リクエスト単位ログ、GPUイベントログ、集計CSVを生成できる。
15. TTFTのECDF、分布、P50/P95/P99、ブレイクダウン、キュー時系列、利用率、UE配置図を生成できる。
16. TTFT成分合計とイベント時刻差が許容誤差内で一致する。
17. すべての必須pytestが成功する。
18. READMEに実行方法、仮定、TTFT式、制約が記載される。

---

## 27. Claude Codeへの実装指示

本要件に従い、まずMVPを完全に実装すること。対象外機能を先回りして複雑に実装しないこと。ただし、`NetworkModel`、`GpuLinkModel`、`ServiceTimeModel`、`RedirectPolicy`を交換可能な構造にし、将来拡張用の設定項目とインターフェースを用意すること。

実装の優先順位は次の通りとする。

1. 設定スキーマと検証
2. 地域別UE配置とプロンプト割り当て
3. 離散イベントエンジン
4. GPU FCFSキューと処理状態
5. \(\tau\) によるタイムアウト
6. `ue_retry`
7. `gpu_forward`
8. CSVログと整合性検証
9. sweepと複数seed
10. グラフ
11. README
12. pytest、lint、型チェック

実装中に要件の矛盾または未定義動作を発見した場合は、勝手に重要な仕様を変更せず、READMEの「Assumptions」に明記し、最小限の合理的な仮定で進めること。

