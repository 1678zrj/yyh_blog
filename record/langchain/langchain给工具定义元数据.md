```
from enum import Enumfrom langchain_core.tools import toolclass ActionType(str, Enum):
    READ = "read"       # 只读/安全操作
    WRITE = "write"     # 修改操作
    EXECUTE = "execute" # 系统级执行class RiskLevel(str, Enum):
    LOW = "low"         # 低风险，直接执行
    MEDIUM = "medium"   # 中风险
    HIGH = "high"       # 高风险，必须人工审核# 1. 学生/所有人可用的只读工具@tool(
    metadata={
        "allowed_roles": ["student", "teacher", "admin"],
        "action_type": ActionType.READ,
        "risk_level": RiskLevel.LOW,
        "require_approval": False
    })def search_course_catalog(query: str) -> str:
    """搜索课程大纲和内容"""
    return f"Found results for: {query}"

```

### 问：上面这段代码中设置工具metadata的方式是否可行》

**不可行，直接运行会抛出报错。**



LangChain 的 `@tool` 装饰器**没有**提供 `metadata` 参数，也不透传未定义的关键字参数。运行此代码时会直接报错：



Plaintext

```
TypeError: tool() got an unexpected keyword argument 'metadata'
```

不过你的**数据结构设计非常优秀**（使用 `str, Enum` 兼具枚举类型约束与 JSON 序列化兼容性）。`BaseTool` 底层本身支持 `metadata` 属性，只需换成以下几种可行的写法：



### 解决方案

#### 方案一：自定义装饰器（推荐：保留原有注解风格）

如果你希望保持 `@tool(...)` 这种声明式写法，只需轻量封装一个自定义装饰器：



Python

```
from functools import wraps
from langchain_core.tools import tool

def secure_tool(metadata: dict = None, **tool_kwargs):
    def decorator(func):
        t = tool(**tool_kwargs)(func)
        if metadata:
            t.metadata = metadata
        return t
    return decorator

# 使用自定义装饰器
@secure_tool(
    metadata={
        "allowed_roles": ["student", "teacher", "admin"],
        "action_type": ActionType.READ,
        "risk_level": RiskLevel.LOW,
        "require_approval": False
    }
)
def search_course_catalog(query: str) -> str:
    """搜索课程大纲和内容"""
    return f"Found results for: {query}"
```

#### 方案二：装饰后直接赋值（改动最小）

`@tool` 生成的对象是 `StructuredTool`（继承自 `BaseTool`），你可以直接修改其 `metadata` 属性：



Python

```
@tool
def search_course_catalog(query: str) -> str:
    """搜索课程大纲和内容"""
    return f"Found results for: {query}"

# 装饰后赋值
search_course_catalog.metadata = {
    "allowed_roles": ["student", "teacher", "admin"],
    "action_type": ActionType.READ,
    "risk_level": RiskLevel.LOW,
    "require_approval": False
}
```

#### 方案三：使用 `StructuredTool.from_function`

`StructuredTool.from_function` 是原生显式支持 `metadata` 参数的工厂方法：



Python

```
from langchain_core.tools import StructuredTool

def search_course_catalog(query: str) -> str:
    """搜索课程大纲和内容"""
    return f"Found results for: {query}"

search_course_catalog_tool = StructuredTool.from_function(
    func=search_course_catalog,
    metadata={
        "allowed_roles": ["student", "teacher", "admin"],
        "action_type": ActionType.READ,
        "risk_level": RiskLevel.LOW,
        "require_approval": False
    }
)
```

#### 方案四：继承 `BaseTool` 强类型定义（适合企业级 RBAC）

如果这是一个严谨的生产级鉴权系统，将权限与风险等级直接作为 Pydantic 字段定义在类上，类型检查和 IDE 提示会更稳健：



Python

```
from typing import List, Type
from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool

class SearchInput(BaseModel):
    query: str = Field(description="搜索关键词")

class CourseCatalogTool(BaseTool):
    name: str = "search_course_catalog"
    description: str = "搜索课程大纲和内容"
    args_schema: Type[BaseModel] = SearchInput

    # 自定义权限控制字段
    allowed_roles: List[str] = ["student", "teacher", "admin"]
    action_type: ActionType = ActionType.READ
    risk_level: RiskLevel = RiskLevel.LOW
    require_approval: bool = False

    def _run(self, query: str) -> str:
        return f"Found results for: {query}"
```

### 下游使用提示

无论使用上述哪种方案，在 Agent 执行前进行**动态工具过滤**或**审批拦截**时都可以统一提取：

