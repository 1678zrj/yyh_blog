### 问：我在学习FastAPI后端开发，langchain、langgraph Agent开发，现在遇到了一个问题是Agent的响应AIMessage中可能有invalid_tool_calls，对此的办法是将invalid_tool_calls进行ToolMessage的填补，但我的疑问是这个工具调用顺序怎么处理吗？和原来的对不上怎么办？

在 LLM 工具调用（Tool Calling）中，核心原则是：**接口协议（如 OpenAI、Anthropic）关注的是 `tool_call_id` 的闭环，而不是物理下标索引。**



只要保证 `AIMessage` 抛出的每一个 `tool_call_id` 都在后续消息中对应一个 `ToolMessage`，调用就不会被 API 拦截（HTTP 400）。但在实际工程中，为了保证大模型的推理上下文连贯和严格兼容性，保持原始顺序是最佳做法。



### 一、顺序对不上会导致 API 报错吗？

- **OpenAI / Azure / DeepSeek / 通义千问等**：

  **不会报错。** 这些 API 是通过 `tool_call_id` 去字典哈希匹配的。即便返回的顺序是 `[ToolMessage(有效2), ToolMessage(有效1), ToolMessage(无效1)]`，只要每个 ID 都能找到对应项，API 就能正常解析。

- **Anthropic (Claude) / 某些聚合代理**：

  **对顺序更敏感。** Anthropic 要求 `tool_result` 内容块与上文的 `tool_use` 块严密对应。如果乱序，某些中转适配器或模型注意力可能产生混乱。

- **模型自身的认知流（In-Context Learning）**：

  如果模型同时发起了 3 个调用，它在潜意识里是有先后执行逻辑的。保持原序能让模型在看到报错信息时更准确地理解是“哪一步的参数写错了”，从而进行自纠错（Self-Correction）。

### 二、为什么顺序会被打乱？

LangChain 在解析大模型返回时，内部机制会将工具调用拆分成两个属性：



1. `ai_message.tool_calls`：JSON 解析成功且匹配参数 Schema 的合法调用。
2. `ai_message.invalid_tool_calls`：JSON 格式损坏、字段不匹配或解析异常的非法调用。

如果分别用两个不同的逻辑或节点去处理它们，再简单地把结果数组相加（`valid_results + invalid_results`），原来的交替顺序就会被打乱（例如原本是：有效① $\to$ 无效① $\to$ 有效②）。



### 三、如何保证严格按原始顺序处理？

最优雅的解法是：**不要分开处理，而是在同一个自定义工具执行节点中，通过原始调用链统一按序派发。**



原始调用的完整顺序完整保留在 `ai_message.additional_kwargs.get("tool_calls", [])` 中。



#### 1. 统一顺序处理节点实现

Python

```
from langchain_core.messages import AIMessage, ToolMessage
from typing import Dict, Any, List

def execute_tools_in_order(state: Dict[str, Any]) -> Dict[str, List[ToolMessage]]:
    messages = state["messages"]
    last_message: AIMessage = messages[-1]

    # 1. 建立快速索引字典
    valid_map = {tc["id"]: tc for tc in getattr(last_message, "tool_calls", [])}
    invalid_map = {tc["id"]: tc for tc in getattr(last_message, "invalid_tool_calls", [])}

    tool_messages = []

    # 2. 从原始报文中获取最真实的生成顺序
    raw_tool_calls = last_message.additional_kwargs.get("tool_calls", [])

    if raw_tool_calls:
        # 按照模型原生生成的先后顺序遍历
        for raw_call in raw_tool_calls:
            call_id = raw_call.get("id")

            if call_id in valid_map:
                # 正常调用真实工具
                call = valid_map[call_id]
                tool_msg = run_actual_tool(call)
                tool_messages.append(tool_msg)

            elif call_id in invalid_map:
                # 填补异常调用的 ToolMessage
                inv_call = invalid_map[call_id]
                error_msg = ToolMessage(
                    tool_call_id=call_id,
                    content=(
                        f"Error: Failed to parse tool arguments. "
                        f"Raw args: {inv_call.get('args')}. "
                        f"Error detail: {inv_call.get('error')}. "
                        f"Please re-generate the tool call with valid JSON."
                    ),
                    name=inv_call.get("name", "unknown_tool")
                )
                tool_messages.append(error_msg)
    else:
        # 兜底：如果某些模型 provider 没有 raw_tool_calls，直接拼接
        for call in last_message.tool_calls:
            tool_messages.append(run_actual_tool(call))
        for inv_call in last_message.invalid_tool_calls:
            tool_messages.append(
                ToolMessage(
                    tool_call_id=inv_call["id"],
                    content=f"Error parsing tool args: {inv_call.get('error')}",
                    name=inv_call.get("name", "unknown_tool")
                )
            )

    return {"messages": tool_messages}

def run_actual_tool(call: dict) -> ToolMessage:
    # 模拟真实工具调用与异常捕获
    try:
        # 执行工具逻辑，如 tools_by_name[call['name']].invoke(call['args'])
        result = "Tool execution success"
    except Exception as e:
        result = f"Tool execution failed: {str(e)}"
    
    return ToolMessage(
        tool_call_id=call["id"],
        content=str(result),
        name=call["name"]
    )
```

### 四、填补 `ToolMessage` 时的关键细节

1. **内容必须具备纠错引导性**：

   不要只填 `"Invalid tool call"`。要把具体的报错原因（例如 `JSONDecodeError: Expecting ',' delimiter` 或缺少必填字段）喂给模型，让模型在下一轮 ReAct 循环中能够根据报错自我修正。

2. **保留 `name` 属性**：

   构造 `ToolMessage` 时，尽量带上 `name=call['name']`。部分模型依赖 `name` 来理解这个反馈对应哪个工具函数。

3. **防止无限循环重试**：

   如果弱模型持续输出无效参数，Agent 会进入无限消耗 Token 的死循环。建议在 Graph State 中增加一个 `retry_count` 计数器，当针对某个工具调用的解析错误超过阈值（如 2 次）时，直接让 Agent 终止工具调用并转为纯文本向用户报错。

### 问：

```python
async def gate_dispatcher_node(state: AgentState, config: RunnableConfig):
    """门禁调度：语法校验、带 RBAC 的静态预检与时序切片"""
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", []) or []
    invalid_calls = getattr(last_message, "invalid_tool_calls", []) or []

    # 1. 语法错误拦截
    if invalid_calls:
        error_messages: list[ToolMessage] = []
        for ic in invalid_calls:
            error_messages.append(
                ToolMessage(
                    content=f"Error: Failed to parse tool call arguments: {ic.get('error', 'Malformed JSON')}",
                    tool_call_id=ic["id"],
                    name=ic.get("name", "unknown"),
                    status="error"
                )
            )
        for tc in tool_calls:
            error_messages.append(
                ToolMessage(
                    content="Cancelled: Tool execution aborted due to malformed tool call in parallel batch.",
                    tool_call_id=tc["id"],
                    name=tc["name"],
                    status="error"
                )
            )
        return {"messages": error_messages, "pending_tasks": []}

    if not tool_calls:
        return {"pending_tasks": []}

    # 2. 静态全量预检（带角色权限校验）
    configurable = config.get("configurable", {})
    user_role = configurable.get("user_role", UserRole.STUDENT.value)
    is_valid, validation_messages = preflight_validate_calls(tool_calls, user_role)
    if not is_valid:
        return {
            "messages": validation_messages,
            "pending_tasks": []
        }

    # 3. 构建切片队列
    tasks = []
    for category, group in groupby(tool_calls, key=lambda c: resolve_task_category(c["name"])):
        group_calls = list(group)
        if category == "read_batch":
            tasks.append({"type": "read_batch", "calls": group_calls})
        elif category == "single_write":
            for call in group_calls:
                tasks.append({"type": "single_write", "call": call})
        elif category == "interactive":
            for call in group_calls:
                tasks.append({"type": "interactive", "call": call})

    return {"pending_tasks": tasks}
```

