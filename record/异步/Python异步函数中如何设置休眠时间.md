### 问：Python异步函数中如何设置休眠时间

在 Python 异步函数中，设置休眠时间需要使用 **`asyncio.sleep()`** 并配合 **`await`** 关键字。



千万不能使用标准的 `time.sleep()`，因为它是同步阻塞操作，会直接冻结整个事件循环（Event Loop），导致其他并发任务全部挂起。



### 基本用法

Python

```
import asyncio

async def main():
    print("开始休眠...")
    # 休眠 2 秒（支持浮点数，如 0.5 秒）
    await asyncio.sleep(2)
    print("休眠结束！")

# 运行异步主函数
asyncio.run(main())
```

### 常见场景

#### 1. 并发休眠（验证非阻塞特性）

使用 `asyncio.gather()` 可以看到多个休眠任务是**同时进行**的，总耗时取决于最长的那个任务，而不是累加：



Python

```
import asyncio
import time

async def worker(task_id: int, delay: float):
    print(f"任务 {task_id} 开始，需等待 {delay} 秒")
    await asyncio.sleep(delay)
    print(f"任务 {task_id} 完成")

async def main():
    start = time.perf_counter()
    # 同时启动两个任务
    await asyncio.gather(
        worker(1, 2),
        worker(2, 3)
    )
    print(f"全部完成，总耗时: {time.perf_counter() - start:.2f} 秒")  # 约为 3 秒，而非 5 秒

asyncio.run(main())
```

#### 2. 休眠同时返回一个结果

`asyncio.sleep(delay, result=...)` 允许在休眠结束后直接返回一个值，常用于构造超时降级或模拟异步请求：



Python

```
import asyncio

async def fetch_data():
    # 休眠 1 秒后返回默认字典
    return await asyncio.sleep(1, result={"status": "success", "data": 42})

async def main():
    res = await fetch_data()
    print(res)

asyncio.run(main())
```

#### 3. 释放控制权（`delay=0`）

如果希望立即让出当前协程的控制权，允许事件循环去执行其他就绪任务，可以使用：

Python

```
await asyncio.sleep(0)
```

### 问：如何在 Python 异步函数中使用 asyncio 设置任务超时处理？

在 Python 中设置异步任务超时，主要有两种方式：**Python 3.11+ 推荐的 `asyncio.timeout()` 上下文管理器**，以及**通用兼容的 `asyncio.wait_for()`**。



### 方法一：`asyncio.timeout()`（推荐，Python 3.11+）

现代 Python 最推崇的方式，使用异步上下文管理器，语法清晰且能精确控制一段代码块的执行时限。



超时会抛出标准内置异常 **`TimeoutError`**（Python 3.11 中已将 `asyncio.TimeoutError` 别名为内置 `TimeoutError`）。



Python

```
import asyncio

async def slow_task():
    print("任务开始...")
    await asyncio.sleep(5)  # 模拟耗时 5 秒的操作
    print("任务完成！")

async def main():
    try:
        # 设置超时上限为 2 秒
        async 带有_timeout_as_cm:
        async with asyncio.timeout(2):
            await slow_task()
    except TimeoutError:
        print("操作已超时！")

asyncio.run(main())
```

> **小技巧（动态调整超时时间）：**
>
> 上下文管理器对象提供了 `.reschedule()` 方法，允许根据执行情况延长或缩短 deadline：
>
> Python
>
> ```
> async with asyncio.timeout(10) as cm:
>     # 比如读取到头部信息后，把最终截止时间重设为 3 秒后
>     cm.reschedule(asyncio.get_running_loop().time() + 3)
>     await slow_task()
> ```

### 方法二：`asyncio.wait_for()`（兼容旧版本 Python）

适用于 Python 3.7+ 的经典写法，直接包裹一个 awaitable（协程或 Task）。



如果发生超时，底层被包裹的任务**会被自动取消（Cancelled）**，并抛出 `TimeoutError`。



Python

```
import asyncio

async def fetch_api():
    await asyncio.sleep(3)
    return {"status": "ok"}

async def main():
    try:
        # 等待 fetch_api 执行，限时 1.5 秒
        result = await asyncio.wait_for(fetch_api(), timeout=1.5)
        print(result)
    except TimeoutError:  # Python 3.11 前通常捕获 asyncio.TimeoutError
        print("请求超时，已自动取消任务")

asyncio.run(main())
```

### 进阶场景：超时后不希望任务被取消（`asyncio.shield`）

默认情况下，`wait_for` 超时会强制取消（Cancel）内部的任务。如果你希望**主流程不再等待，但后台任务继续跑完**（比如写审计日志、保存关键落库数据），需要配合 `asyncio.shield()`：

Python

