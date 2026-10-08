```
asyncio.get_running_loop().time()
```

### 问：这段代码的作用是什么

`asyncio.get_running_loop().time()` 用于获取当前正在运行的事件循环（Event Loop）的**内部单调时钟时间**（以秒为单位的 `float` 数值）。



它由两个操作组合而成：



- **`asyncio.get_running_loop()`**：获取当前 OS 线程中处于激活状态的事件循环实例。如果当前上下文中没有运行中的事件循环（例如在普通的同步顶层代码中直接调用），会立即抛出 `RuntimeError`。
- **`.time()`**：返回该事件循环内部计时器的当前读数，其底层通常基于操作系统的单调时钟（类似于 `time.monotonic()`）。

**核心特性**



- **单调递增**：时间只会向前流动，不受系统墙上时钟（Wall-clock time）变更、夏令时切换或 NTP 网络校时跳变的影响，绝对不会出现“时间倒退”的情况。
- **无绝对时间意义**：它返回的不是 UNIX 时间戳，也没有固定的起点（参考原点通常是系统启动时间或事件循环创建的时间）。因此无法直接将其转换为具体的年月日时间。

**常见应用场景**



- **计算绝对触发点**：配合底层的调度函数 `loop.call_at()` 使用。例如要在 5 秒后执行某个回调：

  Python

  ```
  loop = asyncio.get_running_loop()
  target_time = loop.time() + 5.0
  loop.call_at(target_time, my_callback)
  ```

- **精确的异步耗时测量**：在协程中测量某段异步 I/O 或操作的实际执行耗时，避免因系统调时产生负值或误差：

  Python

  ```
  loop = asyncio.get_running_loop()
  start = loop.time()
  await some_async_task()
  elapsed = loop.time() - start
  ```

### 问：对于这段代码，如果some_async_task执行完毕之后，事件循环没有马上回到当前代码会有什么影响吗

```
loop = asyncio.get_running_loop()
start = loop.time()
await some_async_task()
elapsed = loop.time() - start
```

最直接的影响是 **`elapsed` 记录的值会偏大（耗时虚高）**。它测出的不再仅仅是 `some_async_task()` 自身的执行时间，而是包含了**事件循环处理其他任务所产生的排队与调度延迟**。



具体影响与技术细节如下：



- **测量结果从“任务耗时”变为“端到端等待延迟”**：

  在 `asyncio` 的协作式单线程调度模型中，`some_async_task()` 执行完成时，恢复当前协程的回调只会被推进事件循环的就绪队列（Ready Queue）。如果队列前面还有其他正在执行的协程、回调函数或微小的同步阻塞代码，当前代码就必须排队等待。因此：

  $$\text{elapsed} = \text{任务实际执行时间} + \text{网络/IO等待时间} + \mathbf{调度排队延迟}$$

- **容易误判系统瓶颈**：

  如果外部有其他协程执行了耗时较长的 CPU 密集操作，或者同时间有大量并发回调积压，`some_async_task` 本身可能只耗费了 5ms，但 `elapsed` 测出的结果可能是 100ms。这会导致开发者误以为是 `some_async_task` 内部运行缓慢，掩盖了事件循环被其他任务阻塞的真正问题。

- **业务逻辑层面不会有破坏性影响**：

  从程序正确性来看，当前协程在未被调度恢复前始终处于挂起状态，其局部变量与数据上下文完好无损。除了恢复执行的时间点晚于预期外，不会引起数据错乱或并发竞争（除非该任务对绝对超时有强一致性要求）。

**如何选择改进方案**



- **如果只关注 `some_async_task` 本身的耗时**：将计时逻辑移入函数内部，在其入口和退出点记录时间：

  Python

  ```
  async def some_async_task():
      t0 = asyncio.get_running_loop().time()
      try:
          # 实际执行逻辑
          await do_work()
      finally:
          actual_cost = asyncio.get_running_loop().time() - t0
  ```

- **如果关注的是调用方的端到端感知耗时**：保留当前写法即可，因为对调用者而言，排队延迟本身就是它需要承担的实际等待成本。

### 问：

```
async def some_async_task():
    t0 = asyncio.get_running_loop().time()
    try:
        # 实际执行逻辑
        await do_work()
    finally:
        actual_cost = asyncio.get_running_loop().time() - t0

```

### 这样也没法测出some_async_task 本身的耗时吧

你的判断完全准确。这种写法**依然无法**测出 `some_async_task` 本身纯粹的耗时。



只要函数内部存在 `await`（例如 `await do_work()`），就一定会遭遇同样的问题：



