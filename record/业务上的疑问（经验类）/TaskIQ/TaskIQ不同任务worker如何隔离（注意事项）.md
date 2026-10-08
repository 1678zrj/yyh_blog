```
我在学习FastAPI后端开发，现在引入了 TaskIQ worker，
这是我当前的两个worker:
@agent_broker.task(task_name="agent_run_task")
@merge_broker.task(task_name="merge_file_task")

broker的代码是：
from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import ListQueueBroker

from app.core.config import settings
from app.core.rag_deps import rag_container
from app.redis.redis_client import RedisManager

# 1. 专门处理文件合并的 Broker
merge_broker = ListQueueBroker(
    settings.redis_url,
    # queue_name="merge_queue",
    socket_timeout=None,
    socket_connect_timeout=5.0,
)

# 2. 专门处理 Agent 的 Broker
agent_broker = ListQueueBroker(
    settings.redis_url,
    # queue_name="agent_queue",
    socket_timeout=None,
    socket_connect_timeout=5.0,
)


# ---------- merge_broker Worker 事件 (不需要加载沉重的 RAG/Milvus) ----------
@merge_broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _merge_worker_startup(state: TaskiqState):
    await RedisManager.init()
    await rag_container.startup()
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
    print("Agent worker started, rag ready")


@agent_broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _agent_worker_shutdown(state: TaskiqState):
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

```

### 问：但是有个问题是我将两个都启动之后，后启动的那个总是发挥不了作用

核心原因是两个 Broker 的 **`queue_name` 被注释掉了**。在 `ListQueueBroker` 中，如果不显式指定 `queue_name`，默认都会监听同一个 Redis 队列（名为 `taskiq`）。



当两个 Worker 同时运行在同一个队列上时，Redis 会以轮询（抢占）的方式分发任务。一旦 `merge_broker` 抢到了 `agent_run_task`（或者反过来），Worker 会因为当前进程没有注册该任务而直接忽略或报错，导致看起来“后启动的 Worker 总是失效”。



### 解决步骤

**步骤 1：为两个 Broker 指定独立的 `queue_name`**



取消代码中 `queue_name` 的注释，隔离存储与消费通道：



Python

```
# 1. 专门处理文件合并的 Broker
merge_broker = ListQueueBroker(
    settings.redis_url,
    queue_name="merge_queue",  # 显式指定独立队列
    socket_timeout=None,
    socket_connect_timeout=5.0,
)

# 2. 专门处理 Agent 的 Broker
agent_broker = ListQueueBroker(
    settings.redis_url,
    queue_name="agent_queue",  # 显式指定独立队列
    socket_timeout=None,
    socket_connect_timeout=5.0,
)
```

- **验证方法**：在 Redis CLI 中执行 `KEYS "*queue*"`，派发任务后能分别看到 `merge_queue` 和 `agent_queue` 两个不同的 Key。

**步骤 2：分别启动对应的 Worker 进程**



分别在两个独立的终端中指定对应的 broker 对象启动：



Bash

```
# 终端 1：启动文件合并 Worker
taskiq worker app.core.broker:merge_broker

# 终端 2：启动 Agent Worker
taskiq worker app.core.broker:agent_broker
```

*(请将 `app.core.broker` 替换为你实际存放 broker 代码的模块路径)*



- **验证方法**：观察启动日志输出，确保终端 1 打印了 `Merge worker started, redis ready`，终端 2 打印了 `Agent worker started, rag ready`，并且各自的 Task 列表中仅包含自己负责的任务。

**步骤 3：修复 `merge_broker` 生命周期中的 RAG 残留**



你的注释写明合并 Worker 不需要加载 RAG，但启动钩子里依然调用了 `rag_container.startup()`，且退出时未释放连接：



Python

