### 方案背景与待解决问题

在基于 LangGraph 与分布式 Worker（TaskIQ）的架构中，当工具执行或大模型调用遭遇不可自愈的致命故障（如基础设施 Docker 崩溃、数据库只读、API Key 欠费/硬拦截）时，系统面临两个相互交织的诉求：



1. **消息协议闭环与即时性**：必须让当前失败工具及后续所有排队任务生成合法的 `ToolMessage`（状态为 error/cancelled），写入 Checkpoint 并通过 Redis Stream 流式推向前端，防止悬空 ID。
2. **Worker 执行态收敛**：必须将本次异步任务标记为失败（修改 DB `RunStatus.FAILED`、发送失败事件、释放分布式锁）。

由于 LangGraph 的底层机制为“节点内部一旦发生未捕获的 Python 异常（`raise`），当前节点在本次 Superstep 计算出的局部状态将不会持久化到 Checkpointer”，因此衍生出两种不同的协同设计方案。



### 方案 A：两阶段熔断（图内落盘补丁 + 专用节点抛出异常）

#### 1. 核心设计理念

将“状态收拢”与“阻断抛错”拆分为两个独立的执行阶段：先由业务节点将错误消息和取消消息正常提交并持久化，再由图路由到一个专用的抛错节点（`fatal_abort_node`）执行 `raise`，将 Python 异常穿透给 Worker 的最外层异常捕获块。



#### 2. 状态与图拓扑设计

- **状态扩展**：`AgentState` 增加 `fatal_error: dict[str, Any] | None` 字段。
- **拓扑变更**：新增一个专门用于抛出异常的终端节点 `fatal_abort_node`。
- **路由逻辑**：所有条件边（`route_after_agent`、`route_next_task`）检测到 `fatal_error` 存在时，统一重定向至 `fatal_abort_node`。

#### 3. 执行全流程

```
[发生致命异常的节点] (single_write / agent)
       │ 捕获异常，生成失败消息 + 排空剩余任务
       │ 提交 return {"messages": [...], "fatal_error": {...}}
       ▼
[LangGraph 引擎] (Superstep 边界)
       │ 成功持久化补丁消息至 Checkpointer
       │ 通过 updates / messages 流将事件输出给 Worker
       ▼
[条件路由器]
       │ 检测到 state["fatal_error"] 不为空
       │ 路由至 "fatal_abort_node"
       ▼
[fatal_abort_node]
       │ 执行 raise FatalToolError(...)
       ▼
[Worker 外部捕获] (except Exception as exc)
       │ 被 Worker 最外层 try...except 捕获
       │ 调用 run_crud.set_run_failed(...)
       │ 发送 Redis "failed" 事件
       │ 释放分布式锁，任务以 Python Exception 形式结束
```

#### 4. 系统各组件表现

- **发生异常的节点**：内部 `try...except FatalError` 拦截异常，不直接 `raise`，计算完成并 `return`。
- **Checkpointer 状态**：前序补丁消息已持久化；最后一步记录 `fatal_abort_node` 执行异常，`state.next` 指向 `("fatal_abort_node",)`。
- **Worker (`worker.py`)**：完全复用原有的 `try...except Exception as exc:` 逻辑，主执行流程代码无需新增状态判定分支。
- **任务队列（TaskIQ）**：将此任务视作“抛出未捕获异常而失败”，若队列配置了重试策略（Retry Policy），会根据策略判定是否重试。

### 方案 B：终态收敛（路由至 END + Worker 检查 State 元数据）

#### 1. 核心设计理念

将受控的致命故障视为状态机的合法终态（Terminal State）。业务节点捕获异常后将错误消息、取消消息和错误标记写入状态，图的条件路由直接将控制流引导至 `END` 正常结束。Worker 在消费完流后，通过检查最终 State 的元数据判定执行成败并做收敛。



#### 2. 状态与图拓扑设计

- **状态扩展**：`AgentState` 增加 `fatal_error: dict[str, Any] | None` 字段。
- **拓扑变更**：无需新增任何执行节点，图结构保持现有节点不变。
- **路由逻辑**：所有条件边（`route_after_agent`、`route_next_task`）检测到 `fatal_error` 存在时，统一直接返回 `END`。

