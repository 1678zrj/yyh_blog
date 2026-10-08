```
# from taskiq_redis import ListQueueBroker
# from taskiq import TaskiqEvents, TaskiqState
# from app.core.config import settings
# from app.redis.redis_client import RedisManager
# from app.core.rag_deps import rag_container
#
#
# broker = ListQueueBroker(
#     settings.redis_url,
#     socket_timeout=None,        # 关键：BRPOP 无限期阻塞，读超时必须为 None
#     socket_connect_timeout=5.0, # 只限制建连，不影响读
# )
#
#
# @broker.on_event(TaskiqEvents.WORKER_STARTUP)
# async def _worker_startup(state: TaskiqState):
#     await RedisManager.init()
#     await rag_container.startup()
#     print("TaskIQ worker started, redis ready")
#
#
# @broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
# async def _worker_shutdown(state: TaskiqState):
#     await rag_container.shutdown()
#     await RedisManager.close()
#     print("TaskIQ worker stopped")
#
#
# async def startup_broker() -> None:
#     if not broker.is_worker_process:
#         await broker.startup()
#
#
# async def shutdown_broker() -> None:
#     if not broker.is_worker_process:
#         await broker.shutdown()
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import ListQueueBroker
from app.rag_agent.model import model_client
from app.rag_agent.container import graph_container
from app.core.config import settings
from app.core.rag_deps import rag_container
from app.redis.redis_client import RedisManager



"""
    定义两个broker是为了做到资源隔离，
    比如merge_broker只需要Redis客户端
    而agent_broker还需要rag_container
    实际上真正做到两个worker的隔离靠的是指定queue_name
    不指定queue_name就默认到同一个消息队列中，队列名默认是taskiq
    这就会导致merge_worker和agent_worker
    从同一个消息队列中抢不属于它们的任务
"""
# 1. 专门处理文件合并的 Broker
merge_broker = ListQueueBroker(
    settings.redis_url,
    queue_name="merge_queue",
    socket_timeout=None,
    socket_connect_timeout=5.0,
)

# 2. 专门处理 Agent 的 Broker
agent_broker = ListQueueBroker(
    settings.redis_url,
    queue_name="agent_queue",
    socket_timeout=None,
    socket_connect_timeout=5.0,
)


# ---------- merge_broker Worker 事件 (不需要加载沉重的 RAG/Milvus) ----------
@merge_broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _merge_worker_startup(state: TaskiqState):
    await RedisManager.init()
    # await rag_container.startup()
    print("Merge worker started, redis ready")


@merge_broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _merge_worker_shutdown(state: TaskiqState):
    await RedisManager.close()
    print("Merge worker stopped")


# ---------- agent_broker Worker 事件 (需要 Redis + RAG/Milvus) ----------
@agent_broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _agent_worker_startup(state: TaskiqState):

    await RedisManager.init()
    await rag_container.startup()
    await model_client.startup()
    await graph_container.startup()
    print("Agent worker started, rag ready")


@agent_broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _agent_worker_shutdown(state: TaskiqState):
    await graph_container.shutdown()
    await rag_container.shutdown()
    await RedisManager.close()
    print("Agent worker stopped")


# ---------- 供 FastAPI 主进程调用的统一管理函数 ----------
async def startup_brokers() -> None:
    """FastAPI 启动时，初始化生产者客户端"""
    if not merge_broker.is_worker_process:
        await merge_broker.startup()
    if not agent_broker.is_worker_process:
        await agent_broker.startup()


async def shutdown_brokers() -> None:
    """FastAPI 退出时，释放连接"""
    if not merge_broker.is_worker_process:
        await merge_broker.shutdown()
    if not agent_broker.is_worker_process:
        await agent_broker.shutdown()
这是broker.py的代码
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
            self._graph = graph_builder.compile(checkpointer=checkpointer)

    async def shutdown(self):
        if self._pool:
            await self._pool.close()

    @property
    def graph(self) -> CompiledStateGraph:
        if self._graph is None:
            raise RuntimeError("")
        return self._graph


graph_container = GraphContainer(psycopg_url=settings.psycopg_url)
这是container.py的代码，
启动报错：
[2026-09-20 04:17:16,200][psycopg.pool][WARNING][worker-0] error connecting in 'pool-1': Psycopg cannot use the 'ProactorEventLoop' to run in async mode. Please use a compatible event loop, for instance by setting 'asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())'
[2026-09-20 04:17:18,567][psycopg.pool][WARNING][worker-0] error connecting in 'pool-1': Psycopg cannot use the 'ProactorEventLoop' to run in async mode. Please use a compatible event loop, for instance by setting 'asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())'
```

