# KVキャッシュ容量によるBatch採用本数の決定

作成日: 2026-07-11  
対象実装:

- `serving/core/scheduler.py` の `schedule_base()` / `schedule_with_prefix()`
- `serving/core/memory_model.py` の `get_kv()` / `get_block_kv()`

本ドキュメントでは、SchedulerがBatch候補を作った後、NPUのKVキャッシュ容量に
収まるRequest本数をどのように決めているかを説明する。

---

## 1. この処理の目的

Schedulerは、到着済みRequestの本数とtoken budgetだけではBatchを確定できない。
各Requestの処理を進めると、そのtokenに対応するKey/Valueを全Transformer layer分
保持する必要があり、NPUメモリに新しいKV blockを確保するからである。

そこで、Batch候補について次の判定を行う。

```text
今回新たに必要になるKV容量
    <=
現在利用できるNPU上のKV容量
```

全候補が収まらなければ候補の末尾からRequestを外し、収まる最大本数を探す。

```text
Batch候補を作る
    │
    ▼
各Requestの追加KV block数を計算
    │
    ▼
候補全体の追加KV容量を合計
    │
    ├─ 収まる     → 全候補を採用
    │
    └─ 収まらない → 末尾を1本外して再計算
                         │
                         ▼
                    収まる最大本数を採用
```

---

## 2. NPUメモリのうちKVに使える領域

`MemoryModel`は初期化時にモデル重みをNPUへ配置したものとして扱う。

```python
self.weight = self.get_weight()
self.npu_used = self.weight
```

したがって、クラスタ設定の`npu_mem.mem_size`をすべてKVに使えるわけではない。

```text
KVに使える残容量
= NPU総容量
  - モデル重み
  - 既に確保済みのKV cache
```

モデル重みだけでNPU容量を超える場合は、Schedulerが動き始める前に
`RuntimeError`になる。

---

## 3. 1 tokenあたりのKVサイズ

`MemoryModel.get_kv(seq)`は、`seq` token分のKVサイズを計算する。

```python
def get_kv(self, seq):
    return (
        2
        * self.kv_dim
        * seq
        * self.n_layer
        * self.kv_fp
        // self.num_npus
    )
```

式にすると次の通り。

```text
KV容量
= 2
  × kv_dim
  × token数
  × Transformer layer数
  × KV要素のbyte数
  ÷ instanceのNPU数
```

各項目の意味:

| 項目 | 意味 |
|---|---|
| `2` | KeyとValueの2つ |
| `kv_dim` | `num_key_value_heads × head_dim` |
| `seq` | KVを保持するtoken数 |
| `n_layer` | `num_hidden_layers` |
| `kv_fp` | KV要素のbyte数。BF16なら2、FP8なら1 |
| `num_npus` | 実装上、1 NPUあたりのサイズへ分割する数 |

`kv_dim`は`hidden_size`から推測せず、明示的な`head_dim`を使う。

```python
head_dim = config.get('head_dim', hidden_size // num_attention_heads)
kv_dim = num_key_value_heads * head_dim
```

### 計算例

次のモデルを仮定する。

```text
num_key_value_heads = 8
head_dim            = 128
kv_dim              = 1024
num_hidden_layers   = 32
KV dtype            = BF16（2 bytes）
num_npus            = 1
```

1 tokenあたりのKVサイズは、

```text
2 × 1024 × 1 × 32 × 2
= 131,072 bytes
= 128 KiB
```

FP8 KV cacheなら`kv_fp=1`なので64 KiBとなり、BF16の半分になる。

---

## 4. KVはblock単位で確保する

KV cacheはtokenごとではなく、`--block-size`単位で確保される。

`block_size=16`の場合:

```text
 1〜16 tokens → 1 block
17〜32 tokens → 2 blocks
33〜48 tokens → 3 blocks
```

そのため、今回1 token処理しても、既に確保済みblock内に収まれば追加容量は0である。
block境界を越えると、1 tokenのDecodeでも16 token分のblockを新しく確保する。

---

## 5. Requestごとの追加block数

`get_block_kv()`は、各Requestについて処理前後の必要block数を比較する。

