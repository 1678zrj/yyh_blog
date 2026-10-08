## 问：nn.NLLLoss和nn.KLDivLoss之间有什么区别

`nn.NLLLoss` 和 `nn.KLDivLoss` 都是 PyTorch 中用于训练分类模型的损失函数，但它们的适用场景和输入要求是不同的。下面详细解释两者的区别：

------

## 🔹 1. `nn.NLLLoss`（负对数似然损失）

### ➤ 使用场景：

用于**多分类问题**，并且模型的输出是**对数概率（log-probabilities）**。

### ➤ 输入要求：

- `input`: **对数概率**，通常是通过 `log_softmax` 处理的模型输出，形状为 `(N, C)`（batch_size × 类别数）。
- `target`: 是 **整数标签**，表示每个样本的真实类别，形状为 `(N,)`。

### ➤ 示例：

```python
import torch
import torch.nn as nn

log_probs = torch.log_softmax(torch.tensor([[1.0, 2.0, 0.5]]), dim=1)
target = torch.tensor([1])

loss_fn = nn.NLLLoss()
loss = loss_fn(log_probs, target)
print(loss)
```

------

## 🔹 2. `nn.KLDivLoss`（Kullback-Leibler 散度）

### ➤ 使用场景：

用于**分布对分布**之间的差异测量，常用于**soft target（软标签）训练**，比如**蒸馏学习**或**标签平滑**。

### ➤ 输入要求：

- `input`: 是 **对数概率**（通常由 `log_softmax` 得到）。
- `target`: 是一个 **概率分布**（必须是归一化的概率，通常由 `softmax` 得到）。

默认行为是计算：
$$
KL(target || input) = \sum_i target_i · (log(target_i) - input_i)
$$

### ➤ 示例：

```python
import torch
import torch.nn as nn

log_probs = torch.log_softmax(torch.tensor([[1.0, 2.0, 0.5]]), dim=1)
target_probs = torch.softmax(torch.tensor([[1.0, 2.0, 0.5]]), dim=1)

loss_fn = nn.KLDivLoss(reduction='batchmean')
loss = loss_fn(log_probs, target_probs)
print(loss)
```

------

## 🔸 总结对比：

| 特性          | `nn.NLLLoss`              | `nn.KLDivLoss`                     |
| ------------- | ------------------------- | ---------------------------------- |
| 输入 `input`  | 对数概率（`log_softmax`） | 对数概率（`log_softmax`）          |
| 输入 `target` | 整数标签（类别索引）      | 概率分布（通常为 `softmax` 输出）  |
| 输出类型      | 单个标量损失              | 可配置为标量或逐元素损失           |
| 常用于        | 标准分类任务              | 蒸馏学习、标签平滑、soft target 等 |

------

如果你在使用 `nn.KLDivLoss` 时遇到过报错，大概率是因为 `target` 不是一个概率分布（即没有用 `softmax`），或 `input` 不是 `log_softmax`。

需要进一步对比 `CrossEntropyLoss` 吗？它和这两者有组合关系。

## 问：举个例子说明计算过程吧

