# GPU選定モデル改善の経緯

Redirect時にどの候補GPUへ送るかを決めるモデル(`OfflineTtftFormula` /
policy `NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE`)が実際に良い選択をできて
いるかを検証し、必要なら改善するための一連の作業記録。9項目の検証計画
(README.md参照)のうち、6〜8(特徴量追加・二段階モデル化・目的関数比較)
に対応する部分を中心にまとめる。

## 0. 出発点: 5つのredirectポリシー

| Policy | 内容 |
|---|---|
| A: `NEAREST_KV` | redirectしない(最寄りGPUで待つ) |
| B: `NEAREST_MIGRATE` | redirectするがKV引き継ぎなし(コールドプリフィル) |
| C: `NEAREST_MIGRATE_KV` | redirect + KV引き継ぎ |
| D: `NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE` | 複数候補から**ヒューリスティック(capacity pressure)**で選ぶ |
| E: `NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE` | 複数候補から**学習済みTTFT予測式(OfflineTtftFormula)**で選ぶ |

E(学習モデル)がD(単純ヒューリスティック)より本当に優れているかを検証する
ことが、この一連の作業全体の目的。

## 1. `OfflineTtftFormula`(現行モデル) — 課題: 予測の中身が見えない

`route_ms + scheduler_ms + compute_ms` を線形式で予測する回帰モデル。導入
時点では「予測が良いか」を判断する材料(候補ごとの内訳、実測との比較)が
一切出力されていなかった。

**課題**: ブラックボックスで、redirect判断が本当に妥当か検証できない。

## 2. 診断の追加(1〜3) — 課題: 予測が頻繁に0にクリップされ、ヒューリスティックとも一致しない

- `router.py`に`candidate_diagnostics`収集・`save_candidate_diagnostics()`を追加し、
  全candidate GPUの特徴量と予測内訳をCSV出力するようにした。
- `ttft_formula.py`に`scheduler_raw_ms`(クリップ前の生の線形出力)を追加露出。

**わかったこと**:
- Scheduler成分の予測は多くの候補で負の値になり、`max(0, ...)`で0にクリップ
  されていた(18 redirect中、候補の相当割合で発生)。
- モデルの候補順位(`model_ttft_rank`)と単純なcapacity-pressure順位
  (`capacity_pressure_rank`)の一致率はわずか**39%**(18件中7件)。

**課題**: 「モデルは単純指標と違う何かを見ている」ことは分かったが、
それが「モデルの方が正しい」のか「モデルの方が間違っている」のかは、
予測同士の比較だけでは判定できない。実測の正解データが必要。

## 3. Counterfactual検証基盤(4) — 課題: 選ばれなかった候補の実測TTFTがそもそも存在しない

ベースライン実行では、各redirect判断でモデルが選んだ1候補にしか実際には
リクエストを流していないため、選ばれなかった候補(全161候補中143個)の
「本当のTTFT」が存在しなかった。

`router.py`に`--counterfactual-request-id` / `--counterfactual-target-instance-id`
を追加し、「その1回の判断だけ強制的に指定候補へ送り、それ以外はベースライン
通り」に再実行するone-decision counterfactual機構を実装。143件の追加シミュ
レーションが必要になった(現在も実行中、進行状況はREADME.md参照)。

## 4. 最初の残差モデル試作(`partial_candidate_model.json`, ridge_residual_provisional) — 課題: 候補間の比較情報が構造的に存在しない

Counterfactualシミュレーションが揃うまでの暫定として、既知の18件
(いずれもベースラインで**実際に選ばれた1候補のみ**)を使ってridge回帰で
残差(実測TTFT − 予測TTFT)を学習した。

**課題**: メタデータに明記した通り
`requests_with_multiple_labels: 0`, `within_request_pairs: 0`,
`suitable_for_policy_deployment: false`。
1リクエストにつき1候補しかラベルが無いため、「同じリクエストの中でどの
候補が優れているか」を学習する信号が原理的に存在しない。単なる診断用の
プレースホルダーであり、ランキング改善には使えないモデルだった。

## 5. 実測でのランキング精度測定(5) — 課題: 現行モデルがヒューリスティックに実際に負けていた

Counterfactualシミュレーションが1リクエスト分揃うたびに(n=4→5)、
`analyze_counterfactual_candidates.py`で実測ベースの精度を計算。

| n | 現行モデル(formula_only) Top-1 | pressureヒューリスティック Top-1 | 現行モデル Spearman | pressure Spearman |
|---|---:|---:|---:|---:|
| 4 | 0% | 25% | 0.042 | 0.492 |
| 5 | 0% | 20% | 0.047 | 0.403 |

**わかったこと**: サンプルはまだ小さいが、方向性は一貫している。
**現行の学習済みモデルは、単純なcapacity-pressureヒューリスティックより
実測ベースで悪い**。item 2〜3で見えていた「モデルは何か違うものを見ている」
という違和感が、ここで初めて「モデルの方が悪い」という向きで裏付けられた。

