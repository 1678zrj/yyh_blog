```python
from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from app.services.rag_search_service import RAGSearchService
from app.core.rag_deps import get_rag_container_sync
from langgraph.types import interrupt



rag_container = get_rag_container_sync()
rag_search_service = RAGSearchService(rag_container)




class RunRAGInput(BaseModel):
    query: str = Field(description="需要检索的问题")


@tool(name_or_callable="rag", args_schema=RunRAGInput)
async def search(query: str, config: RunnableConfig):
    """
    检索知识库中的相关参考文档
    """
    configurable = config.get("configurable")
    scope = configurable.get("scope")
    scope_id = configurable.get("scope_id")
    result = await rag_search_service.search(query, scope, scope_id)
    return result


class QuestionOption(BaseModel):
    label: str = Field(description="给用户的问题可选项的标签")
    description: str = Field(description="给用户的问题可选项的正文")


class Question(BaseModel):
    id: str = Field(description="问题的唯一简短标识符，英文蛇行命名，用户回复时使用该id标明对应问题答案")
    header: str = Field(description="问题的标题")
    question: str = Field(description="需要向用户询问的核心问题正文")
    options: list[QuestionOption] = Field(
        default_factory=list,
        description="可以选择给用户提供的问题选项，如果需要用户自由文本输入，可留空"
    )


class AskUserQuestionInput(BaseModel):
    questions: list[Question] = Field(
        description="需要相关用户批量提问的问题列表，单次提问不超过3个"
    )

"""
该工具的用户回复应该按照此格式：
[
  {
    "id": "upload_mode",
    "selected": [
      "后台任务中心 + 悬浮进度"
    ]
  },
  {
    "id": "multi_select",
    "selected": [
      "支持多文件（推荐）"
    ]
  }
]
注：这是对前端的要求
"""

@tool(name_or_callable="ask_user_question", args_schema=AskUserQuestionInput)
async def ask_user_question(questions: list[Question]):
    """
    当用户意图模糊、缺少执行下游任务的必要业务参数，或者需要用户从多个备选项中做出决策时，
    必须调用此工具暂停并向用户索取必要信息。
    严禁在缺少关键参数时自行捏造或幻觉默认值。
    """
    response = interrupt(
        {
            "action_type": "clarify",
            "payload": [question.model_dump() for question in questions]
        }
    )
    return response

```

### 问：这样定义一个工具怎么样？

这种设计**非常优雅且符合现代 LangGraph 的最佳实践**。



利用 LangGraph v0.2+ 的 `interrupt()` 在 Tool 内部触发中断实现人机协同（Human-in-the-Loop, HITL），再配合 Pydantic 结构化多选/表单描述，能够直接让前端渲染富交互卡片（如多选框、输入框），远比纯文本追问更精准、更易收敛。



以下是该设计的亮点与建议优化的关键细节：



### 设计亮点

- **Prompt 约束精准：** `ask_user_question` 的 docstring 明确指出了“严禁捏造默认值”和“何时触发”，对抑制 LLM 幻觉有立竿见影的效果。
- **结构化传参利于前端解耦：** `Question` 拆分了 `id`、`header`、`options`，前端不需要写正则解析大模型的追问，能直接渲染结构化表单。
- **无缝桥接恢复状态：** 外部通过 `Command(resume=...)` 恢复时，传入的值会直接作为 `interrupt()` 的返回值，作为 `ToolMessage` 回传给大模型继续规划。

### 关键优化建议

**1. 在 `Question` 中显式增加交互类型（`type`）**

目前仅靠 `options` 是否为空来区分“自由文本”与“选项”，前端无法区分单选（Radio）与多选（Checkbox）。



Python

```
from typing import Literal

class Question(BaseModel):
    id: str = Field(description="问题的唯一简短标识符，英文蛇形命名")
    header: str = Field(description="问题简要标题")
    question: str = Field(description="向用户询问的核心问题正文")
    type: Literal["single_select", "multi_select", "text"] = Field(
        default="single_select",
        description="交互类型：single_select(单选), multi_select(多选), text(自由文本输入)"
    )
    options: list[QuestionOption] = Field(
        default_factory=list,
        description="备选列表，当 type 为 text 时可留空"
    )
```