```
import asyncio

async def critical_job():
    print("关键任务执行中...")
    await asyncio.sleep(4)
    print("关键任务落盘成功！")

async def main():
    task = asyncio.create_task(critical_job())
    try:
        # shield 保护 task 不受 wait_for 取消信号的影响
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
    except TimeoutError:
        print("等待超时，主流程先继续...")
    
    # 稍后可以继续等待该任务完成
    await task

asyncio.run(main())
```

### 选型建议

| **方式**                          | **适用版本** | **特点**                                                     | **适用场景**                           |
| --------------------------------- | ------------ | ------------------------------------------------------------ | -------------------------------------- |
| **`asyncio.timeout(sec)`**        | Python 3.11+ | 异步上下文管理器，支持包含多条 `await`，支持动态重置截止时间 | 新项目、多步异步操作统一控时           |
| **`asyncio.wait_for(coro, sec)`** | Python 3.7+  | 单个函数调用，超时自动向内部传播 `CancelledError`            | 兼容旧版本代码、单一协程直接获取返回值 |

### 问：如果发生超时，底层被包裹的任务会被自动取消是什么意思？如果被包裹的任务是向第三方系统发送API呢？第三方系统会取消任务吗？还是我们的系统单方面取消呢？

“底层被包裹的任务会被自动取消”指的是：**Python 本地运行时向你的协程注入了一个 `asyncio.CancelledError` 异常，强行中断了本地代码的继续执行**。



这完全是**我们系统的单方面行为**，**第三方系统根本不会知道、也不会自动取消正在处理的任务**。



### 1. 本地“自动取消”的真实运作机制

当 `wait_for` 超时时，Python 底层执行了以下动作：



1. 对目标任务调用 `task.cancel()`。
2. 当协程正暂停在某处 `await` 时，事件循环会在此处立即抛出一个 **`asyncio.CancelledError`**。
3. 你的本地协程栈被迫展开退出，本地的等待和后续逻辑终止。

Python

```
async def my_task():
    try:
        await asyncio.sleep(10)  # 超时触发时，这里会直接抛出 CancelledError
        print("这行永远不会执行")
    except asyncio.CancelledError:
        print("本地任务被取消了！")
        raise  # 正常必须继续向上抛出，以完成协程清理
```

### 2. 向第三方发送 API 时，到底会发生什么？

这取决于超时发生的那一瞬间，**请求到了哪一步**：



#### 场景 A：请求还在排队或刚建连（未发送完毕）

- **状态**：HTTP 报文还没完全送出。
- **结果**：本地取消会直接切断底层 TCP 连接，第三方服务器可能根本没收到完整请求，不会执行业务。

#### 场景 B：请求已完整发送，正在等待第三方响应（最常见）

- **状态**：你的请求（如扣款、创建订单、调用模型）已经完整到达第三方服务器，对方的数据库或计算逻辑**已经在运行**。
- **结果**：
  - **本地系统**：因为超过了时限，本地抛出 `TimeoutError`，关闭底层 TCP 连接（发送 TCP RST 或 FIN 包），不再等待后续数据。
  - **第三方系统**：**继续闷头执行！** 绝大多数标准 HTTP API（如 RESTful / RPC）都是无状态的单向请求。即便 TCP 连接断开，第三方系统通常也不会中断已经进入数据库事务的业务逻辑。
  - **最终影响**：第三方服务器执行成功并落库，但你的系统认为“超时失败”了。这就造成了**状态不一致（幽灵写入 / 悬挂操作）**。

### 3. 如何解决这种“单方面取消”带来的副作用？

如果调用的第三方 API 涉及写操作（创建资源、扣费、下发消息），不能简单地超时捕获后就当无事发生。业界标准解法有三种：



#### ① 接口必须具备幂等性（Idempotency Key）

在调用第三方时，在 Header 或参数中带上全局唯一的业务 ID（如 `Idempotency-Key: uuid`）：



- 当本地超时后，可以安全地发起重试。
- 第三方系统如果发现重复的 Key，会直接返回上一次处理的结果，而不是重复执行扣费或创建。

#### ② 超时后显式调用撤销/回滚接口

如果该业务支持撤销，在捕获到 `TimeoutError` 时，执行补偿逻辑：



Python

```
try:
    async with asyncio.timeout(3):
        await call_payment_api(order_id)
except TimeoutError:
    # 本地虽然单方面取消了等待，但需通知第三方主动撤销该单
    await cancel_or_refund_api(order_id)
```

#### ③ 超时后进入“未决状态”并启动异步对账

在分布式系统设计中，超时不等于“失败”，而等于“状态未知（Unknown）”：



1. 捕获 `TimeoutError` 后，把本地订单状态置为 `PENDING / 处理中`。
2. 启动后台定时轮询任务，去第三方查询该 `order_id` 到底是成功了还是未生成，以此来修正本地最终状态。