#### 3. 执行全流程

```
[发生致命异常的节点] (single_write / agent)
       │ 捕获异常，生成失败消息 + 排空剩余任务
       │ 提交 return {"messages": [...], "fatal_error": {...}}
       ▼
[LangGraph 引擎] (Superstep 边界)
       │ 成功持久化补丁消息至 Checkpointer
       │ 通过 updates / messages 流将事件输出给 Worker
       ▼
[条件路由器]
       │ 检测到 state["fatal_error"] 不为空
       │ 直接返回 END
       ▼
[LangGraph 引擎]
       │ 图正常运行到达 END 边界，完成生命周期
       │ astream 流正常结束，无 Python 异常抛出
       ▼
[Worker 消费完成] (正常主流程内部)
       │ 读取 state = await graph.aget_state(config)
       │ 检测到 state.values.get("fatal_error")
       │ 主动调用 mark_unfinished_tools / set_run_failed
       │ 发送 Redis "failed" 事件
       │ 释放分布式锁，任务以正常 return 形式结束
```

#### 4. 系统各组件表现

- **发生异常的节点**：内部拦截异常，提交消息补丁并打上错误标记，`return` 给图状态。
- **Checkpointer 状态**：图从 `START` 完整流转至 `END`，所有补丁消息持久化，`state.next` 为空元组 `()`。
- **Worker (`worker.py`)**：在 `astream` 迭代结束后，需要在判断 `active_interrupts` 前追加对 `state.values.get("fatal_error")` 的判定分支；外层的 `except Exception` 仅用于兜底底层真正的未捕获系统崩溃。
- **任务队列（TaskIQ）**：将此任务视作“正常执行完毕（ACK）”，不会触发任务队列级别的重试逻辑。

### 关键技术特征客观对照

| **维度**                           | **方案 A（两阶段熔断抛错）**                                 | **方案 B（终态收敛至 END）**                                 |
| ---------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **异常出口机制**                   | 图内部抛出 Python 异常，穿透至 Worker 外层 `except`          | 图正常流转至 `END`，由 Worker 在流结束后读取状态字段         |
| **Checkpointer `state.next` 状态** | 保留未完成的任务游标，指向 `("fatal_abort_node",)`           | 任务游标净空，`state.next` 为空元组 `()`                     |
| **图拓扑结构**                     | 需在图中额外注册 `fatal_abort_node` 节点                     | 保持现有拓扑结构，仅在条件边中增加分支判断                   |
| **Worker (`worker.py`) 改动**      | **无改动**：直接依赖现有的外层 `except Exception` 处理       | **需增加分支**：在流结束处增加约 10 行对 `fatal_error` 的状态检查与 DB 标记 |
| **任务队列（TaskIQ）行为**         | 识别为 Execution Error，若配置队列 Retry Policy 则可能触发重试 | 识别为 Success/ACK，受控故障不触发队列级重试                 |
| **进程崩溃防御（OOM/掉电）**       | 无法处理该类物理崩溃，同样需依赖下次启动的 L1 补丁修复       | 无法处理该类物理崩溃，同样需依赖下次启动的 L1 补丁修复       |
| **大模型调用（`call_model`）适配** | 需在模型节点捕获致命异常后路由至 `fatal_abort_node`          | 需在模型节点捕获致命异常后标记状态并路由至 `END`             |

## 问：上述两个方案你认为哪个更好？

## Gemini:

我明确更认同 **方案 B（终态收敛至 END + Worker 检查 State 元数据）**。



在分布式 Agent 架构中，方案 A（两阶段熔断）本质上是一种**为了复用 Worker 外层 `try...except` 而牺牲状态机纯粹性的“架构 Hack”**；而方案 B 则是**将受控致命故障纳入状态机标准生命周期**的工业级设计。



以下从四个核心维度展开深度对比分析：



## 1. 状态机契约与 Checkpoint 可恢复性（决定性缺陷）

方案 A 在 Checkpointer 层面埋下了一个严重的隐患：