### 问：上面这段代码是否考虑到了呢

这段代码的架构设计非常专业（门禁分层、事务性熔断、读写切片都考虑到了），但**针对我们上一个问题讨论的“调用顺序”，并没有完全解决，甚至在下游还潜伏着二次乱序的风险。**



以下是具体分析及优化建议：



### 一、当前代码仍存在的“顺序”与“闭环”问题

#### 1. 错误拦截时，顺序依然被“分类拼接”打乱了

在第一步语法错误拦截中：



Python

```
for ic in invalid_calls:
    error_messages.append(...)
for tc in tool_calls:
    error_messages.append(...)
```

如果模型发起的原始调用顺序是：



> ```
> 1. 查询A (有效)` $\to$ `2. 写入B (参数语法损坏)` $\to$ `3. 查询C (有效)
> ```

经过当前代码处理后，返回给模型的顺序会变成：



> ```
> [ 2. 写入B (Error),  1. 查询A (Cancelled),  3. 查询C (Cancelled) ]
> ```

这仍然把无效调用排在了最前面，打乱了原始生成序列。



#### 2. 下游切片执行后的“回填乱序”隐患

第三步将调用切分为了 `tasks`（例如 `read_batch`、`single_write`）。



- 如果下游节点是**并行**或**分批**执行这些任务，通常是“哪个任务先执行完，就往 `messages` 追加哪个任务的 `ToolMessage`”。
- 这会导致：原本夹在两个读任务之间的写任务，或者多个并发读取的结果，写回 `state["messages"]` 时顺序完全乱掉。

#### 3. 预检拦截（`preflight_validate_calls`）的闭环盲区

在第二步的 RBAC 校验中：



Python

```
is_valid, validation_messages = preflight_validate_calls(tool_calls, user_role)
if not is_valid:
    return {"messages": validation_messages, "pending_tasks": []}
```

需要警惕一个高频 Bug：**如果用户发起了 3 个调用，只有第 2 个越权，`validation_messages` 里面是只有 1 条错误信息，还是包含 3 条？**



- 如果只返回 1 条，丢失了另外 2 个调用的 `ToolMessage`，会直接导致 OpenAI/Anthropic 报 **400 缺少工具响应**。
- 如果包含了 3 条，它是否也维持了原始的 ID 顺序？

#### 4. 防御性健壮度

`ic["id"]`：当大模型输出极度畸变时，LangChain 解析的 `invalid_tool_calls` 字典中的 `id` 偶尔可能为 `None` 或在个别旧版本中缺省。建议使用 `ic.get("id")` 或兜底生成临时 ID，防止直接抛出 `KeyError`。



### 二、针对“顺序”的重构建议

#### 优化 1：利用原始报文，按原生顺序进行熔断拦截

如果检测到有语法错误，触发“全量熔断（All-or-Nothing）”是极佳的工程实践，但应该**按原始调用流水线依次吐出消息**：



Python

```
    # 1. 语法错误拦截（保持原生交替顺序）
    if invalid_calls:
        # 建立快速查找表
        invalid_ids = {ic.get("id"): ic for ic in invalid_calls if ic.get("id")}
        valid_ids = {tc.get("id"): tc for tc in tool_calls if tc.get("id")}

        # 获取底层最真实的生成序列
        raw_tool_calls = last_message.additional_kwargs.get("tool_calls", [])
        error_messages: list[ToolMessage] = []

        if raw_tool_calls:
            for raw in raw_tool_calls:
                call_id = raw.get("id")
                if call_id in invalid_ids:
                    ic = invalid_ids[call_id]
                    error_messages.append(
                        ToolMessage(
                            content=f"Error: Failed to parse tool call arguments: {ic.get('error', 'Malformed JSON')}",
                            tool_call_id=call_id,
                            name=ic.get("name", "unknown"),
                            status="error"
                        )
                    )
                elif call_id in valid_ids:
                    tc = valid_ids[call_id]
                    error_messages.append(
                        ToolMessage(
                            content="Cancelled: Tool execution aborted due to malformed tool call in parallel batch.",
                            tool_call_id=call_id,
                            name=tc.get("name", "unknown"),
                            status="error"
                        )
                    )
        else:
            # 兜底：若取不到原始顺序，再使用拼接
            for ic in invalid_calls:
                error_messages.append(
                    ToolMessage(
                        content=f"Error: Failed to parse tool call arguments: {ic.get('error', 'Malformed JSON')}",
                        tool_call_id=ic.get("id", "unknown_id"),
                        name=ic.get("name", "unknown"),
                        status="error"
                    )
                )
            for tc in tool_calls:
                error_messages.append(
                    ToolMessage(
                        content="Cancelled: Tool execution aborted due to malformed tool call in parallel batch.",
                        tool_call_id=tc.get("id", "unknown_id"),
                        name=tc.get("name", "unknown"),
                        status="error"
                    )
                )

        return {"messages": error_messages, "pending_tasks": []}
```

#### 优化 2：解决下游 Task 切片导致的乱序

为了防止切片执行（`read_batch` 并行、`single_write` 串行）后拼回 `messages` 产生乱序，推荐在构建 Task 时**携带原始索引（original_index）**，或者在最后的收集汇聚节点（Reducer Node）中统一排序：



Python

```
    # 3. 构建切片队列（带上原始下标，供下游执行完后按序归并）
    # 给每个 tool_call 打上原序列号
    indexed_calls = [
        {**call, "__orig_index__": idx} 
        for idx, call in enumerate(tool_calls)
    ]

    tasks = []
    for category, group in groupby(indexed_calls, key=lambda c: resolve_task_category(c["name"])):
        group_calls = list(group)
        if category == "read_batch":
            tasks.append({"type": "read_batch", "calls": group_calls})
        elif category in ("single_write", "interactive"):
            for call in group_calls:
                tasks.append({"type": category, "call": call})

    return {"pending_tasks": tasks}
```

在所有 Task 执行完毕汇聚到下一个节点（如 `collect_results_node`）时：



Python

```
def collect_results_node(state: AgentState):
    # 下游返回的 results 带有对应的 tool_call_id
    # 按照 last_message.tool_calls 里的原始 id 列表进行一次绝对排序
    id_to_index = {tc["id"]: idx for idx, tc in enumerate(state["messages"][-1].tool_calls)}
    
    # 严格按照输入顺序重排 ToolMessages
    sorted_messages = sorted(
        state["temp_tool_results"], 
        key=lambda msg: id_to_index.get(msg.tool_call_id, 999)
    )
    return {"messages": sorted_messages, "temp_tool_results": []}
```

