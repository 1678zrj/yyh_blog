### 问：我在学习FastAPI后端开发和langchain、langgraph Agent构建，其中一个很重要的点就是发送请求获取大模型响应。在这个场景下，http和http2你更推荐哪个呢？

在 FastAPI + LangChain / LangGraph 调用大模型（无论是流式 SSE 还是非流式）的场景下，**更推荐使用配置了连接池的 HTTP/1.1，而不是盲目启用 HTTP/2**。



虽然 HTTP/2 在前端浏览器并发加载大量静态资源时优势明显，但在 Python 后端请求大模型这一特定场景下，HTTP/1.1 配合 Keep-Alive 是更稳定、坑更少的选择。



### 核心对比：LLM 场景下的特性表现

| **维度**              | **HTTP/1.1 (推荐)**            | **HTTP/2**                                 | **LLM 场景影响**                                             |
| --------------------- | ------------------------------ | ------------------------------------------ | ------------------------------------------------------------ |
| **并发模型**          | 独立 TCP 连接池                | 单 TCP 连接多路复用 (Multiplexing)         | HTTP/2 在单连接上跑几十个长流式推理时，**一旦丢包会导致所有流一起卡顿**（TCP 层队头阻塞）。 |
| **流式传输 (SSE)**    | 成熟稳定，代理/中间件极少缓冲  | 依赖流控制帧 (Window Update)，易与网关冲突 | Nginx、云 WAF 等反向代理常出现针对 HTTP/2 的缓冲策略，导致 Token 无法实时打字吐出。 |
| **Python 生态兼容性** | 极度成熟，无内存泄漏与死锁历史 | 依赖 `h2` 库，边缘场景偶发流挂死           | 官方 SDK（如 OpenAI、Anthropic）底层均为 `httpx`，且**默认禁用 HTTP/2**。 |
| **连接开销**          | 依赖 Keep-Alive 连接池复用     | 原生单连接复用                             | 配置好连接池后，HTTP/1.1 同样无需重复做 TCP/TLS 握手，延迟几乎零差距。 |
| **头部压缩 (HPACK)**  | 无                             | 有                                         | LLM 请求的 Prompt 和 Response Tokens 往往几百上千字节甚至更高，头部压缩省下的几十字节微不足道。 |

### 为什么大模型调用更契合 HTTP/1.1？

#### 1. 流量特征是“长耗时流式”，而非“高频并发小碎片”

大模型请求不是网页加载几十个 2KB 的图标，而是单次持续数秒到数十秒的流式输出（Time-to-First-Token + Streaming）。



- **HTTP/1.1 的行为**：每个并发请求占用连接池中一条独立的 TCP 管道，彼此网络完全隔离。
- **HTTP/2 的隐患**：如果同时有 50 个 Agent 节点并发调用同一个大模型 endpoint，它们会被压缩到同一条 TCP 连接中。只要公网出现轻微丢包，TCP 的滑动窗口重传机制会**冻结该连接上所有 50 个用户的 Token 吐出**。

#### 2. Python 客户端库的工程现状

在 Python 异步生态中：



- `aiohttp` 默认主推 HTTP/1.1。
- `httpx` 虽然通过额外安装 `httpcore[http2]` 支持 HTTP/2，但在超长时间长连接、网络波动中断恢复、异常断开（Client Disconnect）等场景下，HTTP/2 状态机的复杂度更高，历史上曾多次出现连接未正常释放或卡在读写锁的 issue。
- 包括 LangChain 内部封装的通信层以及 OpenAI / Anthropic 官方 Python SDK，底层默认都只使用 HTTP/1.1。

#### 3. 代理与网关的缓冲陷阱

当服务经过代理（如公网代理、企业网关、Nginx）转发至大模型厂商时，HTTP/2 的帧机制常常会遇到中间代理的默认缓冲行为。这容易破坏流式传输的低延迟预期，导致原本应该平滑输出的 Token 变成“卡住 3 秒，然后一次性喷出 100 个字”。



### 最佳工程实践：如何正确调优 HTTP/1.1

用 HTTP/1.1 不等于性能差。性能瓶颈往往出在**每次请求都新建连接**。在 FastAPI 和 LangGraph 中，正确的做法是维护全局单例的连接池。



#### 在 FastAPI Lifespan 中全局复用连接池

Python