**为什么依然不准**



- **`await` 必然导致交出控制权**：当执行到 `await do_work()` 时，`some_async_task` 会立刻挂起，事件循环重新接管线程去调度其他就绪的任务。
- **时钟在挂起期间不会停止**：`loop.time()` 是单调递增的墙上时钟（Monotonic Wall-clock）。在当前协程被挂起、其他任务占用 CPU 的整个过程中，计时器一直在跑。
- **恢复执行依然存在排队延迟**：即便 `do_work()` 已经完成，`some_async_task` 也只是被重新放回事件循环的就绪队列末尾。如果前面有其他耗时任务，它同样要排队等待被唤醒。

把计时器移入函数内部，仅仅去掉了“当前函数执行完毕后，恢复外部调用方”的那一次调度延迟，而**函数内部所有 `await` 产生的调度排队与等待时间依然被算进了 `actual_cost`**。



**如何真正测量不同维度的耗时**



根据你所定义的“自身耗时”，需要采用不同的测量方式：



**1. 如果只想测 CPU 纯计算耗时（扣除挂起和 I/O 耗时）**

协程在单线程中穿插运行，直接使用 `time.thread_time()` 也会把其他并发协程的 CPU 时间算进来。需要借助支持协程上下文感知的专用性能分析工具（如 **`yappi`**）：



Python

```
import yappi

yappi.set_clock_type("cpu")  # 跟踪真实 CPU 执行时间
yappi.start()

await some_async_task()

yappi.stop()
# 打印出的 stats 会自动区分协程本身的 CPU 占用与 yield 挂起时间
yappi.get_func_stats().print_all()
```

**2. 如果只想测同步代码段的真实耗时**

在没有 `await` 的纯同步片段中，事件循环无法抢占执行权，直接用 `time.perf_counter()` 是绝对准确的：



Python

```
async def some_async_task():
    # 纯同步逻辑：无 await，不会被事件循环打断
    t0 = time.perf_counter()
    heavy_sync_computation()
    cost = time.perf_counter() - t0

    # 遇到 await 会释放线程，不要将 await 包含在精确耗时统计中
    await do_work()
```

**3. 如果想诊断到底被事件循环延迟了多久**

可以开启 asyncio 的调试模式。当单个协程或回调占用事件循环过长（默认大于 100ms）导致其他任务饥饿时，asyncio 会自动打出 Warning 日志：

Python

```
# 运行事件循环时开启 debug
asyncio.run(main(), debug=True)

# 或者调整慢回调阈值（例如超过 20ms 就报警）
loop.slow_callback_duration = 0.02
```

### 问：所以说白了异步函数就没法准确测出耗时对吗？

不能说“无法测量”，而是**不能再用同步代码“在函数前后打两个时间戳相减”的方式来理解耗时**。

在同步多线程模型中，一个线程独占时间轴，耗时基本等于 `计算时间 + 阻塞等待时间`；但在 `asyncio` 的单线程协作式模型中，**多个协程在同一条时间线上来回切片交替执行**。“耗时”这个原本单一的概念被拆成了三个完全不同的物理维度：

| 耗时维度                                   | 定义                                       | 能否精确测量 | 正确的测量方式                                    |
| ------------------------------------------ | ------------------------------------------ | ------------ | ------------------------------------------------- |
| **端到端挂钟耗时** *(Wall-clock Latency)*  | 从发出调用到最终拿到结果的实际物理流逝时间 | **能**       | `loop.time()` 或 `time.perf_counter()` 前后相减   |
| **纯 CPU 执行耗时** *(Coroutine CPU Time)* | 该协程真正在执行 Python 字节码的净时间     | **能**       | 借助感知协程上下文的 Profiler（如 `yappi`）       |
| **底层纯 I/O 耗时** *(Net I/O Wait Time)*  | 真正花在网卡发包、等待服务端响应的时间     | **能**       | 借助网络库底层的生命周期追踪钩子（Trace Signals） |

---

**为什么简单打点会让人觉得“测不准”？**

因为在业务层写 `start = time()` ... `await task()` ... `elapsed = time() - start`，测出来的实质上是**端到端挂钟耗时**。

这个数值包含了三部分：


$$\text{挂钟耗时} = \text{任务自身处理} + \text{纯底层 I/O} + \mathbf{被其他协程抢占及排队的时间}$$

如果你把它当成“用户感知的系统接口延迟（SLA）”，它是 **100% 真实且精确** 的——因为外界确实等了这么久；但如果你试图用它去衡量“`some_async_task` 自身有没有代码层面的性能毛病”，它就是**失真**的，因为排队时间是由当前系统的并发状态和其他“慢协程”决定的。