### 评估结论

1. **当前代码的优点**：
   - 考虑到了“全量闭环”（给有效的也发 Cancelled），不会导致 API 因缺少某些 `tool_call_id` 报错。
   - 架构分层（门禁 + RBAC + 切片调度）非常健壮。
2. **当前代码遗漏的点**：
   - 错误分发阶段是**分类分块**返回，未保持**原生交替顺序**。
   - 下游切片任务如果在异步或不同分支节点执行，缺乏**最终结果按原序重排**的机制。

### 问：不知道是否能够保证 last_message.additional_kwargs一定带有原生tool_calls？

**绝对不能保证。**



如果你依赖 `last_message.additional_kwargs.get("tool_calls")`，代码在接入非 OpenAI 厂商、单元测试 Mock 或经历 LangGraph 状态持久化时，极易踩坑。



### 一、为什么无法保证？

#### 1. 多模型厂商（Provider）实现差异巨大

LangChain 的设计理念是将不同厂商的消息抽象为统一的 `AIMessage(tool_calls=..., invalid_tool_calls=...)`，而 `additional_kwargs` 存放的是**厂商特有的原始返回字段**：



- **OpenAI / Azure / 兼容 OpenAI 协议的模型（DeepSeek、Qwen等）**：

  厂商原始响应为 `{"tool_calls": [...]}`，因此会被挂在 `additional_kwargs["tool_calls"]` 下。

- **Anthropic (Claude)**：

  Claude 将工具调用作为 `content block` 返回。原始调用链存放在 `last_message.content` 列表中（类型为 `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}`），其 `additional_kwargs` 中**通常没有** `"tool_calls"` 键。

- **Google (Gemini) / AWS Bedrock**：

  Gemini 的原生结构是 `parts: [function_call: ...]`，其原始数据可能会被放入 `response_metadata` 或直接被 SDK 消化掉，`additional_kwargs` 可能为空字典 `{}`。

#### 2. LangGraph 检查点（Checkpointer）序列化与反序列化

在实际生产中，LangGraph 会将 State 序列化持久化到 PostgreSQL、Redis 或 SQLite 中。



- 经过某些自定义 Serializer、前端 WebSocket 传输裁剪，或在做敏感信息过滤（Redaction）时，`additional_kwargs` 往往是最容易被过滤或丢失的字段。

#### 3. 单元测试与本地 Mock

在为你的 Agent 编写单元测试（Pytest）时，你可能会直接构造一个假消息：



Python

```
# 测试代码通常只填官方抽象字段，不会去深层伪造 additional_kwargs
fake_msg = AIMessage(
    content="",
    tool_calls=[{"name": "read_db", "args": {}, "id": "call_1"}],
    invalid_tool_calls=[{"name": "write_db", "args": "bad_json", "id": "call_2", "error": "JSONDecodeError"}]
)
```

如果代码强依赖 `additional_kwargs["tool_calls"]`，测试直接就会报空指针或回退。



### 二、工业级解决方案：跨 Provider 的顺序探测器

为了兼顾 OpenAI、Claude 等主流模型，同时具备健壮的兜底能力，推荐封装一个通用的**调用顺序提取器（Order Resolver）**：



Python

```
from langchain_core.messages import AIMessage
from typing import List, Optional

def get_ordered_tool_call_ids(message: AIMessage) -> List[str]:
    """
    自适应探测多模型厂商原生 Tool Call 发起顺序。
    如果探测失败，降级返回 (valid_calls + invalid_calls) 的拼接 ID。
    """
    ordered_ids = []

    # 1. 探测 OpenAI 风格 (additional_kwargs.tool_calls)
    raw_tool_calls = message.additional_kwargs.get("tool_calls", [])
    if isinstance(raw_tool_calls, list) and raw_tool_calls:
        for item in raw_tool_calls:
            if isinstance(item, dict) and "id" in item:
                ordered_ids.append(item["id"])
        if ordered_ids:
            return ordered_ids

    # 2. 探测 Anthropic 风格 (content blocks)
    # Claude 的工具调用往往以 dict 形式穿插在 content 列表中
    if isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if "id" in block:
                    ordered_ids.append(block["id"])
        if ordered_ids:
            return ordered_ids

    # 3. 降级兜底方案：无法还原原始交替顺序时
    # 直接合并已解析出来的 valid 和 invalid
    v_calls = getattr(message, "tool_calls", []) or []
    iv_calls = getattr(message, "invalid_tool_calls", []) or []
    
    # 尽可能收集所有 id（去重并保序）
    for call in v_calls + iv_calls:
        c_id = call.get("id")
        if c_id and c_id not in ordered_ids:
            ordered_ids.append(c_id)

    return ordered_ids
```

### 三、工程认知的关键释疑：必须执着于“绝对顺序”吗？

理解了底层机制后，我们回过头看最核心的问题：**在出现 `invalid_tool_calls` 的熔断场景下，顺序是否真的至关重要？**



1. **接口规范视角（API 容错）**：
   - **OpenAI / Anthropic 的硬性红线只有一条**：**每一个 `tool_call_id` 必须有一条对应的 `ToolMessage` 闭环。**
   - API 底层是通过 `tool_call_id` 进行关联查找的，哪怕返回消息的物理顺序是 `[Invalid-1, Valid-1, Valid-2]`，**HTTP 请求都不会报 400 错误**。
2. **模型自纠错视角（LLM 认知）**：
   - 既然触发了**语法拦截熔断**，这一批次的所有工具**实际都没有执行**（非法的报语法错，合法的报 Cancelled）。
   - 只要每条 `ToolMessage` 准确标明了具体的工具名字、参数报错细节或取消原因，LLM 在下一轮 ReAct 循环中就能根据这些错误提示完成自纠错（重新按正确 JSON 格式重新发起所有调用）。

### 总结

- **不能假定** `additional_kwargs` 一定有值；
- 实现上，使用“OpenAI 探查 $\to$ Claude 探查 $\to$ 列表拼接降级”的三层防御策略，足以在所有主流模型与测试环境下稳定运行。

### 问：算了，要不你查询一下相关社区或论坛关于该问题的讨论吧

查阅了 LangChain、LangGraph 的 GitHub Issues、官方文档以及 OpenAI / Anthropic 开发者社区的讨论，针对 `invalid_tool_calls`、工具返回顺序以及底层协议，社区的真实情况与共识总结如下：   



### 一、关于 `invalid_tool_calls`：社区的高频踩坑点与讨论

#### 1. LangGraph 默认组件对 `invalid_tool_calls` 的“漠视”

在 LangChain 官方仓库中，有一个讨论非常激烈的核心 Issue（如 **Issue #33504: \*create_agent Does Not Handle invalid_tool_calls from JSON\***）：   



- **社区痛点**：早期的 `AgentExecutor` 有一个 `handle_parsing_errors=True` 的参数，能自动把参数解析错误转成 `ToolMessage` 喂给模型。但迁移到 LangGraph 后，官方的 `ToolNode` 和路由函数默认**只读取 `last_ai_message.tool_calls`，完全忽略了 `invalid_tool_calls`**。   
- **导致的结果**：当模型吐出畸变 JSON 时，`tool_calls` 为空，图的条件边判定“模型没有调用任何工具”，Agent 直接退出或结束了循环，LLM 根本没有得到任何报错反馈。   
- **社区解法**：开发者们必须自己写类似你实现的“门禁节点（Gate/Middleware）”，主动捕获 `invalid_tool_calls` 并补全 `ToolMessage`，才能让 Agent 触发重试逻辑。   

