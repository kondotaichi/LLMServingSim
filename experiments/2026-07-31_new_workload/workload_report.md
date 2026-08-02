# 100台のRTX 4090を用いた都市型会話ワークロード設計

## 1. 目的

本レポートでは、11 km²の都市領域に居住する20万人のユーザーと、100台のRTX 4090からなるLLM serving環境を想定し、地理routing、KV cache再利用、KV cache移送、Pipeline Parallelism（PP）、および投機的KV事前移送を比較するためのワークロードを定義する。

既存の都市ワークロードで使用している人口、地理配置、request rate、input/output token長の傾向を引き継ぎつつ、独立requestを複数ターンの会話sessionへ拡張する。これにより、会話履歴がターンごとに蓄積され、後半のrequestほどprefix KV cacheの再利用率が高くなる状況を表現する。

## 2. 都市およびユーザー設定

| 項目 | 設定 |
|---|---:|
| 面積 | 11 km² |
| 領域形状 | 正方形 |
| 一辺の長さ | 約3,316.6 m |
| 総ユーザー数 | 200,000 users |
| 人口密度 | 約18,181.8 users/km² |
| Daily active user比率 | 10% |
| Daily active users | 20,000 users |

ユーザーは領域内へ一様ランダムに配置する。各ユーザーの位置は実験中固定し、ユーザー移動は扱わない。各ユーザーには最寄りのGPUをHome GPUとして割り当てる。

ユーザー移動を除外することで、TTFTの変化を地理的移動ではなく、GPU負荷、queue、KV cache配置、およびrouting方式の差として評価できる。

## 3. GPU配置とhardware設定

| 項目 | 設定 |
|---|---:|
| GPU | NVIDIA GeForce RTX 4090 |
| GPU総数 | 100 |
| 配置 | 10行×10列の等間隔grid |
| 平均担当面積 | 0.11 km²/GPU |
| 平均担当人口 | 2,000 users/GPU |
| GPU memory | 24 GB/GPU |
| GPU memory bandwidth | 1,008 GB/s |
| Host memory | 128 GB/node |
| Host memory bandwidth | 33.8 GB/s |
| Model | Llama 3.1 8B |
| Weight dtype | BF16 |
| Tensor Parallelism | TP=1 |

比較時はGPU総数を100台に固定する。

| PP構成 | Logical instance数 | GPU/instance | GPU総数 |
|---|---:|---:|---:|
| PP=1 | 100 | 1 | 100 |
| PP=2 | 50 | 2 | 100 |

したがって、PP=2を100 logical instancesで実行して200 GPU相当とすることは避ける。PP=2では隣接する2台のGPUを1つのlogical instanceとして扱う。

## 4. Traffic model

既存ワークロードと同じ仮定を用いる。

| 仮定 | 値 |
|---|---:|
| 1 active user当たりrequest数 | 20 requests/day |
| 日次request数 | 400,000 requests/day |
| Busy hourへの集中率 | 日次requestの15% |
| Peak倍率 | Busy hourの2倍 |

これにより、全ターン換算のrequest rateは次のようになる。

| Load level | Request rate | 4ターンsessionの開始rateの目安 |
|---|---:|---:|
| `daily_average` | 約4.63 requests/s | 約1.16 sessions/s |
| `busy_hour` | 約16.67 requests/s | 約4.17 sessions/s |
| `peak_2x` | 約33.33 requests/s | 約8.33 sessions/s |

Session開始はPoisson processとする。後続ターンは事前に固定時刻で投入せず、前ターンの完了後にユーザーのthink timeを加えて投入するclosed-loop workloadとする。

## 5. 会話session

基準ワークロードは以下とする。

- 750 sessions
- 4 turns/session
- 合計3,000 LLM requests
- 複数ユーザーのsessionを時間的にinterleave
- 同一sessionの全ターンで同じ`user_id`を使用
- 異なるsession間ではprefixを共有しない

同一sessionの次ターンの到着時刻は、

$$
\boxed{
a_{s,t+1}
=
f_{s,t}
+
\tau_{s,t}^{\mathrm{think}}
}
$$

とする。ここで、$f_{s,t}$は第$t$ターンの生成完了時刻、$\tau_{s,t}^{\mathrm{think}}$はthink timeである。

Think timeは中央値10秒の対数正規分布とし、上限を60秒とする。この値はMethod-Cが参照する直近30秒の履歴内に、一定割合の継続ユーザーが含まれるように設定している。

## 6. 会話履歴とKV cache再利用

会話$s$の第$t$ターンについて、次の記号を用いる。

| 記号 | 意味 |
|---|---|
| $L_{s,t}$ | 第$t$ターンのinput token数 |
| $O_{s,t}$ | 第$t$ターンのoutput token数 |
| $C_{s,t}$ | 第$t$ターンで再利用可能なprefix token数 |
| $U_{s,t}$ | 第$t$ターンで新しく追加されるuser token数 |

前ターンのinputとoutputを次ターンの会話履歴として継承するため、

$$
\boxed{
C_{s,t}
=
L_{s,t-1}+O_{s,t-1}
}
$$

$$
\boxed{
L_{s,t}
=
C_{s,t}+U_{s,t}
}
$$

とする。したがって、第$t$ターンの潜在的なprefix reuse率は、

$$
\boxed{
r_{s,t}^{\mathrm{reuse}}
=
\frac{C_{s,t}}{L_{s,t}}
}
$$

となる。

基準実験では、ターンの進行に伴ってreuse率を次のように増加させる。