```
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx
from langchain_openai import ChatOpenAI

# 1. 配置高效的 HTTP/1.1 异步连接池
limits = httpx.Limits(
    max_keepalive_connections=50,  # 保持保活的连接数
    max_connections=200,            # 最大并发连接数
    keepalive_expiry=30.0          # 保活过期时间 (秒)
)

timeouts = httpx.Timeout(
    connect=5.0,     # 建连超时
    read=60.0,       # LLM 推理生成耗时长，read 必须放宽
    write=10.0,
    pool=5.0
)

client_state = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化全局单一 httpx.AsyncClient (HTTP/1.1)
    async_client = httpx.AsyncClient(
        limits=limits,
        timeout=timeouts,
        http2=False  # 明确保持 HTTP/1.1
    )
    
    # 注入到 LangChain / LangGraph 的 Model 实例中
    llm = ChatOpenAI(
        model="gpt-4o",
        http_async_client=async_client
    )
    
    client_state["llm"] = llm
    client_state["client"] = async_client
    
    yield
    
    # 优雅关闭
    await async_client.aclose()

app = FastAPI(lifespan=lifespan)
```

### 何时才考虑切到 HTTP/2 或类似协议？

- **自建私有推理集群内网通信（如 vLLM / Triton Server）**：通过 **gRPC**（底层运行于 HTTP/2）进行内网跨服务通信，走内网专线丢包率接近零，能充分发挥二进制帧和多路复用的优势。
- **高频低耗时计算**：只请求轻量 Embedding 向量化、或者超低延迟分类模型（单次耗时 < 50ms），且请求并发极高。
- **双向实时音频推流（Voice Agent）**：如 OpenAI Realtime API，此时通常直接使用 **WebSocket** 或 **WebRTC**，而非标准的 HTTP API。

### 问：好吧，那还是使用http/1.1吧，那http/1.1似乎是TCP连接被某个请求占用的时候就不能处理其它用户的请求了。那在Agent问答的场景下，它和用户并发数的关系是什么呢？

**“单个 TCP 连接同一时刻只能处理一个请求”仅针对单一物理连接而言，它并不限制系统的全局并发。** 系统的并发能力是由**异步事件循环**与HTTP 连接池（Connection Pool）共同决定的。



在基于 FastAPI 和 LangGraph 的 Agent 问答架构中，并发实际上被拆解为两个完全独立的物理层级：



```
[前端用户 1..N] 
      │ (层级 1: 下行长连接 / SSE)
      ▼
[FastAPI / Uvicorn (单进程单线程异步事件循环)]
      │ (层级 2: 上行连接池 httpx.AsyncClient)
      ▼
[LLM 供应商 API (OpenAI / vLLM)]
```

### 层级 1：用户 $\leftrightarrow$ FastAPI（下行并发）

用户通过浏览器发起问答请求，通常建立的是 HTTP/1.1 的长连接（例如 SSE 流式推送）。



- **并发机制**：FastAPI 底层的 Uvicorn 依赖操作系统底层的 I/O 多路复用（Linux 下的 `epoll`）。
- **资源消耗**：当用户在等待模型打字吐字时，网络连接处于“监听可写”状态，并不会阻塞 Python 的 CPU。单台服务器仅需消耗极少的内存（文件描述符 File Descriptor），**单进程轻松挂载数千个同时处于接收状态的客户端长连接**。

### 层级 2：FastAPI $\leftrightarrow$ 大模型 API（上行并发）

当 FastAPI 后端需要向大模型（如 OpenAI、DeepSeek 或私有 vLLM）发请求时，`httpx.AsyncClient` 的工作机制是：



1. **连接池借还机制**：
   - 每一个发起的大模型请求，会从连接池借出 **1 条** 空闲的 TCP 物理连接。
   - 请求传输与流式接收期间（通常持续 3～15 秒），该连接被**独占**。
   - 最后一个 Token 接收完毕或连接断开后，该 TCP 连接**不会关闭**，而是被归还给连接池供后续请求复用（Keep-Alive）。
2. **并发上限受制于池大小**：
   - 如果配置了 `max_connections=100`，意味着后端同时**最多只能有 100 个正在与大模型通信的 TCP 链路**。

### Agent 场景下的并发动态关系

在传统单次对话（Simple Chat）中：`1 个在线用户 ≈ 连续占用 1 条 LLM 连接 5 秒`。



但在 **LangGraph / ReAct Agent** 场景下，**用户的并发数与 LLM TCP 连接数并不是 1:1 的直接绑定关系**，它呈现出明显的“脉冲式”占用特征：



