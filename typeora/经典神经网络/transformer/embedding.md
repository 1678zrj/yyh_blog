```python
class Embeddings(nn.Module):
     def __init__(self, d_model, vocab):
         super(Embeddings, self).__init__()
         self.lut = nn.Embedding(vocab, d_model)
         self.d_model = d_model

     def forward(self, x):
         return self.lut(x) * math.sqrt(self.d_model)
```

### 这段代码里nn.Embedding的作用和机制是什么？

这段代码中的 `nn.Embedding` 是 PyTorch 中专门用于处理 **词嵌入（word embedding）** 的一个模块。下面我从作用、机制、输入输出形式、代码中乘以 `sqrt(d_model)` 的目的几方面详细解释：

------

## ✅ 1. `nn.Embedding` 的作用是什么？

`nn.Embedding` 用于将离散的整数索引（比如词 ID）映射成连续的稠密向量（词向量）。它可以看作是一个 **词向量查找表**。

> 📌 它的功能和 NumPy 中的 `embedding_matrix[word_id]` 查表效果类似，但是可学习的。

------

## ✅ 2. `nn.Embedding(vocab, d_model)` 中参数含义：

- `vocab`：词表大小（即总共有多少个不同的 token）
- `d_model`：每个词嵌入的维度（即每个 token 表示成一个多少维的向量）

它的内部本质是一个大小为 `(vocab, d_model)` 的 **可学习权重矩阵**，初始化时随机赋值。

------

## ✅ 3. forward 函数输入输出

```python
x = torch.tensor([[5, 2, 8], [4, 1, 9]])  # shape: (batch_size=2, sequence_length=3)
output = self.lut(x)  # shape: (2, 3, d_model)
```

输入：

- `x` 是一个整数张量，表示句子中 token 的 ID（例如通过 tokenizer 得到的词索引）

输出：

- 每个 token id 被转换成对应的向量，最终得到一个形状为 `(batch_size, sequence_length, d_model)` 的张量。

------

## ✅ 4. 为什么要乘上 `math.sqrt(d_model)`？

这是 Transformer 中的一个细节，来自原论文：

> **"We multiply those embeddings by sqrt(d_model) to scale the weights."**

因为：

- Transformer 的输入是词向量加上位置编码。
- 如果不乘以 `sqrt(d_model)`，词嵌入的初始化值太小，会导致加上位置编码时比例失衡。
- 这个乘法相当于 **放大词向量的初始方差**，让它与其他模块更匹配。

------

## ✅ 总结一下这段代码：

```python
class Embeddings(nn.Module):
    def __init__(self, d_model, vocab):
        super(Embeddings, self).__init__()
        self.lut = nn.Embedding(vocab, d_model)   # 构造词向量查表
        self.d_model = d_model

    def forward(self, x):
        return self.lut(x) * math.sqrt(self.d_model)  # 查表 + 缩放
```

这个模块做的事情就是：

1. 输入一个 batch 的 token 索引张量 `x`。
2. 每个 token 索引被映射成一个 `d_model` 维的向量。
3. 整个词向量乘上 `sqrt(d_model)`，作为最终输出。

### 为什么词嵌入的初始化值会很小？是nn.Embedding内部实现决定的吗？你说加上位置编码时比例失衡，位置编码比词嵌入的初始化值大很多吗？

可运行代码进行比较：

```python
import torch
import torch.nn as nn
import math
import matplotlib.pyplot as plt

# 设置参数
vocab_size = 10      # 词表大小
d_model = 512        # 词嵌入维度
seq_len = 5          # 序列长度

# 创建Embedding层
embedding = nn.Embedding(vocab_size, d_model)
sample_input = torch.tensor([[1, 2, 3, 4, 5]])  # 输入一个样本句子

# 获取未缩放的词嵌入
embedded = embedding(sample_input)
embedded_scaled = embedded * math.sqrt(d_model)

# 创建位置编码
def get_positional_encoding(seq_len, d_model):
    pe = torch.zeros(seq_len, d_model)
    position = torch.arange(0, seq_len, dtype=torch.float).unsqueeze(1)
    div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    return pe

pos_encoding = get_positional_encoding(seq_len, d_model).unsqueeze(0)

# 相加前后的嵌入向量（仅取第一个token作示例）
token_embedding = embedded[0, 0].detach().numpy()
token_embedding_scaled = embedded_scaled[0, 0].detach().numpy()
positional_embedding = pos_encoding[0, 0].detach().numpy()
combined = (embedded_scaled + pos_encoding)[0, 0].detach().numpy()

# 可视化比较
plt.figure(figsize=(16, 5))
plt.plot(token_embedding, label='Raw Embedding')
# plt.plot(token_embedding_scaled, label='Scaled Embedding (×√d_model)')
plt.plot(positional_embedding, label='Positional Encoding')
# plt.plot(combined, label='Combined Embedding')
plt.legend()
plt.title("Comparison of Embedding, Positional Encoding, and Their Sum")
plt.xlabel("Embedding Dimension")
plt.ylabel("Value")
plt.grid(True)
plt.show()
```