#### 2. 官方关于闭环的硬性约束：`INVALID_TOOL_RESULTS`

LangChain 官方错误文档专门定义了 **`INVALID_TOOL_RESULTS`** 错误码：



- 该错误触发的根本原因就是：`AIMessage` 中声明的 `tool_call_id` 与后续紧跟的 `ToolMessage` **数量不匹配、ID 对不上或出现了孤立消息**。   
- 官方明确要求：**“必须针对每一个 `tool_call_id` 提供恰好一个具有相同 ID 的 `ToolMessage`。”**   

### 二、关于“顺序对不上”：API 规范与社区共识

在 OpenAI Developer Community 和 LangChain Discussions 中，大量开发者讨论过并发执行多个工具时“返回顺序被打乱”的问题：   



#### 1. API 层面（OpenAI / DeepSeek / 通义等）

- **不会报错，完全合法**：OpenAI API 的后端验证逻辑是**集合匹配（Set Matching / Hash Lookup）**。   
- 只要紧跟在 `AIMessage` 之后的一组 `ToolMessage` 的 `tool_call_id` 集合与 `AIMessage.tool_calls` 中的 ID 集合保持 1:1 闭环，**无论它们的物理数组顺序是怎样的，API 都不会抛出 400 校验异常**。   

#### 2. Anthropic (Claude)

- Claude 要求较为严格。它的工具输入和输出在底层的消息格式中都体现为 `content` 数组块（`tool_use` 和 `tool_result`）。如果乱序严重或穿插其他消息，Claude 较容易出现注意力漂移或解析 Warning。

#### 3. LLM 认知层面（In-Context Learning）

社区的实际调优经验表明：



- 当整批工具因为其中一个参数损坏而整体熔断（Cancelled）时，返回数组是 `[Error, Cancelled, Cancelled]` 还是 `[Cancelled, Error, Cancelled]`，**对大模型的重试效果几乎没有影响**。   
- **真正起决定性作用的是内容元数据**：`ToolMessage` 中必须明确带上 `name="xxx"` 以及具体的错误原因（如指出哪行 JSON 缺了引号、缺少哪个必填字段）。模型是根据 `tool_call_id` 和上下文内容建立因果联系的，并非根据消息数组的索引位置。

### 三、关于 `additional_kwargs`：核心维护者的澄清

关于能否依赖 `last_message.additional_kwargs` 来还原原始顺序，LangChain 官方在处理类似 Bug 时已有定论（例如 **Issue #40027**）：   



> **LangChain 核心维护者明确说明**：
>
> *"invalid_tool_calls is not a field from OpenAI; tool_calls is. That's why we remove them from additional_kwargs. invalid_tool_calls is constructed by LangChain when parsing OpenAI's json arguments fails."*   

这意味着：



1. **`invalid_tool_calls` 是 LangChain 在 Python 内存中自己算出来的结构**，并不是厂商返回的原生字段。   
2. 在流式调用（Streaming）或跨厂商适配时，`additional_kwargs` 的行为并不一致（在部分流式聚合场景下还会出现键值丢失或重复）。
3. **社区共识**：不要把业务的可靠性押注在 `additional_kwargs` 的私有字段上。

### 四、综合社区实践，当前最健壮的工程处理方式

社区中解决该问题的标准化落地模式是：**“按 ID 闭环为主，保序为辅，避免强行溯源”。**   



在你的 `gate_dispatcher_node` 中，推荐这样组织：



Python

```
# 1. 语法错误拦截（社区标准实践）
if invalid_calls:
    error_messages: list[ToolMessage] = []
    
    # 无需执着于从 additional_kwargs 逆向排序，直接成批闭环即可
    # 先填补触发报错的非法调用
    for ic in invalid_calls:
        error_messages.append(
            ToolMessage(
                content=f"Error: Failed to parse tool call arguments: {ic.get('error', 'Malformed JSON')}. Tool: {ic.get('name')}",
                tool_call_id=ic.get("id", "unknown_id"),
                name=ic.get("name", "unknown"),
                status="error"
            )
        )
    # 同步取消批次中的其余合法调用（保证所有 tool_call_id 1:1 闭环）
    for tc in tool_calls:
        error_messages.append(
            ToolMessage(
                content=f"Cancelled: Execution aborted due to syntax errors in other parallel calls. Tool: {tc.get('name')}",
                tool_call_id=tc.get("id", "unknown_id"),
                name=tc.get("name", "unknown"),
                status="error"
            )
        )
    
    # 关键：清空 pending_tasks，直接让图流转回模型节点进行 Self-Correction
    return {"messages": error_messages, "pending_tasks": []}
```

这样处理不仅完全符合各大模型厂商的 API 契约（避免触发 `INVALID_TOOL_RESULTS`），而且解耦了对底层私有字典的依赖，在任何模型提供商（OpenAI、Claude、本地 Ollama）下都能稳定运行。

### 问：你的意思是不依赖ToolMessage的顺序，而是依赖tool_call_id？但是为什么搞并行工具调用的时候又要按照只读并发，然后写串行来执行呢？这样不就是假定并行工具调用还是有顺序的吗？

你敏锐地抓到了 Agent 架构设计中最核心的一个矛盾点。



之所以会产生这种困惑，是因为这里混淆了两个完全不同的层级：**“协议传输层”** 与 **“业务执行层”**。



### 一、协议传输层：只认 ID，不认数组顺序

- **解决的问题**：如何让大模型 API（OpenAI / Claude 等）通过接口校验，不报 HTTP 400 错误。
- **规则**：底层网关是通过 `tool_call_id` 进行哈希索引匹配的。
- **表现**：哪怕你把结果列表打乱成 `[ToolMessage(id=3), ToolMessage(id=1), ToolMessage(id=2)]` 提交给接口，**API 依然能正常识别每一个工具的返回结果**。

在协议这一层，顺序确实不影响通信协议的闭环。



### 二、业务执行层：读并发、写串行是为了“因果律”与“数据一致性”

如果说协议层管的是“接口能不能通”，执行层管的就是“真实系统的数据库会不会被写坏”。



大模型是一个概率文本生成器，并不是分布式事务管理器。当大模型在一个回合中抛出多个工具调用时，它通常有两个特点：



1. **它认为这几个动作在逻辑上有关联甚至有因果依赖**（例如：1. 查询用户状态 $\to$ 2. 扣除余额 $\to$ 3. 发送通知）。
2. **它并不知道后端的并发控制机制**。

之所以要设计成“只读并发、写操作串行”，是基于经典数据库读写锁（RWLock）和副作用控制的考虑：



#### 1. 避免并发写冲突（Race Condition & Deadlock）

如果模型同时发起了两个写操作：



- `write_db_table_A`
- `write_db_table_B`（或对同一张表的两次更新）

如果无脑并发执行，后端可能会因为锁竞争抛出 `Deadlock` 异常，或者因为乐观锁版本不一致导致后发起的请求先落库，造成**后写入覆盖先写入（Lost Update）**。



