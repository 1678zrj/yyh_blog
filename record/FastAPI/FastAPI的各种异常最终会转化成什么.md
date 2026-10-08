### 问：我感觉可以总结一下FastAPI中的各种异常，包括主动raise HttpException，没有被捕获的异常，参数校验异常等等等等，都会默认被FastAPI包装成什么样的Response？希望你给的详细点，我想看到这些Response的全貌，核心当然是status_code和content

FastAPI 内部所有异常最终都会通过内置的处理函数（Handler）**或**底层的中间件（Middleware）转换成一个标准的 `Response` 对象。

以下是 FastAPI 中最核心的 **5 种异常类型**以及它们默认被包装成的 `Response` 结构全貌。

### 1. 主动抛出的 `HTTPException`

当你在业务代码中显式写下 `raise HTTPException(...)` 时触发。

- **触发机制**：由内置的 `http_exception_handler` 捕获。
- **Response 类别**：`JSONResponse`
- **Status Code**：你在 `HTTPException` 中指定的 `status_code`（如 400, 401, 403, 404 等）。
- **Content (Body) 结构**：

#### 示例 A：`detail` 为简单文本

Python

```
raise HTTPException(status_code=404, detail="User not found")
```

- **Status Code**: `404 Not Found`

- **Content**:

  JSON

  ```
  {
    "detail": "User not found"
  }
  ```

#### 示例 B：`detail` 为字典/列表（高级用法）

Python

```
raise HTTPException(status_code=400, detail={"err_code": 1001, "reason": "Invalid token"})
```

- **Status Code**: `400 Bad Request`

- **Content**:

  JSON

  ```
  {
    "detail": {
      "err_code": 1001,
      "reason": "Invalid token"
    }
  }
  ```

### 2. 请求参数校验异常 `RequestValidationError`

当前端提交的数据（Path 参数、Query 参数、Header、Form 表单或 Body JSON）不符合 Pydantic 模型/类型声明时，FastAPI 会在**进入路由函数之前**自动拦截并抛出此异常。

- **触发机制**：由内置的 `request_validation_exception_handler` 捕获。
- **Response 类别**：`JSONResponse`
- **Status Code**：**`422 Unprocessable Entity`**
- **Content (Body) 结构**：固定包含一个 `detail` 数组，清晰指出每一个校验失败字段的具体位置和原因。

#### 示例代码与实测 Response：

假设接口要求 `age` 为整数，但前端传了 `"abc"`：

- **Status Code**: `422 Unprocessable Entity`

- **Content**:

  JSON

  ```
  {
    "detail": [
      {
        "type": "int_parsing",
        "loc": [
          "body",
          "age"
        ],
        "msg": "Input should be a valid integer, unable to parse string as an integer",
        "input": "abc"
      }
    ]
  }
  ```

  - `loc`: 报错位置（如 `["body", "age"]` 或 `["query", "user_id"]`）。
  - `msg`: Pydantic 自动生成的校验失败原因。
  - `input`: 前端传入的错误原始值。

### 3. 路由层 404 / 405 异常（Starlette `HTTPException`）

当客户端请求了一个**不存在的 URL 路径**，或者**请求 HTTP 方法不匹配**（例如用 POST 去请求一个只开了 GET 的接口）时，路由引擎在匹配阶段直接抛出。

- **触发机制**：由 Starlette 路由层抛出，经由 FastAPI 包装的 `http_exception_handler` 处理。
- **Response 类别**：`JSONResponse`
- **Status Code**：`404 Not Found` 或 `405 Method Not Allowed`
- **Content (Body) 结构**：

#### 访问不存在的路径：

- **Status Code**: `404 Not Found`

- **Content**:

  JSON

  ```
  {
    "detail": "Not Found"
  }
  ```

#### 方法不匹配：

- **Status Code**: `405 Method Not Allowed`

- **Content**:

  JSON

  ```
  {
    "detail": "Method Not Allowed"
  }
  ```

### 4. 返回值校验异常 `ResponseValidationError`

当你的路由函数正常执行完毕并 `return` 了数据，但这个数据**不符合你在 `response_model` 中定义的 Pydantic 模型**时抛出。这是**后端开发者写出的 Bug**。