```
# ---------- merge_broker Worker 事件 ----------
@merge_broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _merge_worker_startup(state: TaskiqState):
    await RedisManager.init()
    # 移除: await rag_container.startup()
    print("Merge worker started, redis ready")


@merge_broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _merge_worker_shutdown(state: TaskiqState):
    await RedisManager.close()
    print("Merge worker stopped")
```

- **验证方法**：启动 `merge_broker` 时，控制台不再打印 Milvus/向量数据库的初始化连接日志。

### 问:现在还有几个问题，

```
(study) PS D:\mycode\python\file_receive\backend> taskiq worker app.core.broker:merge_broker                                                      
[2026-09-16 04:44:48,617][taskiq.worker][INFO   ][MainProcess] Pid of a main process: 31908
[2026-09-16 04:44:48,620][taskiq.worker][INFO   ][MainProcess] Starting 2 worker processes.
[2026-09-16 04:44:48,630][taskiq.process-manager][INFO   ][MainProcess] Started process worker-0 with pid 34992
[2026-09-16 04:44:48,637][taskiq.process-manager][INFO   ][MainProcess] Started process worker-1 with pid 44444 
Redis连接成功
Redis连接成功
Merge worker started, redis ready
Merge worker started, redis ready
[2026-09-16 04:44:51,510][taskiq.receiver.receiver][INFO   ][worker-0] Listening started.
[2026-09-16 04:44:51,510][taskiq.receiver.receiver][INFO   ][worker-1] Listening started.
[2026-09-16 04:45:51,509][taskiq.receiver.receiver][WARNING][worker-1] task "merge_file_task" is not found. Maybe you forgot to import it?

```

### 一个是为什么运行命令后会启动两个worker，一个是task "merge_file_task" is not found. Maybe you forgot to import it?我不太清楚FastAPI后端代码中哪里指定了task_name

这两个现象都是 TaskIQ 最经典的初学者常见问题，具体原因和解决方法如下：



### 问题一：为什么运行命令后会启动 2 个 Worker？

**原因：**

TaskIQ CLI 的 `--workers`（简写 `-w`）参数**默认值就是 2**。

`taskiq worker` 默认会创建 2 个独立的子进程（`worker-0` 和 `worker-1`）以利用多核 CPU 处理任务，因此控制台会输出两次初始化日志。



**控制 Worker 进程数：**

如果你在本地调试，或者只需要单进程处理，可以在启动时加上 `-w 1` 参数：



Bash

```
taskiq worker app.core.broker:merge_broker -w 1
```

- **验证方法**：启动日志中仅出现 `Starting 1 worker processes.` 和单个 `worker-0`。

### 问题二：`task "merge_file_task" is not found` 是什么原因？

#### 1. `task_name` 是在哪里指定的？

`task_name` 就是你在定义任务函数时，装饰器括号里的参数：



Python

```
# 这里指定的 task_name="merge_file_task" 就是任务的全局唯一标识
@merge_broker.task(task_name="merge_file_task")
async def merge_file_task(...):
    pass
```

当你在 FastAPI 业务逻辑中调用 `await merge_file_task.kiq(...)` 发送任务时，生产者会把字符串 `"merge_file_task"` 连同参数一起打包发给 Redis。



#### 2. 为什么 Worker 会提示找不到该任务？

TaskIQ 的任务注册机制依赖 **Python 模块导入**：只有当 Python 执行了包含 `@merge_broker.task` 的那段代码时，任务才会被注册到 `merge_broker` 的内存字典里。



你启动时执行的命令是：



Bash

```
taskiq worker app.core.broker:merge_broker
```

此时 TaskIQ 只导入了 `app.core.broker` 这个文件。**如果你的 `@merge_broker.task` 函数写在其他文件**（例如 `app/tasks/merge.py` 或 `app/services/file.py`），且 `app.core.broker` 里没有显式导入该文件，Worker 进程在内存中就完全不知道存在名为 `merge_file_task` 的任务。



#### 3. 解决方案（任选一种）

