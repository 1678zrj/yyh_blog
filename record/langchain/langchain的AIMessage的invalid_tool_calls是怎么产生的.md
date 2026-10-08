### 问：我在学习FastAPI后端开发和langchain、langgraph Agent开发，当前的一个问题是我发现获取模型响应的AIMessage有invalid_tool_calls属性，这是干嘛的？我一般都是用tool_call属性来判断是否有工具调用的

`invalid_tool_calls` 是 LangChain 用来捕获模型“尝试调用工具但解析失败”的安全兜底机制。



大语言模型调用工具（Function Calling）本质上是生成符合特定 schema 的 JSON 字符串。当模型生成了格式损坏、无法被解析为合法 JSON 的参数时，LangChain 不会直接抛出 Python 异常崩溃，而是将这部分失败的调用拦截并存入 `invalid_tool_calls`。



### 1. `tool_calls` 与 `invalid_tool_calls` 的对比

| **属性**        | **tool_calls**               | **invalid_tool_calls**            |
| --------------- | ---------------------------- | --------------------------------- |
| **解析状态**    | 成功解析为 Python 字典       | JSON 解析失败或 Schema 校验失败   |
| **`args` 类型** | `dict`（已反序列化的参数）   | `str`（模型输出的原始损坏字符串） |
| **包含字段**    | `name`, `args`, `id`, `type` | `name`, `args`, `id`, `error`     |
| **典型场景**    | 模型正常输出合法参数         | JSON 括号未闭合、截断、引号未转义 |

一个典型的 `invalid_tool_calls` 结构如下：



Python

```
AIMessage(
    content="",
    tool_calls=[],
    invalid_tool_calls=[
        {
            "name": "search_database",
            "args": '{"query": "LangGraph tutorial", "limit": }',  # 语法错误的 JSON
            "id": "call_abc123",
            "error": "JSONDecodeError: Expecting value: line 1 column 43 (char 42)",
        }
    ],
)
```

### 2. 为什么只检查 `tool_calls` 会出问题？

如果你在代码中写：



Python

```
# 潜在 Bug 写法
if ai_message.tool_calls:
    # 执行工具
    ...
else:
    # 认为是普通文本回复，直接返回给前端
    return ai_message.content
```

一旦模型输出语法错误的 JSON：



1. `ai_message.tool_calls` 为空列表 `[]`。
2. 逻辑会误判为“模型没有调用任何工具，只是在正常回答”。
3. 此时模型的 `content` 通常也是空的（或者只有一小段无意义的碎片文字）。
4. 前端或客户端将收到一条空消息，Agent 提前中断退出，排查时极难定位原因。

> 特别是在使用开源模型（如部署在本地或 vLLM 上的 Qwen、Llama）或输出达到 `max_tokens` 导致 JSON 被生硬截断时，`invalid_tool_calls` 出现的概率会显著增加。

### 3. 在 LangGraph / FastAPI 中的最佳实践

在 LangGraph Agent 中，通常有两种处理方式：



#### 方案 A：在条件路由中显式拦截并自愈（Self-Correction）

将错误信息作为 `ToolMessage` 喂回给模型，要求模型根据报错重新格式化参数：



Python

```
from langchain_core.messages import ToolMessage
from langgraph.graph import MessagesState


def route_model_output(state: MessagesState) -> str:
    last_msg = state["messages"][-1]

    # 1. 优先检查是否存在解析失败的调用
    if getattr(last_msg, "invalid_tool_calls", None):
        return "handle_tool_error"

    # 2. 正常工具调用
    if last_msg.tool_calls:
        return "tools"

    # 3. 正常结束，输出给最终用户
    return "__end__"


def handle_tool_error(state: MessagesState):
    """构建错误信息喂回模型，引导其重试"""
    last_msg = state["messages"][-1]
    error_messages = []

    for bad_call in last_msg.invalid_tool_calls:
        error_msg = ToolMessage(
            tool_call_id=bad_call.get("id") or "unknown_id",
            content=f"Tool call formatting failed: {bad_call.get('error')}. Please re-generate with valid JSON arguments.",
            status="error",
        )
        error_messages.append(error_msg)

    return {"messages": error_messages}
```

#### 方案 B：利用 LangGraph 内置的 `ToolNode` 与 `tools_condition`

