# Method Fix検証 最終まとめ

## 1. この検証の目的と最終判断

このフォルダでは、従来の手法比較をそのまま受け入れず、主に次の点を再検証した。

1. PP2環境でもKV migrateに追加効果があるか
2. PP1とPP2を公平なcapacityで比較できているか
3. PP2でRouter queueが観測されにくかった理由は実装不具合か、負荷条件か
4. Routerの1 ns間隔再試行をイベント駆動へ変更すべきか
5. 頻出ユーザーに対する投機的KV prewarmに実用上の効果があるか

最終的な方針は次のとおりである。

- **RedirectとKV migrateは維持する。** 局所的な負荷偏りがある場合、cold redirectより
  KV migrateが有効だった。
- **PP2も維持する。** PP1/PP2のcluster-wide capacityを揃えた比較でも、PP2 + KV
  migrateが平均TTFTで最良だった。
- **投機的実行（proactive KV prewarm）は採用しない。** 完全予測oracleの上限が限定的で、
  現行recent-countは再訪率45%のワークロードでもhit率が低く、Peak 5xではTTFTを
  悪化させた。
- **Routerのcapacity再試行は従来の1 nsポーリングへ戻した。** 非効率ではあるが、
  イベント駆動化の試作は同値性と安定性を十分確認できなかったため、今回の範囲では
  安定版を優先した。

したがって、今回の推奨構成は **redirectあり、KV migrateあり、PPあり、proactive
prewarmなし** である。ただし、これは1 seedの機構検証に基づく選択であり、現実の
本郷トラフィックに対する最終的な最適性を証明したものではない。

## 2. 元のワークロードでKV migrateの効果を測れなかった理由

従来の300リクエスト版は、12台のGPUへリクエストが比較的均等に配置されていた。
PP2では6 logical instanceとなるが、各instanceの処理能力とKV容量に余裕があり、
busy hourからPeak 10xまでredirectが発生しなかった。

KV migrateはredirectされたリクエストにしか作用しない。したがって、当時の
PP2 + KV migrateの好成績は、実質的にはPP2そのものの効果であり、PP2上でKV migrateが
有効だったことを示してはいなかった。

到着時間だけを12x、15x、20x、30xへ圧縮した均等負荷でもredirectは0件だった。

| 負荷 | Redirect | 平均TTFT | p95 | p99 |
|---|---:|---:|---:|---:|
| 12x | 0/300 | 2165.0 ms | 4071.6 ms | 4362.7 ms |
| 15x | 0/300 | 2444.2 ms | 4636.6 ms | 4912.0 ms |
| 20x | 0/300 | 2712.0 ms | 5124.4 ms | 5426.0 ms |
| 30x | 0/300 | 2995.8 ms | 5598.8 ms | 5900.0 ms |

単位時間当たりの総リクエスト数が同じでも、負荷の空間分布はredirectとKV migrateの
発火可能性を変える。均等負荷では全instanceが同時に混雑し、homeがadmissibleでなく
なる頃には有効な移送先も残らない。一方、局所hotspotではhomeだけが先に容量不足に
なり、他instanceの余剰capacityへ移送できる。この差がKV migrateを評価できるかどうかを
決めた。

## 3. PP2上でKV migrateを発火させたワークロード

機構確認には`workloads/pp2/load_12x_hot50.jsonl`を使用した。

| 項目 | 設定 |
|---|---:|
| リクエスト数 | 300 |
| ユーザー数 | 288 |
| 再訪率 | 4%（12件） |
| 到着時間幅 | 約2.76秒 |
| 実効到着率 | 約108.8 requests/s |
| Input tokens | 平均3860、p50 3499、p95 5844 |
| Output tokens | 平均279.6、p50 205.5、p95 745.2 |
| 再利用可能prefix | 平均1922.1 tokens（inputの約49.8%） |
| モデル | Meta Llama 3.1 8B |
| GPU | RTX 4090 × 12 |
| PP2 logical instance | 6 |

Instance 0へ150件（50%）、残る5 instanceへ各30件（各10%）を配置した。hotspotは
時間軸全体へ分散させ、instance 0のKV予約容量が不足する一方、他instanceには移送先と
なる余剰capacityが残る状態を作った。

この条件で発生した92件のredirect理由はすべて`npu_memory`だった。接続可能数は
12〜20/32であり、`max_num_seqs`上限ではない。つまり、この実験のredirectは
sequence slot不足ではなく、最大contextまで予約するKV容量不足によって発火した。

## 4. PP2とKV migrateの初期機構プローブ

Routing候補と宛先選択を揃え、redirect先でprefixを再計算するcold版と、KVを移送する
KV版だけを比較した。