**2. 使用 Pydantic 硬约束列表长度**

Prompt 中提到了“单次提问不超过3个”，但 LLM 可能会偶发生成 4-5 个。建议用 `max_length=3` 在 Schema 层做刚性校验：



Python

```
class AskUserQuestionInput(BaseModel):
    questions: list[Question] = Field(
        max_length=3,
        description="需要向用户提问的问题列表，单次最多 3 个"
    )
```

**3. 对 `interrupt` 返回值做容错与格式化**

用户通过前端恢复时，传入的数据可能不符合预期的 JSON/List 格式。如果直接 `return response`，大模型接收到的可能是混杂的原始数据。建议在工具返回前稍作格式化或转成自然文本，降低下一轮 LLM 理解成本：



Python

```
@tool(args_schema=AskUserQuestionInput)
async def ask_user_question(questions: list[Question]):
    """..."""
    response = interrupt(
        {
            "action_type": "clarify",
            "payload": [question.model_dump() for question in questions]
        }
    )
    
    # 若前端传回结构化数据，转为自然语言反馈给 LLM，阅读体验更好：
    if isinstance(response, list):
        formatted = "\n".join(
            [f"- 问题 {item.get('id')}: 用户选择了 {', '.join(item.get('selected', []))}" for item in response]
        )
        return f"用户已回复补充信息：\n{formatted}"
    
    return str(response)
```

**4. `search` 工具中 `configurable` 的空指针保护**

在 `search` 工具中，如果当前调用没有传入配置项，`config.get("configurable")` 可能返回 `None`，导致 `.get()` 报错：

Python

```
# 推荐写法
configurable = config.get("configurable") or {}
scope = configurable.get("scope")
scope_id = configurable.get("scope_id")
```

### 优化后的整体代码

Python

```python
from typing import Literal
from langchain.tools import tool
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field
from langgraph.types import interrupt
from app.services.rag_search_service import RAGSearchService
from app.core.rag_deps import get_rag_container_sync

rag_container = get_rag_container_sync()
rag_search_service = RAGSearchService(rag_container)


class RunRAGInput(BaseModel):
    query: str = Field(description="需要检索的问题")


@tool("rag", args_schema=RunRAGInput)
async def search(query: str, config: RunnableConfig):
    """检索知识库中的相关参考文档"""
    configurable = config.get("configurable") or {}
    scope = configurable.get("scope")
    scope_id = configurable.get("scope_id")
    return await rag_search_service.search(query, scope, scope_id)


class QuestionOption(BaseModel):
    label: str = Field(description="给用户的问题可选项的标签")
    description: str = Field(default="", description="给用户的问题可选项的详细描述")


class Question(BaseModel):
    id: str = Field(description="问题的唯一简短标识符，英文蛇形命名")
    header: str = Field(description="问题简短标题")
    question: str = Field(description="向用户询问的核心正文")
    type: Literal["single_select", "multi_select", "text"] = Field(
        default="single_select",
        description="交互类型：single_select(单选), multi_select(多选), text(自由文本)"
    )
    options: list[QuestionOption] = Field(
        default_factory=list,
        description="选择题的选项列表，文本输入时留空"
    )


class AskUserQuestionInput(BaseModel):
    questions: list[Question] = Field(
        max_length=3,
        description="向用户澄清的问题列表，单次提问不超过3个"
    )


@tool("ask_user_question", args_schema=AskUserQuestionInput)
async def ask_user_question(questions: list[Question]):
    """
    当用户意图模糊、缺少执行下游任务的必要业务参数，或者需要用户从多个备选项中做出决策时，
    必须调用此工具暂停并向用户索取必要信息。
    严禁在缺少关键参数时自行捏造或幻觉默认值。
    """
    user_response = interrupt(
        {
            "action_type": "clarify",
            "payload": [q.model_dump() for q in questions]
        }
    )

    if isinstance(user_response, list):
        summary = "\n".join(
            f"- 问题 ID [{item.get('id')}]: 用户的选择/回答为 {item.get('selected')}"
            for item in user_response
        )
        return f"用户已确认补充信息：\n{summary}"

    return str(user_response)
```