```text
computed_before
= これまでに計算済みのtoken数

tokens_this_step
= 今回このRequestで計算するtoken数
```

計算式:

```text
blocks_before
= ceil(computed_before / block_size)

blocks_after
= ceil((computed_before + tokens_this_step) / block_size)

new_blocks
= max(0, blocks_after - blocks_before)
```

コードでは整数の切り上げ除算を使う。

```python
blocks_after = (
    total_after + self.block_size - 1
) // self.block_size

blocks_before = (
    (computed_before + self.block_size - 1) // self.block_size
    if computed_before > 0 else 0
)

new_blocks = max(0, blocks_after - blocks_before)
```

Requestの追加KV容量は次のようになる。

```text
Request追加KV容量
= get_kv(new_blocks × block_size)
```

---

## 6. Decode Requestの計算例

Decodeは1 iterationあたり1 token進む。

### 6.1 追加blockが不要な場合

```text
block_size      = 16
computed_before = 17
tokens_this_step = 1
```

```text
blocks_before = ceil(17 / 16) = 2
blocks_after  = ceil(18 / 16) = 2
new_blocks    = 0
```

既に2 block目を確保しているため、追加KV容量は0。

### 6.2 新しいblockが必要な場合

```text
block_size      = 16
computed_before = 16
tokens_this_step = 1
```

```text
blocks_before = ceil(16 / 16) = 1
blocks_after  = ceil(17 / 16) = 2
new_blocks    = 1
```

1 tokenのDecodeだが、新しい16-token blockを1つ確保する。

```text
block境界を越えるDecode:
16 → 17
32 → 33
48 → 49
...
```

---

## 7. Prefill Requestの計算例

Chunked Prefillでは、`scheduled_tokens[req.id]`に今回のchunk長が入っている。

```text
block_size          = 16
computed_before     = 20
scheduled_tokens    = 30
total_after         = 50
```

```text
blocks_before = ceil(20 / 16) = 2
blocks_after  = ceil(50 / 16) = 4
new_blocks    = 4 - 2 = 2
```

今回実際に計算するのは30 tokenだが、KVはblock単位なので、

```text
2 blocks × 16 tokens
= 32 token分のKV容量
```

を追加確保する。

---

## 8. Batch候補全体の追加KV容量

`get_block_kv()`は、候補先頭から`batch_len`本について追加KV容量を合計する。

```python
block_kv_size = 0

for i in range(batch_len):
    req = batch_req[i]
    ...
    block_kv_size += self.get_kv(
        new_blocks * self.block_size
    )
```

例えば、

```text
Request A: 2 new blocks
Request B: 0 new blocks
Request C: 1 new block
```

なら、Batch全体では3 blockが必要。

```text
Batch追加KV容量
= get_kv(3 × block_size)
```

---

## 9. 収まる最大本数の探索

Schedulerは候補数から0まで降順に調べる。

```python
temp_len = batch_len

for i in range(batch_len, -1, -1):
    kv_size = self.memory.get_block_kv(
        batch_req,
        i,
        scheduled_tokens,
    )

    if 利用可能容量 >= kv_size:
        temp_len = i
        break
```

候補が`[A, B, C, D]`なら、次の順に判定する。

```text
i=4: A+B+C+D
i=3: A+B+C
i=2: A+B
i=1: A
i=0: Requestなし
```

最初に条件を満たした`i`が、収容可能な最大本数になる。

### 探索例

```text
NPU空き容量 = 100 MiB

Aまで       = 32 MiB
A+Bまで     = 64 MiB
A+B+Cまで   = 96 MiB
A+B+C+Dまで = 128 MiB
```

```text
i=4 → 128 MiB > 100 MiB: 入らない
i=3 →  96 MiB <= 100 MiB: 入る
```

結果:

```python
batch_len = 3
batch_req = batch_req[:3]
```

A、B、Cを採用し、Dは`Scheduler.request`に残して次回を待つ。

この探索は任意の組み合わせ最適化ではない。**候補順序を維持し、末尾から外す**。

---

## 10. Prefix caching無効時の容量判定

通常モードでは、NPUメモリカウンタから空きを求める。

```text
NPU空き容量
= npu_mem - npu_used
```

