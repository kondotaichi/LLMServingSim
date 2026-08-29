# 02. Merit of using distributed AI-RAN GPUs

## 中心的なストーリー

02では並列化を前提としない。Single-GPUで実行可能なLLMを各GPUへ独立配置し、地理分散AI-RANを使うこと自体の性能と経済性を評価する。

```text
02-a: RAN制約なし
  DistributedはCloudとほぼ同じTTFT
  + 既設GPUの追加電気料金はCloud利用料金より低い
  -> 地理分散AI-RANには性能を維持した経済的メリットがある

02-b: RANとのVRAM共有あり
  AIが利用できるVRAMとsession収容能力が減る
  -> Cloudに対する性能上の優位性は薄れる
  + 既設GPUを使う経済性は残る
  -> 経済性を維持しながら性能低下を解消する必要がある

03: 地理的offloadの可能性とstateful inferenceの障壁を示す
04: その障壁に対してsite内PPとsite間KV migrationを提案する
```

## 02におけるPPの扱い

02-aと02-bの主比較はPP=1だけを用いる。PP=2を地理分散環境へ適用した既存結果および実行中の参考runは削除しないが、02の主張には使用しない。

PPは、RAN負荷下でAI向けVRAMが不足し、さらにstateful sessionのoffload先でKV cache収容容量が問題になることを03までで示した後、04において初めて提案手法の構成要素として導入する。04のPPは地理的に離れたsite間ではなく、低遅延なsite内networkだけで行う。

## 各ディレクトリの役割

- `a_interconnect_only/`: RAN制約なし。CloudとDistributedのPP=1性能および経済性を比較する。
- `b_ran_aware_economics_10percent/`: Active TCP UE率10%の完了済み結果。
- `b_ran_aware_economics_20percent/`: Active TCP UE率20%、Cloud/APN、Peak 1x--10xのsweep。