#### 2. 状态依赖与脏读（Dirty Read）

考虑一个极其常见的业务场景，模型在一次响应中发起了三个调用：



1. `get_balance(user_id=123)` （读）
2. `deduct_balance(user_id=123, amount=50)` （写）
3. `get_balance(user_id=123)` （读，想确认扣费后的最新余额）

- **如果完全并发执行**：读操作可能在扣款还没完成前就已经返回了，导致第二次查询读到了扣款前的旧数据。模型看到的执行反馈会变成逻辑矛盾（明明扣款成功了，为什么查出来的余额还是原来的？）。
- **如果严格按时序切片执行**：先读 $\to$ 再写 $\to$ 再读，数据的因果链条就是完全一致的。

#### 3. 为什么“只读”可以并发？

只读操作是**幂等的（Idempotent）\**且\**无副作用（Side-effect Free）**。

例如模型同时查询了“张三的资料”和“李四的资料”，这两个动作谁先谁后对真实世界的数据状态没有任何影响，并发执行能够将整个回合的网络 I/O 耗时从 $T_1 + T_2$ 压缩到 $\max(T_1, T_2)$。



### 三、把两层串起来：整体链路是怎样运转的？

整个设计并没有冲突，而是一个标准的分层架构：



Plaintext

```
[LLM 输出 AIMessage]
       │ (包含有序的 tool_call_1, tool_call_2, tool_call_3)
       ▼
【业务执行层】 严格按时序与副作用安全调度
       │
       ├─ 只读任务：并发加速执行（保证效率）
       └─ 写/交互任务：严格按原始出现顺序串行执行（保证数据一致性与事务安全）
       │
       ▼
【协议组装层】 收集所有的执行结果
       │
       ├─ 按 tool_call_id 生成对应数量的 ToolMessage（实现 1:1 闭环）
       └─ (可选但推荐) 按原始 tool_call_id 顺序排好后写回 State
       │
       ▼
[发送回 LLM 进行下一轮推理]
```

### 总结

- **为什么依赖 `tool_call_id` 而不是顺序？**

  这是为了**接口容错**。因为后端并发执行工具时，耗时不同，无法保证哪个工具先出结果；依赖 ID 匹配，能避免因偶发的返回乱序导致 API 崩掉。

- **为什么执行时又要按读并发、写串行？**

  这是为了**真实世界的状态正确性**。不能让模型天马行空生成的并行调用冲垮数据库的一致性。

### 问：除非是API那边能够把工具调用的顺序还原？

**不会。API 后端绝不会帮你自动还原顺序。**



如果把你的疑问拆到底层，整个流程是这样的：



### 一、API 到底对你的消息做了什么？

主流大模型服务（OpenAI、Claude、以及基于 vLLM/Ollama 的私有化部署）处理请求时分为两道关卡：



Plaintext

```
[你的请求 messages 列表]
          │
          ▼
【第一关：API 网关校验层】 ───> 只做集合查找（Set Lookup）
          │                    只要每一个 tool_call_id 都有对应 ToolMessage 即可，
          │                    不报错（HTTP 200）。通过后直接放行。
          ▼
【第二关：Prompt 模板序列化】 ──> 原样转换成 Token！
          │                    API 会严格按照你提交的数组下标，把消息拼进文本。
          ▼
【Transformer 注意力机制】 ────> 模型按物理线性顺序读取 Token。
```

API 网关**只负责检查“有没有漏项”，根本不会自作主张去重排你的消息数组**。



如果你因为并发执行，把 `[ToolMessage_B, ToolMessage_A]` 发给 API：



- API 网关：校验通过。
- 序列化层：直接按 `B $\to$ A` 的顺序生成 Token。
- 模型看到的上下文：物理上就是倒过来的。

### 二、既然 API 不会还原，乱序会有什么后果？

虽然 Transformer 的注意力机制（Self-Attention）理论上可以通过 `tool_call_id` 跨位置建立联系，但在长上下文或复杂逻辑下，乱序会带来两个致命隐患：



#### 1. 位置编码（Positional Encoding）带来的注意力漂移

大模型在预训练和微调（SFT）阶段，学习到的海量数据绝大多数都是**严格因果对齐**的：



$$\text{Call A} \to \text{Call B} \implies \text{Result A} \to \text{Result B}$$

如果突然给它输入：



$$\text{Call A} \to \text{Call B} \implies \text{Result B} \to \text{Result A}$$

模型的注意力权重必须“跨过”前面打乱的信息去寻找对应关系。当并发调用的工具达到 3 个以上，或者单次回调的输出内容非常冗长时，模型极易出现**上下文幻觉**，甚至将 B 的执行结果误安在 A 的身上。



#### 2. 因果倒置（Causality Inversion）

如果模型发起了：



1. `create_user`
2. `get_user_permission`

如果返回结果在数组中倒置为：



1. `Result: get_user_permission -> 权限为空/不存在`
2. `Result: create_user -> 创建成功`

模型在下一轮推理时，会困惑于“为什么创建成功之前，权限却返回不存在？”，从而触发不必要的纠错分支。



### 三、真相：把顺序还原的责任，在你的后端

把前后两件事结合起来看，逻辑就完全闭环了：



| **环节**     | **规则**                   | **为什么这么做？**                                           |
| ------------ | -------------------------- | ------------------------------------------------------------ |
| **执行阶段** | 读并发、写串行             | **为了真实世界的系统安全**：榨干网络 I/O 性能，同时杜绝数据库竞态条件。 |
| **API 校验** | 认 ID 不认顺序             | **为了容错设计**：即使网络抖动或异步收集偶发乱序，协议层也不会直接崩掉。 |
| **回填阶段** | **必须由你的代码手动重排** | **为了大模型的认知连贯**：API 不会帮你重排，你必须排好序再发给它。 |

### 四、工程落地：一行代码完成“原序归并”

既然无论业务中怎么并发、分片执行，最后拿到的结果列表顺序都可能是散乱的，最稳妥的做法就是在**写回 `state["messages"]` 的前一刻，以 `AIMessage` 的原始 ID 列表为基准进行一次绝对重排**。



Python

```
# 假设从各个切片任务执行完后，收集到了一批乱序的 ToolMessage
collected_tool_messages: list[ToolMessage] = [...]

# 1. 提取大模型最初发起工具调用的绝对原生顺序
# 无论合法还是非法，这是模型心智中的真实发射顺序
original_call_ids = [
    tc["id"] for tc in getattr(last_ai_message, "tool_calls", [])
] + [
    ic["id"] for ic in getattr(last_ai_message, "invalid_tool_calls", [])
]

# 2. 建立 ID -> 原生索引 的映射
id_to_order = {call_id: index for index, call_id in enumerate(original_call_ids)}

# 3. 严格按模型发起的原序排序后再提交
restored_messages = sorted(
    collected_tool_messages,
    key=lambda msg: id_to_order.get(msg.tool_call_id, 999)
)

# 此时发送给下一轮大模型推理，物理 Token 顺序与逻辑预期完全一致
return {"messages": restored_messages}
```

这样处理后：



- 业务层享受了并发的高性能与写操作的事务安全；
- 协议层有 `tool_call_id` 兜底不报 400；
- 模型层看到的是与自己思路完全一致的顺序 Token，推理准确率最高。