### 问：展示在 LangGraph 中如何捕获 ask_user_question 触发的 interrupt，并通过 Command(resume=...) 传递前端参数恢复执行的代码示例。

在 LangGraph 中，配合 `interrupt()` 使用 `Command(resume=...)` 必须满足两个前提：**配置 Checkpointer（保存运行时快照）** 以及在每次调用时传入唯一的 **`thread_id`**。



以下是完整的异步执行与中断恢复流程示例：



Python

```
import asyncio
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command

# 导入上一节中定义的工具
from your_module import ask_user_question, search


async def main():
    # 1. 初始化模型与工具
    model = ChatOpenAI(model="gpt-4o", temperature=0)
    tools = [search, ask_user_question]

    # 2. 创建 Checkpointer 并编译 Agent（interrupt 必须依赖 checkpointer）
    checkpointer = MemorySaver()
    app = create_react_agent(model, tools, checkpointer=checkpointer)

    # 3. 指定会话 thread_id，用于串联前后两次请求
    config = {"configurable": {"thread_id": "session_user_1001"}}

    # ==========================================
    # 阶段一：用户提问，触发 interrupt 中断
    # ==========================================
    print("--- 1. 发起初始提问 ---")
    initial_input = {
        "messages": [
            HumanMessage(
                content="我想上传一批资料到知识库，帮我处理一下。"
            )
        ]
    }

    # 执行图流式输出或单次调用，图在进入 ask_user_question 后会暂停
    await app.ainvoke(initial_input, config=config)

    # 获取当前图的状态，检查是否被 interrupt 中断
    state = await app.aget_state(config)

    if not state.tasks or not state.tasks[0].interrupts:
        print("未触发中断，流程直接结束")
        return

    # 提取 interrupt() 中传入的字典数据（即抛给前端的渲染表单）
    interrupt_payload = state.tasks[0].interrupts[0].value
    print("\n--- 2. 捕获到 Interrupt 事件 ---")
    print(f"Action Type: {interrupt_payload.get('action_type')}")
    print("下发给前端的表单数据：")
    for q in interrupt_payload.get("payload", []):
        print(f"  - [{q['id']}] {q['question']} (类型: {q['type']})")

    # ==========================================
    # 阶段二：模拟前端提交表单，通过 Command 恢复
    # ==========================================
    # 模拟前端收集到用户选择后的提交格式
    frontend_submission = [
        {"id": "upload_mode", "selected": ["后台任务中心 + 悬浮进度"]},
        {"id": "multi_select", "selected": ["支持多文件（推荐）"]},
    ]

    print("\n--- 3. 模拟前端回复并恢复图执行 ---")
    # 通过 Command(resume=...) 传入用户数据
    # 这个值会直接作为 ask_user_question 内部 interrupt(...) 的返回值！
    final_state = await app.ainvoke(
        Command(resume=frontend_submission),
        config=config,  # 必须使用同一个 thread_id
    )

    # 输出 Agent 最终的回复
    last_message = final_state["messages"][-1]
    print(f"\nAgent 最终输出:\n{last_message.content}")


if __name__ == "__main__":
    asyncio.run(main())
```

### 关键机制解析

**1. 中断数据的捕获链路**

当工具内部执行 `interrupt({"action_type": "clarify", "payload": ...})` 时，LangGraph 会立刻挂起当前 Node/Task 并持久化到 Checkpointer。在外部：



- 通过 `state = await app.aget_state(config)` 获取快照。
- 从 `state.tasks[0].interrupts[0].value` 即可提取出刚才塞入 `interrupt()` 的字典对象，直接将其转为 JSON Response 下发给前端渲染。

**2. `Command(resume=...)` 的穿透行为**



