### 问：我在学习langchain、langgraph，其中涉及到了工具调用，我想了解的是通过langchain的@tool注解定义了工具函数且通过args_schema参数赋值了pydantic模型，那么是否支持对模型输出的tool_call进行工具调用参数格式预检呢？

**支持，并且这是内置且默认的行为。**



当使用 `@tool(args_schema=...)` 绑定 Pydantic 模型时，LangChain 会将该工具封装为 `StructuredTool`。无论是在纯 LangChain 还是 LangGraph 中，**在进入你的工具函数主体之前，系统都会先通过 Pydantic 对模型输出的 `tool_call["args"]` 进行严格的格式与类型校验**。如果校验不通过，函数根本不会被执行。



### 一、 核心工作机制

1. **自动拦截（Pre-validation）：**

   当调用 `tool.invoke(tool_call["args"])` 时，LangChain 内部会直接调用 Pydantic 的校验方法（Pydantic v2 的 `model_validate`）。若模型输出的参数类型错误、缺少必填字段或未通过自定义验证器（`field_validator`），会立即抛出 `pydantic.ValidationError`。

2. **函数免受脏数据污染：**

   通过预检后，传入你自定义函数的参数已经是经过类型转换与清洗的合法数据（如字符串数字转为了 `int`，日期字符串转为了 `datetime` 等）。

### 二、 在 LangGraph 中的两种实践方式

在实际应用中，单纯“抛出异常中断”通常不是最佳策略。我们通常希望**捕获校验错误并将错误信息回传给模型，触发自我修正（Self-Correction）**，或者**在单独的预检节点进行前置分流**。



#### 1. 配合 `ToolNode` 实现“校验失败 -> 自动纠错”闭环（最推荐）

LangGraph 预置的 `ToolNode` 提供了 `handle_tool_errors` 参数。当参数未通过 Pydantic 校验时，它会捕获异常并把详细错误构造成 `ToolMessage` 返回给模型，提示模型重新生成格式正确的调用。



Python

```
from typing import Annotated
from pydantic import BaseModel, Field, field_validator
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

# 1. 定义参数 Schema
class SearchQuerySchema(BaseModel):
    query: str = Field(description="搜索关键词")
    limit: int = Field(default=5, ge=1, le=20, description="返回数量，必须在 1 到 20 之间")

    @field_validator("query")
    def no_empty_query(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query 不能为空字符串")
        return v

# 2. 定义工具并绑定 args_schema
@tool(args_schema=SearchQuerySchema)
def search_database(query: str, limit: int = 5) -> str:
    """搜索内部数据库内容"""
    return f"检索 '{query}' 成功，返回 {limit} 条结果。"

# 3. 在 LangGraph 中配置 ToolNode
# 设置 handle_tool_errors=True 会捕获 ValidationError 并返回给模型提示其重试
tool_node = ToolNode(
    tools=[search_database],
    handle_tool_errors=True  # 也可以传自定义处理函数: def custom_handler(e: Exception) -> str
)
```

当大模型输出了错误的参数（例如 `{"query": "LangGraph", "limit": 50}`）时：



- `search_database` 不会执行。
- `ToolNode` 自动生成一个包含 `Input should be less than or equal to 20` 的 `ToolMessage`。
- 图将消息反馈给大模型，模型读取错误原因后即可调整参数重新调用。

#### 2. 自定义独立预检节点（Pre-check Node）

如果你想在工具执行前做拦截分流（例如：格式错误不计入工具调用次数、直接人工审核、或进行权限校验），可以直接利用 `tool.args_schema` 进行手动预检：



Python

```
from pydantic import ValidationError
from langchain_core.messages import ToolMessage
from langgraph.graph import MessagesState

tools_by_name = {"search_database": search_database}

def pre_check_node(state: MessagesState):
    """在进入 ToolNode 之前对所有的 tool_calls 进行静态预检"""
    last_message = state["messages"][-1]
    validation_messages = []
    
    for tool_call in last_message.tool_calls:
        tool_instance = tools_by_name.get(tool_call["name"])
        if not tool_instance:
            continue
            
        # 直接使用绑定的 args_schema 进行预检
        try:
            tool_instance.args_schema.model_validate(tool_call["args"])
        except ValidationError as e:
            # 捕获预检错误，直接构造拦截返回消息
            validation_messages.append(
                ToolMessage(
                    content=f"参数预检失败，请修正后重试。详情: {e.errors()}",
                    tool_call_id=tool_call["id"],
                    name=tool_call["name"],
                )
            )
            
    # 如果有校验失败的调用，返回错误消息；否则进入正常的工具执行
    return {"messages": validation_messages}
```

### 总结

- **原生支持：** `@tool(args_schema=...)` 会默认进行 Pydantic 格式与业务规则预检。
- **防护生效点：** 校验发生在工具函数真正执行之前，非法数据无法侵入函数内部。
- **工程最佳实践：** 在 LangGraph 中搭配 `ToolNode(tools, handle_tool_errors=True)`，既能保证输入参数绝对安全，又能利用错误回显让大模型实现自主纠错。









