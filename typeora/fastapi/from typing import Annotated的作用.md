```python
from typing import Annotated
```

### 问：这个Annotated是干嘛用的？

`typing.Annotated` 是 Python 3.9 引入的一个非常实用的特性（PEP 593）。

用一句话概括：**它允许你在不改变代码逻辑类型检查的前提下，给变量的类型附加“额外的元数据（Metadata）”。**

这些元数据通常被 **框架**（如 FastAPI, Pydantic, Typer）在运行时读取，用来执行验证、依赖注入或生成文档，而静态类型检查器（如 MyPy）会忽略这些元数据，只看前面的类型。

------

### 1. 核心语法

语法结构为：`Annotated[Type, Metadata]`

Python

```
from typing import Annotated

# 在类型检查器眼里，x 就是 int
# 在运行时，你可以读取到 "这是一个元数据"
x: Annotated[int, "这是一个元数据"] = 10
```

### 2. 为什么要用它？（以 FastAPI 为例）

这是 `Annotated` 最常见的使用场景。在 `Annotated` 出现之前，如果你想在 FastAPI 中定义一个带有校验规则的查询参数，你必须让默认值等于 `Query(...)`，这会让类型提示变得奇怪。

#### ❌ 以前的写法 (旧版)

类型提示声称 `q` 是 `str` 或 `None`，但默认值却是一个 `Query` 对象。这让很多静态检查工具（Lint）感到困惑。

Python

```
from fastapi import FastAPI, Query

@app.get("/items/")
#虽然说是 str，但赋值的是 Query 对象，IDE 可能会警告类型不匹配
def read_items(q: str | None = Query(default=None, max_length=50)):
    return {"q": q}
```

#### ✅ 使用 Annotated 的写法 (新版/推荐)

现在，我们将“这是一个字符串”放在**类型**位置，将“它的最大长度是50”放在**元数据**位置。

Python

```
from typing import Annotated
from fastapi import FastAPI, Query

@app.get("/items/")
# 这里的语义非常清晰：
# 1. 变量 q 的类型是 str 或 None
# 2. 附加信息：它来自 Query 参数，最大长度 50
def read_items(q: Annotated[str | None, Query(max_length=50)] = None):
    return {"q": q}
```

### 3. 主要优势

1. **分离关注点**：

   - **类型检查器 (MyPy/Pyright)** 只看 `Annotated` 的第一个参数（类型），确保你的代码没有类型错误。
   - **框架 (FastAPI/Pydantic)** 读取 `Annotated` 的第二个及后续参数（元数据），用来执行具体的逻辑（如校验、数据库字段定义等）。

2. **可复用性**：你可以定义别名，让代码更简洁。

   Python

   ```
   # 定义一个可复用的类型
   UserId = Annotated[int, Query(ge=1, description="用户ID，必须大于0")]
   
   @app.get("/users/")
   def get_user(uid: UserId):  # 代码非常干净
       pass
   ```

### 总结

当你看到 `x: Annotated[int, ...]` 时，你就把它理解为 **`x` 是一个 `int`**，后面跟着的 `...` 只是给框架看的配置说明书，不会影响 `x` 本质上是一个整数的事实。

