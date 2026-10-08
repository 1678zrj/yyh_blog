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