```
用户请求进来
   │
   ├─► [阶段 1] LLM 推理决定调用工具  ──► 借出 1 条 TCP 连接 (耗时 ~1s)
   │                                  └── 归还连接到池
   │
   ├─► [阶段 2] Agent 执行外部工具   ──► 0 条 LLM 连接 (此时查向量库/执行 Python，耗时 ~2s)
   │
   ├─► [阶段 3] LLM 总结最终答案      ──► 再次借出 1 条 TCP 连接 (耗时 ~3s)
   │                                  └── 归还连接到池
   ▼
流程结束
```

#### 瞬时并发计算模型

假设系统的并发表现如下：



- **活跃用户并发数**：$N$（正在等待 Agent 完成任务的用户数）
- **单次 Agent 任务平均总耗时**：$T_{total}$（例如 8 秒）
- **其中真正调用 LLM 的累计耗时**：$T_{llm}$（例如 4 秒）

那么后端瞬时向大模型发起的 TCP 连接数 $C_{active}$ 约等于：



$$C_{active} \approx N \times \frac{T_{llm}}{T_{total}}$$

**在 Agent 存在大量工具调用（检索、计算、API交互）时，占空比 $\frac{T_{llm}}{T_{total}}$ 往往显著小于 1。** 这意味着一个仅配置了 `max_connections=100` 的连接池，理论上可以支撑 **150 ~ 200 个并发的 Agent 任务流程** 平稳流转。



### 当用户并发超过连接池上限时会发生什么？

如果配置了 `max_connections=50`，而瞬间涌入了 70 个需要调大模型的请求：



1. **排队等待（Queuing）**：

   前 50 个请求立刻拿到连接与大模型通信。第 51~70 个请求会在 `asyncio` 内部挂起排队，等待前面先完成的请求释放连接。

2. **缓冲池超时（Pool Timeout）**：

   若前面的请求生成耗时过长，排队的请求等待时间超过了设置的 `pool_timeout`（如默认 5 秒），`httpx` 会直接抛出 `httpx.PoolTimeout` 异常。

### 真正的系统瓶颈优先级

在真实的 Agent 生产架构中，HTTP/1.1 连接池本身几乎从来不是最先倒下的环节。瓶颈通常按以下顺序暴露：

| **瓶颈排位** | **环节**                        | **实际表现**                                                 | **解决方案**                                             |
| ------------ | ------------------------------- | ------------------------------------------------------------ | -------------------------------------------------------- |
| **Top 1**    | **LLM 供应商频控 (Rate Limit)** | 触发 HTTP 429 报错（RPM: 每分钟请求数 或 TPM: 每分钟 Token 数被打满）。 | 接入多 Key 轮询、增加供应商配额、上缓存。                |
| **Top 2**    | **FastAPI 内存 (RAM)**          | 每个进行中的 LangGraph 节点都维护着巨大的上下文状态（State/Messages），并发达上千时内存迅速吃紧。 | 状态瘦身、缩短上下文轮数、外部化存储 Checkpoint。        |
| **Top 3**    | **工具执行链超时**              | 多个 Agent 并发检索向量库或外部 API，导致数据库连接池被打爆。 | 工具调用侧加独立的连接池和熔断降级。                     |
| **Top 4**    | **HTTP 连接池与操作系统限制**   | `PoolTimeout` 或系统抛出 `Too many open files`。             | 调大 `max_connections` 并调高 Linux 系统的 `ulimit -n`。 |

### 问：好吧，那理论上单worker能够支持多少Agent并发呢

在纯异步架构下，一个单核 Python 进程（单 Uvicorn Worker）支持的 Agent 并发数：**理论物理极限在上万，但在生产工程实际中，单 Worker 的安全稳定并发通常在 200 ～ 1,000 之间**。



真正限制单 Worker 并发的并不是网络 I/O，而是 **CPU 计算（GIL）、内存占用** 以及 **事件循环延迟（Event Loop Lag）**。



### 三大核心瓶颈的深度推演

#### 1. CPU 与 GIL 瓶颈（最先被击穿的隐形杀手）

许多人认为大模型调用是纯 I/O 密集型，但**高并发下的流式 Agent 实际上是“微 CPU 密集型”任务**。

每个 Token 吐给前端时，Worker 内部都在执行一系列同步 CPU 逻辑：



- `httpx` 从底层 Socket 读取数据块并解包。
- LangChain / LangGraph 触发回调链（Callbacks），执行 State Reducer（合并新 Token 到历史消息）。
- Pydantic 模型校验与序列化、Tool Call 的 JSON 解析。
- FastAPI 封装 SSE 协议帧（`data: {...}\n\n`）并写入客户端 Socket。