| 指標 | PP2 cold | PP2 KV | KVによる改善 |
|---|---:|---:|---:|
| Redirect | 92 | 92 | 同一集合 |
| 平均TTFT | 4104.8 ms | 3515.7 ms | 589.1 ms（14.4%） |
| p50 | 2733.4 ms | 2044.0 ms | 689.4 ms |
| p95 | 12086.6 ms | 10947.2 ms | 1139.4 ms |
| p99 | 15509.2 ms | 15509.2 ms | 0 ms |

PP2上でもKV migrateがcold redirectより有効であることは確認できた。ただし、PP1と
PP2でlogical instance当たりの上限を同じ`max_num_seqs=32`、token budget 2048にして
おり、logical instance数が半分のPP2はcluster-wide capacityも半分になっていた。
この数値をそのままPP1/PP2の優劣に使うことはできない。

## 5. Capacityを揃えた5手法比較

PP1を基準に、PP2ではlogical instance当たりの上限を2倍にした。

| 設定 | PP1 | PP2 |
|---|---:|---:|
| Logical instance数 | 12 | 6 |
| `max_num_seqs`/instance | 32 | 64 |
| Cluster-wide sequence上限 | 384 | 384 |
| Token budget/instance | 2048 | 4096 |
| Cluster-wide token budget | 24576 | 24576 |

この補正後の結果は次のとおりである。

| 手法 | 平均TTFT | p50 | p95 | p99 | Redirect |
|---|---:|---:|---:|---:|---:|
| Naive PP1 | 9822.6 ms | 1111.9 ms | 38919.5 ms | 43137.2 ms | 0 |
| Cold redirect PP1 | 3587.4 ms | 2987.6 ms | 9408.9 ms | 10509.3 ms | 158 |
| KV migrate PP1 | 2611.6 ms | 2036.4 ms | 6895.5 ms | 8100.7 ms | 160 |
| Cold redirect PP2 | 1681.5 ms | 1609.2 ms | **3659.4 ms** | **4534.9 ms** | 92 |
| KV migrate PP2 | **1367.3 ms** | **1170.2 ms** | **3659.4 ms** | **4534.9 ms** | 92 |
| PP2 + KV + proactive | **1367.3 ms** | **1170.2 ms** | **3659.4 ms** | **4534.9 ms** | 92 |

この比較から、次が分かった。

- Naiveは一部のhomeへリクエストを閉じ込め、p95が約39秒まで悪化した。
- Redirectは局所hotspotを逃がす手段として不可欠だった。
- KV migrateはPP1でcold比27.2%、PP2でcold比18.7%平均TTFTを改善した。
- PP2 + KV migrateが平均とp50で最良だった。
- PP2 coldとPP2 KVのp95/p99は同一で、KV migrateだけでは最悪tailを解消できなかった。
- Proactive版は36回配置したがhitが0件で、非proactive版と完全に同一だった。

### 「相乗効果」の正確な解釈

PP2とKV migrateは併用でき、組み合わせが最良だった。しかし、KV migrateの限界改善は
PP1で975.8 ms、PP2で314.1 msであり、交互作用は`-661.6 ms`だった。したがって、
統計的な意味で超加算的な相乗効果が確認されたわけではない。

正確には、**PP2上でもKV migrateは18.7%の追加改善を持つが、PP2がすでにqueueを
減らしているため、KV migrateが追加で救える余地はPP1より小さかった**と解釈する。

## 6. PP2でRouter queueが見えにくかった理由

KV migrateが発生することとRouter queueが発生することは同義ではない。

- KV redirect：homeが容量不足でも、別instanceが受け入れ可能なら即座に移送する
- Router queue：Routerが確認する全候補が受け入れ不能な場合にのみ待機する

Capacity-matched hotspotでは、homeのKV容量は不足したが他instanceに余裕があった。
そのため92件をredirectでき、Router queue平均はPP2 coldで約0.5 ms、PP2 KVで
約1.6 msに留まった。Router queueがほぼゼロなのは、KV容量判定が働いていないから
ではなく、redirectによって待機を回避できたからである。

### 全候補のcapacityを超えるプローブ

PP2、6 logical instance、`max_num_seqs=1`とし、最初の6件で全instanceを占有した後、
さらに6件を到着させるtwo-waveワークロードを作った。

| 指標 | 観測値 |
|---|---:|
| リクエスト数 | 12 |
| Router待ち発生 | 6件 |
| Router待ち平均（全12件） | 1729.5 ms |
| Router待ち最大 | 3459.2 ms |
| Capacity retry合計 | 2,058,335回 |