**方法 A：在启动命令末尾追加任务所在的模块路径（官方推荐）**



TaskIQ 支持在命令行末尾直接传入需要 Worker 加载的任务模块：



Bash

```
# 语法：taskiq worker <broker路径> [任务模块1] [任务模块2] ...
taskiq worker app.core.broker:merge_broker app.tasks.merge
```

*(请将 `app.tasks.merge` 替换为你实际编写 `merge_file_task` 函数的文件路径，用点号分隔，不需要加 `.py`)*



- **验证方法**：启动后触发一次任务，控制台不再出现 `[WARNING] task "merge_file_task" is not found`，而是打印任务执行成功的日志。

**方法 B：在 `broker.py` 底部显式导入任务模块**



如果你希望启动命令保持简洁，可以在定义 `merge_broker` 的文件（`app/core/broker.py`）**最底部**直接导入任务文件，强行触发注册：



Python

```
# app/core/broker.py 文件的最后一行添加：
import app.tasks.merge  # 替换为你存放 merge_file_task 的模块路径
```

- **验证方法**：再次执行 `taskiq worker app.core.broker:merge_broker -w 1`，任务即可被正常消费执行。

### 问：所以TaskIQ的queue_name是在broker初始化中指定的，而不是启动指令时指定的

**是的，完全正确。**



在 TaskIQ 的设计理念中，队列名称（`queue_name`）属于 **Broker 实例的属性**，必须在 Python 代码中初始化 Broker 时指定，无法通过 `taskiq worker` 启动命令行直接传入参数指定。



### 为什么与 Celery 等框架不同？

| **特性**            | **Celery**                                    | **TaskIQ**                                                 |
| ------------------- | --------------------------------------------- | ---------------------------------------------------------- |
| **架构思路**        | 单个 App，多队列路由                          | 实例级隔离（每个 Broker 代表一个独立通道）                 |
| **指定队列方式**    | 命令行参数 `-Q <queue_name>`                  | Broker 初始化参数 `queue_name="..."`                       |
| **Worker 启动绑定** | 绑定整个应用 `celery -A app worker -Q queue1` | 绑定具体的 Broker 实例 `taskiq worker app:broker_instance` |

TaskIQ 的命令行只接受 `模块路径:Broker实例名`，Worker 启动时直接加载该 Broker 对象的内部配置进行监听。



### 如果需要动态指定队列名称怎么办？

如果不想在代码中硬编码队列名，最常见的做法是**通过系统环境变量读取**：



Python

```
import os
from taskiq_redis import ListQueueBroker

# 优先读取环境变量，没有则使用默认队列名
MERGE_QUEUE = os.getenv("MERGE_QUEUE_NAME", "merge_queue")

merge_broker = ListQueueBroker(
    settings.redis_url,
    queue_name=MERGE_QUEUE,
)
```

启动时即可通过环境变量控制：

Bash

```
# Windows PowerShell
$env:MERGE_QUEUE_NAME="custom_merge_queue"; taskiq worker app.core.broker:merge_broker

# Linux / macOS
MERGE_QUEUE_NAME="custom_merge_queue" taskiq worker app.core.broker:merge_broker
```

### 问：不过我就感觉在命令中指定任务是不是有点麻烦？如果指定多个任务怎么写？是不是不同的broker写在不同的Python文件中比较好啊

在命令行里写一长串任务模块确实非常繁琐且容易遗漏。在实际工程项目中，**通常不会把所有任务都堆在启动命令里**，而**将不同的 Broker 拆分到不同的 Python 文件中也正是最佳实践**。



### 一、多任务在命令中怎么写？

如果一定要通过命令行传入多个任务模块，语法是用**空格隔开**每个模块路径：



Bash

```
taskiq worker app.core.broker:merge_broker app.tasks.file_split app.tasks.file_merge app.tasks.file_clean
```

*优化技巧（包级聚合）：*