- **方案 A 制造了“地雷游标”**：当在 `fatal_abort_node` 抛出 Python 异常时，LangGraph 不会保存该节点内部状态，但该节点会被记入待恢复堆栈，导致持久化 Checkpoint 的 `state.next = ("fatal_abort_node",)`。这意味着如果后续通过该 `thread_id` 进行状态快照排查、回滚、人工干预介入（Human-in-the-loop）或二次恢复调用（`ainvoke(None, config)`），状态机会无条件立刻重新执行 `fatal_abort_node` 并再次崩溃。
- **方案 B 保持状态终态闭环**：发生不可自愈故障时，业务节点已经成功生成了所有未决任务的 `ToolMessage(status="error"/"cancelled")`，对整个图而言，**消息协议已经补齐，状态转移已经完成**。此时控制流流转至 `END`，`state.next = ()`，精确反映了“本次会话已经执行完结并合法归档”的语义，完全不破坏 Checkpointer 的版本一致性与只读查阅价值。

## 2. 分布式任务调度（TaskIQ）的职责边界

在现代后端体系中，必须严格区分**系统级物理崩溃**与**业务级受控失败**：



| **异常类型**       | **典型场景**                                                | **TaskIQ 调度视角**          | **正确应对策略**                                             |
| ------------------ | ----------------------------------------------------------- | ---------------------------- | ------------------------------------------------------------ |
| **非受控系统崩溃** | Worker OOM 退出、宿主机断电、网络物理中断、第三方库 C-panic | 任务执行异常（Task Error）   | 触发队列重试（Retry Policy）、死信队列处理、由下次启动的 L1 补丁做未决状态抢占 |
| **受控致命故障**   | 基础设施容器挂掉、DB 只读拦截、LLM API Key 欠费/硬拦截      | **任务执行成功（Task ACK）** | 消息补全入库，RunStatus 标为 FAILED，释放锁，**坚决不重试**  |

- **方案 A 的硬伤**：由于穿透抛出了 Python 异常，TaskIQ 会将其判定为 Task Failed。一旦队列配置了重试策略（如网络重试 3 次），TaskIQ 将机械化地重试此任务，对于 API Key 欠费或 DB 只读这类**确定性失败**，重试不仅毫无意义，还会导致重复触发报警、加剧下游风暴，甚至重复执行前序无害逻辑。
- **方案 B 的清晰边界**：图成功运行并补全了消息，Worker 消费完毕后主动更新业务数据库 `RunStatus.FAILED` 并释放资源，最后向 TaskIQ 返回成功（ACK）。TaskIQ 仅需专注于“进程是否存活、队列消息是否被妥善消费”，实现了基础设施调度与图执行状态解耦。

## 3. 流式协议（Redis Stream / SSE）的平稳退场

前端在接收复杂 Agent 执行流时，强依赖于流生命周期的完整性：



- 在方案 A 中，异常穿透至 Worker 最外层会直接中断消费协程。如果此时某些流式推送任务尚未 flush 或连接处理未完备，极易出现**连接被粗暴 reset**，导致前端接收不到最终的错误信令或 cancelled 帧，停留在半挂起状态。
- 方案 B 中，`astream` 完整遍历完毕，所有针对前序/排队任务生成的 `ToolMessage` 补丁事件顺畅落入 Redis Stream 并推送到前端，随后 Worker 发送显式的 `failed` 事件帧。整个流的关闭是协议化的优雅断开。

## 4. 架构防腐与代码洁癖

- **方案 A 污染图拓扑**：引入 `fatal_abort_node` 纯粹是为了充当“抛错起爆器”，在可视化图（LangSmith / LangGraph Studio）中会多出一个毫无业务语义的孤立节点。
- **方案 B Worker 适配成本极低**：方案 B 唯一的所谓“缺点”是需要在 Worker 的 `astream` 结束后追加约 10 行状态检查逻辑。但事实上，成熟的分布式 Worker 主流程本身就需要一个**终态检查器（State Classifier）**，用来区分当前执行是“正常完成”、“断点挂起（Interrupt）”还是“受控失败”。

## 方案 B 工业级落地实施建议

在落地方案 B 时，Worker 端的主执行循环建议按如下分层结构设计：



Python

