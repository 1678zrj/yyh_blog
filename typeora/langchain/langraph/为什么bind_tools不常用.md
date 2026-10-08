## 问：为什么我看到很多基于langraph的deepresearch项目，它们只是用langraph框架去编排工作流，并没有用到bind_tools方法，而是让大模型输出JSON字符串，然后自己再来提取

你观察得非常准确，这种做法确实在许多基于 LangGraph 的 DeepResearch 项目中很常见。出现这种现象的原因主要有以下几点：

------

### 🧠 一、为什么不使用 `bind_tools`？

`bind_tools` 是 LangChain 提供的一种工具调用机制，通常用于 **结构化调用（Structured Tool Calling）**，即让模型直接调用某个工具（函数），然后由框架来自动调度执行。但它也有一些局限性或不适合的场景：

| 原因                                         | 说明                                                         |
| -------------------------------------------- | ------------------------------------------------------------ |
| 🔧 **灵活性不足**                             | 使用 `bind_tools` 时，模型需要按照函数签名准确输出，限制了模型的表达自由。而很多项目需要更复杂或不确定的输出格式（如嵌套 JSON、多个意图、策略决策等），这时候直接输出 JSON 更自由。 |
| 🧪 **无法应对复杂 reasoning 和迭代计划**      | 在 DeepResearch 或多轮推理任务中，模型常常需要**先规划多个子任务，再逐步执行**。这种情况下一次调用一个工具不够，框架无法自动处理复杂推理逻辑。直接输出 JSON（如 list of tasks）再人工解析更方便。 |
| 🔍 **可控性强**                               | JSON 输出后，开发者可以手动检查、修改、注入元信息、执行日志追踪等，更容易 debug 和监控每一步工作流状态。 |
| 🔄 **易于多工具组合/链式调用**                | 很多项目会让模型同时规划多个工具调用（例如先 RAG，再 SQL 查询），而 `bind_tools` 往往只支持单个函数调用。用 JSON 方式能表达更多复杂流程。 |
| 🧩 **与 LangGraph 的 Node + Edge 模型更契合** | LangGraph 更像是“状态机 + 有向图”，模型的每一步输出决定下一步执行哪个 node。用 JSON 格式描述动作和参数，正好可以作为跳转逻辑的依据。 |

------

### 🧰 二、常见的做法：让模型输出 JSON 的“计划”，然后手动解析执行

一个典型流程如下：

1. **提示词要求模型输出 JSON 格式的操作计划或工具调用信息**

   ```json
   {
     "action": "query_rag",
     "query": "压裂液黏度的设计原则是什么？"
   }
   ```

2. **LangGraph 中的某个节点（比如 decision node）解析这个 JSON**

   ```python
   def route_node(state):
       model_output = state['messages'][-1].content
       action = json.loads(model_output)["action"]
       if action == "query_rag":
           return "rag_node"
       elif action == "sql_query":
           return "sql_node"
   ```

3. **传参给下一个工具节点，进行实际操作**

------

### ✅ 总结：为什么很多项目更倾向不用 `bind_tools`？

- `bind_tools` 适合简单单步调用、结构清晰、工具参数固定的场景。
- 而实际 DeepResearch、Multi-agent、ReAct 等项目，很多时候模型要做 **自主规划、复杂策略决策、组合工具调用、多轮迭代执行**。
- 用 JSON 格式输出让框架更**透明、灵活、可控、便于调试和扩展**。

------

### 📌 补充建议

如果你在做自己的 Agent 或多工具工作流项目，推荐你：

- 用 prompt 让模型输出结构化 JSON（先规划再执行）
- 用 LangGraph 的 state + 条件边（conditional edges）控制流程跳转
- 工具执行的逻辑不要全依赖于 LangChain 提供的封装，自己掌握更稳

如果你愿意，我可以给你展示一段「JSON 规划 + LangGraph 节点执行」的实际范例代码。要吗？

## 问：好的