判定対象は今回の新規KVだけではない。

```python
kv_size = self.memory.get_block_kv(...)
load_size = self._get_reload_size(...)

if self.memory.is_avail(
        kv_size + load_size,
        Device.NPU):
    ...
```

| 値 | 意味 |
|---|---|
| `kv_size` | 今回新しく追加するKV block容量 |
| `load_size` | 以前CPUへ退避したRequestのKVをNPUへ戻す容量 |

条件は、

```text
NPU空き容量
>= 新規KV容量 + 再読込KV容量
```

となる。

収容可能なRequestが確定した後、`kv_size`と`load_size`をNPUへallocateする。
CPUから戻したKVについてはCPU側使用量を解放する。

---

## 11. Prefix caching有効時の容量判定

Prefix caching有効時は、現在の空きに加え、追い出せるprefix cacheも利用可能容量へ
含める。

```python
total_useable_size = (
    self.memory.avail_size(Device.NPU)
    + self.memory.evictable_size(Device.NPU)
)
```

```text
利用可能容量
= 現在空いているprefix cache容量
  + eviction可能なprefix容量
```

### `avail_size`

RadixCacheのcapacityのうち、まだ使われていない領域。

```text
avail_size
= prefix cache capacity - 現在保存中のprefix容量
```

### `evictable_size`

現在どの実行中Requestからもロックされておらず、追い出せるprefix容量。

```text
ロック中prefix       → eviction不可
終了済み・未使用prefix → eviction可能
```

判定は、

```text
新しく必要なKV容量
<= 現在の空き + 追い出せるprefix容量
```

となる。

採用Batchの`kv_size`が実際の空きを超える場合は、その差分だけprefixをevictする。

```python
evict_size = max(
    0,
    kv_size - self.memory.avail_size(Device.NPU),
)

self.memory.evict_prefix_cache(
    evict_size,
    Device.NPU,
)
```

---

## 12. Prefix cache hitの扱い

新規Prefill Requestに対して`prefix_match()`が成功すると、hitしたtoken数が
`req.num_computed_tokens`へ反映される。

例えば、

```text
prompt長            = 1000
prefix cache hit    = 600
num_computed_tokens = 600
今回のchunk          = 400
```

なら、600 token分を再計算・再確保するのではない。

```text
blocks_before = ceil(600 / block_size)
blocks_after  = ceil(1000 / block_size)
```

の差だけを新規blockとして数える。

ただしKVはblock単位なので、追加容量は単純な`400 × 1 token分のKVサイズ`と
完全には一致せず、block境界へ切り上げられる。

---

## 13. 1本も収まらない場合のpreemption / eviction

候補を1本まで減らしても収まらない場合、探索結果は`temp_len == 0`になる。
Schedulerは既存Decode Requestを後ろからpreemptし、容量を作って再探索する。

### 13.1 Prefix caching無効時

Decode RequestのKVをNPUからCPUへ退避する。

```python
self.memory.free(evicted_kv_size, Device.NPU)
self.memory.allocate(
    evicted_kv_size * self.num_npus,
    Device.CPU,
)
```

`get_evict_kv()`が返すNPU側サイズはper-rankだが、CPU側使用量はクラスタ全体の
bytesで追跡するため、CPUへ移すときは`num_npus`倍する。

そのRequestが後のBatchに再採用されたときは、CPUからNPUへKVを戻す。

### 13.2 Prefix caching有効時

ロックされていないRadixCache entryをevictする。必要に応じて既存Decode Requestも
preempt対象にする。

二次prefix storageが設定されていれば、NPUより長いprefixがCPU/CXLにある場合の
読込量を`prefix_load_size`へ加える。

退避可能なDecode Requestもprefixもなく、1本分のKVすら確保できなければ、
Schedulerは`None`を返す。

---

## 14. 全体の具体例

説明を簡単にするため、次の条件を仮定する。

```text
block_size            = 16
1 blockのKVサイズ     = 8 MiB
現在のNPU空き容量     = 32 MiB
```

候補Requestは3本。

### Request A