```
async def run_agent_task(ctx: TaskIQContext, thread_id: str, run_id: str) -> None:
    try:
        # 1. 正常执行流消费
        async for mode, chunk in graph.astream(
            None,
            config=graph_config,
            stream_mode=["messages", "updates"]
        ):
            await handle_stream_chunk(run_id, mode, chunk)

        # 2. 状态机终态判定（State Classifier）
        final_state = await graph.aget_state(graph_config)
        
        # 检查是否包含受控的致命故障元数据
        if fatal_err := final_state.values.get("fatal_error"):
            await run_crud.set_run_failed(
                run_id=run_id,
                error_code=fatal_err.get("code", "FATAL_ERROR"),
                error_message=fatal_err.get("message")
            )
            await stream_publisher.publish_event(run_id, event="failed", data=fatal_err)
            return

        # 检查是否存在人工介入断点
        if final_state.next:
            await run_crud.set_run_interrupted(run_id=run_id)
            return

        # 正常业务成功
        await run_crud.set_run_success(run_id=run_id)

    except Exception as unhandled_exc:
        # 3. 仅兜底：进程级崩溃、Checkpointer 写入物理失败等不可抗力
        logger.exception("Worker 捕获到未受控物理异常: %s", unhandled_exc)
        await run_crud.set_run_failed(run_id=run_id, error_message=str(unhandled_exc))
        await stream_publisher.publish_event(run_id, event="failed", data={"raw_error": str(unhandled_exc)})
        raise unhandled_exc  # 抛出由 TaskIQ 决定死信或重试
    finally:
        await distributed_lock.release()
```

方案 B 使得 LangGraph 专注于状态推演，Worker 专注于生命周期管理，TaskIQ 专注于分布式调度，系统各层的职责边界与容灾语义保持了高度一致。

## GPT：

我更认同 **方案 B：终态收敛至 END**。

而且从你这个项目的目标——**LangGraph 负责工作流状态一致性，TaskIQ 负责异步执行，PostgreSQL/Redis 负责运行态收敛**——来看，B 的职责划分更干净。

我会把两者的核心差异概括成一句话：

> **A 是“为了让 Worker 感知失败，故意把 LangGraph 再炸一次”；B 是“LangGraph 已经知道这是一个受控终止，直接把状态机收敛到终态，再由 Worker 完成运行态收敛”。**

### 为什么我倾向 B

首先，你这里讨论的并不是普通的“程序 bug 未处理异常”，而是一个已经被系统识别出来的 **不可自愈、不可继续执行的受控故障**。

例如：

- 数据库进入只读；
- LLM API 明确返回额度耗尽；
- Docker/Milvus 服务不可用；
- 当前工具已经确定失败，后面的工具全部必须取消。

这种情况下，从状态机角度看：

```text
正常执行
   ↓
发现不可继续执行的致命故障
   ↓
生成 ToolMessage(error/cancelled)
   ↓
记录 fatal_error
   ↓
END
```

其实就是一个非常合理的 **Terminal State**。

LangGraph 当前官方文档本身也把“错误处理后更新 state 并继续/路由”视为正常的工作流机制；节点失败后可以由 error handler 更新状态并通过 `Command` 路由，而不是一定要让异常继续向外冒泡。([GitHub](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/fault-tolerance.mdx))

所以对你的场景而言，B 的语义更自然。

------

## A 最大的问题，其实不是多一个节点

表面上 A 只是：

```text
业务节点
  ↓
fatal_abort_node
  ↓
raise
  ↓
Worker except
```

但实际上你在这里人为制造了两个不同层面的语义：

```text
LangGraph 语义：
“我已经完成错误收拢，并且知道这个 Run 不应该继续了”

Python/TaskIQ 语义：
“这个 Run 又发生了一次未捕获异常”
```

也就是说，**你的状态机认为它已经处理完了，异常系统却认为它还没处理完。**

这会产生一个很重要的副作用：

### TaskIQ Retry 会被你的业务语义污染

你自己已经指出了这个问题。

TaskIQ 的 retry middleware 的基本语义就是：