Python

```
# 1. 动态过滤出指定角色可用的工具
user_role = "student"
available_tools = [
    t for t in all_tools 
    if user_role in t.metadata.get("allowed_roles", [])
]

# 2. 判断是否触发人工介入 (Human-in-the-loop)
if tool.metadata.get("require_approval") or tool.metadata.get("risk_level") == RiskLevel.HIGH:
    # 挂起流程，等待人工确认
    ...
```

### 问：对于方案四，我感觉比较神奇的是除了

### name: str = "search_course_catalog"

###     description: str = "搜索课程大纲和内容"

###     args_schema: Type[BaseModel] = SearchInput

### 其它属性都自动被放到了metadata中吗

**你的感觉非常敏锐！真相是：并不会自动放进 `metadata`。**



上一轮下游使用示例中的 `t.metadata.get(...)` 是针对方案一、二、三的写法；如果方案四直接运行 `t.metadata.get("allowed_roles")`，会发现 `metadata` 还是 `None`（或报 `AttributeError`）。



### 底层到底发生了什么？

`BaseTool` 本质是一个 **Pydantic Model**（继承自 `pydantic.BaseModel`）。



当你用方案四在类里声明这些变量时：



Python

```
class CourseCatalogTool(BaseTool):
    name: str = "search_course_catalog"
    description: str = "搜索课程大纲和内容"
    args_schema: Type[BaseModel] = SearchInput

    # 这里的属性并没有进 metadata
    allowed_roles: List[str] = ["student", "teacher", "admin"]
    risk_level: RiskLevel = RiskLevel.LOW
    require_approval: bool = False
```

Pydantic 会将它们解析为当前类的 **原生字段（First-class Fields）**。



#### 属性访问对比

| **方案**                        | **存储位置**                | **访问方式**                                                 |
| ------------------------------- | --------------------------- | ------------------------------------------------------------ |
| **方案 1~3**（装饰器/工厂函数） | 存在 `tool.metadata` 字典里 | `tool.metadata["risk_level"]`  或 `tool.metadata.get("risk_level")` |
| **方案 4**（类继承）            | 直接作为类实例的属性        | `tool.risk_level`  或 `getattr(tool, "risk_level")`          |

也就是说，方案四的直接读取方式是：



Python

```
tool = CourseCatalogTool()

# 正确读取方式：直接作为属性读取
print(tool.risk_level)       # 输出: RiskLevel.LOW
print(tool.allowed_roles)    # 输出: ['student', 'teacher', 'admin']

# 此时 metadata 是空的：
print(tool.metadata)         # 输出: None
```

### 如果既想用类继承，又想统一收到 `tool.metadata` 怎么做？

如果你希望统一外部读取逻辑（所有人只通过 `tool.metadata` 获取），有以下两种优雅的写法：



#### 做法 A：在类中显式定义 `metadata` 字段

`BaseTool` 已经声明了 `metadata: Optional[Dict[str, Any]]` 字段，你可以直接在子类中给它赋值：



Python

```
class CourseCatalogTool(BaseTool):
    name: str = "search_course_catalog"
    description: str = "搜索课程大纲和内容"
    args_schema: Type[BaseModel] = SearchInput

    # 直接给 metadata 赋默认字典
    metadata: dict = {
        "allowed_roles": ["student", "teacher", "admin"],
        "action_type": ActionType.READ,
        "risk_level": RiskLevel.LOW,
        "require_approval": False
    }
```

这样外部统一使用 `tool.metadata.get(...)` 就完全兼容了。



#### 做法 B：使用 Pydantic 校验器自动同步进 `metadata`

如果你既想在写代码时享受强类型约束和 IDE 自动补全，又想让下游统一从 `metadata` 中读取，可以用 Pydantic 的 `model_validator` 自动打包：



Python

```
from pydantic import model_validator
from typing import List

class CourseCatalogTool(BaseTool):
    name: str = "search_course_catalog"
    description: str = "搜索课程大纲和内容"
    args_schema: Type[BaseModel] = SearchInput

    # 1. 强类型字段（享受 IDE 提示和校验）
    allowed_roles: List[str] = ["student", "teacher", "admin"]
    action_type: ActionType = ActionType.READ
    risk_level: RiskLevel = RiskLevel.LOW
    require_approval: bool = False

    # 2. 实例化后自动注入进 metadata
    @model_validator(mode="after")
    def sync_to_metadata(self):
        if self.metadata is None:
            self.metadata = {}
        self.metadata.update({
            "allowed_roles": self.allowed_roles,
            "action_type": self.action_type,
            "risk_level": self.risk_level,
            "require_approval": self.require_approval,
        })
        return self
```