不需要逐个敲每个文件。你可以在 `app/tasks/__init__.py` 中统一导入所有任务：



Python

```
# app/tasks/__init__.py
from app.tasks import file_split, file_merge, file_clean
```

此时启动命令只需指定顶层包路径即可：



Bash

```
taskiq worker app.core.broker:merge_broker app.tasks
```

### 二、为什么建议将不同的 Broker 拆分到不同文件？

你的两个 Worker 职责差异非常明显：



- `merge_broker`：纯 I/O、文件处理任务，轻量级。
- `agent_broker`：包含 LangChain/LlamaIndex、Milvus 向量库、大模型客户端等重型依赖。

如果写在同一个文件（例如 `broker.py`）里，启动 `merge_broker` 时 Python 解释器也会把 Milvus/RAG 的全部重依赖加载进内存，失去多 Broker 架构“资源隔离”的初衷，还容易引发**循环导入（Circular Import）**。



### 三、推荐的工程化目录结构

Plaintext

```
app/
├── brokers/
│   ├── merge_broker.py   # merge_broker 定义 + 其轻量生命周期 + 导入自身任务
│   └── agent_broker.py   # agent_broker 定义 + RAG生命周期 + 导入自身任务
├── tasks/
│   ├── merge_tasks.py    # 使用 @merge_broker.task 定义的任务
│   └── agent_tasks.py    # 使用 @agent_broker.task 定义的任务
└── main.py               # FastAPI 入口（生命周期统一管理）
```

#### 1. 拆分后的 Broker 文件写法（以 `merge_broker.py` 为例）

让 Broker 自身在模块加载完毕时自动挂载自己的任务，这样命令行就完全不需要指定任务模块：



Python

```
# app/brokers/merge_broker.py
from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import ListQueueBroker
from app.core.config import settings
from app.redis.redis_client import RedisManager

merge_broker = ListQueueBroker(
    settings.redis_url,
    queue_name="merge_queue",
)

@merge_broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _startup(state: TaskiqState):
    await RedisManager.init()
    print("Merge worker started")

@merge_broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _shutdown(state: TaskiqState):
    await RedisManager.close()
    print("Merge worker stopped")

# 关键：在文件末尾显式导入归属于该 Broker 的任务，完成自动注册
import app.tasks.merge_tasks  # noqa: E402, F401
```

#### 2. 对应的任务文件写法 (`app/tasks/merge_tasks.py`)

Python

```
# app/tasks/merge_tasks.py
from app.brokers.merge_broker import merge_broker

@merge_broker.task(task_name="merge_file_task")
async def merge_file_task(file_id: str):
    # 处理文件合并逻辑
    return {"status": "ok", "file_id": file_id}
```

### 四、拆分后的终极简化启动命令

拆分并自动导入后，无论你有 1 个任务还是 20 个任务，启动命令都精简为一句话，不需要带任何任务路径后缀：



Bash

```
# 终端 1：启动文件合并 Worker
taskiq worker app.brokers.merge_broker:merge_broker -w 2

# 终端 2：启动 Agent 智能体 Worker
taskiq worker app.brokers.agent_broker:agent_broker -w 2
```

此时：



1. **启动极简**：命令只认 Broker 对象。
2. **依赖隔离**：Merge 进程不会加载哪怕一行 Milvus / RAG 相关的代码与模型，启动极快且节省内存。
3. **职责清晰**：新增任务只需要在对应 Broker 文件的尾部添加一行 `import`，或者在统一的 `tasks` 包中管理。

### 问：你说“在文件末尾显式导入归属于该 Broker 的任务，完成自动注册”，那如果导入了不属于该broker的任务会报错还是不会有什么影响？另外请提供一份用 Docker Compose 同时部署和管理这两个 TaskIQ Worker 的配置示例。

### 一、导入了不属于该 Broker 的任务会怎样？

**结论：不会直接报错，代码能正常跑，但会产生严重的“副作用”，违背了做架构拆分的初衷。**