## 6. 今回実装した比較(`scripts/evaluate_candidate_models.py`) — item 6/7/8を切り分けて検証

前段の「残差モデルは効かなかった」という結果には、実は2つの原因が混ざって
いた可能性がある: (a) 二段階構造そのものが有効でない、(b) 特徴量が候補間の
違いを捉えられていない。この2つを混同したまま次の改善に進むと、効果の
無い変更を追加してしまうリスクがあるため、要素ごとに切り離して評価する
ことにした。また、item 4以外のシミュレーションが100件以上残っている状態
でも早期に方向性を確認したいという要望があったため、既存の実測ラベル
(現時点でフル解決済み5リクエスト)を使い、**leave-one-request-out CV**
(同一リクエスト内の候補が学習と評価に同時に混ざらないようにする)で
5つのバリアントを比較する評価スクリプトを新規作成した。

比較したバリアント:

| バリアント | 対応item | 内容 |
|---|---|---|
| `formula_only` | (現行) | `OfflineTtftFormula`の予測をそのまま使用。フィッティングなし |
| `pressure` | (既存比較対象) | capacity-pressureが最小の候補を選ぶヒューリスティック |
| `stage2_base` | #7のみ | 旧15特徴量のまま、絶対TTFT残差を学習する二段階モデル |
| `stage2_expanded` | **#6 + #7** | 候補固有特徴量(`capacity_pressure_rank`, `model_ttft_rank`, `candidate_required_kv_bytes`, `scheduler_clipped_ms`など)を追加した二段階モデル |
| `rank_model` | **#8** | 絶対TTFTではなく`actual_regret_ms`(相対順位)を直接学習するモデル |

### 結果(n=5、leave-one-request-out)

| モデル | Top-1正解率 | 平均regret | p95 regret | 平均Spearman |
|---|---:|---:|---:|---:|
| formula_only | 0% | 88.0 ms | 245.7 ms | 0.047 |
| pressure | 20% | 48.8 ms | 183.6 ms | 0.403 |
| stage2_base | 0% | 61.7 ms | 214.2 ms | −0.050 |
| **stage2_expanded** | **20%** | **8.1 ms** | **15.4 ms** | 0.133 |
| rank_model | 0% | 52.0 ms | 183.6 ms | −0.010 |

### 解釈

- **#7(二段階化)だけでは効かなかった**: `stage2_base`はSpearmanがむしろ
  悪化しており、旧特徴量のまま残差を学習しても候補間の違いを正しく
  補正できていない。上記(a)/(b)の切り分けで言うと、原因は(b)寄りだった
  ことが示唆される。
- **#6(候補固有特徴量の追加)が効いている**: `stage2_expanded`のみが
  pressureヒューリスティックとTop-1で並び、regretの大きさ(外した時の
  外し方)を桁違いに縮小した。
- **#8(regretを直接学習)は今回は#6+#7に負けた**: `rank_model`は
  `stage2_expanded`より明確に劣り、pressure/formula_onlyに近い結果
  だった。この小サンプルでは「絶対TTFTの残差を学習→補正」の方が
  「regretを直接学習」より安定して機能している。

## 重要な留保

上記の結果はすべて**n=5(5リクエストのleave-one-out)**に基づく。1件の
勝敗が入れ替わるだけでTop-1正解率が20ポイント動く水準であり、
**方向性の初期シグナルであり結論ではない**。まだ本番の`ttft_formula.py`
/`router.py`には組み込んでいない。

`scripts/evaluate_candidate_models.py`はDockerを使わずCSVから直接計算する
ため、残りのcounterfactualシミュレーション(現在13リクエスト分が未完了)
が完了するたびに再実行し、`stage2_expanded`の優位性が保たれるかを監視する
運用とする。

### 追記(n=6時点): 上記の結論は既に反転した

request 90が完了しn=6になった時点で再実行したところ、**`stage2_expanded`の
優位性は消えた**。

| モデル | n=5 Top-1 | n=6 Top-1 | n=5 mean regret | n=6 mean regret |
|---|---:|---:|---:|---:|
| pressure | 20% | 33.3% | 48.8 ms | 40.6 ms |
| stage2_base | 0% | 0% | 61.7 ms | 135.8 ms(悪化) |
| **stage2_expanded** | **20%** | **0%** | **8.1 ms** | **68.9 ms(pressureより悪化)** |
| rank_model | 0% | 33.3% | 52.0 ms | 57.4 ms |

原因はrequest 90で、`formula_only`の予測が9候補中8候補で
`predicted_total_ttft_ms`が小数点以下まで完全に同一(727.791547ms、候補8
のみ820.8ms)になっており、式がほとんど候補を区別できていなかったこと。
その状態で`rank_model`はGPU1(実測1027ms、9候補中最悪)を選び、
regret 323.5msという大きな外れを出した。

**この反転自体が、n=5時点で「stage2_expandedが良い」と結論づけず、
データを増やしながら監視する方針を選んだことの正しさを裏付けている。**
現時点でどのバリアントが優れているかはまだ確定させず、item 4/5の完走を
待つ。

