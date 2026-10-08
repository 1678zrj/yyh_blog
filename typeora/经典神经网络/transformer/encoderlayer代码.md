```python
from FFN import PositionwiseFeedForward
from MultiHeadedAttention import MultiHeadedAttention
from LayerNorm import SublayerConnection
from torch import nn
from MultiHeadedAttention import clones

class EncoderLayer(nn.Module):
    def __init__(self,size,self_attn,feed_forward,dropout):
        super().__init__()
        self.self_attn=self_attn
        self.feed_forward=feed_forward
        self.sublayer=clones(SublayerConnection(size,dropout),2)
        #d_model
        self.size=size

    def forward(self,x,mask):
        x= self.sublayer[0](x,lambda x:self.self_attn(x,x,x,mask))
        return self.sublayer[1](x,self.feed_forward)
```

### x = self.sublayer\[0\](x, lambda x: self.self_attn(x, x, x, mask))要加个lambda x:，不能直接x = self.sublayer\[0\](x, self.self_attn(x, x, x, mask))吗?

这个问题非常关键，下面是**为什么需要 `lambda x:`** 的详细解释：

------

### ❓问题本质

你提到的这两行代码：

```python
# 正确写法（使用 lambda）
x = self.sublayer[0](x, lambda x: self.self_attn(x, x, x, mask))

# 错误写法（直接调用）
x = self.sublayer[0](x, self.self_attn(x, x, x, mask))
```

它们的**区别在于传递的是“函数”还是“结果”**。

------

### ✅ 解释

#### `SublayerConnection` 的定义是这样的：

```python
def forward(self, x, sublayer):
    return x + self.dropout(sublayer(self.norm(x)))
```

> 它要求 `sublayer` 是一个 **函数**，并且会传入一个参数 `self.norm(x)`，再执行：
>  `sublayer(self.norm(x))`

------

### 🔍 如果你写成：

```python
x = self.sublayer[0](x, self.self_attn(x, x, x, mask))
```

这表示你**提前就计算好了** `self.self_attn(x, x, x, mask)`，它已经是一个张量了（比如 `Tensor[B, L, D]`），你传给 `SublayerConnection` 的是这个张量，**不是函数**。

所以执行 `sublayer(self.norm(x))` 就会报错，因为你实际上写的是：

```python
some_tensor(self.norm(x))  # 报错！Tensor 不能调用
```

------

### ✅ 正确方式：使用 `lambda`

```python
x = self.sublayer[0](x, lambda x: self.self_attn(x, x, x, mask))
```

这样传进去的是一个函数（匿名函数 `lambda x: ...`），当 `SublayerConnection` 执行：

```python
sublayer(self.norm(x))
```

就变成：

```python
self.self_attn(self.norm(x), self.norm(x), self.norm(x), mask)
```

成功！

------

### 💡总结

| 写法                                                         | 传入类型 | 是否正确 | 说明                                  |
| ------------------------------------------------------------ | -------- | -------- | ------------------------------------- |
| `self.sublayer[0](x, self.self_attn(x, x, x, mask))`         | Tensor   | ❌ 错误   | 把计算结果（Tensor）传进去了          |
| `self.sublayer[0](x, lambda x: self.self_attn(x, x, x, mask))` | 函数     | ✅ 正确   | 把函数（可以传入 x 后再执行）传进去了 |

------

### 如果不熟悉 `lambda`，你也可以用普通函数代替：

```python
def attn_func(x):
    return self.self_attn(x, x, x, mask)

x = self.sublayer[0](x, attn_func)
```