- **触发机制**：FastAPI 内部发现类型无法匹配后，安全切断响应。

- **Response 类别**：`JSONResponse`

- **Status Code**: **`500 Internal Server Error`**

- **Content (Body) 结构**：

  为了防止在生产环境向前端暴露后端的数据结构敏感信息，FastAPI 默认隐藏详细报错信息：

  JSON

  ```
  {
    "detail": "Internal Server Error"
  }
  ```

  *(注：在终端控制台中，FastAPI 会打印出极度详细的 Pydantic 校验失败日志，方便开发者排查 Bug。)*

### 5. 未捕获的 Python 原生崩溃异常 (`Exception`)

代码中出现未主动 `try...except` 也没有 `raise HTTPException` 的代码崩溃（如 `1 / 0`、`IndexError`、`AttributeError`、数据库连接超时未捕获等）。

- **触发机制**：由最外层的底层的 `ServerErrorMiddleware`（ Starlette 中间件）拦截。

- **Response 类别**：**`PlainTextResponse`**（注意：这里默认**不是** JSONResponse！）

- **Status Code**: **`500 Internal Server Error`**

- **Content (Body) 结构**：

  - **生产环境 (`debug=False`)**：纯文本字符串：

    Plaintext

    ```
    Internal Server Error
    ```

  - **开发环境 (`debug=True`)**：返回一段包含完整报错堆栈信息的 **HTML 页面**（或者格式化后的富文本报错 JSON）。

### 总结全景对比表

| **异常类型 / 触发场景**     | **负责转换的 Handler / Middleware**         | **默认 Response 类**    | **HTTP 状态码**  | **默认 Body Content 结构**                                   |
| --------------------------- | ------------------------------------------- | ----------------------- | ---------------- | ------------------------------------------------------------ |
| **`raise HTTPException`**   | `http_exception_handler`                    | `JSONResponse`          | 自定义 (4xx/5xx) | `{"detail": <自定义文本或对象>}`                             |
| **请求参数校验失败**        | `request_validation_exception_handler`      | `JSONResponse`          | **422**          | `{"detail": [{"loc": [...], "msg": "...", ...}]}`            |
| **路径不存在 / 方法错误**   | Starlette Router + `http_exception_handler` | `JSONResponse`          | **404 / 405**    | `{"detail": "Not Found"}` / `{"detail": "Method Not Allowed"}` |
| **接口返回值校验失败**      | FastAPI 内部响应解析器                      | `JSONResponse`          | **500**          | `{"detail": "Internal Server Error"}`                        |
| **代码未捕获崩溃 (如 1/0)** | `ServerErrorMiddleware`                     | **`PlainTextResponse`** | **500**          | `"Internal Server Error"`（纯文本）                          |

### 全局统一的改造建议

如果你的项目需要**统一接口规范**（比如成功失败都返回 JSON 且带 `code`/`msg`/`data`），你只需要覆盖处理上述列表中的 3 个入口即可：

1. **`@app.exception_handler(HTTPException)`**：拦截手动 raise 的 4xx/5xx 错误。
2. **`@app.exception_handler(RequestValidationError)`**：拦截 422 错误，将 Pydantic 的 `loc` 和 `msg` 格式化为前端看得懂的提示文本。
3. **`@app.exception_handler(Exception)`**：拦截所有未知的 500 代码崩溃，同时将默认的纯文本 `"Internal Server Error"` 转为你统一的 JSON 格式，并在内部记下 Log 日志。

### 问：请提供一份 FastAPI 全局异常处理的代码模板，要求同时覆盖 HTTPException、RequestValidationError 以及 Exception(500)，并全部转为统一的 JSONResponse 格式。

这里是一份直接可运行的 FastAPI 全局异常处理模板。

这份模板涵盖了 **`HTTPException`**、**`RequestValidationError`** 以及 **`Exception (500)`**，统一将输出转换为 `{"code": ..., "msg": ..., "data": ...}` 结构的 `JSONResponse`，并自带了测试接口和堆栈日志打印功能。

Python