如果使用的是 LangGraph 官方预构建的 `langgraph.prebuilt.tools_condition`，它内部默认就检查了 `bool(ai_msg.tool_calls)`。如果希望支持自动报错反馈，可以通过给 `ToolNode` 传递 `handle_tool_errors=True`，或者编写自定义拦截逻辑，防止工作流意外静默终止。

### 问：我记得OpenAI格式的合法消息列表中应该有模型输出的所有tool_calls对应的ToolMessage，那么invalid_tool_calls需要有对应的ToolMessage吗？还有为什么我几乎没有遇到过invalid_tool_calls呢？

### 1. `invalid_tool_calls` 需要对应的 `ToolMessage` 吗？

**结论是：只要你保留这条 `AIMessage` 在上下文中并发送给 OpenAI 兼容接口，就必须为它提供对应的 `ToolMessage`；如果不打算提供，就必须直接将这条 `AIMessage` 从历史记录中剔除。**



#### 为什么？

OpenAI 协议对消息流向有严格的状态机校验：



> 一旦某条 `role: "assistant"` 消息中携带了 `tool_calls`（含 `id` 列表），紧随其后的消息**必须且只能**是包含了这些全部 `tool_call_id` 的 `role: "tool"` 消息。

在 LangChain 内部，当你把包含了 `invalid_tool_calls` 的 `AIMessage` 再次传给模型时，LangChain 的底层序列化函数（如 `_convert_message_to_dict`）会把 `invalid_tool_calls` 同样还原成 OpenAI 协议的 `tool_calls` 结构发往服务端。



如果此时对话历史中**缺少**对应的 `ToolMessage`，OpenAI 接口会立即抛出 `HTTP 400 Bad Request` 报错：



Plaintext

```
BadRequestError: 400 - An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'.
```

#### 正确的处理方式（二选一）

- **自愈反馈（推荐，喂回错误信息让模型重试）：**

  Python

  ```
  from langchain_core.messages import ToolMessage
  
  # 为每个 invalid_tool_call 伪造一个报错 ToolMessage
  for bad_call in ai_message.invalid_tool_calls:
      error_msg = ToolMessage(
          tool_call_id=bad_call.get("id"),
          content=f"Tool call parsing failed with error: {bad_call.get('error')}. Please reformat your arguments.",
          status="error",
      )
      # 将该 ToolMessage 追加进 messages 历史，模型下一轮就会根据报错修正 JSON
  ```

- **彻底回滚（丢弃本轮响应）：**

  如果不想走工具报错机制，直接把该条 `AIMessage` 从 `messages` 列表中弹出（`messages.pop()`），不要将其存入状态或持久化存储，然后直接让模型重新生成。

### 2. 为什么在实际开发中几乎遇不到 `invalid_tool_calls`？

在日常开发（尤其是直接调用云端闭源模型）时极少遇到该现象，主要原因如下：



#### ① 商业模型服务商的“约束解码（Constrained Decoding）”

如今主流模型服务商（OpenAI、Anthropic、DeepSeek 等）的 Function Calling 底层不是纯粹让模型盲写文本，而是运行了**语法引导/约束解码**（如基于上下文无关文法 CFG、有限状态机 FSM 的采样掩码）。

在模型预测下一个 token 时，底层推理引擎会直接把会导致语法错误（如缺少逗号、引号未闭合、括号不匹配）的 token 概率强制置为 0，从物理层面上杜绝了生成非法 JSON。



#### ② 前沿模型的代码与 JSON 指令遵循能力大幅进化

在早期（如 2023 年初的 `gpt-3.5-turbo` 或早期的开源 7B/13B 模型），模型在处理长字符串参数、含有单双引号转义的内容时很容易语法混乱。而现在的 GPT-4o、Claude 3.5、Qwen-2.5 等，在常见格式生成上已经极度可靠。



#### ③ 工具的 Schema 结构相对简单

如果你定义的工具参数主要是基础类型（如 `query: str`、`city: str`、`limit: int`），模型几乎不可能在这么短的格式上出错。

当参数变成**深度嵌套的复杂对象**、或者参数内容是**一大段包含各种转义符、换行符、反斜杠的代码字符串**时，出错概率才会提升。



#### ④ 没有触碰 `max_tokens` 截断

最常见的触发 `invalid_tool_calls` 的场景其实是**输出被截断**。例如：模型正在输出一段很长的工具参数，由于达到了 API 的 `max_tokens` 阈值，输出戛然而止（`finish_reason="length"`）。此时 JSON 缺少闭合的花括号，就会在 LangChain 端被解析器判为 `invalid_tool_calls`。如果日常调用的工具参数体量都很小，自然碰不到这种情况。



