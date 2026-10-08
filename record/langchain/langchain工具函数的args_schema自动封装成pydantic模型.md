### 问：我在学习langchain，使用它的@tool注解定义工具后，工具函数是否就有了args_schema属性，可以对模型的tool_call工具调用的参数进行格式校验了呢？还是说必须得指定args_schema等于pydantic模型才能够进行格式检验？

**不需要显式指定。** 只要 Python 函数中写了**类型注解（Type Hints）**，`@tool` 装饰器就会自动通过反射和 Pydantic 动态构建 `args_schema`，并在调用时自动执行参数格式与类型校验。



### 1. `@tool` 的自动推导机制

当你为函数提供标准的类型注解和文档字符串时，`@tool` 会在底层调用 Pydantic 的 `create_model` 动态创建一个 Schema：



Python

```
from langchain_core.tools import tool

@tool
def calculate_area(length: float, width: float = 1.0) -> float:
    """计算矩形面积。
    
    Args:
        length: 矩形长度
        width: 矩形宽度
    """
    return length * width

# 1. 自动生成了 args_schema 属性
print(calculate_area.args_schema)
# 输出: <class 'pydantic.main.calculate_area'>

# 2. 查看提供给大模型的参数描述字典 (JSON Schema)
print(calculate_area.args)
# 输出结构类似:
# {'length': {'title': 'Length', 'type': 'number', 'description': '矩形长度'}, 
#  'width': {'title': 'Width', 'default': 1.0, 'type': 'number', 'description': '矩形宽度'}}
```

#### 校验何时生效？

当 Agent 或模型返回 `tool_call` 并通过 `calculate_area.invoke(tool_call["args"])` 执行时，LangChain 会自动使用生成的 `args_schema` 对输入字典进行解析与校验：



- **正确类型转换**：如果模型传入字符串数字 `{"length": "10.5", "width": 2}`，Pydantic 会自动将其转换为 `float`。
- **类型不合法**：如果传入无法解析的值 `{"length": "abc"}` 或缺少必填参数 `{"width": 2}`，会直接抛出 Pydantic 的 `ValidationError`。

> **注意**：如果函数**完全没有写类型注解**（例如 `def calculate_area(length, width):`），LangChain 依然会生成 `args_schema`，但参数类型会被降级为 `Any`，此时只会校验参数名是否存在，失去强类型约束。

### 2. 现代推荐：使用 `Annotated` + `Field`（无需显式 Model）

如果你需要为单个字段增加详细描述或约束条件（如数值范围、正则限制），无需单独写一个 Pydantic 类，直接使用 `typing.Annotated` 配合 Pydantic 的 `Field` 即可：



Python

```
from typing import Annotated
from pydantic import Field
from langchain_core.tools import tool

@tool
def fetch_user_orders(
    user_id: Annotated[int, Field(description="用户ID，必须为正整数", gt=0)],
    limit: Annotated[int, Field(default=10, description="分页数量，范围 1-50", ge=1, le=50)],
) -> str:
    """查询指定用户的历史订单列表。"""
    return f"Fetching orders for user {user_id}, limit {limit}"
```

这种方式生成的 `args_schema` 会自动包含 `gt=0`、`ge=1, le=50` 的约束规则。



### 3. 什么时候必须显式定义 `args_schema`？

只有在以下较复杂场景下，才建议单独定义 Pydantic Model 并传入 `@tool(args_schema=...)`：



1. **复杂/自定义校验逻辑**：需要使用 `@field_validator` 或 `@model_validator` 进行多字段联动校验（例如：`end_date` 必须大于 `start_date`）。
2. **深度嵌套的数据结构**：入参本身包含复杂的列表字典、嵌套对象，签名直接写在函数参数中过于臃肿。
3. **接口契约复用**：已有定义好的 API 请求 DTO，需要直接复用为工具入参。

Python

```
from pydantic import BaseModel, Field, model_validator
from langchain_core.tools import tool

class BookingQueryInput(BaseModel):
    start_date: str = Field(description="开始日期，格式 YYYY-MM-DD")
    end_date: str = Field(description="结束日期，格式 YYYY-MM-DD")
    guests: int = Field(default=1, ge=1, description="入住人数")

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_date >= self.end_date:
            raise ValueError("end_date 必须晚于 start_date")
        return self

@tool(args_schema=BookingQueryInput)
def search_rooms(start_date: str, end_date: str, guests: int = 1) -> str:
    """根据入住日期和人数检索可用房间。"""
    return f"Rooms available from {start_date} to {end_date} for {guests} guests."
```

常规业务工具开发中，保持良好的 **Type Hints + Docstring** 或 **`Annotated[..., Field(...)]`** 习惯即可，`@tool` 会自动完成所有的 Schema 生成与参数校验工作。

### 问：主要我想使用tool.args_schema.model_validate函数，能使用吗

**完全可以，直接就能用。**



通过 `@tool` 装饰器生成的工具对象，其 `args_schema` 本质上就是一个标准的 Pydantic `BaseModel` 子类。你可以直接调用 Pydantic V2 的所有模型方法，包括 `model_validate` 和 `model_validate_json`。



### 1. 代码验证与使用方式

Python