- 在普通 LangGraph 节点中，`resume` 恢复的是节点函数。
- 在工具内部使用时，`Command(resume=frontend_submission)` 会**直接穿透至 Tool 内部**，替代挂起的 `interrupt()` 调用并将 `frontend_submission` 赋给返回值变量。
- 工具执行完毕后正常生成 `ToolMessage`，LLM 随后自动被唤起继续处理任务。

**3. API 接口落地形态（FastAPI 场景）**

在实际 Web 服务中，通常拆分为两个端点：



- `POST /chat`：接收初始用户消息，调用 `ainvoke`。如果 `state.tasks[0].interrupts` 存在，返回 `200 OK` 伴随表单定义（HTTP 请求在此处闭合，不长连接等待）。
- `POST /chat/resume`：接收前端表单提交结果，调用 `ainvoke(Command(resume=req.body), config={"configurable": {"thread_id": req.thread_id}})`，执行完毕后将 LLM 最终回答返回。

### 问：还有一个问题是用async def定义函数没关系吧

**完全没问题，而且在基于 FastAPI 或高并发的异步服务中，强烈推荐使用 `async def`。**



LangChain 的 `@tool` 装饰器原生支持协程函数，LangGraph 的 `ToolNode` 在异步运行时（`ainvoke` / `astream`）下也会原生调度异步工具。



不过在 `async def` 下使用 `interrupt`，需要留意以下两个关键细节：



### 1. `interrupt()` 本身**不要**加 `await`

虽然外层函数是 `async def`，但 `interrupt` 的底层实现是通过抛出特殊的图控制异常（Control-Flow Signal）来挂起状态的。它是一个同步函数：



Python

```
# 正确写法
user_response = interrupt({"action_type": "clarify", ...})

# 错误写法（会抛出 TypeError: object dict can't be used in 'await' expression）
user_response = await interrupt({"action_type": "clarify", ...})
```

### 2. 必须全程使用异步 API 调用图

如果你的工具链中有 `async def` 的工具，在运行和恢复图时，必须始终调用 LangGraph 的异步方法：



- **运行与恢复：** 使用 `await app.ainvoke(...)` 或 `app.astream(...)`
- **获取状态：** 使用 `await app.aget_state(config)`
- **避免混用同步调用：** 不要调用同步的 `app.invoke(...)`，否则在执行纯异步工具时会因找不到同步实现而报错。

### 问：其实我不太了解langgraph的graph对象的ainvoke函数运行的底层逻辑，它如果遇到的是同步节点会怎么处理？

在 LangGraph 的 `ainvoke` 异步运行时中，如果遇到同步节点（普通的 `def` 函数），它**不会阻塞事件循环，而是会自动将该节点移交到底层的线程池（Worker Thread）中执行**。



LangGraph 底层基于 Google 的 **Pregel** 分布式图计算模型，整体调度逻辑与同步节点的处理机制如下：



### 1. 同步节点的底层处理：线程池卸载（Thread Offloading）

Python 的 `asyncio` 事件循环是单线程的，如果直接在协程中调用耗时的同步 `def` 函数，会卡死整个事件循环。



LangGraph（以及底层的 LangChain Runnable 机制）在调度节点时会执行以下逻辑：



1. **函数签名自省（Introspection）：**

   在包装节点时，通过 `inspect.iscoroutinefunction` 检测该节点是 `async def` 还是普通 `def`。

2. **异步节点（`async def`）：**

   直接在当前的 `asyncio` 事件循环中作为协程任务 `await` 运行。

3. **同步节点（`def`）：**

   底层通过 `asyncio.get_running_loop().run_in_executor(...)`（或 `asyncio.to_thread`）将同步函数包装进一个 `ThreadPoolExecutor`（默认工作线程池）中异步等待结果：

   Python

   ```
   # 概念层面的底层等价逻辑
   if inspect.iscoroutinefunction(node_func):
       result = await node_func(state, config)
   else:
       # 扔到子线程跑，避免阻塞 asyncio 事件循环
       result = await loop.run_in_executor(None, node_func, state, config)
   ```

因此，在同一个图内**混用同步节点与异步节点是完全安全的**。