## 7. 全counterfactualシミュレーション完走とanalysis再生成(n=18確定) — 課題: analysisファイルがn=6のまま止まっていた

143件の追加シミュレーションが完走(141件COMPLETED、2件FAILED)し、18
リクエスト全件(候補161件: baseline 18 + counterfactual 143)が揃った。
しかし`analysis/counterfactual_candidate_actuals.csv`等は前回実行時点
(n=6)のまま更新されていなかったため、`analyze_counterfactual_candidates.py`
と`evaluate_candidate_models.py`を再実行してn=18の値に更新した。

**注**: FAILEDの2件(`request90_target1`, `request90_target6`)はstatus
ファイル上は失敗扱いだが、ログを見る限りTTFT等は正常に出力されている。
status判定ロジックの不具合の可能性があり、要調査(未着手)。

### n=18確定結果(現行式モデル vs capacity pressureヒューリスティック)

| 選択方法 | Top-1正解率 | 平均regret | 中央値regret | p95 regret | 平均Spearman |
|---|---:|---:|---:|---:|---:|
| 現行式モデル(formula_only) | 16.7%(3/18) | 76.6 ms | 6.1 ms | 311.6 ms | 0.287 |
| capacity pressure | 38.9%(7/18) | 44.9 ms | 0.8 ms | 219.9 ms | 0.447 |

大外れの内訳: request 173(557.7 ms)、request 49(268.1 ms)、
request 154(189.5 ms)、request 67(155.9 ms)、request 197(149.2 ms)。
中央値は6.1msと悪くないが、少数の致命的な外れが平均を押し上げる
「普段は近いが、ときどき大きく外す」タイプのモデルであることが確定した。

`evaluate_candidate_models.py`(item 6/7/8、leave-one-request-out CV)も
n=18で再確定:

