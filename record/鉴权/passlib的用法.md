```python
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
```

### 问：这段代码是干嘛的？

这段代码的作用是**初始化一个密码哈希（CryptContext）管理工具**，常用于 Web 开发（特别是基于 FastAPI 或 Flask 等 Python 框架）中的密码加密和校验。

拆解分析它的具体作用：

- **`schemes=["bcrypt"]`**：指定采用 **bcrypt** 作为首选的密码哈希算法。bcrypt 是一种专门设计用于保存用户密码的安全哈希算法，带有自动盐值（Salt）加盐机制，且计算成本可调，能有效防范彩虹表和暴力破解攻击。
- **`deprecated="auto"`**：开启自动弃用标识。如果未来你在 `schemes` 中添加了旧的加密算法（比如 `md5_crypt`），它会自动把除第一个算法外的其他旧算法标记为弃用。
- **`pwd_context` 对象**：创建好的上下文对象，后续通常配合以下两个方法使用：
  - **加密密码**：`pwd_context.hash("user_password")` —— 将用户输入的明文密码生成不可逆的哈希字符串存储到数据库。
  - **校验密码**：`pwd_context.verify("user_password", hashed_password)` —— 在用户登录时，验证用户输入的明文密码是否与数据库中保存的哈希值匹配。

### 问：pwd_context 的verify 或 hash 计算会成为FastAPI异步主线程的瓶颈吗 ？

**是的，绝对会。** 如果你在 `async def` 路由或依赖项中直接调用 `pwd_context.verify()` 或 `pwd_context.hash()`，它会成为整个 FastAPI 应用最大的性能瓶颈之一。

### 为什么会阻塞？

1. **Bcrypt 属于 CPU 密集型任务（CPU-bound）**

   为了抵御暴力破解，Bcrypt 在设计上被故意调整得很慢（每次计算通常耗时 **100ms 到 300ms** 甚至更久）。

2. **Asyncio 的事件循环是单线程的**

   当你声明 `async def` 时，FastAPI 会在主线程的 asyncio 事件循环中直接执行该函数。如果在其中调用同步且耗时长的 CPU 密集型方法，事件循环将被**彻底卡死**。

3. **后果**

   在 Bcrypt 计算的这几百毫秒内，主线程无法处理任何其他并发请求（即便其他请求只是简单的 `async` 数据库查询或 I/O 操作）。如果有多个用户同时登录，响应时间会呈线性急剧恶化。

### 解决方案

#### 方案 1：在 `async def` 中使用线程池（推荐）

使用 `asyncio.to_thread`（Python 3.9+）或 FastAPI 自带的 `run_in_threadpool` 将计算扔到后台线程池，释放主事件循环：

Python

```
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
import asyncio

# 方式 A：使用 asyncio.to_thread (Python 3.9+)
async def verify_password_async(plain_password: str, hashed_password: str) -> bool:
    return await asyncio.to_thread(pwd_context.verify, plain_password, hashed_password)

async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(pwd_context.hash, password)


# 方式 B：使用 FastAPI 的 run_in_threadpool
async def verify_password_fastapi(plain_password: str, hashed_password: str) -> bool:
    return await run_in_threadpool(pwd_context.verify, plain_password, hashed_password)
```

#### 方案 2：将路由直接定义为普通同步函数 `def`

FastAPI 对普通 `def`（非 `async def`）路由有特殊的处理机制：**会自动将其放入 ThreadPoolExecutor 线程池中运行**，因此不会阻塞主事件循环。

Python

```
@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # 在普通 def 中直接调用是安全的，FastAPI 会在独立线程中执行它
    user = authenticate_user(form_data.username, form_data.password)
    ...
```

*注意：如果你的路由内部有大量 `await`（如异步 ORM 操作），请使用 **方案 1**。*

### 💡 额外补充：关于 `passlib` 的现状

`passlib` 已经多年未积极维护，且与较新版本的 `bcrypt`（如 bcrypt 4.0+）存在兼容性问题（经常抛出 `AttributeError: module 'bcrypt' has no attribute '__about__'` 异常）。

如果你在开发新项目，建议考虑以下方案：

1. **官方推荐的新替代品：`pwdlib`**（FastAPI 官方文档近期的更新已改用 `pwdlib` 结合 `argon2` 或 `bcrypt`）。
2. **直接使用 `argon2-cffi`**：Argon2 是目前推荐度极高的现代哈希算法。