使用这种做法，两边都能正常访问：



- `tool.risk_level`（内部开发有类型安全提示）
- `tool.metadata["risk_level"]`（下游中间件/框架统一通过字典读取）

### 问：方案三：使用 StructuredTool.from_function

### 那像工具描述、工具参数、工具名这些属性如何定义呢？

在使用 `StructuredTool.from_function` 时，定义**工具名**、**工具描述**和**工具参数**主要有两种方式：



1. **显式传参（推荐）：** 传入 `name`、`description` 和基于 Pydantic 的 `args_schema`（对大模型提示词最友好、最精准）。
2. **隐式自动推断：** 直接从 Python 函数的**函数名**、**Docstring 文档注释**和类型注解（Type Hints）中自动提取。

### 方式一：显式定义（生产环境最推荐）

大模型进行 Function Calling 时，工具的 `description` 以及各个参数的 `description` 质量直接决定了模型调用的准确率。通过 Pydantic 的 `Field` 可以为每个参数注入详细提示：



Python

```
from typing import Optional, List
from enum import Enum
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

class ActionType(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

# 1. 定义工具参数结构 (args_schema)
class CourseSearchInput(BaseModel):
    query: str = Field(
        description="课程名称、关键词或授课教师名称"
    )
    semester: Optional[str] = Field(
        default=None, 
        description="可选的学期过滤器，例如 '2024-秋季'、'2025-春季'"
    )
    limit: int = Field(
        default=5, 
        description="返回的最大结果条目数，默认为 5"
    )

# 2. 实际执行业务逻辑的普通函数
def search_course_catalog(query: str, semester: Optional[str] = None, limit: int = 5) -> str:
    return f"检索关键词 '{query}' (学期: {semester}, 条数: {limit}) 的结果..."

# 3. 通过 from_function 组装工具
course_search_tool = StructuredTool.from_function(
    func=search_course_catalog,
    
    # --- 核心属性定义 ---
    name="search_course_catalog",  # 工具名称
    description=(                  # 工具功能描述（大模型依据此描述判断何时调用）
        "用于搜索学校课程大纲和内容，支持按课程名、关键词模糊匹配，"
        "并可选按特定学期过滤。"
    ),
    args_schema=CourseSearchInput, # 参数 Schema（指导大模型如何生成参数）
    
    # --- 自定义元数据 ---
    metadata={
        "allowed_roles": ["student", "teacher", "admin"],
        "action_type": ActionType.READ,
        "risk_level": RiskLevel.LOW,
        "require_approval": False
    }
)
```

### 方式二：隐式推断（轻量级写法）

如果你不想额外编写 Pydantic 类，可以省略 `name`、`description` 和 `args_schema`，LangChain 会自动从函数签名与 Docstring 中反射解析：



Python

```
def search_course_catalog(query: str, limit: int = 5) -> str:
    """搜索课程大纲和内容，支持关键词查询。"""
    return f"Found results for: {query}"

course_search_tool = StructuredTool.from_function(
    func=search_course_catalog,
    # 无需显式传 name、description、args_schema
    metadata={
        "allowed_roles": ["student", "teacher", "admin"],
        "action_type": ActionType.READ,
        "risk_level": RiskLevel.LOW,
        "require_approval": False
    }
)
```

#### LangChain 的自动推断规则：

- **`name`**：默认为函数名 `func.__name__`（即 `"search_course_catalog"`）。
- **`description`**：默认为函数的 Docstring `func.__doc__`（即 `"搜索课程大纲和内容，支持关键词查询。"`）。
- **`args_schema`**：根据函数的参数类型注解（`query: str`, `limit: int = 5`）自动在内存中生成一个动态 Pydantic 模型。

### 两种方式的核心属性对照

| **属性**     | **显式指定参数**            | **隐式自动推断来源**               | **作用**                                   |
| ------------ | --------------------------- | ---------------------------------- | ------------------------------------------ |
| **工具名**   | `name="xxx"`                | `func.__name__`                    | 注册给 LLM 的函数名                        |
| **工具描述** | `description="xxx"`         | `func.__doc__` (Docstring)         | 告诉 LLM 工具的用途与触发时机              |
| **参数定义** | `args_schema=PydanticModel` | 函数类型注解 (`func(*args: type)`) | 自动转换为 JSON Schema 供模型传参          |
| **直接返回** | `return_direct=True/False`  | 默认为 `False`                     | 决定执行结果是否直接作为最终回答返回给用户 |
| **异步实现** | `coroutine=async_func`      | 无                                 | 如果业务函数需要异步执行时传入             |

