1. AI-and-RANは、基地局側のGPUをRAN処理とAI処理で共用することで、設備利用率を高めつつ、新たなAIサービスを提供できる可能性がある。
2. 想定するAIサービスとして、一般モバイルユーザ向けのinteractive / agentic AIを考える。
なぜならagentic-aiは
- ますます多くの人に利用されており、
- マルチターン会話が前提であるため、スマホで動く軽量のモデルでは対処できない一方で、並列化が必要なほど巨大なモデルが必要なわけではなく、
- 応答速度がユーザ体験に直結するものであるため。

本研究ではtool useやagent orchestrationそのものではなく、その基盤となるsingle-turn / multi-turnのLLM text generationを対象とする。
想定するLLMは、端末内AIだけで処理するにはある程度重い一方、巨大クラウドLLMほど大規模ではないsingle-GPU-classのモデルとする。

3. AI-and-RANでは、RAN用GPUの余剰資源をAI推論に利用できるため、クラウドGPUを専用に利用する場合より推論コストを抑えられる可能性がある。
したがって、低コストかつ低遅延なLLM serving基盤として経済的なメリットが期待できる。

4. しかし、AI-and-RANのGPU資源は基地局サイトに固定的に配置される一方、RAN trafficとAI requestは時間・空間的に動的に変化する。
仮に平均的なユーザ分布に合わせて適切にGPUを配置できたとしても、各時刻の需要と計算資源が常に一致するとは限らない。

5. RANとAIが同じGPU資源を共有するため、AIが利用可能な計算資源そのものもRAN負荷に応じて動的に変化する。
ある地域ではRAN負荷とAI負荷が同時に高まりGPUが逼迫する一方、別地域ではGPU資源が余る可能性がある。

6. この地理的・時間的なresource imbalanceによって、負荷が集中したAI-RAN siteではAI requestのqueueingが発生する。
その結果、AI-RAN本来の利点である低TTFTが失われ、ユーザ体験が悪化する。

7. このresource imbalanceを解消するには、複数の地理的に離れたAI-RAN site間で計算資源を共有する必要がある。
8. ただし、RAN workloadとAI workloadでは地理的な可搬性が異なる。
RAN処理はradioとの対応関係や厳しい遅延制約を持つため、遠隔地のGPUへ自由にオフロードすることが難しい。
一方、LLM推論はRAN処理より地理的制約が弱く、別地域のGPUへオフロード可能である。

9. したがって、AI-and-RAN全体の負荷不均衡を解消するためには、RANではなくAI workloadを余剰計算資源のある遠隔地へ移動させることが合理的である。
（ここまでがストーリー。ここからが提案手法）


10. マルチターンLLMやagentic AIの基盤となるstateful inferenceでは、各sessionが過去contextに対応するKV cacheを保持している。
そのため、単純に次のrequestだけを別siteへroutingすると、移送先で過去contextのprefillをやり直す必要があり、TTFTが増加する。

11. そこで、APNのような高速・低遅延な地域間ネットワークを利用して、KV cacheごとsessionを遠隔AI-RAN siteへ移送する。
一度sessionを移送した後は、以降の推論を移送先site内で完結させる。
地域間でtoken生成のたびに通信するようなmodel parallelismは行わない。

12. ただし、KV cacheを移送してremote siteの計算資源を利用するためには、移送先GPUに新たなsessionのKV cacheを収容できる十分なVRAM余力が必要である。
特にmulti-turn / agentic AIでは、会話の継続に伴ってKV cacheが増大する。
そのため、計算負荷には余裕があるsiteであっても、モデル重みと既存sessionのKV cacheによってVRAMが逼迫し、新たなoffloaded sessionを受け入れられない可能性がある。
すなわち、遠隔GPUを負荷分散先として活用するには、sessionのKV cacheを収容できる十分なVRAM容量を確保する必要がある。

13. そこで、各AI-RAN site内部ではPipeline Parallelismを利用して、KV cacheを収容するためのVRAM余力を確保する。
PPの目的は巨大モデルを動かすことではない。single-GPUでも動作可能なモデルを複数GPUに分割することで、各GPU上のmodel weight footprintを削減し、KV cacheに利用可能なVRAM容量を増やす。
PPは低遅延なsite内ネットワークに限定し、地理的に離れたsite間では行わない。

14. したがって提案方式は、二段階のresource optimizationとして整理できる。
Local optimization: site内PPによってKV cache容量を拡大し、より多くのstateful sessionを収容する。
Global optimization: APNを介したKV cache migrationによって、AI sessionを負荷の高いsiteから余剰資源のある遠隔siteへ再配置する。

15. これにより、固定配置されたAI-and-RAN GPUと動的に変化するRAN/AI需要とのミスマッチを吸収し、地理分散GPU全体の利用率を高めながら、AI推論のqueueing latencyおよびTTFTを抑制することを目指す。