### 问：上面这个报错是什么原因，如何解决？

这个错误发生的原因是：**你在 `broker.py` 开头设置的 `WindowsSelectorEventLoopPolicy()` 并没有在 Taskiq Worker 子进程创建 EventLoop 之前生效。**

---

### 原因分析

1. **子进程生成机制**：Taskiq 在 Windows 下使用 `multiprocessing` 的 `spawn` 模式启动工作进程（日志中的 `[worker-0]` 即代表独立子进程）。
2. **执行时序错位**：Taskiq 的 Worker 子进程启动时，会**先创建并启动事件循环**（Python 3.8+ 在 Windows 上默认创建 `ProactorEventLoop`），**然后才动态导入**你的 `app.broker` 模块。
3. **设置失效**：当代码执行到 `broker.py` 顶部的 `asyncio.set_event_loop_policy(...)` 时，当前子进程的 EventLoop 已经在运行了，修改 Policy 无法改变已经运行中的循环类型。
4. **Psycopg 限制**：Psycopg 的异步驱动底层依赖 `loop.add_reader` 监听 Socket，而 Windows 默认的 `ProactorEventLoop` 不支持该方法，因此触发报错。

---

### 解决方案

要解决这个问题，必须让 Python 在**任何事件循环被创建之前**就设置好 EventLoopPolicy。

#### 方案一：使用 `sitecustomize.py`（推荐，零侵入、最稳定）

Python 在启动每一个进程（包括主进程和多进程 `spawn` 出的所有子进程）初始化 `site` 模块时，会自动寻找并执行 `sitecustomize.py`。

在你的**虚拟环境的 `site-packages` 目录**中（或者项目根目录下）新建一个名为 `sitecustomize.py` 的文件：

> 虚拟环境路径通常为：`.venv/Lib/site-packages/sitecustomize.py`

写入以下内容：

```python
# sitecustomize.py
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

```

*设置后，无论运行 FastAPI、Taskiq CLI 还是它派生的任何 `worker-N` 子进程，都会在第一时间将全局 Policy 切换为 Selector。*

---

#### 方案二：在 `site-packages` 中添加 `.pth` 文件

如果不想新建 `.py` 脚本，可以在当前虚拟环境的 `site-packages` 目录下新建一个文件 `fix_loop.pth`，内容写为单行：

```python
import sys, asyncio; sys.platform == "win32" and asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

```

Python 解释器在启动时会自动解析并执行 `.pth` 文件中以 `import` 开头的代码行，同样能在 Taskiq 创建循环前生效。

---

### 顺带提醒：`AsyncPostgresSaver` 的潜在问题

修复 EventLoop 后，如果使用的是空的 PostgreSQL 库，你的 `container.py` 可能会接着报 `relation "checkpoints" does not exist`。

建议在 `GraphContainer.startup()` 中显式调用 `await checkpointer.setup()` 并显式打开连接池：

```python
class GraphContainer:
    def __init__(self, psycopg_url: str):
        self.psycopg_url = psycopg_url
        self._graph: CompiledStateGraph | None = None
        self._pool: AsyncConnectionPool | None = None

    async def startup(self):
        if self._pool is None:
            self._pool = AsyncConnectionPool(
                conninfo=self.psycopg_url, 
                max_size=8, 
                kwargs={"autocommit": True}
            )
            await self._pool.open()  # 确保连接池就绪

        if self._graph is None:
            checkpointer = AsyncPostgresSaver(self._pool)
            await checkpointer.setup()  # 关键：首次运行自动在 PG 中建表
            self._graph = graph_builder.compile(checkpointer=checkpointer)

    async def shutdown(self):
        if self._pool:
            await self._pool.close()
```