当然可以，下面我通过一个具体的例子来说明 nn.NLLLoss 和 nn.KLDivLoss 的计算过程，并解释它们之间的差别。
### 示例设置（统一）
我们用同一个模型输出 logits = [1.0, 2.0, 0.5] 来演示：
```python
import torch
import torch.nn as nn
import torch.nn.functional as F
```
### 1. nn.NLLLoss 示例
#### 场景：类别标签是硬标签（整数）
我们使用 log_softmax 生成对数概率，并用 NLLLoss 计算损失。
```python
logits = torch.tensor([[1.0, 2.0, 0.5]]) # shape: (1, 3)
log_probs = F.log_softmax(logits, dim=1) # 对数概率
target = torch.tensor([1]) # 真实标签为第2类（从0开始）
loss_fn = nn.NLLLoss()
loss = loss_fn(log_probs, target)
print("NLLLoss:", loss.item())
```
手动计算：
```text
log_softmax([1.0, 2.0, 0.5]) ≈ [-1.4076, -0.4076, -1.9076]
target = 1
loss = -log_probs[0][1] = -(-0.4076) = 0.4076
```
### 2. nn.KLDivLoss 示例
#### 场景：目标是 soft label（概率分布）
```python
logits = torch.tensor([[1.0, 2.0, 0.5]])
log_probs = F.log_softmax(logits, dim=1) # 模型预测（对数概率）
# soft target，假设我们用softmax模拟了真实分布
target_probs = F.softmax(torch.tensor([[1.0, 2.0, 0.5]]), dim=1)
loss_fn = nn.KLDivLoss(reduction='batchmean')
loss = loss_fn(log_probs, target_probs)
print("KLDivLoss:", loss.item())
```
手动计算：
```text
log_probs ≈ [-1.4076, -0.4076, -1.9076]
target_probs ≈ [0.1863, 0.5060, 0.3077]
KLDivLoss = Σ target[i] * (log(target[i]) - log_probs[i])
≈ 0.1863 * (log(0.1863) + 1.4076)
+ 0.5060 * (log(0.5060) + 0.4076)
+ 0.3077 * (log(0.3077) + 1.9076)
≈ 0.0003 # 这里因为 target 和预测一致，所以结果非常小
```
### 总结对比
| 对比项       | NLLLoss                      | KLDivLoss                                                    |
| ------------ | ---------------------------- | ------------------------------------------------------------ |
| 目标类型     | 整数标签（如 1）             | 概率分布（如 [0.1, 0.7, 0.2]）                               |
| 损失计算方式 | 只看正确类的 log 概率        | 对所有类的概率进行 KL 散度计算                               |
| 适用场景     | 普通分类任务                 | 蒸馏、标签平滑、soft target 学习                             |
| 数学表达     | $(-\log(p_{\text{target}}))$ | $(\sum p_i^{\text{target}} \log \frac{p_i^{\text{target}}}{p_i^{\text{model}}})$ |

## 问：既然如此，那为什么Transformer模型复现项目中，作为分类问题，为什么要使用Label Smoothing呢？是因为CrossEntropyLoss相当于LogSoftmax+nn.NLLLoss，因此也只能接受target整数索引吗？

![image-20250618192900610](C:\Users\28269\AppData\Roaming\Typora\typora-user-images\image-20250618192900610.png)

## 问：我已经理解了 label smoothing 是将 one-hot 向量转换为 soft label，但这是不是label smoothing不使用nn.NLLLoss的原因之一呢？因为NLLLoss和KLDivLoss的计算公式并不相同。

![image-20250618193405687](C:\Users\28269\AppData\Roaming\Typora\typora-user-images\image-20250618193405687.png)

## 问：嗯，我之前学习到nn.CrossEntropyLoss 就是 nn.LogSoftmax + nn.NLLLoss，所以nn.CrossEntropyLoss输入的 target也是整数类别索引，所以也不直接适配LabelSmoothing，所以LabelSmoothing用的是nn.KLDivLoss，虽然我觉得这应该是原因之一，但是还有没有其它的原因呢？因为我看nn.KLDivLoss和nn.NLLLoss不只是输入的 target不同，计算方式也不同，和交叉熵损失函数不太一样

![image-20250618193723344](C:\Users\28269\AppData\Roaming\Typora\typora-user-images\image-20250618193723344.png)

## 问：那现在的大语言模型在预训练时用的损失函数是CrossEntropyLoss还是label smoothing的KLDivLoss呢？

![image-20250618193932169](C:\Users\28269\AppData\Roaming\Typora\typora-user-images\image-20250618193932169.png)

## 问：好的，查一下

![image-20250618194144809](C:\Users\28269\AppData\Roaming\Typora\typora-user-images\image-20250618194144809.png)