```
import logging
import traceback
from typing import Any
from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# 1. 配置标准错误日志
logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="FastAPI 全局异常处理模板")


# 2. 统一响应格式构建辅助函数
def unified_response(
    code: int, msg: str, data: Any = None, http_status: int = 200
) -> JSONResponse:
    """
    统一构建 JSON 响应体结构
    :param code: 业务状态码 (如 20000, 40000, 42200, 50000)
    :param msg: 提示信息
    :param data: 返回的数据载体
    :param http_status: HTTP 协议状态码 (默认跟随 HTTP 标准，若团队要求 HTTP 始终返回 200 则设为 200)
    """
    return JSONResponse(
        status_code=http_status,
        content={
            "code": code,
            "msg": msg,
            "data": data,
        },
    )


# ==============================================================================
# 3. 注册全局异常处理器 (Exception Handlers)
# ==============================================================================


# 【处理 1】：主动 raise HTTPException 捕获 (如 400, 401, 403, 404 等)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # 兼容 detail 是字符串或字典/列表的情况
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)

    return unified_response(
        code=exc.status_code * 100,  # 业务码举例：404 -> 40400
        msg=msg,
        data=None,
        http_status=exc.status_code,  # 若需 HTTP 强行设为 200，改写为 http_status=200
    )


# 【处理 2】：请求参数校验异常 (422) 格式化
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    # 提取 Pydantic 校验失败的具体字段和原因，拼装成易读文本
    error_messages = []
    for err in exc.errors():
        # 获取字段层级路径，例如 body -> age
        loc_str = " -> ".join(
            [str(loc) for loc in err.get("loc", []) if loc != "body"]
        )
        msg = err.get("msg", "参数不合法")
        error_messages.append(f"[{loc_str}]: {msg}" if loc_str else msg)

    combined_msg = "参数校验失败: " + " | ".join(error_messages)

    return unified_response(
        code=42200,
        msg=combined_msg,
        data=exc.errors(),  # 可以在 data 里带上原始校验错误列表，方便前端定位
        http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


# 【处理 3】：未捕获的全局代码崩溃 (500 系统未知异常)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # 核心：必须使用 logger 打印完整的堆栈信息，避免生产环境变成“错误盲盒”
    logger.error(
        f"全局拦截未处理异常 | 请求路径: {request.method} {request.url.path}"
    )
    logger.error(traceback.format_exc())

    return unified_response(
        code=50000,
        msg="服务器内部错误，请联系管理员",
        data=None,
        http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


# ==============================================================================
# 4. 测试路由 (直接运行后打开 /docs 即可测试效果)
# ==============================================================================


class UserRegisterDTO(BaseModel):
    username: str
    age: int


@app.post("/api/user/register", summary="1. 测试 422 参数校验报错")
async def test_validation(user: UserRegisterDTO):
    """请求体传参不满足条件时（例如 age 传 "abc"）触发"""
    return user


@app.get("/api/user/detail", summary="2. 测试 404 手动 raise HTTPException")
async def test_http_exception(user_id: int):
    """请求 user_id != 1 时触发"""
    if user_id != 1:
        raise HTTPException(status_code=404, detail="请求的用户不存在")
    return {"user_id": 1, "username": "Alice"}


@app.get("/api/system/crash", summary="3. 测试 500 未捕获代码崩溃")
async def test_crash():
    """除零异常，触发 500 未捕获代码崩溃"""
    return 1 / 0
```

### 关键细节说明

1. **`HTTP 状态码` vs `业务 code` 自定义选项**：

   模板中默认让网络层的 HTTP Status Code 跟随报错类型（`422`, `404`, `500`），并在 Response Body 中输出带 `code`/`msg`/`data` 的统一外壳。如果你的团队规范要求**所有网络请求无论成功失败 HTTP 状态码必须统一是 200 OK**，只需在调用 `unified_response(...)` 时，把 `http_status` 参数全部写死为 `200` 即可。

2. **`RequestValidationError` 信息的友好化**：

   模板将 Pydantic 默认吐出的极长原始嵌套数组做了一层提取，拼装成 `[age]: Input should be a valid integer` 这种人话，同时保留原始报错放在 `data` 中。

3. **500 崩溃排查保护**：

   在 `Exception` 处理函数中显式使用了 `traceback.format_exc()`，确保生产环境中前端收到干净的 `"服务器内部错误"`，而后端控制台和日志文件中保留着哪行代码报错的完整堆栈记录。