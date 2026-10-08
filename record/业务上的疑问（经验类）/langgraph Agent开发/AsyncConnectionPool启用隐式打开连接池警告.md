```
D:\myenvir\python\envs\study\lib\site-packages\psycopg_pool\pool_async.py:167: RuntimeWarning: opening the async pool AsyncConnectionPool in the constructor is deprecated 
and will not be supported anymore in a future release. Please use `await pool.open()`, or use the pool as context manager using: `async with AsyncConnectionPool(...) as pool: `...
  warnings.warn(
import asyncio
import sys

from langgraph.graph.state import CompiledStateGraph
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.rag_agent.graph import graph_builder
from app.core.config import settings


class GraphContainer:
    def __init__(self, psycopg_url: str):
        self.psycopg_url = psycopg_url
        self._graph: CompiledStateGraph | None = None
        self._pool: AsyncConnectionPool | None = None

    async def startup(self):

        if self._pool is None:
            self._pool = AsyncConnectionPool(conninfo=self.psycopg_url, max_size=8, kwargs={"autocommit": True})
        if self._graph is None:
            checkpointer = AsyncPostgresSaver(self._pool)
            await checkpointer.setup()
            self._graph = graph_builder.compile(checkpointer=checkpointer)

    async def shutdown(self):
        if self._pool:
            await self._pool.close()
            self._pool = None
        self._graph = None

    @property
    def graph(self) -> CompiledStateGraph:
        if self._graph is None:
            raise RuntimeError("")
        return self._graph


graph_container = GraphContainer(psycopg_url=settings.psycopg_url)

```

### 问：以上是警告内容和我的代码，为什么会有警告？

出现该警告的原因是：**`AsyncConnectionPool` 默认在构造函数 `__init__` 中隐式打开连接池，而在异步对象构造时执行同步/隐式的资源初始化已被官方弃用**。



在较新版本的 `psycopg_pool` 中，为了避免事件循环竞争与阻塞，官方要求必须显式以异步方式打开连接池（通过 `await pool.open()` 或 `async with` 上下文管理器），而不能依赖 `AsyncConnectionPool(...)` 自动开启。



### 解决方案

在初始化 `AsyncConnectionPool` 时显式传入 `open=False`，随后在异步方法中使用 `await self._pool.open()` 显式打开连接池：



Python

```
class GraphContainer:
    def __init__(self, psycopg_url: str):
        self.psycopg_url = psycopg_url
        self._graph: CompiledStateGraph | None = None
        self._pool: AsyncConnectionPool | None = None

    async def startup(self):
        if self._pool is None:
            # 1. 设置 open=False，阻止在构造函数中自动打开
            self._pool = AsyncConnectionPool(
                conninfo=self.psycopg_url,
                max_size=8,
                kwargs={"autocommit": True},
                open=False,
            )
            # 2. 显式异步打开连接池
            await self._pool.open()

        if self._graph is None:
            checkpointer = AsyncPostgresSaver(self._pool)
            await checkpointer.setup()
            self._graph = graph_builder.compile(checkpointer=checkpointer)

    async def shutdown(self):
        if self._pool:
            await self._pool.close()
            self._pool = None
        self._graph = None

    @property
    def graph(self) -> CompiledStateGraph:
        if self._graph is None:
            raise RuntimeError("Graph is not initialized. Call startup() first.")
        return self._graph
```

通过传入 `open=False` 并执行 `await self._pool.open()`，既消除了弃用警告，也确保了连接池的初始化完全纳入当前的 `asyncio` 事件循环管理。

### 问：哦，这是为了防止多进程情况下fork模式多个子进程共享资源吗？所以也改成这种显式延迟加载方式？

你的直觉非常敏锐——**“防止在 `fork` 之前初始化资源导致子进程共享 Socket/FD”** 确实是 Python 服务端开发中极为经典的一大痛点。



但这**并不是**官方这次弃用 `__init__` 隐式打开的**最核心原因**（虽然它客观上顺带收益了）。



这次改动的最核心驱动力，是 **Python 异步编程中 `__init__` 同步机制与 `asyncio` 事件循环（Event Loop）之间的天然矛盾**。



### 1. 最核心的原因：Event Loop 绑定与 `__init__` 无法 `await`

在 Python 中，类的构造函数 `__init__` **必须是同步的**，它无法执行 `await`。



旧版本在 `AsyncConnectionPool(...)` 构造时默认自动打开，内部只能通过类似“在后台悄悄提交一个 Task”的方式去建立连接。这就引发了几个严重的工程问题：



- **绑定了错误的事件循环（Event Loop）：**

  很多时候，数据库池对象会在模块导入期（全局变量）或服务初始化阶段创建，此时当前的 `asyncio` 事件循环可能**还没有启动**，或者随后在 Web 框架（如 FastAPI/Uvicorn、Tornado）启动时被**替换成了新的事件循环**。

  在旧的/不存在的事件循环里偷偷注册 Task，会导致极其隐蔽的 `RuntimeError: Task attached to a different loop` 或 `no running event loop`。

- **无法优雅地“快速失败”（Fast-Fail）：**

  连接数据库是有网络 I/O 的，可能超时、鉴权失败或断网。同步构造函数无法 `await` 握手结果，导致网络报错变成未捕获的后台异常；而改成 `await pool.open()` 后，如果数据库连不上，程序会在启动阶段立即抛出异常并中止，易于排查。

### 2. 你提到的 `fork` 多进程问题，关系有多大？

**你说的完全正确，它是客观收益，但适用场景略有差异：**



- **确实存在的痛点：**

  在 Gunicorn 等采用预派生模型（pre-fork worker）的部署中，如果在 Master 进程导入代码阶段就打开了连接池，`fork()` 出的子进程会直接复制父进程的**文件描述符（Socket FD）和内部锁状态**。多个子进程同时往同一个 Socket 读写数据，会直接导致协议混乱、TLS 握手损坏甚至死锁。

- **为什么不是官方做此改动的主要推手：**

  因为即便是同步连接池 `ConnectionPool`，只要有人在 `fork()` 前打开，照样会踩这个坑。

  而且在现代 Python 异步部署下（比如 `gunicorn -k uvicorn.workers.UvicornWorker`），每个 Worker 进程各自有独立的内存空间和独立的事件循环，通常推荐在框架的 **Lifespan / Startup 事件**中去初始化资源。

### 总结

官方推行 `open=False` + 显式 `await pool.open()`（或 `async with`）的根本逻辑是：



> **“纯正的异步资源，就应该在异步上下文中被显式创建与管理，而不是在同步的构造函数里偷偷摸摸干 I/O 操作。”**

而这一改动带来的直接好处包括：



1. **彻底解耦对象的创建与资源的连接**，不再受 import 期事件循环未就绪的困扰；
2. **连接异常可控**，启动失败即刻感知；
3. 顺带杜绝了你提到的 **“在主进程/模块加载期误建连接并在 fork 后被多个 Worker 冲突复用”** 的隐患。