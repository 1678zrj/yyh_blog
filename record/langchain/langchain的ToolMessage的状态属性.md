### 问：langchain的ToolMessage的status属性有什么讲究吗

`ToolMessage` 的 `status` 字段主要用于**向大模型、Agent 运行时以及监控链路明确指示工具执行的成败**。



它的类型为 `Literal["success", "error"]`，默认值为 `"success"`。具体讲究体现在以下四个核心维度：



### 1. 模型提供商原生协议的映射（如 Anthropic `is_error`）

不同 LLM 厂商对工具调用的报错处理机制不同：



- **Anthropic Claude**：其 API 原生支持在工具响应块（`tool_result`）中传入 `is_error: True`。当 `ToolMessage(status="error")` 转换为 Claude 的 payload 时，会自动开启该标识，告知模型“工具调用崩溃了”。
- **OpenAI**：虽然其官方 `tool` 消息体没有独立的 error 字段，但框架和上层 Prompt 可以借由 status 对内容做针对性注入。

### 2. 避免大模型的语义幻觉

如果不标记 `status="error"`，工具抛出错误并捕获后返回的字符串（如 `"FileNotFoundError: config.json not found"`）会被模型误认为是**正常的工具返回值**。



- **无标记（默认 success）**：模型可能会认为 `config.json` 的文本内容就是 `"FileNotFoundError..."`，从而产生逻辑荒诞的后续回答。
- **标记为 error**：模型会明确意识到本次 Action 失败，进而触发自我纠错机制（如换用备选参数重试、调用其他工具或向用户解释失败原因）。

### 3. LangGraph / Agent 流程控制与容错

在构建工作流（尤其是使用 `ToolNode` 或 `create_react_agent`）时：



- **自动异常捕获**：当工具配置了 `handle_tool_errors=True` 时，底层捕获到 Python 异常后，会自动生成一个 `status="error"` 的 `ToolMessage`，而不是让整个服务直接 crash。

- **条件边路由（Conditional Routing）**：可以在 LangGraph 的条件路由函数中直接依据最后一条消息的 status 进行分流：

  Python

  ```
  def route_after_tools(state):
      last_message = state["messages"][-1]
      if isinstance(last_message, ToolMessage) and last_message.status == "error":
          return "error_recovery_node"  # 走人工干预或降级逻辑
      return "agent"
  ```

### 4. LangSmith 可观测性与指标统计

在 LangSmith、Datadog 等追踪系统中：



- 如果工具报错被 `try...except` 吞掉并返回普通文本，链路监控会将其判定为成功的 span。
- 显式指定 `status="error"` 能让追踪系统直接标记该 tool run 为 **Failed**，从而精准统计 Agent 的工具失败率、重试次数和耗时分布。

**代码示例**



Python

```
from langchain_core.messages import ToolMessage

# 1. 正常执行结果
success_msg = ToolMessage(
    content='{"temperature": 22}',
    tool_call_id="call_123",
    status="success"  # 可省略，默认为 success
)

# 2. 捕获异常后返回给模型的错误消息
error_msg = ToolMessage(
    content="Database connection timeout after 3 retries.",
    tool_call_id="call_124",
    status="error"   # 明确提示模型工具失败
)
```

在编写自定义工具拦截器、自定义 ReAct 循环或在 LangGraph 中做状态恢复时，凡是捕获了异常且希望让模型感知并重试的场景，务必显式传入 `status="error"`。

### 问：这样吗？看来还是要人为在图的工具调用代码中捕获异常和显式设置了，因为我是自定义的tool_node节点

是的，如果使用自定义的 `tool_node` 而非 LangGraph 预制的 `ToolNode`，异常捕获与 `status="error"` 的组装必须由代码显式接管。



如果自定义节点不主动处理，遇到异常时只有两种糟糕的结果：要么 Python 进程直接 crash 导致整个图中断，要么粗暴 `except` 后返回普通文本，导致大模型把异常堆栈误当成执行数据。



### 标准自定义 `tool_node` 实现模板

在遍历 `tool_calls` 执行工具时，建议采用如下容错结构：



Python