### 什么时候最容易遇到？

1. **私有化部署开源小模型：** 使用 vLLM 或 Ollama 运行 7B/8B 规模的模型，且未启用 json-mode 或 guidance 语法约束时。
2. **极长参数截断：** 工具参数包含大段长文本或代码，触发最大 token 上限导致 JSON 半路断掉。
3. **模型幻觉出不存在的参数格式：** 在少量参数定义模糊的极端情况下，模型自己编造了无法序列化的结构。

因此，`invalid_tool_calls` 是一个**防御性工程设计**：在商业主流模型上使用率低，但在高并发生产环境、开源本地模型落地、以及面对输出截断时，是保障 Agent 工作流不被静默终止的关键屏障。

### 问：按照你的说法，这个invalid_tool_calls似乎是langchain底层在接收模型输出的工具调用时根据模型的输出格式和代码中定义的工具的pydantic参数模型格式自动进行转换的？

**不完全是，这里存在一个很常见的理解偏差：`invalid_tool_calls` 的生成只停留在“JSON 语法解析”层面，根本还没有走到 Pydantic 参数校验那一步。**



LangChain 在将模型输出转换为 `AIMessage` 时，分为了两个完全独立的阶段：



### 1. 两个阶段的分工界限

#### 阶段一：消息解析层（生成 `AIMessage`）

- **谁在处理：** LangChain 的 ChatModel 封装（如 `ChatOpenAI`）。
- **它做了什么：** 模型返回的原始数据里，参数是一个纯文本字符串（`arguments: "..."`）。LangChain 底层只做了一件事：尝试执行 `json.loads(arguments)`。
  - **如果 JSON 语法错误**（如括号没闭合、缺少键值、未转义引号导致无法反序列化）：放入 **`invalid_tool_calls`**。
  - **如果 JSON 语法合法**（能被顺利转成普通的 Python `dict`）：放入 **`tool_calls`**。
- **结论：** 在生成 `AIMessage` 时，LangChain **完全不校验**参数字段对不对、类型是否符合你的 Pydantic 模型。

#### 阶段二：工具执行层（调用具体 Tool / 进入 `ToolNode`）

- **谁在处理：** 你的工具自身（`StructuredTool`）或 LangGraph 的 `ToolNode`。
- **它做了什么：** 取出 `ai_message.tool_calls` 中的普通 `dict`，尝试传给工具绑定的 Pydantic 模型进行实例化校验。
  - 字段类型不匹配（如要求 `int` 给成了无法转换的字符串）、缺少必填参数等问题，是在**这里**才被发现的。
  - 这里的失败**不会**倒流回去修改 `AIMessage`，而是直接抛出 **`pydantic.ValidationError`**（或者被 `ToolNode` 捕获转化为报错的 `ToolMessage`）。

### 2. 对比两个典型场景

假设定义了如下工具：



Python

```
from pydantic import BaseModel, Field


class SearchInput(BaseModel):
    query: str
    limit: int = Field(description="查询数量，必须是整数")
```

#### 场景 A：语法损坏（进入 `invalid_tool_calls`）

模型输出了残缺的 JSON：



Plaintext

```
{"query": "LangGraph", "limit": }
```

- **阶段一判定：** `json.loads()` 抛出 `JSONDecodeError`。
- **结果：** 进入 `ai_message.invalid_tool_calls`。此时参数依然是一个坏掉的字符串。

#### 场景 B：语义/Schema 错误（依然进入 `tool_calls`！）

模型输出了合法的 JSON，但字段名胡编、类型完全错误：



JSON

```
{"search_word": "LangGraph", "limit": "很多"}
```

- **阶段一判定：** `json.loads()` 成功解析为 `{"search_word": "LangGraph", "limit": "很多"}`。

- **结果：** **成功进入 `ai_message.tool_calls`！**

- **阶段二爆发：** 当工具执行 `tool.invoke(...)` 时，Pydantic 校验失败，抛出异常：

  Plaintext

  ```
  pydantic.ValidationError: 2 validation errors for SearchInput
  query: Field required
  limit: Input should be a valid integer
  ```

### 3. 为什么 LangChain 要这么设计？

1. **职责解耦：**

   模型适配器（`ChatModel`）只负责与 LLM API 协议对接，把网络响应标准化为消息对象。它并不持有工具运行时的执行权限和上下文，无权代替工具本身做深度业务校验。