好的，我们以一个简化的 **DeepResearch 风格智能体** 为例：

### ✅ 目标

让模型决定是否使用：

- `RAG` 查询工具（从知识库中查资料）
- `SQL` 查询工具（从数据库中查字段）
- 或直接回答

并且我们 **不使用 `bind_tools`**，而是让模型输出一个结构化 JSON，我们再用 LangGraph 来解析这个 JSON 并跳转到相应节点。

------

### 📦 一、定义 LangGraph 的状态结构

```python
from typing import TypedDict, List
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    messages: List[BaseMessage]
    action: str  # 比如 "rag", "sql", "final_answer"
    query: str   # 工具调用需要的内容
    result: str  # 工具调用的结果
```

------

### 🤖 二、模型节点（输出 JSON）

```python
def call_model(state: AgentState) -> AgentState:
    messages = state["messages"]
    
    # 假设你已经封装好了本地 LLM 调用，这里用伪代码代替
    model_output = call_llm(messages)

    # 示例模型输出：
    # {
    #   "action": "rag",
    #   "query": "压裂液黏度的设计原则是什么？"
    # }

    parsed = json.loads(model_output)
    action = parsed.get("action", "unknown")
    query = parsed.get("query", "")
    
    return {
        **state,
        "action": action,
        "query": query,
        "messages": messages + [AIMessage(content=model_output)]
    }
```

------

### 🔄 三、Router 节点：根据模型输出的 JSON 跳转

```python
def router(state: AgentState) -> str:
    action = state["action"]
    if action == "rag":
        return "rag_node"
    elif action == "sql":
        return "sql_node"
    elif action == "final_answer":
        return "final_answer_node"
    else:
        return "fallback_node"
```

------

### 🔍 四、工具节点（例如 RAG 工具）

```python
def rag_tool(state: AgentState) -> AgentState:
    query = state["query"]
    result = rag_search(query)  # 你自己实现的 RAG 查询方法

    return {
        **state,
        "result": result,
        "messages": state["messages"] + [SystemMessage(content=f"RAG结果：{result}")]
    }
```

------

### 💬 五、最终回答节点

```python
def final_answer_node(state: AgentState) -> AgentState:
    result = state["result"]
    # 你可以直接返回，也可以再次通过模型润色
    final_response = f"这是最终答案：{result}"
    return {
        **state,
        "messages": state["messages"] + [AIMessage(content=final_response)]
    }
```

------

### 🧩 六、LangGraph 组图

```python
from langgraph.graph import StateGraph

workflow = StateGraph(AgentState)

workflow.add_node("call_model", call_model)
workflow.add_node("rag_node", rag_tool)
workflow.add_node("sql_node", sql_tool)
workflow.add_node("final_answer_node", final_answer_node)
workflow.add_node("fallback_node", lambda state: {...})

workflow.set_entry_point("call_model")
workflow.add_conditional_edges("call_model", router)

workflow.add_edge("rag_node", "final_answer_node")
workflow.add_edge("sql_node", "final_answer_node")

graph = workflow.compile()
```

------

### 🚀 七、执行

```python
from langchain_core.messages import HumanMessage

initial_state = {
    "messages": [HumanMessage(content="压裂液黏度设计原则有哪些？")]
}

result = graph.invoke(initial_state)
print(result["messages"][-1].content)
```

------

### ✅ 总结

这个方式实现了：

- **LLM 自主决策**（使用哪个工具）
- **输出结构化 JSON**，可解释、可扩展
- **LangGraph 条件跳转**，构建智能工作流
- 不依赖 `bind_tools`，你可以自由定义参数、跳转逻辑、日志收集等

------

如果你想，我可以继续拓展这个例子为：

- 多轮推理（比如模型调用了一个工具后，再根据结果决定下一步）
- 多工具组合（比如先 RAG 后 SQL）
- 加入 retry / fallback 策略（模型输出格式错误时自动重试）