### 2. `ainvoke` 整体运行机制：Pregel 状态机

`ainvoke` 并不是简单的一步步线性调用，它是一个由通道（Channels）和步骤（Supersteps）驱动的状态机：



```
输入 State 
   │
   ▼
[ 评估就绪任务 ] ── 检查哪些节点的触发条件已被满足
   │
   ▼
[ 并发执行任务 ] ── 异步协程直接跑，同步节点分发给线程池；
   │                所有任务通过 asyncio.gather / TaskGroup 并行推进
   │
   ▼
[ 状态聚合归约 ] ── 等待所有分支完成，按 Reducer（如 add_messages）合并输出
   │
   ▼
[ 检查 Checkpoint / Interrupt ] ── 遇到 interrupt 则存盘并挂起，否则进入下一轮
```

- **超步（Superstep）并发：** 如果图中有两条并行的分支，一条是异步的，一条是同步的，`ainvoke` 会通过 `asyncio.gather` 同时启动它们。异步节点占用主事件循环，同步节点占用后台工作线程，两者在下一个超步前汇合。
- **中断（Interrupt）的特殊性：** 无论是同步节点还是异步节点，调用 `interrupt()` 时都是抛出一个内部专用的控制流异常（`GraphInterrupt`）。LangGraph 在调度外层捕获此异常后，将当前状态序列化写入 Checkpointer，并立刻优雅退出。

### 3. ToolNode 对 `@tool` 的分流

在使用预构建的 `create_react_agent` 或 `ToolNode` 时，内部处理工具调用的逻辑也是类似的：



- 如果你定义了 `async def my_tool(...)`，`ToolNode.ainvoke` 会直接 `await my_tool(...)`。
- 如果你定义了同步的 `def my_tool(...)`，`ToolNode.ainvoke` 会在线程池中调用它。
- 如果大模型一次性输出了多个 Tool Calls（并行工具调用），`ToolNode` 会同时启动这些工具，同步与异步工具混合并发执行。

### 4. 实践中的注意事项

1. **I/O 密集型 vs CPU 密集型：**

   - 同步的网络 I/O（如使用 `requests` 库）：虽然在线程池中不会阻塞事件循环，但大量并发时会迅速耗尽线程池配额（默认线程池大小通常为 `CPU 核心数 * 5`）。高并发场景下，网络 I/O 最好重构为 `httpx.AsyncClient` 或 `aiohttp` 并使用 `async def`。
   - CPU 计算任务：受 Python GIL（全局解释器锁）限制，扔进线程池依然会抢占 GIL。

2. **ContextVars 传递：**

   Python 3.7+ 的 `asyncio` 在使用线程池时，会自动复制当前的 Context（包括 LangChain 的 tracing 上下文、token 计数器等），因此同步节点中的追踪监控不会丢失。

3. **避免在同步节点中调用 `asyncio.run()`：**

   如果一个节点是同步 `def`，但你在其内部又手写了 `asyncio.run(...)` 去调异步代码，在复杂的事件循环嵌套下很容易导致锁死或事件循环冲突。需要异步操作的地方，直接将节点定义为 `async def` 即可。

### 问：那按照你的说法，即使langgraph的图中全是同步节点，也推荐用ainvoke方法了？

**不一定，是否推荐 `ainvoke` 取决于你的外层运行环境，而不是图内部节点是同步还是异步。**



如果把纯同步图丢进 `ainvoke`，底层会把每个节点都放到线程池里执行，这会带来额外的线程调度和上下文切换开销。



判断标准主要看以下两种场景：



### 推荐使用 `ainvoke` 的场景

**1. 运行在异步 Web 框架中（如 FastAPI、Starlette、Tornado）**



- 即使图内全是同步节点，在 FastAPI 的 `async def` 路由里也**必须**使用 `await app.ainvoke()`。
- 如果在异步路由中调用同步的 `app.invoke()`，这些同步节点会直接在 Web 服务的**主事件循环线程**上执行，从而卡死整个服务，导致其他并发用户的 HTTP 请求全部排队等待。

**2. 需要 SSE 流式打字机输出或 WebSocket 交互**