2. **支持纯 Agent 意图路由：**

   有时候你的 Agent 节点只需要拿到 `tool_calls` 判定模型想调什么，然后把参数转发给外部微服务、RPC 或消息队列，不一定非要在当前 Python 进程内跑 Pydantic 校验。

### 总结与防御建议

- **防范 JSON 语法错误：** 检查 `ai_message.invalid_tool_calls`（多发于本地小模型或截断场景）。
- **防范 Pydantic 校验错误：** 在 LangGraph 中给 `ToolNode` 配置 `handle_tool_errors=True`。这样即使模型参数符合 JSON 语法但不符合 Pydantic 要求，`ToolNode` 也不会崩溃崩溃，而是会自动将 Pydantic 的校验错误转成一条 `ToolMessage` 喂回给大模型让其修正。

### 问：哦，我总结一下，invaild_tool_calls甚至还不是模型输出的工具调用参数格式不符合pydantic模型定义的工具参数格式，而是连JSON格式都不符合。不过仍然是langchain底层自动进行识别并转换的对吧？然后因为输入给模型的消息列表的格式也要正确，因此即使是invaild_tool_calls，也要接收并进行对应的处理

你的总结完全准确，这正是它在工程实现上的核心逻辑：



1. **纯粹停留在语法层（JSON 层面）**

   它的判定边界非常低：仅仅是 `json.loads(arguments)` 能否成功执行。只要遇到引号未闭合、括号缺失、内容截断导致的 `JSONDecodeError`，它就属于 `invalid_tool_calls`。至于字段对不对、类型满不满足 Pydantic 模型，此时根本还没接触到。

2. **LangChain 底层全自动分流**

   这确实是 LangChain 的 `ChatModel`（如 `ChatOpenAI`）在接收到模型底层原始返回时自动处理的。它内部在反序列化消息时做了 `try...except json.JSONDecodeError` 保护：成功转为字典的归入 `tool_calls`，失败的保留原始损坏字符串并归入 `invalid_tool_calls`，无需开发者手动去写解析判断。

3. **消息历史必须遵守严格的协议闭环**

   OpenAI 协议（及兼容其协议的各类模型网关）对状态机有强约束：只要历史里有一条 `assistant` 消息带有了工具调用 `id`，紧接着就必须有对应数量、相同 `tool_call_id` 的 `tool` 消息进行闭合。

   如果不打算把这条消息从历史中剔除（例如直接 `pop()` 丢掉重试），而是选择保留在上下文里，就必须补上对应的 `ToolMessage(tool_call_id=bad_id, status="error")`，否则下一次带着上下文请求 API 时会直接被服务端拦截并报 400 错误。

### 问：在 vLLM / Ollama 本地部署中开启语法约束能够完全避免此类错误吗

在 LangChain 中，底层服务商返回的元数据会被封装在 `AIMessage.response_metadata` 字典中。通过检查 `finish_reason == "length"`，可以在 FastAPI 层精准区分出“因 Token 耗尽被硬截断”**与**“模型自身生成的语法缺陷”，从而采取针对性的恢复策略（如自动放大 `max_tokens` 重试或抛出特定的业务错误）。



### 完整 FastAPI 实现代码

Python