**CPU 算力推演：**



- 在 Python 异步环境中，单个 Token 经过上述链路的处理开销大约为 **0.1 ～ 0.3 毫秒**。

- 单个 CPU 核心每秒仅有 **1000 毫秒** 的可用算力。

- 即使完全不考虑其他逻辑，单核单进程每秒最多处理 **3,300 ～ 10,000 个 Token/秒** 的吞吐。

- 假设每个并发 Agent 正在以平均 **25 Token/秒** 的速度流式打字：

  $$\text{单核并发上限} \approx \frac{1000\text{ ms}}{0.2\text{ ms/token} \times 25\text{ tokens/s}} \approx 200\text{ 个活跃打字流}$$

  当并发超过这个临界点，CPU 利用率会直接冲到 100%，导致事件循环严重卡顿（Loop Lag），甚至造成心跳超时、连接集体断开。

#### 2. 内存（RAM）开销

传统 Web 服务（如查 MySQL 查详情页）单个请求在内存中只有几 KB，存活几十毫秒就释放了。

而在 LangGraph Agent 中，单个运行态不仅占用时间长（5~30 秒），内存体积也极为庞大：



- **LangGraph 运行态（State）**：保存完整的 System Prompt、历史消息列表（数千 Token 的文本对象）、工具调用的元数据。
- **中间对象开销**：Pydantic V2 模型实例、Checkpoint 快照缓存、异步 Task 闭包环境。
- **单 Agent 内存消耗**：通常在 **200 KB ～ 5 MB** 不等（取决于上下文长度与工具返回的数据量）。

| **并发 Agent 数量** | **纯状态与任务内存估算** | **加上 Python 运行时堆开销** |
| ------------------- | ------------------------ | ---------------------------- |
| **100 并发**        | 50 MB ~ 200 MB           | 容易支撑                     |
| **500 并发**        | 250 MB ~ 1 GB            | 需 2G+ 规格容器              |
| **2000 并发**       | 1 GB ~ 5 GB+             | 极易触发 OOM 杀进程          |

#### 3. 操作系统与网络资源限制

这是最容易通过配置解决的一层：



- **文件描述符（FD）**：每个在线用户占用 1 个下行 Socket，每个活跃的 LLM/工具请求占用 1 个上行 Socket。如果并发 1000 个任务，瞬时可能需要 1500~2000 个 FD。Linux 默认 `ulimit -n` 常为 1024，必须提前上调至 65535。
- **连接池锁竞争**：`httpx` 连接池在超高并发申请/释放连接时，底层 `asyncio.Queue` 和锁会产生微小的竞争损耗，通常调大 `max_connections=500+` 即可。

### 不同业务场景下的单 Worker 并发量级

| **场景类型**                   | **业务特征**                                       | **单 Worker (1核 2G) 推荐承载上限** |
| ------------------------------ | -------------------------------------------------- | ----------------------------------- |
| **极轻量问答 (Simple Chat)**   | 无复杂 Agent 循环，Prompt 短，仅 SSE 流式转发      | **800 ~ 1,500 并发**                |
| **典型 ReAct Agent**           | 包含 1~2 次工具调用（查向量库、查SQL），中等上下文 | **300 ~ 600 并发**                  |
| **复杂/多模态/大上下文 Agent** | 状态庞大，数十轮对话，高频 JSON Tool Call 解析     | **100 ~ 200 并发**                  |

### 如何榨干单机性能，突破单 Worker 限制？

1. **进程级横向扩展（多 Worker）**：

   Python 受限于 GIL，永远不要试图在一个 Worker 里硬抗万级 Agent。利用 `gunicorn -k uvicorn.workers.UvicornWorker -w $(2 * CPU核心数 + 1)` 启动多进程，充分打满服务器的多核 CPU。

2. **极速序列化与事件传递**：

   - 采用 `orjson` 或 `ujson` 替代标准库的 `json` 来封装 SSE 载荷。
   - 减少在流式回调（`on_llm_new_token`）中做沉重数据处理，仅转发增量增量文本。

3. **架构解耦（Gateway 与 Runner 分离）**：

   如果并发规模进一步扩大（成千上万同时在线）：

   - **FastAPI 仅作为网关**：只负责维持前端的 SSE / WebSocket 连接，将用户指令推入消息队列（如 Redis Stream）。
   - **Agent 引擎后置**：由后端的分布式 Worker 池（如 TaskIQ / Celery）消费并执行复杂的 LangGraph 状态机，结果再发布回前端，避免单进程同时承载连接维持与图状态计算。