```
import json
from langchain_core.messages import ToolMessage
from typing import Dict, Any, List

def custom_tool_node(state: Dict[str, Any]) -> Dict[str, List[ToolMessage]]:
    """自定义工具执行节点，包含健全的异常拦截与 status 标记"""
    last_message = state["messages"][-1]
    tool_messages = []

    # 1. 构建工具名称与可调用对象的映射表
    tools_by_name = {t.name: t for t in my_registered_tools}

    for tc in last_message.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        call_id = tc["id"]

        # 情况 A: 模型调用了不存在的工具
        if tool_name not in tools_by_name:
            tool_messages.append(
                ToolMessage(
                    content=f"Error: Tool '{tool_name}' does not exist.",
                    tool_call_id=call_id,
                    status="error"
                )
            )
            continue

        tool = tools_by_name[tool_name]

        # 情况 B: 执行工具并捕获运行时异常
        try:
            # 兼容同步/异步执行
            result = tool.invoke(tool_args)
            
            tool_messages.append(
                ToolMessage(
                    content=str(result),
                    tool_call_id=call_id,
                    status="success"
                )
            )
        except Exception as e:
            # 捕获工具内部的业务错误、网络超时、参数类型解析失败等
            error_feedback = (
                f"Tool '{tool_name}' failed with {e.__class__.__name__}: {str(e)}.\n"
                "Please adjust your parameters or try an alternative approach."
            )
            tool_messages.append(
                ToolMessage(
                    content=error_feedback,
                    tool_call_id=call_id,
                    status="error"  # 核心：必须显式设为 error
                )
            )

    return {"messages": tool_messages}
```

### 自定义节点的三点工程细节

- **避免将完整 Traceback 原样塞入 `content`**：冗长的 Python 报错堆栈会白白消耗大量 Context Window，甚至干扰模型注意力。建议提取 `e.__class__.__name__` 和简短的 `str(e)`，并补上一句引导语（如“请检查参数后重试”）。
- **保证 `tool_call_id` 严格配对**：无论成功还是抛异常，模型发出了几个 `tool_calls`，返回的 `ToolMessage` 就必须有几个，且 `tool_call_id` 一一对应。如果漏掉某个报错的 tool call，OpenAI 等严格校验的 API 会直接报协议错误（`400 Bad Request`）。
- **装饰器层面的备用方案**：如果部分工具是通用的，也可以直接在 `@tool` 装饰器上声明 `handle_tool_error=True`。但由于参数反序列化阶段发生的错误发生在工具函数执行之前，在自定义节点外层加上 `try...except` 才是最稳妥的兜底手段。

### 问：不过兼容同步/异步执行是不是做的不太好？如果工具函数是异步的怎么跑，而同步的话岂不是要放到线程池中执行？

你的直觉非常敏锐。如果应用运行在异步事件循环中（例如使用 FastAPI 或 LangGraph 的 `ainvoke`/`astream`），之前的同步遍历 `tool.invoke()` 会带来两个严重隐患：



1. **对于原生异步工具（`async def`）**：直接调用同步的 `tool.invoke()` 会尝试在当前线程借由临时事件循环驱动协程；如果外层已有正在运行的 Event Loop，会直接抛出 `RuntimeError: This event loop is already running`。
2. **对于同步耗时工具（`def`）**：在主事件循环中直接调用会阻塞整个 Loop，导致其他并发请求卡死。

在生产级自定义 `tool_node` 中，推荐通过**异步节点 + 并发调度**来彻底解决这个问题。



### 1. 核心机制：LangChain 的 `ainvoke` 底层行为

如果你的工具是通过 `@tool` 装饰器或继承自 `BaseTool` 构建的，它天生就是一个 `Runnable`。你不需要手写复杂的线程池逻辑，因为 **`tool.ainvoke()` 本身就已经做了同步/异步抹平**：



- **如果工具是 `async def`**：`tool.ainvoke()` 直接作为原生协程被 `await` 执行。
- **如果工具是普通 `def`**：LangChain 底层会自动调用 `asyncio.to_thread` / `run_in_executor` 将其放入默认线程池执行，不会阻塞主事件循环。

### 2. 生产级实现：并发与独立异常隔离

当 LLM 一次性发出多个 `tool_calls` 时，如果用 `for` 循环依次 `await`，总延迟会线性叠加。正确的做法是将单个工具的“执行 + 捕获 + 组装 ToolMessage”封装为一个子任务，使用 `asyncio.gather` 并发执行。



Python