---

**如何针对性地“准确测量”？**

想要测准，必须把关注点从“粗粒度的大函数”下沉到具体的物理动作：

* **想测纯计算瓶颈**：不要测包含 `await` 的大函数。把纯 CPU 计算剥离成没有 `await` 的独立同步小函数，直接在同步函数内部用 `time.perf_counter()` 测量，或者全局挂载 `yappi` 追踪。
* **想测网络 I/O 瓶颈**：不要在 `await client.get(...)` 外部掐表。利用客户端自带的事件钩子捕获底层耗时。以 `aiohttp` 为例，使用 `TraceConfig` 可以精确剥离出建连、DNS、首包、传输各自的净时间，完全排除外部事件循环的调度干扰：
```python
import aiohttp

async def on_request_end(session, context, params):
    # 这里记录的是网络请求从发出到底层接收完成的真实耗时
    # 不受业务层其他排队协程的影响
    print(f"URL: {params.url}, 实际传输耗时: {params.response.headers}")

trace_config = aiohttp.TraceConfig()
trace_config.on_request_end.append(on_request_end)

```



只要界定清楚你究竟要找的是**业务等待耗时**、**纯算法开销**还是**网络净延迟**，每一项都有准确测算的方法。

### 问：为什么不用time.time呢？我感觉作用都是一样的

表面上看它们都返回以秒为单位的浮点数，但在**测耗时**或**任务调度**时，使用 `time.time()` 属于经典的编程反模式。



核心原因在于两者的时钟本质完全不同：`time.time()` 获取的是**现实世界的日历时间（墙上时钟）**，而 `loop.time()` 和 `time.perf_counter()` 获取的是**单调时钟（Monotonic Clock）**。



**1. 致命隐患：`time.time()` 可能会“时光倒流”**



`time.time()` 依赖于操作系统的系统时间（比如右下角显示的几点几分几秒）：



- **NTP 自动校时**：服务器每天会频繁与网络时间服务器同步，当发现本地系统时间走快或走慢时，操作系统会直接向前或向后调整系统时间。
- **时钟突变**：如果代码运行期间恰好发生了 NTP 校准、用户手动修改了时间、或者跨越了夏令时切换点，`time.time()` 的读数可能突然跳跃，甚至出现后一次读取比前一次还要小的情况，导致 `end - start` 算出**负数**或**离谱的巨大数值**。
- **单调时钟的保证**：`loop.time()` 底层依赖于操作系统的单调时钟（类似 `time.monotonic()`），严格保证时钟**只会一直向前走**，绝不受外部调时的任何干扰。

**2. 调度机制的对齐：避免定时器彻底失效**



`asyncio` 内部使用最小堆（Min-heap）来管理所有的定时任务（如 `asyncio.sleep`、`loop.call_later`、超时控制等）。



堆中记录的触发时间点全部基于 `loop.time()`。如果你用 `time.time()` 去配合 `loop.call_at()` 进行调度：



Python

```
# 致命错误：基准不同
loop.call_at(time.time() + 5, callback)
```

因为 `time.time()` 返回的是以 1970 年为原点的绝对秒数（约 $1.7 \times 10^9$ 秒），而 `loop.time()` 往往是以系统开机时间为原点（可能才几千秒）。混用的结果是该回调会被排到遥远的未来，**永远无法触发**。



**3. 时钟精度差异**



- `time.perf_counter()` 专为性能评测设计，调用的是 CPU 硬件级别的性能计数器，精度达纳秒级别。
- `time.time()` 的精度取决于操作系统更新日历时钟的频率，在某些系统（如早期的 Windows）上可能存在毫秒级的颗粒度跳跃，无法捕获微秒级的轻量操作。

**三者定位对比**



| **函数**                  | **时钟性质**   | **会受调时/NTP影响吗**   | **正确应用场景**                                  |
| ------------------------- | -------------- | ------------------------ | ------------------------------------------------- |
| **`time.time()`**         | 墙上时钟       | **会**（可能突变、倒流） | 记录日志产生时间、存数据库、向用户展示当前时间    |
| **`loop.time()`**         | 内部单调时钟   | **否**（严格递增）       | `asyncio` 内部调度、`call_at` 定时、协程超时控制  |
| **`time.perf_counter()`** | 高精度单调时钟 | **否**（严格递增）       | 基准性能测试（Benchmark）、测量某段逻辑的精确耗时 |

简而言之：**只要是计算“时间差（耗时）”或“相对等待时间”，永远不要用 `time.time()`**。