#### 1. 为什么不会报错？

在 TaskIQ 中，任务注册是在**装饰器执行时**完成的：



Python

```
# app/tasks/agent_tasks.py
@agent_broker.task(task_name="agent_run_task")
async def agent_run_task(...):
    pass
```

当你执行 `@agent_broker.task` 时，这个函数只会被登记在 `agent_broker` 的内部任务字典里。

如果你的 `merge_broker.py` 错误地导入了 `app.tasks.agent_tasks`：



- Python 解释器只是正常执行了这段代码，并把任务挂载到了内存中的 `agent_broker` 上。
- `merge_broker` 启动后**依然只会监听 `merge_queue` 队列**，只有当 `merge_queue` 收到消息时，它才会去查自己的任务列表。
- 因此 Worker 不会崩溃，语法和逻辑上都是“静默通过”的。

#### 2. 会带来哪些隐性危害？

虽然不会报错，但有三点非常致命的问题：



1. **彻底打破资源与内存隔离（最核心问题）**：

   `agent_tasks` 往往包含 LangChain、LlamaIndex、PyTorch、Milvus 或大型 SDK。如果 `merge_broker` 导入了它，原本只需要 50MB 内存的轻量文件 Worker，启动瞬间就会吃掉 500MB~1GB 内存，彻底失去了多 Worker 拆分的意义。

2. **引发循环导入（Circular Import）**：

   如果 `agent_tasks` 某些地方依赖了辅助模块，而该辅助模块又引用了 `merge_broker`，错综复杂的导入极易直接触发 Python 的 `ImportError: cannot import name ... from partially initialized module`。

3. **日志与状态混淆**：

   如果两个任务文件中包含模块级的全局初始化代码（如连接池、日志器），不当的导入会导致 Worker 在启动时做很多与自身无关的无用功。

**最佳原则**：**各扫门前雪**。`merge_broker.py` 只导入 `@merge_broker.task` 修饰的任务；`agent_broker.py` 只导入 `@agent_broker.task` 修饰的任务。



### 二、Docker Compose 部署与编排方案

在生产或测试环境中，最标准的做法是：**同一个代码镜像（Image），通过覆盖 `command` 启动不同角色的容器**。



#### 1. 目录结构参考

Plaintext

```
backend/
├── app/
│   ├── brokers/
│   │   ├── merge_broker.py
│   │   └── agent_broker.py
│   ├── tasks/
│   ├── main.py
│   └── ...
├── Dockerfile
└── docker-compose.yml
```

#### 2. Dockerfile 示例

Dockerfile

```
FROM python:3.11-slim

WORKDIR /app

# 防止 Python 缓冲输出（保证 taskiq 和 fastapi 的 print 日志能实时输出到 docker logs）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 安装必要的基础依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 默认命令（Compose 中会按需覆盖）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### 3. docker-compose.yml 配置

YAML

```
version: "3.8"

services:
  # 1. Redis 消息队列与缓存
  redis:
    image: redis:7-alpine
    container_name: app_redis
    ports:
      - "6379:6379"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    networks:
      - backend-network

  # 2. FastAPI 后端主服务
  api:
    build: .
    container_name: app_api
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      redis:
        condition: service_healthy
    networks:
      - backend-network

  # 3. 文件合并 Worker (轻量级，侧重 I/O，配置适度并发)
  worker-merge:
    build: .
    container_name: app_worker_merge
    restart: unless-stopped
    # 覆盖默认 CMD，启动 merge_broker，开启 2 个并发 Worker
    command: taskiq worker app.brokers.merge_broker:merge_broker -w 2
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      redis:
        condition: service_healthy
    networks:
      - backend-network

  # 4. Agent Worker (重依赖，高 CPU / 显存占用，可配置资源限制)
  worker-agent:
    build: .
    container_name: app_worker_agent
    restart: unless-stopped
    # 覆盖默认 CMD，启动 agent_broker
    command: taskiq worker app.brokers.agent_broker:agent_broker -w 2
    environment:
      - REDIS_URL=redis://redis:6379/0
      # 可以在此添加 Agent 所需的额外变量
      # - MILVUS_HOST=...
      # - OPENAI_API_KEY=...
    depends_on:
      redis:
        condition: service_healthy
    # 可选：对 Agent 容器进行资源限制，防止它跑满宿主机内存影响 API 服务
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
    networks:
      - backend-network