| Turn | 目標reuse率 |
|---:|---:|
| 1 | 0% |
| 2 | 50% |
| 3 | 約67% |
| 4 | 75% |

目標reuse率$r_{s,t}^{\mathrm{reuse}}$から新規user token数を決める場合は、

$$
\boxed{
U_{s,t}
=
C_{s,t}
\left(
\frac{1-r_{s,t}^{\mathrm{reuse}}}
{r_{s,t}^{\mathrm{reuse}}}
\right)
}
$$

を用いる。

ここで定義するreuse率は、会話内容から理論上再利用可能な割合である。実際のcache hit率は、KV cacheのevictionやrequestのredirectによって低下する可能性がある。そのため、評価時には潜在reuse率と実効reuse率を区別する。

$$
\boxed{
r_{s,t}^{\mathrm{actual}}
=
\frac{\text{実際にcache hitしたtoken数}}
{L_{s,t}}
}
$$

## 7. Token長

初回input token数は次の3種類からsamplingする。

| Session size | Turn 1 input | 確率 | Turn 4 inputの目安 |
|---|---:|---:|---:|
| Short | 512 | 30% | 約4,000 |
| Medium | 1,000 | 45% | 約6,000 |
| Long | 2,000 | 25% | 約10,000 |

Output token数は既存ワークロードの分布を維持する。

| Output tokens | Probability |
|---:|---:|
| 128 | 30% |
| 256 | 40% |
| 512 | 20% |
| 1,024 | 10% |

Input token数の上限は12,000 tokensとする。最初の比較では履歴truncateによるprefix構造の変化を避けるため、原則として4ターンでsessionを終了する。

代表的なLong sessionは次のように成長する。

| Turn | Input tokens | Reusable prefix | Reuse率 | Output例 |
|---:|---:|---:|---:|---:|
| 1 | 2,000 | 0 | 0% | 256 |
| 2 | 4,512 | 2,256 | 50% | 256 |
| 3 | 7,152 | 4,768 | 約67% | 256 |
| 4 | 9,877 | 7,408 | 75% | 256 |

## 8. Prefix contentの生成条件

同一session内のtoken列は、

$$
X_{s,t}
=
X_{s,t-1}
\mathbin{\Vert}
Y_{s,t-1}
\mathbin{\Vert}
U_{s,t}
$$

として構成する。$X_{s,t-1}$は前ターンのinput、$Y_{s,t-1}$は前ターンのoutput、$U_{s,t}$は新しいuser messageである。

`reuse_prefix_toks`の数値だけを設定するのではなく、再利用対象となる先頭token列を実際に一致させる。Sessionごとに固有のtoken列を生成し、無関係なユーザー間で偶然同じprefixを共有しないようにする。

## 9. Network設定

既存のRTX 4090都市ワークロードの設定を引き継ぐ。

| 項目 | 設定 |
|---|---:|
| User access throughput | 100 Mbit/s |
| 距離遅延 | 5 ns/m |
| Protocol overhead | 500 bytes/request |
| Input token payload | 4 bytes/token |
| First-token payload | 100 bytes |
| GPU backbone bandwidth | 16 GB/s |
| GPU backbone latency | 20 µs |
| CPU staging bandwidth | 33.8 GB/s |

KV cache移送の比較では、GPU backboneとCPU stagingの設定を全手法で共通にする。

## 10. Simulator設定

| 項目 | 設定 |
|---|---:|
| Prefix caching | Enabled |
| Chunked prefill | Enabled |
| KV block size | 16 tokens |
| Max batched tokens | 2,048 |
| Max sequences | 128 |
| Weight dtype | BF16 |
| KV cache dtype | Auto |

Random seedは1、2、3の3種類を使用する。全比較手法で、session、ユーザー、token長、token内容、session開始順、およびthink timeの乱数列を共通化する。

## 11. 実験で区別する条件

主実験には`peak_2x`を使用し、`daily_average`と`busy_hour`を負荷感度分析に使用する。また、主条件は最終ターンreuse率75%とし、最終ターンreuse率50%のsessionをreuse感度分析として追加する。

100 GPUに対して元のrequest rateが軽く、redirectやKV移送がほとんど発生しない場合は、同一request列のarrival間隔だけを圧縮した負荷倍率を別条件として追加する。人口、ユーザー位置、token列、およびsession構造は変更しない。

$$
\lambda_k=k\lambda,
\qquad
k\in\{1,2,4,8\}
$$

元の人口モデルによる負荷とstress testの結果は混ぜず、別々に報告する。

## 12. 評価指標

- TTFTのmean、p50、p95、p99
- Turn別TTFT
- Session完了時間
- 潜在prefix reuse率
- 実効prefix cache hit率
- Redirect率
- KV cache移送回数、移送量、移送時間
- Method-Cのprewarm回数、hit率、無駄移送率
- Router queue時間
- Scheduler queue時間
- Prefill計算時間
- GPU utilization
- KV capacity pressure
- Request throughput

特にTurn別TTFTを重視する。Turn 1ではcache再利用が存在しないため手法差は小さく、Turn 3およびTurn 4でKV cache移送と事前移送の効果が現れることを想定する。

## 13. 現在の実装状態

既存の`workloads/*.jsonl`は、地理配置とtraffic modelを確認するための独立request baselineであり、prefix reuseは無効である。本レポートで定義した4ターン会話session workloadは、そのbaselineを拡張する次段階の設計である。

したがって、既存JSONLへ単純に`reuse_prefix_toks`を追加するのではなく、session依存関係、共通prefix token列、およびclosed-loop arrivalを含む新しいworkloadを生成する必要がある。