### 问：这个问题报错真的好蠢啊，怎么样才能避免呢

这个报错确实是 Python 生态里最臭名昭著的“幽灵坑”之一。



之所以让人觉得“蠢”，是因为**在生产环境（Linux）中这个 Bug 根本不存在**。它完全是 Windows、Python 官方决策与驱动库三方互相甩锅的产物：



1. **Python 官方**：Python 3.8+ 把 Windows 默认的事件循环改成了 `ProactorEventLoop`（基于 IOCP），但它偏偏**没有实现**用来监听网络套接字的底层接口 `add_reader()`。
2. **Psycopg 官方**：坚持使用 `add_reader()` 这一 POSIX 标准方案，遇到 Windows 直接抛异常，把解决责任丢给用户（“请你自己切回 Selector”）。
3. **Taskiq / Celery 多进程**：主进程改了设置，子进程 `spawn` 启动时又被重置成系统默认，导致常规的“在代码顶部改 policy”彻底失效。

要彻底避免或者摆脱这种折磨，可以从以下几个维度来解决：



### 1. 治本方案：开发环境接入 WSL2 或 Dev Containers（最推荐）

现代 Python 后端项目（尤其是涉及异步高并发、Redis、PostgreSQL、Celery/Taskiq）在 Windows 原生环境运行，后续还会遇到**信号处理（优雅退出失效）、子进程文件句柄继承、网络套接字差异**等一系列隐蔽问题。



- **WSL2（Windows Subsystem for Linux）**：在 Windows 终端中运行 Ubuntu 子系统，VS Code / PyCharm 直接 Remote 接入开发。
- **效果**：底层是纯正的 Linux 原生内核，使用的是基于 `epoll` 的高性能循环，`psycopg`、`asyncpg`、`taskiq` 无缝运行，完全不需要写任何针对 Windows 的兼容代码。

### 2. 代码级规避：改用脚本编程启动 Worker（摆脱 CLI 时序陷阱）

如果你必须在 Windows 本机直接运行，不要直接在终端敲 `taskiq worker app.broker:agent_broker`，因为 CLI 命令行工具会在导入你的代码前强行创建默认循环。



可以编写一个启动脚本 `run_agent_worker.py`，在 Python 进程刚跑第一行时就锁死 EventLoop，再拉起 Taskiq：



Python

```
# run_agent_worker.py
import sys
import asyncio

# 必须在导入 taskiq 和业务模块的最前排执行
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from taskiq.cli.worker.run import run_worker_args
from app.broker import agent_broker

if __name__ == "__main__":
    # 使用 Taskiq 提供的函数直接以当前进程/指定参数启动
    # 或者用 --workers 1 单进程模式，避免 spawn 导致重新初始化
    sys.argv = ["taskiq", "worker", "app.broker:agent_broker", "--workers", "1"]
    from taskiq.cli.worker.cmd import main
    main()
```

这样启动的子进程/主进程天然处于 `Selector` 策略下。



### 3. 一劳永逸：使用 `.env` 触发或 `.pth` 注入（当前工程最优解）

如果你依然习惯用官方命令 `taskiq worker ...`，不想在业务逻辑里加冗余补丁，最优雅的方式就是前文提到的：



- 在 `.venv/Lib/site-packages/` 目录下扔一个 `fix_win_async.pth` 文件：

  Python

  ```
  import sys, asyncio; sys.platform == 'win32' and asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
  ```

- **优势**：

  - **完全不污染业务代码**：Git 仓库里干干净净，没有平台判断逻辑。
  - **全局穿透**：无论主进程、子进程、FastAPI 还是多 worker 线程，只要由该虚拟环境解释器唤醒，第一时间强制就绪。

### 开发建议

对于后端异步项目，长期维护的原则是：**业务代码不要为了 Windows 妥协**。



用 `sitecustomize.py` 或 `.pth` 把问题压制在**本地开发机配置**层面，或者直接转入 WSL2。这样当你的代码打包进 Docker 镜像部署到服务器时，不用额外清理或担心 Windows 特有的 hack 代码引起生产事故。