### 问：如何查看 LangChain 工具最终转换后发送给大模型的 JSON Schema（Function Calling 参数定义）？

要查看 LangChain 工具转换后发送给大模型（如 OpenAI、Claude 等）的完整 Function Calling 定义，主要有以下三种最常用的方式：



### 方式一：使用 `convert_to_openai_tool`（最推荐，查看完整 Payload）

LangChain 在调用大模型前，底层会使用 `convert_to_openai_tool`（或对应的适配器）将工具打包为 OpenAI 标准的 Tool Definition 格式。你可以直接调用该函数：



Python

```
import json
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

# 1. 定义参数 Schema
class CourseSearchInput(BaseModel):
    query: str = Field(description="搜索关键词")
    limit: int = Field(default=5, description="返回的最大条目数")

# 2. 创建工具
def search_courses(query: str, limit: int = 5) -> str:
    return f"Results for {query}"

tool = StructuredTool.from_function(
    func=search_courses,
    name="search_course_catalog",
    description="用于搜索学校课程大纲和内容。",
    args_schema=CourseSearchInput,
)

# 3. 转换为发往大模型的 JSON 结构
schema = convert_to_openai_tool(tool)
print(json.dumps(schema, indent=2, ensure_ascii=False))
```

#### 输出的 JSON 结构：

这就是真正通过 HTTP 请求发送给大模型 API 的 `tools` 字段内容：



JSON

```
{
  "type": "function",
  "function": {
    "name": "search_course_catalog",
    "description": "用于搜索学校课程大纲和内容。",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "description": "搜索关键词",
          "type": "string"
        },
        "limit": {
          "default": 5,
          "description": "返回的最大条目数",
          "type": "integer"
        }
      },
      "required": [
        "query"
      ]
    }
  }
}
```

### 方式二：直接查看工具自带的 Schema（仅查看参数定义）

如果不需要完整的 `type: function` 外壳，只想看参数部分的 JSON Schema，可以使用工具内置方法：



#### 1. 获取完整 JSON Schema（Pydantic 规范）

Python

```
# 适用于 LangChain 0.2+ / Pydantic v2
input_schema = tool.get_input_schema().model_json_schema()
print(json.dumps(input_schema, indent=2, ensure_ascii=False))
```

**输出：**



JSON

```
{
  "title": "CourseSearchInput",
  "type": "object",
  "properties": {
    "query": { "title": "Query", "description": "搜索关键词", "type": "string" },
    "limit": { "title": "Limit", "default": 5, "description": "返回的最大条目数", "type": "integer" }
  },
  "required": ["query"]
}
```

#### 2. 获取参数字典精简视图（`tool.args`）

`tool.args` 提供了一个简化版的参数字段字典，常用于快速调试：



Python

```
print(tool.args)
# 输出: {'query': {'title': 'Query', 'description': '搜索关键词', 'type': 'string'}, 'limit': {'title': 'Limit', 'description': '返回的最大条目数', 'default': 5, 'type': 'integer'}}
```

### 方式三：查看 `llm.bind_tools()` 绑定后的内部对象

当你通过 `llm.bind_tools([tool])` 绑定工具后，可以直接从返回的 `RunnableBinding` 对象中读取大模型最终接收到的 `tools` 参数：



Python

```
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4o", temperature=0)

# 绑定工具
model_with_tools = llm.bind_tools([tool])

# 查看注入到 API 请求中的 kwargs
bound_tools = model_with_tools.kwargs.get("tools")
print(json.dumps(bound_tools, indent=2, ensure_ascii=False))
```

### 常见格式适配转换器一览

如果你使用的不是 OpenAI 格式，`langchain_core.utils.function_calling` 还提供了针对其他厂商的转换函数：

| **转换函数**                       | **适用模型/场景**                                         |
| ---------------------------------- | --------------------------------------------------------- |
| `convert_to_openai_tool(tool)`     | OpenAI、DeepSeek、Qwen、vLLM 等兼容接口（标准 Tool 格式） |
| `convert_to_openai_function(tool)` | OpenAI 早期的旧版 Function Calling 格式（废弃中）         |
| `convert_to_anthropic_tool(tool)`  | Anthropic Claude 系列的 Tools 格式                        |