この結果により、PP2でも全候補のcapacityを超えればRouter queueが蓄積することを確認
した。したがって、PP2固有の「Router queueが消える実装不具合」が主因だったわけでは
ない。通常のPP2実験ではcluster全体に空きが残り、redirectで吸収できていた。

なお、このtwo-waveはRouter待ちの発火確認用に`max_num_seqs=1`まで下げた人工的な
ストレステストであり、実運用性能の比較には使えない。

## 7. 1 ns再試行問題

Routerは全候補が受け入れ不能なとき、該当リクエストの次回到着時刻を
`current_time_ns + 1`へ更新して再試行する。空きが数ms後にしか生まれなくても1 nsごとに
判定するため、シミュレーション上の結果を変えずに膨大なループが発生する。

Two-waveプローブでは6件の待機だけで約206万回のretryとなった。この問題はPP2専用
ではなく、PP1でも全候補が飽和すれば起きる。PP2でRouter queueを意図的に発生させた
ことで顕在化した。

イベント駆動にすれば、次にcapacityが変化し得る時刻まで直接進められるため、理論上は
シミュレーション時間を短縮できる。一方で、正しいwake-up eventの列挙、同時刻eventの
順序、routing再評価による状態変化を厳密に扱う必要がある。試作では極端な600件条件を
安定して完走できず、従来結果との同値性も十分確認できなかった。

このため今回の最終状態では、性能上は非効率でも挙動が既知である1 ns再試行へ戻した。
イベント駆動化は独立した改善課題とし、small-caseでrequest単位のrouting、TTFT、完了
順序が一致する回帰確認を用意してから導入すべきである。

## 8. 投機的実行の再評価

### 8.1 従来300件版の問題

従来ワークロードは300件中288ユーザー、再訪12件、再訪率4%だった。頻出ユーザーを
選んでKVを先回り配置するrecent-count方式に必要な再訪信号がほぼ存在しなかった。
この結果だけで投機的実行を否定することはできなかった。

元の2000件版には1129ユーザーと871件の再訪があり、再訪率は43.55%だった。問題は
「300件に固定したこと」そのものより、先頭300行で切り、セッション継続を失ったこと
だった。

### 8.2 Perfect-prewarm oracle

Oracleは、redirect先と再利用prefixを完全に予測し、request-visibleなKV転送時間、
background bandwidth、expiry、cache pollutionをすべて無料とする楽観的上限である。

Peak 7xの従来300件では、通常KV redirectに対し平均TTFTを1754.9 msから1704.0 msへ
2.9%改善した。完全予測でもシステム全体への改善は小さく、現行proactiveは4 hitで
1755.7 msとなり通常版を上回れなかった。

2000件・再訪率43.55%のPeak 7xでは、oracleが通常版より悪化した。

| 指標 | 通常KV | Oracle |
|---|---:|---:|
| 平均TTFT | 47211.9 ms | 48283.3 ms |
| p50 | 46084.5 ms | 47456.1 ms |
| p95 | 102446.9 ms | 104170.7 ms |
| p99 | 111049.0 ms | 108726.6 ms |
| Redirect | 1667 | 1679 |

これは「KV転送を消すと各requestが必ず遅くなる」という意味ではない。極端な飽和下で
処理時刻が変わり、後続のrouting、redirect先、batch形成、queue順序も変わるため、局所的
な短縮がシステム全体の平均改善へ単調には結び付かなかった。単一runの動的フィードバック
をoracleの本質的な害と断定することもできないが、少なくとも大きな安全余裕は見えない。

### 8.3 300件・再訪率45%の比較

セッションを壊さず165ユーザーを選び、合計300件、再訪135件、再訪率45%の
ワークロードを生成した。コンテンツを固定し、到着時間だけを変えてPeak 2x、5x、10xを
作成した。

| 負荷 | 観測時間 | 到着率 |
|---|---:|---:|
| Peak 2x | 16.597秒 | 18.076 requests/s |
| Peak 5x | 6.639秒 | 45.190 requests/s |
| Peak 10x | 3.319秒 | 90.380 requests/s |

比較方式は通常KV redirect、現行recent-count、oracleの3方式である。

#### Peak 2x

| 手法 | 平均TTFT | p50 | p95 | p99 | Redirect |
|---|---:|---:|---:|---:|---:|
| 通常KV | 377.0 ms | 232.1 ms | 1009.6 ms | 2051.0 ms | 52 |
| Recent-count | 372.6 ms | 232.9 ms | 1115.4 ms | 2164.4 ms | 54 |
| Oracle | 342.9 ms | 221.5 ms | 1076.3 ms | 1839.3 ms | 59 |