```
import asyncio
from typing import Dict, Any, List
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

async def _execute_single_tool(tc: dict, tools_by_name: dict[str, BaseTool]) -> ToolMessage:
    """单个工具调用的隔离执行单元"""
    tool_name = tc["name"]
    tool_args = tc.get("args", {})
    call_id = tc["id"]

    # 1. 拦截未注册的工具
    if tool_name not in tools_by_name:
        return ToolMessage(
            content=f"Error: Tool '{tool_name}' is not registered.",
            tool_call_id=call_id,
            status="error"
        )

    tool = tools_by_name[tool_name]

    # 2. 执行与异常捕获
    try:
        # ainvoke 内部自动分流：
        # - 异步工具直接 await
        # - 同步工具自动 dispatch 到工作线程池执行
        res = await tool.ainvoke(tool_args)
        
        return ToolMessage(
            content=str(res),
            tool_call_id=call_id,
            status="success"
        )
    except Exception as e:
        return ToolMessage(
            content=f"Tool execution failed: {type(e).__name__} - {str(e)}",
            tool_call_id=call_id,
            status="error"
        )

async def custom_tool_node(state: Dict[str, Any]) -> Dict[str, List[ToolMessage]]:
    """异步并发 tool_node"""
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", [])
    
    if not tool_calls:
        return {"messages": []}

    tools_by_name = {t.name: t for t in my_registered_tools}

    # 并发调度所有 tool_calls，单工具崩溃不影响整体
    tasks = [_execute_single_tool(tc, tools_by_name) for tc in tool_calls]
    tool_messages = await asyncio.gather(*tasks)

    return {"messages": list(tool_messages)}
```

### 3. 如果工具是原生 Python 函数（非 `BaseTool`）

若使用的不是 LangChain 的 `@tool` 对象，而是普通的 Python 原生函数，可以借助 `inspect` 库手动调度：



Python

```
import inspect
import asyncio

async def run_raw_callable(func, args):
    if inspect.iscoroutinefunction(func):
        # 协程函数直接异步执行
        return await func(**args)
    else:
        # 同步函数扔进事件循环的线程池中执行
        return await asyncio.to_thread(func, **args)
```

### 4. 关键设计细节

- **异常就地捕获（Fail Isolation）**：`try...except` 必须放在 `_execute_single_tool` 内部，不能直接在 `asyncio.gather` 外层包。否则只要一个工具抛出异常，整个 `gather` 就会中断，导致其他已经执行完的工具消息丢失，破坏 `tool_call_id` 的配对完整性。
- **并发限流（可选）**：如果某些工具会调用带 Rate Limit 的外部 API（如搜索引擎或数据库写入），可在自定义节点内引入 `asyncio.Semaphore(max_concurrent_tasks)` 防止瞬时并发过高。

### 问：我想了解一下关于这个线程池，是不是和数据库连接池类似，一旦所有的线程都在使用了，那么新的任务就要一直等待有空闲的线程才能开始跑？

**是的，核心运行逻辑完全一致。** 无论是线程池还是数据库连接池，本质上都是典型的**资源池化模式（Resource Pooling）**。



当池中的所有工作线程都在忙碌时，新提交的任务不会被直接丢弃，而是进入一个等待队列（Task Queue）排队，必须等待某个正在运行的线程执行完毕释放出来，排在队首的任务才能被唤醒执行。



### 1. Python `asyncio` 默认线程池的工作细节

在使用 `asyncio.to_thread` 或 LangChain 的 `tool.ainvoke()` 执行同步工具时，底层使用的是 Python 事件循环自带的默认 `ThreadPoolExecutor`：



- **默认容量（Worker 数量）**：Python 3.8+ 默认最大线程数通常为 `min(32, (os.cpu_count() or 1) + 4)`。在一台 8 核机器上，池子默认最多只有 12 个线程。
- **默认队列是无界队列（Unbounded Queue）**：只要线程用满，任务就会无限往队列里堆积。虽然不会立即报错崩溃，但会导致后续任务的**排队等待时间急剧上升（延迟暴增）**。

### 2. 线程池与数据库连接池的异同

| **维度**         | **数据库连接池（如 HikariCP / SQLAlchemy）**                 | **线程池（如 Python ThreadPoolExecutor）**                   |
| ---------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **池化对象**     | 网络 Socket 连接（对 DB Server 的会话）                      | 操作系统的内核线程 / 工作线程                                |
| **打满后的表现** | 通常有严格的 **获取超时（Pool Timeout）**，排队超时立即抛出 `TimeoutError` 快速失败 | 默认**无上限排队**，任务一直挂在内存队列中等待，直到有空闲线程（除非外部加上超时控制） |
| **瓶颈所在**     | 数据库服务器的并发连接数与承载能力                           | 应用所在机器的 CPU、内存开销与上下文切换成本                 |
| **释放机制**     | 查询完毕后必须主动调用 `.close()` 归还连接                   | 目标函数 `return` 或抛出异常后，线程自动拾取下一任务         |

### 3. 在 Agent / 工具调用中的潜在隐患与对策

在 LangChain 或 LangGraph 的自定义工具节点中，依赖线程池最容易踩中两个坑：