> task execution 抛出异常 → task 被重新投递。([Taskiq](https://taskiq-python.github.io/available-components/middlewares.html?utm_source=chatgpt.com))

所以 A 中：

```python
raise FatalToolError(...)
```

对于 TaskIQ 来说，它根本不知道：

> “这是一个已经经过业务判定的不可恢复错误。”

它只看到：

```text
task exception
```

于是如果你的 Retry Policy 没有非常严格地排除这个异常，就可能：

```text
LangGraph 已经生成：
EditA -> error
EditB -> cancelled
Bash -> cancelled
ReadB -> cancelled

但 TaskIQ：
“exception？那我重跑一下。”
```

这对你的 Agent Runtime 来说实际上是非常危险的。

因为第二次执行并不等价于第一次执行的 continuation，而很可能变成：

```text
同一个 Run
↓
再次调度 graph
↓
重新读取 checkpoint
↓
重新处理已经失败的工具批次
```

因此 A 必须非常小心地设计：

```python
retry_on = lambda exc: ...
```

并且必须保证：

```text
FatalToolError
FatalModelError
InfrastructureFatalError
...
```

明确 **not retryable**。

这意味着你为了让 Worker 进入统一的 `except Exception`，反而把一个**已经被业务层判定为终止**的故障重新暴露给了基础设施 retry 层。

我认为这是 A 最大的问题。

------

# B 的语义实际上更加清晰

B 是：

```text
              ┌──────────────┐
              │  正常运行     │
              └──────┬───────┘
                     ↓
                fatal_error
                     ↓
          ToolMessage 补丁落盘
                     ↓
              fatal_error state
                     ↓
                    END
                     ↓
          astream 正常结束
                     ↓
           Worker 检查最终 state
                     ↓
             RunStatus.FAILED
```

这里有一个非常漂亮的职责分层：

### LangGraph

负责：

> **“这次 Agent Run 最终发生了什么？”**

所以：

```python
fatal_error = {
    "type": "database_read_only",
    "message": "...",
    "failed_tool_call_id": "...",
}
```

作为状态的一部分。

### Worker

负责：

> **“这个异步任务最终应该以什么运行态结束？”**

所以：

```python
state = await graph.aget_state(config)

if state.values.get("fatal_error"):
    await run_crud.set_run_failed(...)
    await publish_failed_event(...)
    return
```

这两者完全不冲突。

------

# 而且 B 更符合“状态机”和“Worker”的边界

你的系统现在实际上有三个状态：

```text
LangGraph State
        ↓
Agent Run State
        ↓
TaskIQ Task State
```

它们本来就不应该强行一一对应。

例如：

| 层级           | 状态        |
| -------------- | ----------- |
| LangGraph      | END         |
| PostgreSQL Run | FAILED      |
| TaskIQ         | SUCCESS/ACK |

这乍看很反直觉，但实际上是合理的。

因为：

> **“LangGraph 正常结束” ≠ “业务成功”**

这里的 `END` 只表示：

> **状态机已经完成自己定义的执行路径。**

而不是：

> “用户请求成功”。

你完全可以定义：

```text
LangGraph END
   ├── normal completion → RunStatus.COMPLETED
   └── fatal completion  → RunStatus.FAILED
```

这其实非常像业务工作流里的：

```text
Workflow completed
        ├── SUCCESS
        ├── FAILED
        ├── CANCELLED
        └── REJECTED
```

因此我认为：

**END 应该表示“工作流生命周期结束”，而不是“业务成功”。**

这个概念一旦明确，B 就变得非常顺畅。

------

# B 对 Checkpoint 也更加干净

这也是我比较看重的一点。

A：

```text
last checkpoint
     ↓
next = ("fatal_abort_node",)
```

于是 checkpoint 从图结构上看仍然像：

> “这里还有一个节点没完成。”

可实际上你已经决定：

> “这个 Run 永远不会再继续执行。”

这种状态有一点语义上的矛盾。

而 B：

```text
last checkpoint
     ↓
next = ()
```

表示：

> **这个状态机已经没有任何待执行节点。**

这和你的真实业务状态完全一致。

尤其考虑到你后面还有：

```python
active_interrupts
state.next
unfinished tools
RunStatus
```

这些恢复/运行态判断，**越接近真正的 terminal state，系统越容易维护。**

LangGraph 官方对持久化执行的描述也是以节点边界/checkpoint 为恢复单位；节点执行失败后的恢复/重试本质上围绕 checkpoint 展开。([Docs by LangChain](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph?utm_source=chatgpt.com))

------

# 我反而认为 A 最大的优势，并不足以抵消这个问题

A 唯一特别漂亮的地方就是：

```text
Worker 不需要知道 fatal_error
```

因为：

```python
except Exception:
    set_run_failed()
```

直接复用了。

从代码简洁性上：

```text
A：Worker 几乎零改动
B：Worker 增加十来行
```

但这其实是一个很典型的：

> **为了减少十几行业务判断，引入整个系统更复杂的异常语义。**

我不会为了这个换 A。

你的 Worker 多十几行：

```python
state = await graph.aget_state(config)

fatal_error = state.values.get("fatal_error")

if fatal_error:
    await set_run_failed(...)
    await publish_failed(...)
    return
```

成本非常低。

而 A 后续需要维护的是：

```text
fatal exception hierarchy
↓
TaskIQ retry policy
↓
LangGraph retry policy
↓
fatal_abort_node
↓
unknown exception vs controlled exception
↓
后续新增 fatal error 类型
```

整体复杂度反而更高。

------

# 还有一个非常重要的设计：不要把 `fatal_error` 设计成“异常信息”

我建议你把它定义成：

> **Run termination reason / execution outcome metadata**

例如：

```python
class FatalErrorInfo(TypedDict):
    code: str
    message: str
    node: str
    tool_call_id: str | None
    failed_task_index: int | None
```

然后：

```python
class AgentState(TypedDict):
    messages: list[Any]
    fatal_error: FatalErrorInfo | None
```

这样它不是：

```text
“我这里偷偷存了一个 Exception”
```

而是：

```text
“这次 Agent Run 的终止原因”
```

我认为这个语义会让你的架构稳定很多。

------

# 但是 B 有一个地方我建议你再调整

不要简单理解为：

```python
astream(...)
# 流结束

state = await graph.aget_state(config)

if fatal_error:
    ...
```

然后完全依赖这个分支。

更准确的设计应该是：

```text
Worker
 │
 ├── 正常 graph 执行
 │
 ├── astream 完成
 │
 └── 读取最终 checkpoint
       │
       ├── fatal_error → FAILED
       ├── interrupt   → REQUIRES_ACTION
       └── otherwise   → COMPLETED
```

也就是把 Worker 最后做成一个明确的：

```python
reconcile_run_outcome()
```

例如：

```python
final_state = await graph.aget_state(config)

if final_state.values.get("fatal_error"):
    await run_crud.set_run_failed(...)
    await publish_run_failed(...)
elif has_pending_interrupt(final_state):
    await run_crud.set_requires_action(...)
else:
    await run_crud.set_completed(...)
```

这样你的 Worker 就不会变成：

```text
“Graph 出异常怎么办？”
“Graph 正常怎么办？”
“Interrupt 怎么办？”
“Fatal 怎么办？”
“取消怎么办？”
```

而是统一成：

> **Graph 执行结束 → 根据最终 durable state 做一次 outcome reconciliation。**

我非常推荐这个模式。

------

# 另外一个关键点：A 更适合什么？

我并不是认为 `raise` 永远不该使用。

实际上我会把它留给：

```text
真正的系统级、不可预测、Worker 层需要兜底的异常
```

例如：

```text
OOM
进程崩溃
Python bug
checkpoint 存储异常
Redis 彻底异常
数据库连接池内部异常
未分类 RuntimeError
```

这些事情你的业务状态机根本来不及正常收敛。

那么：

```python
except Exception as exc:
    set_run_failed(exc)
    raise
```

完全合理。

所以最后可以形成非常清晰的“双通道”：

```text
                     Agent Run
                        │
          ┌─────────────┴─────────────┐
          │                           │
  可识别、可收敛的故障             未知/真正异常
          │                           │
   fatal_error state              Python raise
          │                           │
         END                     Worker except
          │                           │
   Worker reconciliation       Worker exception handler
          │                           │
       FAILED                       FAILED
```

**这比单纯选择 A 或 B 都更完整。**

------

# 最终我会这样定

### 业务层可判定的 Fatal Error

采用 **方案 B**：

```text
捕获
→ ToolMessage error/cancelled
→ fatal_error
→ checkpoint
→ END
→ Worker reconciliation
→ RunStatus.FAILED
```

并且：

```text
TaskIQ = ACK
```

**不触发队列级 retry。**

### 真正未捕获的系统异常

采用现有 Worker：

```text
raise
→ Worker except Exception
→ RunStatus.FAILED
→ Redis failed
→ lock release
→ TaskIQ retry（仅针对明确允许 retry 的异常）
```

也就是说，**TaskIQ retry 应该由“异常类型是否可重试”决定，而不是由“这个 Run 最终是否 FAILED”决定。**

这条边界非常重要。

------

## 所以在你这两个方案之间

我的选择是：

> **B > A。**

不是因为 B 少一个节点，而是因为它在语义上把三件事情彻底分开了：

```text
LangGraph：
工作流有没有继续执行的必要？

Agent Run：
这次运行最终成功还是失败？

TaskIQ：
这个异步任务是否需要重新投递？
```

这三个问题本来就不是同一个问题。

而你的方案 B 恰好允许它们拥有**独立的状态语义**。

另外，我查了当前 LangGraph 文档，1.2+ 已经正式支持节点级 retry / timeout / error handler，并明确把“错误处理后更新 state + 路由”作为正常机制；这和你把受控 Fatal Error 收敛进图内状态的思路是吻合的。([GitHub](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/fault-tolerance.mdx))

有一点值得特别注意：当前 LangGraph 在错误处理、并行 superstep、checkpoint durable ordering 等方向仍有一些 2026 年正在讨论/修复的边界问题，所以你最终落地前，最好针对你自己的 `astream + checkpointer + TaskIQ worker crash` 场景做故障注入测试，而不要仅凭框架抽象推断。([GitHub](https://github.com/langchain-ai/langgraph/issues/8277?utm_source=chatgpt.com))

如果按你现在这个 Agent Runtime 项目继续往生产级方向收敛，我会进一步把 **`COMPLETED / FAILED / CANCELLED / REQUIRES_ACTION` 的 Run Outcome State Machine** 单独抽出来，这会比现在单纯增加 `fatal_error` 更完整。

## DeepSeek:

如果必须二选一，我更认同 **方案 B：终态收敛（路由至 END + Worker 检查 State 元数据）**。  
更准确地说：**以 B 为主，吸收 A 的“外层异常兜底”作为最后防线**，但不建议把“不可自愈致命故障”设计成图内主动抛异常来驱动 Worker 收敛。

核心理由如下。

### 1. 语义更正确：受控致命故障应是“业务终态”，不是“未捕获异常”
API Key 欠费、DB 只读、硬拦截这类问题，已经被业务节点识别并处理了：生成 error/cancelled ToolMessage、排空剩余任务、写入 `fatal_error`。  
这说明它已经不是“未知系统崩溃”，而是**受控失败**。

方案 B 把它作为图的一个合法终态，路由到 END，语义是：

> 图已完整执行完，业务结果是失败。

方案 A 则把它伪装成 Python 未捕获异常，让 Worker 外层 `except` 接管。这会把“业务致命失败”和“真正未捕获的系统崩溃”混在一起，语义上不够干净。

### 2. Checkpoint 更干净，恢复/清理更简单
方案 A 最后会留下：

```python
state.next == ("fatal_abort_node",)
```

这意味着图没有真正结束，Checkpoint 里还有未完成游标。后续如果做恢复、重放、清理、人工修复，都要额外处理这个特殊节点，否则可能再次执行 `fatal_abort_node` 并重复抛错。

方案 B 到达 END 后：

```python
state.next == ()
```

图生命周期完整结束，补丁消息也已持久化。对 Checkpointer 来说，这是一个干净终态，后续不会误恢复执行。

### 3. 不可自愈故障不应该触发 TaskIQ 队列级重试
方案 A 的明显风险是：TaskIQ 会把它识别为 Execution Error，如果配置了 Retry Policy，就可能自动重试。  
但 API Key 欠费、数据库只读、硬拦截这类故障，重试通常没有意义，反而可能导致：

- 重复发送失败事件；
- 重复释放锁或锁状态混乱；
- 重复写 DB 状态；
- 重复向前端推流；
- 如果同 thread 恢复，还可能反复进入 `fatal_abort_node`。

方案 B 将其识别为 ACK，不触发队列级重试。这里的 ACK 只代表“队列消息已处理完”，不代表业务成功。业务失败由：

```python
RunStatus.FAILED
```

和 Redis `failed` 事件表达。这是更合理的分层：  
**队列负责投递，业务状态负责成败。**

如果某类故障确实可重试，应该显式标记 `retryable=True`，再由 Worker 主动抛可重试异常或返回 TaskIQ 重试信号，而不是让所有致命异常都走 Retry Policy。

### 4. Worker 多 10 行检查，换来的是确定性和可观测性
方案 A 看起来“Worker 无改动”，但这是表面优势。它把复杂度转移到了：

- 新增 `fatal_abort_node`；
- 所有条件边都要重定向；
- 异常作为控制流；
- Checkpoint 留下未完成游标；
- Worker 外层 `except` 需要区分受控致命异常和真正崩溃。

方案 B 在 Worker 流结束后增加一个显式分支：

```python
final_state = await graph.aget_state(config)
fatal = final_state.values.get("fatal_error")
if fatal:
    await mark_unfinished_tools(...)
    await run_crud.set_run_failed(...)
    await redis.xadd(..., "failed", ...)
    return
```

代码多十几行，但控制流是显式的、可测试的、可观测的。  
对于分布式 Worker 架构，这种显式收敛比依赖异常穿透更可靠。

### 5. 流式输出更可控
方案 A 中 `fatal_abort_node` 抛异常会导致 `astream` 迭代中断。虽然之前的补丁消息可能已经通过 updates/messages 流出，但后续 failed 事件、DB 标记、锁释放都依赖异常路径。

方案 B 中图正常到 END，`astream` 自然结束。Worker 可以在流结束后稳定地：

1. 读取最终 State；
2. 检查 `fatal_error`；
3. 标记 RunStatus.FAILED；
4. 发送 Redis failed 事件；
5. 释放分布式锁；
6. 正常 return，让 TaskIQ ACK。

前端收到的是：先收到 ToolMessage error/cancelled，再收到 failed 事件。顺序清晰，流不会因为异常而断裂。

### 6. 进程崩溃防御两者一样，不构成 A 的优势
OOM、掉电、Worker 被杀，方案 A 和 B 都无法在进程内完成收敛，最终都要依赖下次启动的 L1 补丁修复。  
所以“异常传播”并不能更好地防御物理崩溃。

---

## 推荐落地形态：B 为主，A 作兜底

我会这样设计：

- 业务节点捕获致命异常后：
  - 生成 error/cancelled ToolMessage；
  - 排空剩余 tool calls；
  - 写入结构化 `fatal_error`；
  - `return` 补丁，不 `raise`。
- 条件边统一判断：
  ```python
  if state.get("fatal_error"):
      return END
  ```
- Worker 在 `astream` 结束后、判断 interrupts 前：
  - 读取 `graph.aget_state(config)`；
  - 若 `fatal_error` 存在，调用统一 `finalize_failed`；
  - 发送 Redis `failed`；
  - 释放锁；
  - 正常 return，让 TaskIQ ACK。
- 外层 `except Exception` 仍然保留，但只用于真正未捕获的系统崩溃。
- `finalize_failed` 必须幂等，锁释放放 `finally`。
- 如果 `fatal_error.retryable == True` 且业务希望重试，再显式抛可重试异常，而不是依赖所有异常都重试。

一句话总结：

> **方案 B 更符合“受控致命故障 = 业务终态”的语义，Checkpoint 干净，TaskIQ 不误重试，Worker 收尾显式可控。方案 A 适合异常统一兜底和队列重试场景，但对不可自愈致命故障，容易把控制流、重试和状态恢复复杂化。**