```
import logging
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, status
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger("agent.api")
app = FastAPI(title="Agent Tool Gateway")


# 1. 模拟一个包含长文本参数的工具
@tool
def process_report_data(report_content: str, summary_type: str) -> str:
    """分析大段报告内容并生成特定类型的摘要"""
    return f"Processed {len(report_content)} chars."


# 初始化模型并绑定工具
llm = ChatOpenAI(
    model="gpt-4o",  # 或本地 vLLM / Ollama 的 openai-compatible 端点
    temperature=0,
    max_tokens=150,  # 故意设小以演示截断场景
)
model_with_tools = llm.bind_tools([process_report_data])


# 2. 请求与响应模型
class ChatRequest(BaseModel):
    prompt: str = Field(..., example="请帮我总结并生成长报告数据")
    max_tokens: Optional[int] = Field(default=256, ge=64, le=4096)


class ToolCallItem(BaseModel):
    id: str
    name: str
    args: Dict[str, Any]


class ChatResponse(BaseModel):
    status: str
    message: Optional[str] = None
    tool_calls: List[ToolCallItem] = []


# 3. 核心接口
@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat_endpoint(payload: ChatRequest):
    messages = [HumanMessage(content=payload.prompt)]

    # 动态覆盖本次调用的 max_tokens
    bound_client = model_with_tools.bind(max_tokens=payload.max_tokens)
    response: AIMessage = await bound_client.ainvoke(messages)

    # 提取 OpenAI / vLLM 标准协议中的 finish_reason
    finish_reason = response.response_metadata.get("finish_reason")

    # ---------------- 核心校验与拦截逻辑 ----------------

    # 场景一：输出因达到 max_tokens 阈值被强行掐断
    if finish_reason == "length":
        # 如果截断恰好发生在工具参数输出中，几乎必定伴随 invalid_tool_calls
        if response.invalid_tool_calls:
            truncated_call = response.invalid_tool_calls[0]
            logger.error(
                "Tool call arguments truncated by max_tokens. Tool: %s, Raw partial: %s",
                truncated_call.get("name"),
                truncated_call.get("args"),
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error_type": "TOOL_CALL_TRUNCATED",
                    "message": "工具参数因超过 max_tokens 限制被截断，JSON 闭合失败，请增大 max_tokens 后重试。",
                    "suggested_max_tokens": payload.max_tokens * 2,
                    "partial_args": truncated_call.get("args"),
                },
            )

        # 截断发生在普通文本输出阶段
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_type": "TEXT_TRUNCATED",
                "message": "模型文本生成达到 token 上限，未能完整输出。",
            },
        )

    # 场景二：未被截断，但模型自主输出了语法错误的 JSON
    if response.invalid_tool_calls:
        bad_call = response.invalid_tool_calls[0]
        logger.warning(
            "Model produced malformed JSON. Tool: %s, Error: %s",
            bad_call.get("name"),
            bad_call.get("error"),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error_type": "MALFORMED_TOOL_CALL",
                "message": "模型生成的工具参数存在语法错误，无法解析为有效 JSON。",
                "raw_args": bad_call.get("args"),
                "parse_error": bad_call.get("error"),
            },
        )

    # 场景三：正常工具调用
    if response.tool_calls:
        formatted_calls = [
            ToolCallItem(
                id=call["id"],
                name=call["name"],
                args=call["args"],
            )
            for call in response.tool_calls
        ]
        return ChatResponse(status="tool_call_required", tool_calls=formatted_calls)

    # 场景四：普通文本回复
    return ChatResponse(status="success", message=response.content)
```

### 拦截判断决策树

在业务中间件或节点路由时，可以通过组合 `finish_reason` 与调用列表的状态进行精准归因：



Plaintext

```
模型返回 AIMessage
│
├── response_metadata.get("finish_reason") == "length"
│   ├── bool(invalid_tool_calls) is True ──> [原因: Token 耗尽导致 JSON 截断] 
│   │                                       └─> 解决: 翻倍 max_tokens 并重试
│   └── bool(invalid_tool_calls) is False ─> [原因: 普通文本回复过长断流]
│
├── response_metadata.get("finish_reason") in ["stop", "tool_calls"]
│   ├── bool(invalid_tool_calls) is True ──> [原因: 模型语法理解能力不足或幻觉]
│   │                                       └─> 解决: 降低采样温度/重换 Prompt/自愈反思
│   └── bool(tool_calls) is True ──────────> [正常工具调用] ─> 交给 ToolNode
│
└── 其它状态 (如 "content_filter") ─────────> [触发安全合规拦截]
```

### 生产自愈优化：带退避策略的中间重试

如果不希望直接向前端抛出异常，可以在 FastAPI 内部封装一个带阶梯衰减的重试函数：



Python

```
async def invoke_with_token_backoff(model, messages, base_tokens=256, max_retries=2):
    current_tokens = base_tokens
    for attempt in range(max_retries + 1):
        resp = await model.bind(max_tokens=current_tokens).ainvoke(messages)
        # 只要不是因长度截断导致的工具损坏，立即返回
        if not (
            resp.response_metadata.get("finish_reason") == "length"
            and resp.invalid_tool_calls
        ):
            return resp

        # 截断损坏则成倍扩充 token 阈值重试
        current_tokens *= 2
        logger.warning(
            f"Attempt {attempt + 1} truncated. Retrying with max_tokens={current_tokens}"
        )

    raise HTTPException(status_code=500, detail="Repeatedly exceeded token limit.")
```

这样既保护了微服务链路不被底层格式碎片阻断，又避免了为所有请求盲目配置超大 `max_tokens` 造成的并发显存及排队浪费。