networks:
  backend-network:
    driver: bridge
```

### 三、常用运维命令

Bash

```
# 1. 一键构建并启动所有服务（后台运行）
docker compose up -d --build

# 2. 查看特定 Worker 的实时执行日志
docker compose logs -f worker-merge
docker compose logs -f worker-agent

# 3. 水平扩展 Worker 数量（例如高峰期文件合并压力大）
docker compose up -d --scale worker-merge=3
```

这样编排的好处在于：



- 所有的镜像构建只需要一次（共享同一个 Dockerfile）。
- `worker-merge` 和 `worker-agent` 容器互不干扰，甚至可以单独对某一个容器做资源限额（CPU / Memory Limit）或单独水平伸缩。



### 问：总结下来，TaskIQ定义不同的broker主要是为了做不同worker需要的资源隔离用的。而真正要做到任务队列的隔离实际上是需要通过指定queue_name才能够做到。而没指定broker的queue_name的话是不是会被分配一个默认的queue_name？

**你的理解完全准确，总结得非常切中本质。**



### 1. 关于默认的 `queue_name`

**是的，如果不显式指定，会被赋予默认值。**



在 `taskiq_redis` 的源码实现中，`ListQueueBroker` 的构造函数签名大致如下：



Python

```
class ListQueueBroker(AsyncBroker):
    def __init__(
        self,
        url: str,
        queue_name: str = "taskiq",  # 默认值就是 "taskiq"
        ...
    ):
```

如果不传 `queue_name`：



- 不管你定义了多少个 Broker 实例（例如 `merge_broker`、`agent_broker`），它们底层在 Redis 里读写的都是**同一个 Key（名为 `taskiq` 的 List）**。
- 发送任务时执行 `LPUSH taskiq ...`，Worker 监听时执行 `BRPOP taskiq ...`。两个 Worker 进程都在同一个列表里抢数据，后启动的或者手慢的 Worker 抢到不属于自己的任务后，就会触发 `task not found`。

### 2. Broker 与 Queue 的分工定位

这两者在架构设计上解决的是两个层面的问题：



| **维度**                   | **核心作用**                                | **解决的具体问题**                                           |
| -------------------------- | ------------------------------------------- | ------------------------------------------------------------ |
| **`queue_name`（队列名）** | **通信与数据隔离**  *(物理层面的信箱)*      | 确保“合并任务”发到合并信箱，“Agent 任务”发到 Agent 信箱，**任务不会被错误的进程抢占**。 |
| **`Broker`（实例对象）**   | **资源与环境隔离**  *(Python 进程的上下文)* | 决定 Worker 启动时要加载哪些依赖、运行哪些事件钩子（Startup/Shutdown）、占用多少内存与 CPU。 |

### 总结两者的搭配关系

- **只改 `Broker` 不配 `queue_name`**：两个进程依赖虽然各自写了，但都在抢同一个队列，直接报冲突或丢任务。
- **配好了 `queue_name` 但放在同一个 `Broker` / 同一个文件**：任务不会抢错，但无法做冷热依赖隔离，文件 Worker 依然会被迫加载重型的 RAG/大模型代码。
- **独立 Broker + 专属 `queue_name`（最佳实践）**：**一个 Broker 绑定一个独立队列**。既保证了消息投递的精准分流，又保证了不同任务对应 Worker 进程的极致轻量与安全隔离。