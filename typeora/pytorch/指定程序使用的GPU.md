## 多GPU服务器，我只想程序使用编号0或1的GPU

```sh
export CUDA_VISIBLE_DEVICES=0,1
MODEL_NAME=/workspace/public/Baichuan
MODEL_ABBR=baichuan-7b
WATERMARK_METHOD_FLAG="--watermark_method kgw"

python3 gen.py \
    --base_model $MODEL_NAME \
    --fp16 \
    --batch_size 32 \
    --input_file data/dataset/mc4/mc4.zh.jsonl \
    --output_file gen/$MODEL_ABBR/kgw/mc4.zh.mod.jsonl \
    $WATERMARK_METHOD_FLAG
```

## 脚本这样写能够保证吗？

### DeepSeek:

是的，**完全能够保证程序只使用0和1号GPU（或单独一块GPU）**。您的脚本配置正确，原因如下：

### ✅ 保证机制分析
1. **CUDA_VISIBLE_DEVICES 的隔离作用**
   - `export CUDA_VISIBLE_DEVICES=0,1` 创建了**硬件级隔离**
   - CUDA运行时只能"看到"这两块GPU
   - 其他GPU（如2,3,...）对程序**完全不可见**

2. **操作系统层面的强制约束**
   - 该环境变量由NVIDIA驱动直接处理
   - 程序尝试访问其他GPU时会直接失败（返回`cudaErrorInvalidDevice`错误）

3. **验证方法（在脚本中添加检查）**
   ```bash
   # 在python命令前添加验证
   echo "可见GPU: $CUDA_VISIBLE_DEVICES"
   nvidia-smi --query-gpu=index,uuid,name --format=csv
   ```

### 🔒 双重保证建议
在Python脚本中增加硬件检查（以PyTorch为例）：
```python
import torch

# 检查实际可见设备
visible_devices = [torch.cuda.device(i) for i in range(torch.cuda.device_count())]
print(f"程序可见GPU: {[d for d in visible_devices]}")

# 强制使用指定设备
device = torch.device('cuda:0')  # 主设备
```
这将输出类似：
```
程序可见GPU: [<torch.cuda.device at 0x7f6b0c02b7f0>, <torch.cuda.device at 0x7f6b0c02b8d0>]
```

### ⚠️ 极端情况处理
唯一可能失效的场景：
1. **脚本被意外修改**：
   ```bash
   # 错误示例：在脚本中覆盖了变量
   export CUDA_VISIBLE_DEVICES=0,2  # 错误修改！
   ```
   **防护方案**：使用只读变量
   ```bash
   readonly CUDA_VISIBLE_DEVICES=0,1
   ```

2. **程序内部硬编码设备**：
   ```python
   # 错误示例：在gen.py中硬编码设备
   device = torch.device("cuda:3")  # 绕过环境变量
   ```
   **防护方案**：在脚本中添加设备检查
   ```python
   assert torch.cuda.device_count() == 2, "必须使用2块GPU!"
   ```

### 📊 GPU使用验证方法
运行后实时监控：
```bash
# 在另一个终端执行
watch -n 1 "nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv"
```
正常输出应显示：
```
0, 75%, 12000 MiB
1, 80%, 11800 MiB
2, 0%, 10 MiB
3, 0%, 10 MiB
```

### 最终结论
您的脚本**完全正确且安全**：
1. 当设置为`0,1`时 → 只使用GPU0和1
2. 当设置为`0`时 → 只使用GPU0
3. 其他GPU绝对不会被使用

这是多GPU服务器分配设备的**标准且可靠方法**，被所有主流深度学习框架严格遵循。

## 这个export CUDA_VISIBLE_DEVICES=0的作用域只限制在脚本里面对吗?

是的，`export CUDA_VISIBLE_DEVICES=0` **的作用域只在该脚本及其衍生进程中有效**，不会影响到系统的其他程序或用户。具体说明如下：

------

### ✅ **作用域说明**

当你在脚本 `run_gen.sh` 中这样写：

```bash
export CUDA_VISIBLE_DEVICES=0
python3 gen.py ...
```

这条 `export` 命令的效果是：

- 它设置了一个环境变量 `CUDA_VISIBLE_DEVICES=0`。
- 这个环境变量只对这个脚本内（以及它调用的子进程，例如 `python3 gen.py`）有效。
- 不会影响你当前 shell 会话之外的其他程序或用户。

------

### ✅ 举例说明

假设你有两个终端：

- **终端 A** 中运行了 `export CUDA_VISIBLE_DEVICES=0 && python3 a.py`，这个 `a.py` 只会看到 GPU 0；
- **终端 B** 没有设置这个变量，运行的 `python3 b.py` 还是能看到所有 GPU。

------

### ✅ 更进一步：如何验证？

你可以在 `gen.py` 中加一句：

```python
import os
print("Visible GPUs:", os.environ.get("CUDA_VISIBLE_DEVICES"))
```

或者用 PyTorch 验证：

```python
import torch
print(torch.cuda.device_count())
```

如果你设置了 `CUDA_VISIBLE_DEVICES=0`，它应该只输出一个 GPU。

------

### ✅ 补充说明

- 如果你在终端里直接 `export CUDA_VISIBLE_DEVICES=0`，这个环境变量会一直存在于当前终端会话，除非你关闭终端或者运行 `unset CUDA_VISIBLE_DEVICES`。
- 在 shell 脚本中设置的环境变量，不会污染到你外部 shell 环境。

------

### 🔒 结论

你写在 `run_gen.sh` 里的 `export CUDA_VISIBLE_DEVICES=0` 是 **局部生效、对其他任务无影响** 的，**是推荐用法**，特别适合多用户/多任务共享 GPU 的情况。