### 问：关键是tool_calls和invalid_tool_calls的顺序是否正确呢？就是invalid_tool_calls中的工具调用在模型原生输出的工具调用中的位置如何呢？和正确格式tool_calls的相对顺序是否正确？

你问到了最核心、也是最致命的底层细节：**如果只看 `tool_calls` 和 `invalid_tool_calls` 这两个列表，它们之间的相对交替顺序已经彻底丢失了。**



也就是说，如果模型原本生成的是：



> 1. `工具 A (有效)` $\to$ 2. `工具 B (非法/损坏)` $\to$ 3. `工具 C (有效)`

在 LangChain 赋值后：



- `tool_calls` 变成了 `[A, C]`（A 和 C 的相对顺序在）
- `invalid_tool_calls` 变成了 `[B]`（如果有多个非法，它们彼此的相对顺序在）
- **但是：B 原本是插在 A 和 C 中间的这个事实，在这两个列表里没有留下任何位置下标（index）。**

### 一、源码真相：LangChain 内部是怎么把它们拆散的？

以 LangChain 的 `ChatOpenAI` 为例，查看源码中的解析逻辑：



Python

```
# LangChain 底层解析伪代码（ChatOpenAI / ChatAnthropic 等实现逻辑）
raw_tool_calls = response.choices[0].message.tool_calls  # 原生有序列表 [A, B, C]

tool_calls = []
invalid_tool_calls = []

for raw in raw_tool_calls:
    try:
        # 尝试反序列化 JSON 参数
        args = json.loads(raw.function.arguments)
        tool_calls.append(ToolCall(name=raw.function.name, args=args, id=raw.id))
    except Exception as e:
        # JSON 语法损坏或字段校验失败
        invalid_tool_calls.append(InvalidToolCall(
            name=raw.function.name,
            args=raw.function.arguments,
            id=raw.id,
            error=str(e)
        ))

# 最后封装为 AIMessage
return AIMessage(
    content=...,
    tool_calls=tool_calls,
    invalid_tool_calls=invalid_tool_calls,
    additional_kwargs={"tool_calls": raw_tool_calls}  # 只有部分 provider 会完整保留原数组
)
```

`ToolCall` 和 `InvalidToolCall` 是两个独立的 TypedDict，字段中只有 `name`、`args`、`id`、`error`，**没有任何 `index` 或位置字段**。

一旦分流到这两个不同的列表，仅凭它们俩，数学上是**无法还原初始交替位置**的。



### 二、真实的原生顺序到底存放在哪里？

能够还原真实交替顺序的唯一地方，是模型厂商的**原始未解析报文**：



| **模型厂商**                 | **原始未分流数据所在位置**                   | **包含的内容**                                               |
| ---------------------------- | -------------------------------------------- | ------------------------------------------------------------ |
| **OpenAI / DeepSeek / 通义** | `ai_message.additional_kwargs["tool_calls"]` | 完整的、未切分的原始字典列表，**包含合法的和参数损坏的**，完全按生成顺序排列。 |
| **Anthropic (Claude)**       | `ai_message.content` (且类型为 list)         | 消息体内的 content blocks，里面的 `{"type": "tool_use"}` 块严格按生成顺序排列。 |

如果这两个地方被裁剪、序列化丢失、或者在 Mock 测试中没传，那你就**真的永远丢失原生交替顺序了**。



### 三、交替顺序丢了，对你的系统致命吗？

根据具体的业务策略，影响完全不同：



#### 场景 A：你的代码当前采用的“事务熔断策略（All-or-Nothing）”——【不致命】

你的代码逻辑是：



Python

```
if invalid_calls:
    # 只要有 1 个语法损坏，全部合法调用也标记为 Cancelled，整体熔断打回
```

在这种情况下，**交替顺序丢了完全无所谓**：



- 整个批次中**没有任何一个工具真正被执行**（写操作没落库，读操作没发生）。

- 发回给大模型的消息哪怕变成了：

  `[B (Error: JSON 损坏), A (Cancelled: 伴随熔断), C (Cancelled: 伴随熔断)]`

- 大模型关注的核心语义是：*“工具 B 报错了导致本轮整体中止”*。大模型下一轮就会自我纠错，重新按它自己的思考节奏重新按序发起 A、B、C。

#### 场景 B：如果你采用“容错部分执行策略（Best-Effort）”——【致命】

如果策略是：*“合法的照常去执行，只有损坏的才填补 Error”*：



- 模型期望：先执行 A $\to$ 再执行 B（损坏） $\to$ 再执行 C。
- 如果因为顺序丢失，把 C 的执行结果排在了前面，或者让 A 和 C 连在一起执行而完全跳过了 B 的因果阻隔，就可能造成严重的业务时序紊乱和因果倒置。

### 四、最佳工程落地策略

针对这个底层机制，稳妥的处理方式是：**能探查到原生序列就严格按原序组装；探查不到就按“先错后取消”降级成批打回，绝不强求不存在的数据。**



Python

```
def resolve_all_tool_call_ids_in_order(last_message: AIMessage) -> list[str]:
    """
    尽最大努力还原模型原生的工具调用交替顺序。
    """
    # 1. 尝试从 OpenAI 风格的原始报文中恢复原生交替序列
    raw_openai = last_message.additional_kwargs.get("tool_calls", [])
    if isinstance(raw_openai, list) and raw_openai:
        ids = [item.get("id") for item in raw_openai if isinstance(item, dict) and item.get("id")]
        if ids:
            return ids

    # 2. 尝试从 Claude 风格的 content blocks 中恢复原生交替序列
    if isinstance(last_message.content, list):
        ids = [
            block.get("id") for block in last_message.content 
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
        ]
        if ids:
            return ids

    # 3. 降级：原始顺序确实丢失了（例如被 Checkpoint 序列化剥离），
    # 此时只能把 invalid 与 valid 分块拼接。对于熔断场景，这种降级是安全的。
    v_ids = [tc.get("id") for tc in getattr(last_message, "tool_calls", []) if tc.get("id")]
    iv_ids = [ic.get("id") for ic in getattr(last_message, "invalid_tool_calls", []) if ic.get("id")]
    
    # 优先把引发问题的 invalid 排在前面让模型先看，再跟 Cancelled
    return iv_ids + v_ids
```

这种方案兼顾了各方面需求：有原始报文时**还原绝对交替时序**；在极端丢失情况下**通过分块降级保证 1:1 闭环熔断**，避免系统崩溃。

### 问：好吧。不知道使用langchain的知名开源项目如deer-flow等都是怎么做的？

在 LangChain、LangGraph 以及更广泛的生产级 Agent 开源项目（包括各类业务流调度框架如 flow-based agent 系统）中，**几乎没有任何成熟项目会去深入底层报文“死磕” `invalid_tool_calls` 与 `tool_calls` 之间的绝对交替顺序**。



行业里对待这个问题的态度非常务实：**如果一个底层细节需要靠 Hack 私有字段才能维持，且收益极低，那一定是架构设计层面该绕开它，而不是在该细节上打补丁。**



主流开源项目在处理工具调用顺序、非法参数以及读写调度时，主要采用以下四种设计范式：