Recent-countは平均を4.4 ms（1.17%）改善したが、p95/p99を悪化させた。112回の先回り
配置に対しhitは8件、配置単位hit率は約7.1%、配置tokenの有効率は約6.7%だった。
Oracleの平均改善は34.1 ms（9.0%）であり、低〜中負荷では理論的余地が存在したが、
現行予測器が回収できたのはその一部だった。

#### Peak 5x

| 手法 | 平均TTFT | p50 | p95 | p99 | Redirect |
|---|---:|---:|---:|---:|---:|
| 通常KV | 1856.9 ms | 822.7 ms | 7320.0 ms | 9091.6 ms | 123 |
| Recent-count | 1918.2 ms | 788.2 ms | 7590.2 ms | 9464.2 ms | 119 |
| Oracle | 1793.1 ms | 762.9 ms | 7033.8 ms | 8255.9 ms | 126 |

Recent-countはp50を改善した一方、平均を61.3 ms（3.3%）、p95/p99も悪化させた。
120回の配置に対してhitは8件（6.7%）、配置278,848 tokensに対して有効だったのは
19,856 tokens（7.1%）だった。推定background transferは合計約29.5秒であり、現在は
これを無料としている。それでも悪化した点は否定的である。

Peak 5xでは通常版のRouter待ち平均が1274.9 msと支配的だった。OracleでKV transferを
平均90.8 msから0へ消しても、動的なqueue変化を含む正味改善は63.8 ms（3.4%）に
留まった。高負荷では、KV転送より全候補のcapacity待ちが主要因になる。

Peak 10xは通常版のみ完走した。投機的実行を不採用とする方針決定後にrecent-countと
oracleの追加実行を停止したため、3方式比較の根拠には含めない。

## 9. 投機的実行を採用しない理由

今回の不採用判断は、再訪率の低いワークロードだけに基づくものではない。再訪率45%へ
補正した条件でも次が観測された。

1. Recent-countの配置hit率はPeak 2x/5xとも約7%に留まった。
2. 配置したKV tokenの約93%が観測上のhitへ結び付かなかった。
3. Background転送を無料とする有利なモデルでも、Peak 5xの平均とtailを悪化させた。
4. Perfect oracleの改善上限もPeak 5xで3.4%に留まり、極端な2000件負荷では平均が
   悪化した。
5. 投機配置はcapacity、cache residency、将来のroutingを変え、誤予測時の副作用がある。

将来、投機的実行を再検討するなら、recent-countの微調整ではなく、宛先予測を含む
request-level予測、TTL、cache pollution、background bandwidthを明示的にモデル化し、
複数seedの分離評価でoracleとの隔たりを確認する必要がある。現段階では、その複雑性に
見合う改善根拠がない。

## 10. 批判的に見た制約

- 多くの比較は1 seedであり、差の信頼区間を求めていない。
- 50% hotspotと`max_num_seqs=1`のtwo-waveは機構確認用の人工条件である。
- Network contention、queueing、jitter、packet lossが無効で、KV migrateとproactive
  transferに有利である。
- PP2のcapacity補正はcluster-wide上限を揃えるが、batch形成やpipeline bubbleまで
  PP1と等価にするものではない。
- Hotspot生成ではlogical homeを変更した一方、ユーザー座標と一部の通信latencyを
  再生成していない。
- Oracleでもroutingとschedulerの状態が変わるため、request単位の反実仮想として完全に
  同一経路を比較しているわけではない。
- Proactiveのbackground transferは無料であり、実際の帯域競合を入れれば結果がさらに
  悪化する可能性がある。

以上から、今回の結論は「すべての環境でPP2 + KV migrateが最適」という一般則では
なく、**局所偏りと移送先余力がある今回の条件では、capacityを公平に揃えたPP2 + KV
migrateが最も妥当であり、投機的実行を追加する証拠は得られなかった**という範囲に
限定される。

## 11. 主要artifact

- Capacity-matched 5手法詳細：`report/corrected_hotspot_five_method_results.md`
- PP2 + KV機構プローブ：`report/pp2_kv_migration_synergy.md`
- Peak 7x oracle：`report/peak_7x_oracle.md`
- TTFT CDF：`figures/corrected_hotspot_ttft_cdf.svg`
- TTFT breakdown：`figures/corrected_hotspot_ttft_breakdown.svg`
- Router queueプローブ：`results/five_arm_pp2_router_queue_two_wave_msq1/`
- 再訪率45%ワークロード：`workloads/revisit45_300/`
- 再訪率45%の結果：`results/revisit45_300_three_way/`