```text
computed_before  = 0
scheduled_tokens = 20

blocks_before = 0
blocks_after  = ceil(20 / 16) = 2
new_blocks    = 2
追加KV         = 16 MiB
```

### Request B

```text
computed_before  = 16
scheduled_tokens = 1

blocks_before = 1
blocks_after  = 2
new_blocks    = 1
追加KV         = 8 MiB
```

### Request C

```text
computed_before  = 32
scheduled_tokens = 20

blocks_before = 2
blocks_after  = ceil(52 / 16) = 4
new_blocks    = 2
追加KV         = 16 MiB
```

候補全体は、

```text
A + B + C
= 16 + 8 + 16
= 40 MiB
```

なので32 MiBには収まらない。

```text
3本: A+B+C = 40 MiB > 32 MiB → 不採用
2本: A+B   = 24 MiB <= 32 MiB → 採用
```

最終結果:

```text
今回のBatch: A、B
待ち行列に残るRequest: C
```

Prefix caching有効で、空き32 MiBに加えてeviction可能prefixが16 MiBあるなら、

```text
total_useable_size = 32 + 16 = 48 MiB
```

となるため、40 MiBのA、B、Cをすべて採用し、必要な8 MiB以上のprefixを
evictできる。

---

## 15. `max_num_seqs`・token budgetとの関係

KV容量判定はBatch選択の最後の制約であり、それ以前にも候補は絞られている。

```text
到着済みRequest
    │
    ▼
max_num_seqsの空き
    │
    ▼
max_num_batched_tokens
    │
    ▼
KV cache容量
    │
    ▼
最終Batch
```

したがって、実際のBatch本数は概念的に次の最小値で決まる。

```text
実際のBatch本数
= min(
    到着済みRequest数,
    max_num_seqsの空き,
    token budgetに収まる本数,
    KV cacheに収まる本数
  )
```

`max_num_seqs=128`でも、長いcontextを持つRequestが多くKV cacheが不足すれば、
実際のBatchは128本より少なくなる。

---

## 16. 実装を読む際の重要点

### 16.1 総KV容量ではなく「今回の追加量」を計算する

既に確保済みのblockを毎iteration加算し直すのではない。

```text
new_blocks = blocks_after - blocks_before
```

によって、今回新しく増える分だけを計算する。

### 16.2 block境界で容量が段階的に増える

Decodeは常に1 token進むが、KV使用量は毎回同じ量ずつ増えるわけではない。
`16→17`のようなblock境界で1 block分まとめて増える。

### 16.3 探索は候補の先頭部分に限定される

「AとCなら収まるがAとBは収まらない」といった任意組み合わせ探索はしない。
Schedulerが決めた順序を維持し、末尾からRequestを外す。

### 16.4 Prefix caching有効時はeviction可能容量を先に数える

現在の物理的な空きだけではなく、安全に追い出せるprefixを含めてBatch採用可否を
判断し、採用後に必要量を実際にevictする。

---

## 17. まとめ

KV容量によるBatch本数決定の中心式は次の通り。

```text
blocks_before
= ceil(computed_tokens / block_size)

blocks_after
= ceil((computed_tokens + scheduled_tokens) / block_size)

new_blocks
= blocks_after - blocks_before

追加KV容量
= get_kv(new_blocks × block_size)
```

各Requestの追加KV容量を合計し、次を満たすか確認する。

```text
Prefix caching無効:
  新規KV + 再読込KV <= NPU空き容量

Prefix caching有効:
  新規KV <= 現在の空き + eviction可能prefix容量
```

収まらなければ候補末尾を1本ずつ外し、収まる最大の先頭部分をBatchに採用する。
1本も収まらない場合は既存Decode KVやprefix cacheを退避し、再度判定する。

## 関連ファイル

- `serving/core/memory_model.py` — KVサイズ、block数、空き容量、prefix eviction
- `serving/core/scheduler.py` — Batch候補の探索と最終採用
- `serving/core/request.py` — `num_computed_tokens`、`chunk_len`、`Batch`の定義
- `kondoFolder/tutorial/detail/scheduler.md` — Scheduler全体の詳細解説
- `kondoFolder/tutorial/detail/router.md` — Schedulerへ入る前のRequest routing