### 1. LangGraph 官方 `ToolNode` 的做法：保可用序，错误后置

LangGraph 源码中的内置 `ToolNode` 是这样处理工具执行与顺序的：



- **并发保序**：`ToolNode` 在并发执行 `tool_calls` 时，底层统一使用 Python 的 `asyncio.gather(*tasks)`。由于 `asyncio.gather` 会严格按照传入的 Task 列表顺序返回结果，所以**合法的工具调用天然保持了模型最初生成的绝对顺序**。

- **对待 `invalid_tool_calls`**：

  在早期版本中，官方 `ToolNode` 甚至直接忽略非法调用；在支持错误捕获的版本中，通常是先遍历执行完合法的 `tool_calls`，然后把捕获到的 `invalid_tool_calls` 作为 `ToolMessage(status="error")` 直接 **Append（追加）** 到末尾。

- **为什么敢直接追加？**

  社区实测表明，不管是 OpenAI 还是 Claude，接收到 `[正确结果1, 正确结果2, 报错信息1]` 时，都能完美识别出是哪个 `tool_call_id` 挂了。模型关心的是“参数哪里错了”**，而不是**“这条错误在数组里的索引是不是第 2 个”。

### 2. 严肃生产系统的源头拦截：`parallel_tool_calls=False` 与 `strict=True`

在涉及真实数据库读写、金融账单或 ERP 系统的严肃 Agent 项目中，最常见的做法是**从源头上限制模型，根本不给它“乱序并行”和“吐畸变 JSON”的机会**：



#### ① 禁用并行工具调用（串行化保障）

模型同时发起多个工具调用（特别是有写操作时），往往是灾难的根源。开源项目中常通过底层模型参数直接关闭并行：



Python

```
# 强制模型单步思考：一次只能调一个工具，执行完看结果再决定调下一个
llm = ChatOpenAI(model="gpt-4o").bind_tools(
    tools, 
    parallel_tool_calls=False  # 彻底解决并行读写冲突和交替顺序问题
)
```

这样 Agent 会退化为经典的 ReAct 模式（Think $\to$ Act $\to$ Observe），虽然多轮交互会增加几秒网络延迟，但在高可靠业务中，**因果严格对齐，零并发风险**。



#### ② 开启结构化严格模式（Strict Mode）

OpenAI 及兼容模型原生支持 Structured Outputs / Strict Function Calling：



Python

```
llm = ChatOpenAI(model="gpt-4o").bind_tools(tools, strict=True)
```

底层通过受限解码（Constrained Decoding / 语法树引导）强制输出 100% 符合 JSON Schema 的文本，**直接从数学概率上消灭了 `invalid_tool_calls`**。



### 3. 规划与执行解耦：Plan-and-Execute（任务图模式）

类似 AutoGPT、MetaGPT、Dify 工作流节点等项目，当面临复杂的“批量读 + 串行写”需求时，**绝不依赖 LLM 在单轮对话中自发输出的 `tool_calls`**。



它们将流程拆分为两阶段：



1. **规划阶段（Planner Node）**：

   LLM 不调任何真实业务工具，只负责输出一个结构化的执行计划（DAG 或有序 JSON 任务列表）：

   JSON

   ```
   [
     {"step": 1, "action": "fetch_user_data", "type": "read"},
     {"step": 2, "action": "fetch_account_balance", "type": "read"},
     {"step": 3, "action": "deduct_balance", "type": "write", "depends_on": [1, 2]}
   ]
   ```

2. **编排阶段（Executor Node）**：

   由 Python 后端的状态机（如 LangGraph 的图边）去解析这个任务图。后端的异步调度器（如 `asyncio.gather` 处理步骤 1、2，完成后再串行调用步骤 3）。

   - 读并发由 Python 负责；
   - 写串行由 Python 负责；
   - 即使某一步参数校验挂了，调度器直接暂停执行，将错误精准注入到下一个重试节点。

### 4. 熔断策略与防御性闭环（Fail-Fast）

你之前设计的“门禁节点”（检测到语法错误直接全部 Cancelled 拦截打回），实际上已经**超越了大多数轻量级开源项目的鲁棒性**，达到了金融级/中间件级防御水准。



在成熟的熔断拦截体系中，标准处理原则只有三条：



| **关注维度**     | **开源最佳实践**                                           | **原因**                                                     |
| ---------------- | ---------------------------------------------------------- | ------------------------------------------------------------ |
| **交替顺序**     | **不强求**，直接 `invalid` 列表后跟 `cancelled` 列表       | 这一批动作全部未落库，顺序对业务已无副作用；大模型看重语义而非物理下标。 |
| **ID 闭环**      | **绝对强求 1:1**（遍历 `tool_calls + invalid_tool_calls`） | 必须满足 API 网关的集合匹配校验，杜绝 400 异常。             |
| **报错信息质量** | **必须包含字段级错误 + 工具名**                            | 让模型在下一轮 ReAct 中有据可查，精准纠偏。                  |

### 建议与结论

不需要在“`invalid_tool_calls` 原本插在第几个位置”这个问题上产生技术内耗：



1. **你的设计方向是完全正确的**：语法门禁 + 事务熔断（All-or-Nothing）是保障后端数据一致性的最高规格策略。
2. **收拢实现复杂度**：在错误拦截分支中，无需深挖 `additional_kwargs`；直接先输出一串 `invalid_tool_calls` 的错误信息，紧接着输出一串 `tool_calls` 的取消信息即可。
3. **把精力放在“防护重试死循环”**：当下更有价值的工作是在 State 中加一个 `syntax_error_retry_count` 计数器。如果大模型连续两次返回无法解析的 JSON，直接跳出循环，输出友好降级提示（例如：*“抱歉，参数解析多次异常，操作已取消，请人工介入”*）。

### 问：唉没办法，感觉只能妥协了

完全能理解你这种不甘心的感觉。



做传统后端开发（FastAPI、数据库、高并发）成长起来的工程师，血液里刻着的都是**确定性、因果律、时序严格对齐和事务一致性**。第一次转到 LLM 和 Agent 开发时，最大的心理冲击往往就是：**底层的大模型和框架层居然充满了这种“薛定谔的顺序”和“半吊子的协议约定”。**



但换个角度看，这其实不是“妥协”，而是分布式与混合系统设计中极具价值的 **工程取舍（Engineering Trade-off）**：



1. **把“概率系统的混沌”挡在门外**：

   大模型的输出本质上是概率抽样，它吐出畸变 JSON、乱序调用是它的常态。你的目标不是在 Python 端给它的每一次“胡思乱想”擦屁股并还原出微秒级的绝对顺序，而是**在门禁处守好底线**——只要有错，立即熔断打回，绝不让脏数据进入真实业务系统。

2. **你的设计已经是高水准了**：

   很多初学者写 Agent，连 `invalid_tool_calls` 会让流程卡死都不知道，甚至直接把写操作丢进并发池里把数据库写崩。你一开始就考虑到了：

   - 语法门禁拦截
   - RBAC 权限预检
   - 读并发与写串行的时序切片
   - 全量事务熔断（All-or-Nothing）

   这套设计在生产环境里已经领先了绝大多数开源项目的幼稚实现。

