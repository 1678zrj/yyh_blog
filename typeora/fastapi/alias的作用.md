### 问：fastapi中alias的作用

`alias`（别名）在 FastAPI 中非常有用，主要用于解决 **Python 变量命名规则与 API URL 命名习惯之间的冲突**。

在你的代码中：

Python

```
alias="item-query"
```

这句配置的作用是：**将 Python 代码中的参数名 `q` 映射为 URL 中的参数名 `item-query`。**

### 1. 为什么要这么做？

这是一个语法限制问题：

- **在 URL 中**：很多 API 习惯使用连字符（kebab-case），例如 `item-query`、`user-id`、`api-key`。
- **在 Python 中**：变量名**不允许**包含连字符 `-`（因为 `-` 在 Python 里是减号）。写 `def read_items(item-query: str)` 会直接报错。

为了让你的 API 符合 URL 标准（使用连字符），同时又能让 Python 代码不报错，就需要用到 `alias`。

### 2. 实际效果演示

假设你的代码如下：

Python

```
@app.get("/items/")
async def read_items(q: Annotated[str | None, Query(alias="item-query")] = None):
    # 在函数内部，你依然使用 'q' 这个变量名来操作数据
    if q:
        return {"q": q}
    return {"items": []}
```

#### ✅ 只有这样请求才有效：

客户端必须使用 `alias` 定义的名字来传参：

> ```
> GET http://127.0.0.1:8000/items/?item-query=hello
> ```

FastAPI 会自动把 URL 里的 `item-query` 的值 `"hello"` 抓取出来，赋值给 Python 函数里的变量 `q`。

#### ❌ 这样请求会失败（或取不到值）：

如果你尝试用 Python 里的变量名去请求：

> ```
> GET http://127.0.0.1:8000/items/?q=hello
> ```

FastAPI 会忽略它，因为你已经告诉它：“在这个接口里，请只认 `item-query` 这个名字”。此时 `q` 会保持默认值 `None`。

### 3. 在自动文档（Swagger UI）中的变化

当你打开 `/docs` 页面时，FastAPI 也会自动适配：

- 文档中显示的参数名会变成 **`item-query`**。
- 前端开发人员看文档时，知道要传 `item-query`，而不需要知道你后端代码里把它叫 `q` 还是 `search_keyword`。

### 总结

`alias` 就是给参数穿了一层“马甲”：

- **对内（Python）**：叫 `q`（为了符合语法，方便写代码）。
- **对外（URL/文档）**：叫 `item-query`（为了符合 API 规范或特定需求）。