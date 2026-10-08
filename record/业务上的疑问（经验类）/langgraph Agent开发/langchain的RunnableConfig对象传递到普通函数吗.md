```python
async def call_model(state: OverAllState, config: RunnableConfig):

    # 获取当前的消息列表
    messages = state["messages"]
    # 动态从 config 中获取当前用户的个性化配置（若无则取 None，走系统默认）
    configurable = config.get("configurable", {})
    user_provider = configurable.get("provider")
    user_api_key = configurable.get("api_key")
    user_base_url = configurable.get("base_url")
    user_model_name = configurable.get("model_name")
    model = llm_client_manager.get_model(
        provider=user_provider,
        api_key=user_api_key,
        base_url=user_base_url,
        model_name=user_model_name
    )
    model_with_tools = model.bind_tools(tools)
    response = await model_with_tools.ainvoke(messages, config)
    return {"messages": [response]}
```

### 问：我在学习构建langgraph Agent，这是其中的一个节点，我的疑问主要是我打算将：

###  response = await model_with_tools.ainvoke(messages, config)，

### 单独用一个函数来封装，因为想要增添很多的处理步骤。这种情况下，config还能生效吗，包括graph.astream方法的messages模式能够接收到模型输出吗？我担心放到节点外面就失效了

**完全不会失效。** 只要显式将 `config` 传递给封装的外部函数，并在调用模型的 `.ainvoke()` 时带上它，个性化配置与 `graph.astream(..., stream_mode="messages")` 的流式输出都能正常工作。



### 原理解析：为什么不会失效？

1. **`config` 的传递本质是对象引用**

   `config`（`RunnableConfig`）本质上是一个标准的 Python 字典。将其作为形参传给任何外部函数、并在内部调用 `model.ainvoke(messages, config)`，执行环境完全一致，`configurable` 中的 API Key、Model Name 等参数能照常读取。

2. **`stream_mode="messages"` 依赖 Callback 而非节点层级**

   LangGraph 的 `stream_mode="messages"` **不是通过监听节点函数的返回值实现的**，而是通过预先注入在 `config["callbacks"]` 中的回调处理器，捕获模型底层实时触发的 `on_chat_model_stream` 事件。

   因此，只要调用链底层执行 `model.ainvoke(..., config)` 时带上了包含这些 callbacks 的 `config`，LangGraph 的流式监听器就能收到 Token，不论调用发生在节点内、封装函数内还是深层子函数中。

### 代码重构示例

将模型创建与调用逻辑拆分为独立的辅助函数：



Python

```
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from typing import List

# 1. 独立封装的调用函数（支持添加前置/后置处理步骤）
async def run_model_pipeline(
    messages: List[BaseMessage],
    config: RunnableConfig,
    tools: list
) -> BaseMessage:
    # --- [前置处理] 例如：消息修剪、安全过滤、Prompt 增强 ---
    processed_messages = messages  # 在此加入你的前置逻辑

    # 从 config 中提取动态配置
    configurable = config.get("configurable", {})
    user_provider = configurable.get("provider")
    user_api_key = configurable.get("api_key")
    user_base_url = configurable.get("base_url")
    user_model_name = configurable.get("model_name")

    model = llm_client_manager.get_model(
        provider=user_provider,
        api_key=user_api_key,
        base_url=user_base_url,
        model_name=user_model_name
    )
    model_with_tools = model.bind_tools(tools)

    # 关键点：必须显式将 config 传给 ainvoke
    response = await model_with_tools.ainvoke(processed_messages, config)

    # --- [后置处理] 例如：内容脱敏、自定义元数据注入、结构校验 ---
    # processed_response = custom_post_process(response)

    return response


# 2. LangGraph 节点函数保持极简
async def call_model(state: OverAllState, config: RunnableConfig):
    messages = state["messages"]
    
    # 显式透传 config
    response = await run_model_pipeline(
        messages=messages, 
        config=config, 
        tools=tools
    )
    
    return {"messages": [response]}
```

### 关键注意事项

- **“后处理”与“流式输出”的时序差异**

  `stream_mode="messages"` 监听到的是 LLM 实时吐出的 **原始 Token**。如果在封装函数中对 `response` 进行了后置修改（如脱敏、纠错、格式化），前端在流式生成阶段看到的仍是模型生成的**原始内容**，只有在图执行完毕、读取 State 时才会拿到修改后的最终结果。

- **显式传递不可省略**

  在异步函数调用中，虽然 LangChain 内部尝试通过 Python `contextvars` 传递上下文，但在某些复杂嵌套或跨协程场景下隐式传递可能断裂。始终**显式传参**（`await model.ainvoke(messages, config)`）是保证 Callbacks 和 Tracing 稳定的最佳实践。

- **LangSmith 链路追踪层级**

  如果使用了 LangSmith，且希望将封装函数本身也作为一个独立的追踪 Span 记录在调用链树上，可以在封装函数上添加 `@chain` 装饰器：

  Python

  ```
  from langchain_core.runnables import chain
  
  @chain
  async def run_model_pipeline(inputs: dict, config: RunnableConfig):
      # ...
  ```