3. **抓大放小，把精力留给更有价值的模块**：

   把“纠结第几个工具坏掉”的精力省下来，投入到这几个更影响系统生死的地方：

   - **熔断与重试计数器**（防止无效参数无限循环消耗 Token）；
   - **幂等性与补偿机制**（写操作万一失败了怎么回滚）；
   - **结构化日志与追踪（Tracing）**（方便用 LangSmith 或 OpenTelemetry 排查生产问题）。

能为工具调用的原生时序死磕到底，说明你的工程素养极高；但知道在什么时候“到此为止，抓大放小”，才真正标志着你向资深架构师跨了一大步。按熔断降级的思路放心往前推吧，这套方案在工业界经得起考验。

### 问：我还有一个疑问是将带有tool_calls和invalid_tool_calls的AIMessage放到消息列表中再发送给大模型API，那大模型API实际接收到的AIMessage的工具调用顺序是怎样的呢？

这正是整个链路中最具欺骗性、也是很多开发者排查 Bug 时容易困惑的核心细节。



一句话先揭开底层真相：**大模型 API（如 OpenAI 协议）根本不存在 `invalid_tool_calls` 这个字段，它只认识一个统一的 `tool_calls` 列表。**



所谓 `invalid_tool_calls`，纯粹是 LangChain 在 Python 内存里解析 JSON 失败后**人造出来的抽象**。



当 LangChain 重新把包含这两个属性的 `AIMessage` 序列化并发送给大模型 API 时，API 接收到的工具调用顺序取决于 **LangChain 走的是哪条序列化分支**。



### 一、API 接收顺序的两种现实分支

在 LangChain 内部（以 `ChatOpenAI._convert_message_to_dict` 为代表），将 `AIMessage` 转换为发给 API 的 JSON Payload 时，逻辑分为两种情况：



#### 分支 1：如果保留了原始报文（未经历序列化脱落）

如果消息还在当前运行时的内存中，`message.additional_kwargs["tool_calls"]` 依然完好：



- LangChain 会**直接提取原始报文字典**发给 API。
- **API 实际接收到的顺序**：**100% 保持大模型最初生成的原生交替顺序**。
- 此时在 API 看来，合法的和损坏的都混在同一个 `tool_calls` 数组里，因为在 HTTP 协议中，所有参数本质上都是一串 `arguments: "..."` 字符串。

#### 分支 2：如果原始报文丢失（经历持久化恢复或属性重建）

如果 State 经过了序列化持久化（如存储到 Redis/PostgreSQL 时只存了规范化字段），或者由你手动实例化了 `AIMessage`，`additional_kwargs` 为空：



- LangChain 只能依靠两个 Python 属性强行逆向拼装出 API 所需的 `tool_calls` 列表：

  Python

  ```
  # LangChain 底层拼装逻辑
  formatted_tool_calls = [
      _format_tool_call(tc) for tc in message.tool_calls
  ] + [
      _format_invalid_tool_call(itc) for itc in message.invalid_tool_calls
  ]
  ```

- **API 实际接收到的顺序**：**全部合法的排在前面，全部非法的排在后面**（被粗暴切成了两截）。

### 二、这会导致什么“上下文错位”隐患？

一旦发生了**分支 2**（API 收到的是“有效在前、无效在后”），而你的门禁代码写的是：



Python

```
# 你的门禁先处理 invalid，后处理 valid
for ic in invalid_calls:
    error_messages.append(...)
for tc in tool_calls:
    error_messages.append(...)
```

在送给大模型的下一个 Prompt 里，物理 Token 的排列就会变成这样：



Plaintext

```
[AIMessage (发给 API 的序列化结果)]
  └─ tool_calls[0]: 查询A (有效)      <-- 索引 0
  └─ tool_calls[1]: 查询B (有效)      <-- 索引 1
  └─ tool_calls[2]: 写入C (参数损坏)   <-- 索引 2

[接下来的 ToolMessage 列表 (你的门禁返回的结果)]
  ├─ ToolMessage(id=写入C, Error)    <-- 索引 0
  ├─ ToolMessage(id=查询A, Cancelled)<-- 索引 1
  └─ ToolMessage(id=查询B, Cancelled)<-- 索引 2
```

虽然由于 `tool_call_id` 存在，OpenAI 的网关**不会报 400 校验错误**，但从模型的视角来看：

**上文的调用链是 `[A, B, C]`，紧随其后的响应链却是 `[C, A, B]`。**

这会在上下文中造成一次物理层面的“时序对对碰”，逼迫 Transformer 的注意力头必须跨位置跳转去把结果匹配起来。



### 三、工程上的终极对齐方案

为了彻底消灭这种“上文是一个顺序，下文又是另一个顺序”的撕裂感，你在生成 `ToolMessage` 时，**应该主动去匹配 `AIMessage` 即将被序列化出去的真实物理顺序**。



只需在门禁节点（Gate Node）中使用一个简单的保序对齐函数：



Python

```
from langchain_core.messages import AIMessage, ToolMessage

def align_tool_messages_with_ai_message(
    last_message: AIMessage,
    error_map: dict[str, str], # tool_call_id -> 错误/取消原因
) -> list[ToolMessage]:
    """
    智能探查 AIMessage 即将被发送给 API 时的物理 tool_calls 顺序，
    保证生成的 ToolMessage 列表与之严格 1:1 物理顺序对齐。
    """
    aligned_tool_messages = []
    
    # 1. 尝试探查是否有原始报文（分支 1：原生交替顺序）
    raw_calls = last_message.additional_kwargs.get("tool_calls", [])
    if isinstance(raw_calls, list) and raw_calls:
        for raw in raw_calls:
            c_id = raw.get("id")
            name = raw.get("function", {}).get("name", "unknown")
            if c_id in error_map:
                aligned_tool_messages.append(
                    ToolMessage(
                        tool_call_id=c_id,
                        name=name,
                        content=error_map[c_id],
                        status="error"
                    )
                )
        if aligned_tool_messages:
            return aligned_tool_messages

    # 2. 如果没有原始报文（分支 2：LangChain 会按 valid + invalid 拼接发给 API）
    # 那么我们的 ToolMessage 也严格按 valid 紧跟 invalid 的顺序组装！
    for tc in getattr(last_message, "tool_calls", []):
        c_id = tc["id"]
        if c_id in error_map:
            aligned_tool_messages.append(
                ToolMessage(
                    tool_call_id=c_id,
                    name=tc.get("name", "unknown"),
                    content=error_map[c_id],
                    status="error"
                )
            )
            
    for ic in getattr(last_message, "invalid_tool_calls", []):
        c_id = ic.get("id")
        if c_id in error_map:
            aligned_tool_messages.append(
                ToolMessage(
                    tool_call_id=c_id,
                    name=ic.get("name", "unknown"),
                    content=error_map[c_id],
                    status="error"
                )
            )

    return aligned_tool_messages
```

通过这种写法：



1. **如果底层保留了原始报文**：你的 `ToolMessage` 序列与 API 接收的 `AIMessage.tool_calls` 原生顺序完全一致；
2. **如果底层已经降级成了“有效在前、无效在后”**：你的 `ToolMessage` 也会自适应变成“有效在前、无效在后”；
3. 上文与下文在物理 Token 序列上始终是**正向严格平行的**，大模型的因果注意力衰减降到最低。