- 如果前端需要逐字流式展示 Token（`app.astream`），无论节点内部写得有多同步，外层交互通道本身是异步长连接，统一走异步生态更顺畅。

**3. 使用了异步持久化存储（Async Checkpointer）**



- 如果图使用了生产级异步存储（例如 `AsyncPostgresSaver`），整体运行状态必须依赖异步 I/O，此时哪怕计算逻辑全是同步的，也必须配合 `ainvoke`。

### 推荐坚持使用 `invoke` 的场景

**1. 脚本、离线批处理或自动化测试**



- 简单的 CLI 脚本、本地单测、Jupyter Notebook 或离线离线批量跑数据的任务。
- 如果环境本身没有 `asyncio` 事件循环，为了调用 `ainvoke` 还得手写 `asyncio.run(...)`，不仅没有任何性能收益，还会因为频繁将任务分发到线程池而徒增微小的性能损耗。

**2. 传统同步应用与工作流引擎（如 Flask、Celery、Django 同步视图）**



- Celery worker 或 Flask 视图本身就是多进程/多线程模型，直接在当前线程顺序执行 `app.invoke()` 最轻量，堆栈追踪（Stack Trace）和断点调试也比多线程异步切换更直观。

**选型决策：**



- **在 FastAPI / 异步服务中：** 无条件使用 `await app.ainvoke(...)`。
- **在普通单脚本 / 同步 Worker 中：** 无条件使用 `app.invoke(...)`。

### 问：按照这个逻辑，langgraph中异步的node节点必须是真异步，否则反而会阻塞FastAPI事件循环，而同步节点不会，因为它本来就被放到线程池里跑了

你的理解**完全正确，而且精准点出了 Python 异步编程中最经典的反直觉陷阱**：写了 `async def` 却包含阻塞调用的“假异步”，对系统的破坏力远大于老老实实写同步 `def`。



**1. “假异步”为何会彻底卡死系统？**



`asyncio` 依靠单线程内的**协作式调度（Cooperative Multitasking）**。只有在遇到真正的 `await`（底层交出系统 I/O 控制权）时，事件循环才能去处理其他请求。



如果你定义了 `async def node(...)`，LangGraph 会信任你的声明，直接把它放在主事件循环线程上调度：



- 如果你在里面调用了 `requests.get()`、`time.sleep()` 或耗时的同步 SDK，代码**不会让出执行权**。
- 此时整个 FastAPI 进程的主线程被霸占，**所有其他用户的 HTTP 请求、心跳检测和 WebSocket 都会在此期间全部停摆**。

**2. 同步节点的“被动防御”机制**



当你定义为普通函数 `def node(...)` 时，LangGraph 检测到它不是协程，会通过 `run_in_executor` 将它甩给后台线程池：



- 即使你在里面执行 `time.sleep(10)` 或 `requests.get()`，阻塞的也只是线程池里的**某一个工作子线程**。
- FastAPI 的主事件循环线程不受影响，依然可以自如处理其他并发请求。这就是为什么老实写同步 `def` 反而比写出假异步更安全。

**3. 同步节点扔进线程池并非高枕无忧**



虽然同步节点不会直接锁死事件循环，但仍需警惕两个瓶颈：



- **线程池耗尽（Thread Pool Starvation）：** 默认线程池大小有限（通常是 `CPU 核心数 * 5` 或固定数十个）。如果并发量骤增，阻塞的同步节点占满了所有线程，新的同步节点和框架内其他依赖线程的任务就必须排队等待。
- **GIL 限制（针对 CPU 密集型任务）：** 如果同步节点执行的是复杂数据清洗、正则表达式匹配或本地小模型计算，由于 Python GIL 的存在，多个线程依然无法利用多核，仍会与主事件循环争抢 CPU 时间。

**开发避坑建议**