| バリアント | Top-1 | 平均regret | p95 regret |
|---|---:|---:|---:|
| stage2_base(#7のみ) | 16.7% | 13.9 ms | 35.3 ms |
| stage2_expanded(#6+#7) | 11.1% | 28.2 ms | 76.8 ms |
| rank_model(#8) | 11.1% | 45.5 ms | 285.2 ms |

n=5〜6時点で優位に見えていた`stage2_expanded`の優位性は、n=18では
**確定的に消えた**(pressureの38.9%/44.9msに対し11.1%/28.2msで劣る)。
item 5(n=6時点で反転)で懸念していた通り、小サンプルでの初期シグナルは
信頼できなかった。

## 8. 改善ロードマップの提示

item 7/8のいずれも単純なpressureヒューリスティックに勝てなかったことを
受け、次の5方針を優先順位付きで提示した。

1. 現行式＋残差補正(実施済み、item 4で否定)
2. 特徴量追加(scheduler状態・batch構成・GPU時間的状態・prefix cache hit等)
3. schedulerの生予測が候補の47.8%で負値になりmax(0,...)で0クリップされて
   いる問題の修正
4. Admissibility filter + capacity-risk guardrail
   (`score = predicted_ttft_ms + λ×capacity_risk_penalty`)
5. 大外れを重視した損失関数(Huber loss、非対称損失、regret加重、
   pairwise ranking、quantile loss)への変更

ユーザーの選択で、まずitem 4(guardrail)からオフライン検証することにした
(新規シミュレーション不要、`counterfactual_candidate_actuals.csv`の
既存データのみで検証可能なため)。

## 9. テスト1: capacity-risk guardrailスコアリング(item 4) — 初めてpressureに全指標で勝った

`scripts/evaluate_guardrail.py`を新規作成。
`score = predicted_total_ttft_ms + λ × candidate_capacity_pressure`という
単純な線形結合で、λはfold(request)ごとにnested leave-one-out CVで選定
(held-out requestには一切触れない、学習側17件のみでλをグリッド探索)。
λグリッドは0〜1,024,000まで広げ、境界張り付きでなくプラトーに達している
ことを確認した。

### 結果(n=18)

| 選択方法 | Top-1 | 平均regret | 中央値regret | p95 regret | 平均Spearman |
|---|---:|---:|---:|---:|---:|
| formula_only | 16.7% | 76.6 ms | 6.1 ms | 311.6 ms | 0.287 |
| pressure | 38.9% | 44.9 ms | 0.8 ms | 219.9 ms | 0.447 |
| **guardrail(nested CV λ)** | **50.0%** | **25.2 ms** | **0.09 ms** | **195.0 ms** | **0.479** |

item 4〜7で試した中で**5指標すべてでpressureに勝った初めてのバリアント**。
request 173の557.7 ms大外れも縮小した。

### メカニズムの解釈

18 fold中17foldがλ=51,200に収束した。このスケールでは
`λ×candidate_capacity_pressure`の項(0.18〜0.99 ×λ)が式のTTFT予測レンジ
(約625ms)を圧倒するため、実質的に**pressureが最小の候補を選び、
pressure差が約0.012未満の僅差のときだけ式のTTFT予測がタイブレークに使われる**
という挙動になっている。「pressureで危険域を除外・粗く順位付けし、
残った僅差は解析式で裁定する」というitem 4の設計意図と整合的な、
解釈可能な結果になっている。

**留保**: n=18foldはまだ小さく、1件の判断が変わるだけで数値が大きく動く
(item 5でstage2_expandedがn=5→n=6で反転した前例あり)。この結果も
方向性の初期シグナルであり、確定結論ではない。
結果は`analysis/guardrail_summary.csv` / `analysis/guardrail_by_request.csv`
に保存。

## 10. テスト1追加検証: pressureとguardrailで選択が変わったrequestの内訳 — 課題: 勝利が2件のfoldにほぼ全面依存していた

`scripts/analyze_guardrail_flips.py`を新規作成し、item 9のguardrailが
`pressure`と実際に異なる候補を選んだrequestを特定した上で、その
requestを1つずつ除外していく感度分析を行った。

### 判断が変わった3件

| request | pressureの選択 | guardrailの選択 | pressure top2のpressure差 | 実際のregret差(guardrail−pressure) |
|---|---|---|---:|---:|
| 267 | GPU0 | GPU2 | 0.000(完全同点) | **−218.8 ms(大勝)** |
| 156 | GPU1 | GPU6 | 0.0017(ほぼ同点) | **−137.1 ms(大勝)** |
| 84 | GPU6 | GPU7 | 0.0028(ほぼ同点) | +2.4 ms(小負け) |

残り15件は`pressure`と`guardrail`が完全に同じ候補を選んでおり、
regret差は厳密に0だった。

### 除外による感度分析

| 除外したrequest | n | pressure平均regret | guardrail平均regret | pressure Top1 | guardrail Top1 |
|---|---:|---:|---:|---:|---:|
| なし | 18 | 44.9 ms | 25.2 ms | 38.9% | 50.0% |
| 267のみ | 17 | 34.7 ms | 26.7 ms | 41.2% | 47.1% |
| 267+156 | 16 | 28.3 ms | 28.4 ms | 43.8% | 43.8% |
| 267+156+84 | 15 | 29.2 ms | 29.2 ms | 46.7% | 46.7% |

267と156の2件を除くと、guardrailの平均regretはpressureとほぼ完全に
一致する(28.3ms vs 28.4ms)。**item 9の「5指標すべてでpressureに勝つ」
という結果は、18件中2件の同点裁定にほぼ全面的に依存していた。**

### メカニズムの検証: 筋は通っているが、脆い

- **request 267**: 2候補のcapacity_pressureが小数点まで完全一致。
  `pressure`ヒューリスティックは(pandasの`idxmin`により暗黙に)instance ID
  昇順で同点を裁定するため、たまたま悪い方(実測940.6ms)を選んでいた。
  式のTTFT予測(1131ms vs 732ms)は明確に区別でき、guardrailは良い方
  (実測721.8ms)を正しく選んだ。
- **request 156**: pressure差0.0017とほぼ同点。式の予測差
  (816.9ms vs 727.8ms ≈ 89ms)がλ×gap(≈88.5ms)の閾値をわずかに上回り、
  正しい候補(実測723.9ms)へ裁定できた。
- **request 84(唯一の負け)**: pressure差0.0028で式が上書きしたが、
  式の予測差94.7msに対し実際の差はわずか2.4ms(しかも逆方向)で、
  式自身のノイズがわずかな損失を生んだ。

さらに**request 82(判断は変わらなかったが示唆的)**: 9候補中複数が
`predicted_total_ttft_ms=727.791547ms`と小数点まで完全同一という、
item 5(n=6反転)で見たのと同じ式の縮退問題が起きていた。guardrailは
pressureで裁定して外れ(GPU6, regret 226ms)を選び、`formula_only`単体は
pandasの`idxmin`が昇順instance IDで同点を裁定した偶然でGPU0
(regret 4.8ms)を引き当てていた。同じ縮退した予測値に対し、裁定方法が
違うだけで結果が真逆になっており、どちらも「判断」ではなく同点処理の
クセに過ぎないことを示している。

### 結論

item 4(guardrail)の勝利は実データに基づくが、n=18のうち2件の同点裁定に
依存する脆い結果であり、`stage2_expanded`がn=5→n=6で反転した前例
(item 5)と同じ危うさを抱えている。また式の縮退予測問題(item 3が本来
対処すべき問題)がguardrailの勝敗どちらの側にも現れており、**item 4は
item 3を素通りしては正しく評価できない**ことが分かった。現段階では
production投入は時期尚早と判断する。

結果は`analysis/guardrail_flip_analysis.csv` /
`analysis/guardrail_flip_sensitivity.csv`に保存。

## 11. テスト2: 特徴量追加によるstage2再学習(item 2) — 平均・p95は最良、だがTop-1は最悪という非対称な結果

`scripts/evaluate_feature_expansion.py`を新規作成。
`counterfactual_candidate_actuals.csv`に既に含まれていたが
`evaluate_candidate_models.py`では未使用だった、候補ごとに値が変わる
特徴量9個(`feature_home_arrivals_1s/5s`, `feature_home_workload_share`,
`feature_router_initial_waiting_reqs/running_reqs/available_kv_bytes/
projected_active_kv_bytes/capacity_pressure/slot_pressure`)を
`stage2_expanded`の15+9特徴量に追加し(`stage2_full_features`)、
同じleave-one-request-out CVで評価した。

### 結果(n=18、guardrail_nested_cvも同条件で並記)

| バリアント | Top-1 | 平均regret | 中央値regret | p95 regret | 平均Spearman |
|---|---:|---:|---:|---:|---:|
| formula_only | 16.7% | 76.6 ms | 6.1 ms | 311.6 ms | 0.287 |
| pressure | 38.9% | 44.9 ms | 0.8 ms | 219.9 ms | 0.447 |
| stage2_base | 16.7% | 13.9 ms | 7.9 ms | 35.3 ms | 0.251 |
| stage2_expanded | 11.1% | 28.2 ms | 14.9 ms | 76.8 ms | 0.176 |
| **stage2_full_features** | **5.6%** | **12.2 ms** | 10.2 ms | **26.6 ms** | 0.220 |
| guardrail_nested_cv | 50.0% | 25.2 ms | 0.09 ms | 195.0 ms | 0.479 |

`stage2_full_features`は、これまで試した全バリアント中で**平均regretと
p95 regretが最小**(全18foldでの最大regretはわずか28.8ms)。参考までに
requestあたりの実測TTFT幅(最良候補と最悪候補の差)は中央値296ms、
最大919msあり、その幅に対して最大でも29ms未満の取りこぼしに収まって
いるのは他のどのバリアントにもない特徴。一方で**Top-1正解率は全バリアント
中最低(5.6%、18件中1件)**、中央値regretもstage2_baseより悪い。
「毎回そこそこ近いが、ほぼ正解を的中させない」という、item 4の
guardrail(「たまに大勝ちするが基本pressureと同じ」)とは対照的な性質。

### 直接の前身(stage2_base)との比較 — 純増は小さく、勝敗は偏っている

特徴量追加の効果を単体で見るため、`stage2_base`(9特徴量追加前)との
fold単位の差分を取った。

| request | stage2_base regret | stage2_full regret | 差分(full − base) |
|---|---:|---:|---:|
| 154 | 41.2 ms | 8.6 ms | **−32.6 ms** |
| 156 | 34.2 ms | 9.0 ms | **−25.2 ms** |
| 90 | 24.4 ms | 16.0 ms | −8.5 ms |
| 205 | 25.1 ms | 20.3 ms | −4.8 ms |
| (残り11件) | — | — | 0(選択不変) |
| 82 | 0.0 ms | 4.2 ms | +4.2 ms |
| 49 | 3.8 ms | 11.4 ms | +7.6 ms |
| 84 | 5.0 ms | 13.6 ms | +8.7 ms |
| 203 | 0.0 ms | 20.3 ms | **+20.3 ms** |

18件中、選択が変わったのはわずか7件(4件改善・3件悪化)、11件は
`stage2_base`と全く同じ候補を選んでいた。純増(合計regret 249.99ms→
219.69ms)は1requestあたり平均1.7msに過ぎず、冒頭の「平均・p95が
最良」という見た目ほど広範な改善ではない。しかも改善の主因である
request 154・156のうち**request 156はitem 10でguardrailの2大勝利の
片方だった request でもある**。特徴量追加とguardrailという別々の
アプローチが同じ1件のrequestで揃って効いており、item 10で指摘した
「n=18のうち少数の決定にシグナルが集中している」という懸念が
ここでも再現している。

### 結論

`stage2_full_features`は、外れ値(p95・最大regret)を抑える方向では
最も有望な結果だが、それは主にrequest 154・156という少数のfoldに
依存しており、かつ既存の`stage2_base`比では純増が小さい(平均1.7ms/
request)。guardrailと相補的な強み(guardrailはTop-1・中央値に強く、
full_featuresは平均・p95に強い)を持つため、両者を組み合わせる
(`stage2_full_features`で補正したTTFTにguardrailのλ項を足す)価値は
あるが、まずitem 3(式の縮退予測問題)を先に潰してから再評価すべき
という次アクションの優先順位は変わらない。

結果は`analysis/feature_expansion_by_request.csv` /
`analysis/feature_expansion_summary.csv`に保存。

## 12. テスト3: schedulerクリップ問題の根本原因特定と単純な非クリップ化の検証(item 3) — 根本原因は特定できたが、単純な修正では解決しなかった

`serving/core/ttft_formula.py`の`OfflineTtftFormula.predict()`を読み、
縮退予測の根本原因を特定した。`ttft_ms = route_ms + scheduler_ms +
compute_ms`のうち、`compute_ms`は`input_tokens`と
`home_cached_prefix_tokens`(いずれもrequest単位で、candidate=送り先GPUに
よらない)だけから計算されている(`ttft_formula.py:120-126`)。つまり
**候補GPU間を区別できる情報は`route_ms`と`scheduler_ms`にしか存在しない**。
`route_ms`はロジスティック確率が0に近いと容易に消失し(exp(logit)が
ほぼ0)、`scheduler_ms = max(0.0, scheduler_raw_ms)`は候補の47.8%で
負値を0にクリップしている。両方が同時に消えると、`predicted_total_ttft_ms`
は**候補GPUの状態を一切見ないrequest単位の定数**に潰れる。

request 49で実例確認: 9候補中7候補が`727.791547ms`に完全一致していたが、
その7候補の実測TTFTは698.9ms〜967.0ms(268ms差)に分布しており、
`candidate_capacity_pressure`はその7候補内で実測順位とおおむね対応して
いた。式が丸ごと見落としている差を、pressureは見えていたことになる。

18件中7件で何らかのtie(最大tie_size 2〜7)が発生しており、
`request_id`あたりの「最頻値に一致する候補の割合」の平均は19.8%。

### テストした修正: ランキング用にscheduler成分をクリップしない

`scripts/evaluate_unclipped_formula.py`を新規作成。
`predicted_total_unclipped_ms = predicted_route_ms +
predicted_scheduler_raw_ms(符号付きのまま) + predicted_compute_ms`という
単純な変更を、新規シミュレーション・fittingなしで(既存CSV列から)
決定的に計算し、候補選択に使った場合の効果を測定した。

| バリアント | Top-1 | 平均regret | 中央値regret | p95 regret | 平均Spearman |
|---|---:|---:|---:|---:|---:|
| formula_only(クリップあり) | 16.7% | 76.6 ms | 6.1 ms | 311.6 ms | 0.287 |
| **formula_unclipped** | 22.2% | **84.4 ms**(悪化) | **13.5 ms**(悪化) | 275.8 ms | **0.222**(悪化) |
| pressure | 38.9% | 44.9 ms | 0.8 ms | 219.9 ms | 0.447 |
| guardrail(クリップあり、item 9と同じ) | 50.0% | 25.2 ms | 0.09 ms | 195.0 ms | 0.479 |
| guardrail(クリップなし版の式で再構築) | 50.0%(同じ) | 25.2 ms(同じ) | 0.09 ms(同じ) | 195.0 ms(同じ) | 0.465(ほぼ同じ) |

tie率は19.8%→11.2%に下がり、Top-1とp95はわずかに改善したが、
**平均regret・中央値regret・Spearmanはむしろ悪化した**。単純な非クリップ化は
明確な改善にならなかった。

理由を`actual_scheduler_ms`(実測、常に0.176ms以上で負値は取り得ない)と
突き合わせて確認したところ、`predicted_scheduler_raw_ms`と実測値の相関は
クリップの有無でほぼ変わらない(raw: 0.363、clipped: 0.367)。つまり
**負のraw予測は「本当は小さい正の値なのに予測がノイズで負に振れている」
だけで、符号自体に信頼できる追加情報は無い**。クリップを外すと一部の
tie(request 49のような明確なケース)は正しく解けるが、同じノイズを
別の場所で新たな誤順位付けとして持ち込んでしまい、平均的には相殺されて
しまう。

さらに、guardrail(item 9)の上に載せた場合は**クリップの有無で結果が
ほぼ変わらなかった**。λが非常に大きいためpressure項が支配的になり、
式(クリップ有無いずれも)は僅差の裁定にしか使われないため、item 3の
クリップ問題はguardrail経由では既にほぼ無効化されていたことになる。

### 結論

根本原因(`compute_ms`が候補GPUの状態を一切見ない設計になっている)は
特定できたが、「クリップを外すだけ」という単純な修正では解決しない
ことが分かった。真因は2つある: (1) `compute_ms`/`route_ms`のうち
候補間で変化しうる`route_ms`が確率的に消失しやすい設計、
(2) `scheduler_raw_ms`自体の回帰(`experiments/2026-07-16_ttft_component_regression/`
の`scheduler_ridge_coefficients.csv`)が符号レベルで信頼できるほど
較正されていない。本質的な修正には次のいずれかが必要:
(a) `experiments/2026-07-16_ttft_component_regression/scripts/fit_ttft_formula.py`
まで遡ってscheduler回帰を再学習・再較正する、または
(b) `compute_ms`/`route_ms`自体に候補GPUの実負荷特徴量(capacity_pressure
相当)を組み込み、生成モデルとして候補を区別できるようにする(item 2の
発想を、事後補正ではなく本体の式に組み込む)。いずれも今回のオフライン
検証の範囲を超える本実装作業であり、次のアクションとして持ち越す。

結果は`analysis/unclipped_formula_by_request.csv` /
`analysis/unclipped_formula_summary.csv`に保存。

## 13. item 3の本実装: scheduler回帰をlog1p/expm1に切り替え、production artifactを更新 — 単純な非クリップ化より大きく改善、ただし勝敗は依然一部requestに偏る

item 12で「単純に非クリップ化するだけでは解決しない」と分かったため、
より根本的な修正を実装した。`experiments/2026-07-16_ttft_component_regression/
scripts/fit_ttft_formula.py`のroute tail modelは既に
`log1p(t_route_positive_ms)`をfitして`expm1`で戻す設計になっていた
(38-42行目)。scheduler Ridge回帰だけがms空間で直接fitして
`max(0, raw_ms)`でクリップする設計になっており、これが非負性を
保証する代わりに候補間の順序情報を破壊していた。同じlog1p/expm1の
扱いをscheduler回帰にも適用した。

### 変更内容

- `fit_ttft_formula.py`: `scheduler_model`を`train.scheduler_queue_ms`
  ではなく`np.log1p(train.scheduler_queue_ms)`に対してfit。予測時は
  `route_tail`と同じパターンで、学習データのlog1p値の`(min, max)`
  (`scheduler_log_bounds`)にクリップしてから`np.expm1`。この境界を
  新規ファイル`scheduler_ridge_meta.json`に書き出す。
  `--output-dir`/`--model-dir`/`--figure-dir`のCLI引数も追加し、
  productionのartifact directoryを上書きせずに試験実行できるようにした。
- `serving/core/ttft_formula.py`: `OfflineTtftFormula.__init__`で
  `scheduler_ridge_meta.json`があれば読み込み、`predict()`は
  `scheduler_log_bounds`が存在する場合のみ`clip→expm1`経路を使う
  (存在しない場合は旧`max(0, raw_ms)`にフォールバックし、旧artifact
  directoryとの後方互換を保つ)。
- `predict_ttft_formula.py` / `trace_ttft_formula_examples.py`も同じ
  clip→expm1ロジックに合わせて更新(前者はjoblibバンドルに
  `scheduler_log_bounds`が無い場合は旧ロジックにフォールバック)。
- `tests/test_ttft_formula.py`が参照する
  `analysis/ttft_formula/examples/example_summary.csv`等を
  `trace_ttft_formula_examples.py`で再生成し、テストは新formulaに対して
  引き続きPASS。

### 検証1: 一次回帰指標(scenario-held-out cross validation、n=13500、
ranking評価とは独立)

| 指標 | 旧(ms直接fit+クリップ) | 新(log1p/expm1) |
|---|---:|---:|
| scheduler_mae_ms | 56.6 | **48.4**(-14.5%) |
| ttft_mae_ms | 716.1 | 714.6 |
| ttft_r2 | 0.4674 | 0.4663(ほぼ不変) |
| route_mae_ms / compute_mae_ms | 630.2 / 59.7 | 不変(scheduler以外は未変更) |

scheduler成分単体の予測精度が、我々のn=18ランキング評価とは無関係な
独立した検証(15シナリオでのscenario-held-out CV)で明確に改善している。
これはn=18の少数決定への過学習ではなく、回帰そのものの改善であることを
示す一次証拠。

### 検証2: n=18 counterfactualランキング評価(item 3-12と同じ手法)

`scripts/evaluate_log1p_scheduler_formula.py`を新規作成。production
artifactを読み込むproduction版`OfflineTtftFormula`をそのまま使い、
`counterfactual_candidate_actuals.csv`の`feature_*`列から
`_ttft_formula_features`相当の特徴量を再構成して161候補すべてを
再予測した(新規シミュレーション不要)。router.pyの実際のランキング総和
(`predicted_total_ttft_ms = ttft_ms + request_migration_ms +
kv_migration_ms + downlink_ms`)を再現するようスクリプトを実装
(この3項がrequest内で候補によらず一定であることを確認済みのため、
今回のデータでは省略しても結果は変わらないが、正しい実装として残す)。

| バリアント | Top-1 | 平均regret | 中央値regret | p95 regret | 平均Spearman |
|---|---:|---:|---:|---:|---:|
| formula(旧、クリップ) | 16.7% | 76.6 ms | 6.1 ms | 311.6 ms | 0.287 |
| **formula(新、log1p)** | **44.4%** | **42.3 ms** | 1.5 ms | **217.0 ms** | 0.314 |
| pressure | 38.9% | 44.9 ms | 0.8 ms | 219.9 ms | 0.447 |

**式単体(guardrailもstage2補正も無し)で、初めてpressureヒューリスティック
にTop-1・平均regret・p95 regretで勝った。** tie率も19.8%→11.2%に低下。
これはitem 9のguardrail(pressureに依存し式は僅差裁定のみ)やitem 11の
stage2_full_features(式を直接は触らない事後補正)より一段深い、
production formula自体の改善。

### 勝敗の内訳と留保: 依然一部requestに集中、新たな外れ値も1件発生

request単位で旧→新の差分を確認した。

| 変化 | request | regret変化 |
|---|---|---:|
| 大幅改善 | 173 | 557.7 ms → 0 ms |
| 大幅改善 | 49 | 268.1 ms → 0 ms |
| 大幅改善 | 197 | 149.2 ms → 0 ms |
| 改善 | 267 | 16.5 ms → 0 ms |
| 改善 | 90 | 4.9 ms → 0 ms |
| **悪化** | **51** | 7.7 ms → **372.5 ms** |
| 悪化(小) | 161 | 1.3 ms → 14.5 ms |
| 不変 | 残り11件 | 0 |

18件中5件が改善(うち3件はitem 12で確認した縮退tie問題の直撃ケース)、
2件が悪化、11件は不変。合計regretは1379.3ms→760.9ms(45%減)で
明確な純増だが、除外実験で内訳を見ると:

| 除外条件 | n | 旧平均regret | 新平均regret | pressure平均regret |
|---|---:|---:|---:|---:|
| なし | 18 | 76.6 ms | 42.3 ms | 44.9 ms |
| 173のみ除外 | 17 | 48.3 ms | 44.8 ms | 47.5 ms |
| 173+49+197除外 | 15 | **27.0 ms** | **50.7 ms**(旧より悪化) | 53.6 ms |
| 5件の改善requestを全除外 | 13 | 29.5 ms | 58.5 ms(旧より悪化) | 45.0 ms |

**縮退tie問題を直撃した3件(173, 49, 197)を除くと、新formulaは旧formulaより
むしろ平均regretが悪化する。** つまりこの修正は「あらゆる場面で式を賢く
した」のではなく、「縮退tie問題という特定の欠陥を狙って潰した」もので、
それ以外の場面での式の予測精度そのものは(一次回帰指標のscheduler_mae_ms
改善が示す通り)わずかに改善している程度で、大きな差ではない。

request 51の悪化(372.5ms)を掘り下げると、GPU8が
`feature_router_initial_waiting_reqs=0`という(decision時点での)
スナップショットにより新formula・pressureの両方で「空いている」候補に
見えていたが、実際には実行時に混雑し実測1078.7ms(9候補中最悪)になって
いた。新formulaはGPU8とGPU5(実際の最良候補、僅差0.11ms)のほぼ完全な
同点を僅かに間違った側に倒しており、pressureも同じ2候補でほぼ同点
(pressure差0.0013)だったが逆に正しい側に倒れていた——item 10で見た
「僅差裁定はコイントス」という同じ現象が、新formula側でも起きている。
これはスナップショットの鮮度(staleness)の問題であり、log1p化では
解決しない別種の限界。

### 決定: production artifactへ反映済み

一次回帰指標・n=18ランキング評価の両方で明確な改善が確認できたため、
`experiments/2026-07-16_ttft_component_regression/analysis/ttft_formula/`
(`OfflineTtftFormula()`のデフォルト読み込み先、つまりpolicy
`NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE`が実際に使うartifact)を
再学習結果で上書きした。`tests/test_ttft_formula.py`はPASS。
変更はgit差分として残っており未コミット(コミットはユーザー指示が
あった場合のみ)。

結果は`analysis/log1p_scheduler_formula_by_request.csv` /
`analysis/log1p_scheduler_formula_summary.csv`に保存。

## 次にやるべきこと

1. item 4(guardrail)・item 11(stage2_full_features)を、item 13で更新
   したproduction formula(log1p scheduler)の上で再評価する。特にitem 12
   /13の両方で「僅差候補の裁定はコイントスに近い」現象(request 82,
   51)が繰り返し観測されているため、guardrailの大勝ちの一部が新formula
   でも再現するか、それとも解消するかを確認する。
2. item 13で見つかったrequest 51型の限界(`router_initial_*`スナップ
   ショットの鮮度(staleness)が原因で、決定時に空いて見えた候補が
   実行時に混雑していた)は、log1p化では解決しない別種の問題。
   is-stale判定や再評価(reevaluation)の仕組みを見るか、item 9の
   confidence fallbackで対応するか検討が必要。
3. item 5(外れ値重視の損失関数)を同様にオフラインで検証する。
4. guardrail(Top-1・中央値に強い)と`stage2_full_features`(平均・p95に
   強い)を組み合わせたハイブリッド
   (`score = stage2_full_features補正後TTFT + λ×capacity_pressure`)を、
   新formulaの予測を入力に使う形で試す価値がある。ただしitem 10・11・
   12・13のいずれでも「少数のrequestにシグナルが集中している」問題が
   繰り返し出ているため、結果は同様の慎重さで解釈する必要がある。
5. item 9(confidence fallback付きpolicy)は未着手のまま。
6. `request90_target1` / `request90_target6`のFAILED status判定
   (ログ上は正常終了に見える)の原因調査は未着手。
7. item 13でproduction artifact
   (`experiments/2026-07-16_ttft_component_regression/analysis/
   ttft_formula/`)を書き換えた。まだuncommitted。five-policy実験
   (README.md)のpolicy Eを再実行して、実際のシミュレーション結果
   (TTFT・GPU使用率)がこのオフライン評価と整合するかの確認は未実施。