```
from langchain_core.tools import tool
from pydantic import ValidationError

@tool
def search_database(query: str, limit: int = 10) -> str:
    """根据查询词检索数据库。"""
    return f"Results for: {query}"

# 1. 模拟大模型返回的原始参数字典（可能存在类型不严格的情况）
raw_tool_args = {"query": "LangChain Agent", "limit": "5"}

# 2. 直接调用 model_validate 进行数据校验与类型强制转换
if search_database.args_schema:
    validated_obj = search_database.args_schema.model_validate(raw_tool_args)
    
    print(type(validated_obj))        # <class 'pydantic.main.search_database'>
    print(validated_obj.limit)        # 5 (自动从字符串 "5" 转为整数 5)
    
    # 转回干净的标准 dict
    clean_args = validated_obj.model_dump()
    print(clean_args)                 # {'query': 'LangChain Agent', 'limit': 5}
```

### 2. 两个非常实用的衍生用法

#### ① 处理原始 JSON 字符串：`model_validate_json`

如果直接从底层 API 或流式输出中拿到了大模型输出的原始 JSON 字符串（尚未转为 Python `dict`），可以直接用 `model_validate_json`，省去一步 `json.loads`：



Python

```
raw_json_str = '{"query": "RAG architecture", "limit": 20}'
validated_obj = search_database.args_schema.model_validate_json(raw_json_str)
```

#### ② 捕获 `ValidationError` 实现自定义容错/回传

手动调用 `model_validate` 最核心的价值，通常是在执行工具前做拦截处理。如果模型少传参数或类型错误，可以精准捕获错误详情并构造成消息反馈给模型：



Python

```
bad_args = {"limit": "not_a_number"}  # 缺少必填的 query，且 limit 类型非法

try:
    search_database.args_schema.model_validate(bad_args)
except ValidationError as e:
    # 提取格式化的错误详情，以便反馈给大模型重试
    error_summary = e.errors()
    print(error_summary)
    # [{'type': 'missing', 'loc': ('query',), 'msg': 'Field required'}, ...]
```

### 3. 注意事项

1. **类型检查器警告（Mypy / Pyright）**：

   在 LangChain 的 `BaseTool` 定义中，`args_schema` 的类型标注为 `Optional[Type[BaseModel]]`。如果项目启用了严格类型检查，直接写 `tool.args_schema.model_validate(...)` 可能会提示 `Item "None" of "Optional[...]" has no attribute "model_validate"`。建议加上断言或守卫：

   Python

   ```
   assert search_database.args_schema is not None
   search_database.args_schema.model_validate(...)
   ```

2. **Pydantic 版本匹配**：

   LangChain 现已全面使用 **Pydantic V2**。如果你使用的是较老的 Pydantic V1 环境，对应的方法为 `.parse_obj(...)` 而非 `.model_validate(...)`。

### 问：所以你的意思是即使我不显式指定pydantic模型，工具函数的参数也会被封装成Pydantic模型保存在args_schema中

**是的，完全正确。**



即使你不显式传入任何 Pydantic 类，只要被 `@tool` 装饰，LangChain 就会在初始化工具时动态“组装”出一个继承自 `pydantic.BaseModel` 的新模型，并绑定到 `tool.args_schema` 上。



### 底层是如何运作的？

当你写下如下代码时：



Python

```
from langchain_core.tools import tool
from pydantic import BaseModel

@tool
def send_email(to: str, subject: str, cc: str = "") -> str:
    """发送邮件工具。
    
    Args:
        to: 收件人邮箱
        subject: 邮件主题
        cc: 抄送人邮箱
    """
    return "success"
```

LangChain 在后台实际执行了以下步骤：



1. **解析函数签名与类型**：利用 Python 标准库的 `inspect` 和 `typing.get_type_hints` 获取参数名、类型标注（`to: str`）和默认值（`cc=""`）。

2. **提取参数描述**：解析函数文档字符串（Docstring），提取每个字段的说明文本。

3. **动态创建 Pydantic 模型**：调用 Pydantic 内部的 `create_model` 接口：

   Python

   ```
   # 类似 LangChain 底层的逻辑伪代码：
   DynamicModel = pydantic.create_model("send_email", to=(str, ...), subject=(str, ...), cc=(str, ""))
   ```

4. **绑定到属性**：将该动态生成的类赋值给 `send_email.args_schema`。

### 代码验证：它就是一个纯正的 Pydantic 模型

你可以直接用 Python 的继承检查机制来验证：



Python

```
# 1. 验证它是不是 BaseModel 的子类
print(issubclass(send_email.args_schema, BaseModel))
# 输出: True

# 2. 查看其父类
print(send_email.args_schema.__bases__)
# 输出: (<class 'pydantic.main.BaseModel'>,)

# 3. 它拥有普通 Pydantic 模型具备的全部方法
instance = send_email.args_schema.model_validate({
    "to": "test@example.com",
    "subject": "Hello"
})
print(type(instance))
# 输出: <class 'pydantic.main.send_email'>
print(instance.to)
# 输出: test@example.com
```

### 两种方式的本质区别

| **方式**                           | **本质**                                                     | **适用场景**                                       |
| ---------------------------------- | ------------------------------------------------------------ | -------------------------------------------------- |
| **仅用类型标注 + `@tool`**         | LangChain 帮你调用 `create_model` **动态生成** `args_schema` | 绝大多数日常工具、基础类型校验                     |
| **显式传入 `args_schema=MyModel`** | 你自己**手动定义**并传给 LangChain，跳过动态生成步骤         | 复杂嵌套结构、跨字段联动校验（`@model_validator`） |

因此，无论是哪种方式，最终得到的 `tool.args_schema` 都是一个标准的 Pydantic 模型类，你可以放心地在它上面调用 `.model_validate()`、`.model_validate_json()` 或 `.model_dump()`。