| **节点内部逻辑**                                | **推荐声明方式** | **处理方案**                                                 |
| ----------------------------------------------- | ---------------- | ------------------------------------------------------------ |
| **纯异步 I/O**（`httpx`, `asyncpg`, Redis 等）  | `async def`      | 全程原生 `await`，最高吞吐量                                 |
| **同步 I/O**（`requests`, 阻塞式第三方 SDK）    | `def`            | 声明为同步函数，交由 LangGraph 自动卸载到线程池              |
| **既有异步又有同步阻塞库**                      | `async def`      | 在协程内使用 `asyncio.to_thread(sync_func, *args)` 手动隔离阻塞代码 |
| **重度 CPU 计算**（大批量文本正则、图像预处理） | `def` / 外部进程 | 放入 `ProcessPoolExecutor` 或独立的 Celery 任务，避免拖慢主进程 |

### 问：其实我对ask_user_question这个工具还有疑问，因为它实际上全是同步代码，却还是用async def定义为异步函数，这是因为它内部全是同步非阻塞代码，用异步函数定义也不会阻塞主线程，反而def定义为同步函数会放到线程池跑增大开销

你的分析**完全正确，而且直接抓住了核心性能本质**。



在 `ask_user_question` 这个特定场景下，用 `async def` 定义纯同步代码不仅合理，反而是最优雅、开销最小的做法。



### 1. 内部代码的实际消耗：微秒级纯内存操作

看一下这个函数内部到底干了什么：



Python

```
response = interrupt(
    {
        "action_type": "clarify",
        "payload": [question.model_dump() for question in questions]
    }
)
return response
```

1. **Pydantic 序列化**：最多 3 个 `Question` 对象的 `.model_dump()`，纯内存字典转换，耗时在 **微秒（μs）** 级别。
2. **`interrupt()` 调用**：底层没有任何网络 I/O 或休眠，它本质上是直接抛出一个图专用的控制流异常（`GraphInterrupt`），通知引擎立即保存快照并终止当前轮次。
3. **唤醒后返回**：拿到 `resume` 注入的内存数据直接返回。

这种微秒级别的轻量计算，对单线程的事件循环（Event Loop）造成的阻塞可以忽略不计（相当于执行了几行基础的 Python 语句）。



### 2. 如果写成同步 `def`，反而会产生“负优化”

如果你把它声明为同步函数 `def ask_user_question(...)`，LangGraph 在异步运行时（`ainvoke`）中为了“保护”主线程，必须启动安全防御策略：



1. **打包任务**：通过 `loop.run_in_executor(...)` 把函数包装为一个线程任务。
2. **线程切换开销**：将任务塞进线程池队列，触发操作系统的线程上下文切换（Context Switch），唤醒一个工作子线程去跑这几微秒的代码。
3. **Future 监听与包装**：主事件循环需要注册并监听这个子线程的 Future 完成状态。

**结果**：线程调度和上下文切换的开销（通常在几十到几百微秒），可能比这几行代码本身的执行时间还要长好几倍。



### 3. 异常传递链路更清晰

`interrupt` 的运行逻辑本质是**抛出异常**：



- **在 `async def` 中**：控制流异常直接在主事件循环的协程栈里向上抛出，LangGraph 立即就地捕获、序列化状态、优雅挂起。
- **在 `def`（线程池）中**：异常必须在子线程内被捕获并封装进 `Future`，主线程 await 时解包重新抛出，徒增跨线程传播的复杂度和微小延迟。

### 总结：何时该在 `async def` 里放纯同步代码？

| **代码特征**                      | **典型场景**                                    | **推荐写法** | **原因**                                         |
| --------------------------------- | ----------------------------------------------- | ------------ | ------------------------------------------------ |
| **微秒级内存操作 / 快速异常触发** | 简单参数拼接、Pydantic 校验、`interrupt()` 调用 | `async def`  | 避免线程池往返调度开销，执行速度最快             |
| **毫秒或秒级 CPU 密集计算**       | 大文本正则清洗、本地加密算法、百万循环          | `def`        | 必须移交线程池（或进程池），否则真会卡死事件循环 |
| **同步阻塞 I/O**                  | `requests.get()`、阻塞式数据库 SDK              | `def`        | 必须移交线程池，等待期间释放主线程               |