**问题 A：低效长耗时同步工具“占着茅坑不拉屎”**

如果某个同步工具内部写了耗时 30 秒的 `requests.get()` 或阻塞型第三方 SDK，当 LLM 一次性并发调用 20 个这样的工具时，默认的十几个线程瞬间被全部占满。此时**哪怕其他简单的同步工具只需执行 1 毫秒，也必须在队列里苦等 30 秒**。



**问题 B：排队过久导致上游超时**

如果队列越堆越长，整个 Agent 请求可能会触发 Web 网关（如 Nginx、FastAPI）的超时中断，导致计算资源白白浪费。



### 4. 优化实践方案

**首选：IO 密集型任务改写为原生 `async def`**

原生异步工具执行时只注册事件回调，不消耗任何操作系统线程。无论并发几十还是几千个 HTTP 请求，单线程事件循环都能轻松支撑，完全绕过线程池瓶颈。



**备选：为繁重的同步任务配置专用、独立配置的线程池**

不要让重型任务占用全局默认的线程池。可以在图服务启动时初始化一个容量更大的专用执行器：

Python

```
import asyncio
from concurrent.futures import ThreadPoolExecutor

# 创建一个专用于工具调用的线程池，扩大最大工作线程数
custom_tool_executor = ThreadPoolExecutor(max_workers=50)

# 在需要执行同步函数时指定专用池
loop = asyncio.get_running_loop()
result = await loop.run_in_executor(custom_tool_executor, sync_heavy_tool, *args)
```

### 问：还有，LangChain 的 @tool 对象不能借助inspect来检测函数类型吗

**不能直接检测。** 如果直接把 `@tool` 对象传给 `inspect.iscoroutinefunction(tool)`，无论原始函数是同步还是异步，它**一律会返回 `False`**。



### 为什么直接 `inspect` 会失效？

被 `@tool` 装饰后，返回的已经不是原生的 Python 函数，而是一个继承自 `BaseTool`（基于 Pydantic 的 `BaseModel`）的 **`StructuredTool` 类实例**。



Python

```
from langchain_core.tools import tool
import inspect

@tool
async def my_async_tool(x: int) -> int:
    """异步工具"""
    return x * 2

print(type(my_async_tool)) 
# 输出: <class 'langchain_core.tools.structured.StructuredTool'>

print(inspect.iscoroutinefunction(my_async_tool)) 
# 输出: False ❌（误判为非异步函数！）
```

`inspect.iscoroutinefunction` 的底层机制是检查对象的 `__code__.co_flags` 是否包含协程标志位 `CO_COROUTINE`。对于一个普通的类实例，它找不到该标志，自然直接判定为 `False`。



### 正确的检测方式

如果你在自定义执行逻辑中**确实需要**判断该工具到底是不是异步实现的，有以下两种方式：



#### 方式 1：检查内置的 `coroutine` 属性（推荐，最优雅）

LangChain 在创建 `StructuredTool` 时，会把底层真正的函数拆解保存：



- 如果被装饰的是 `async def`，该函数会被存入 `tool.coroutine`，而 `tool.func` 通常为 `None`。
- 如果被装饰的是普通的 `def`，该函数会被存入 `tool.func`，而 `tool.coroutine` 为 `None`。

因此只需判断属性是否存在：



Python

```
is_async = my_async_tool.coroutine is not None
```

#### 方式 2：使用 `inspect` 检测内部属性

如果一定要用 `inspect`，必须针对 `tool.coroutine` 或 `tool.func` 进行检测，而不是工具实例本身：



Python

```
if my_async_tool.coroutine and inspect.iscoroutinefunction(my_async_tool.coroutine):
    print("这是一个真正的异步工具")
```

### 为什么通常不需要手动检测？

LangChain 内部的 `tool.ainvoke()` 已经封装了这套判断逻辑。其底层的调用分支类似于：



Python

```
# LangChain BaseTool 底层核心伪代码
async def _arun(self, *args, **kwargs):
    if self.coroutine:
        # 如果开发者提供了 async 实现，直接 await 原始协程
        return await self.coroutine(*args, **kwargs)
    elif self.func:
        # 如果只有同步实现，自动扔进线程池避免阻塞事件循环
        return await asyncio.to_thread(self.func, *args, **kwargs)
    else:
        raise NotImplementedError("Tool does not support execution.")
```

只要你的工具对象是由 `@tool` 或 `BaseTool` 派生出来的，直接无脑调用 `await tool.ainvoke(args)` 即可，它会自动以最优路径执行，省去了手动判断和调度的代码。