### 问：对于langchain的ChatOpenAI,ChatDeepSeek等库，我知道它们底层是维护了TCP连接池的，所以最好以单例模式全局复用比较好。但是在一个多用户的系统中，如果支持用户使用它们自定义的model_name,base_url,api_key，那么ChatOpenAI,ChatDeepSeek不就得每次都重新初始化了吗？

你的直觉非常敏锐，这确实是开发多租户 AI 平台（如支持用户自带 API Key / 自定义代理的 SaaS、Agent 系统）时最核心的性能与架构痛点之一。



如果对每个请求都直接 `ChatOpenAI(...)` 重新初始化，每次都会新建底层的 `httpx.Client` / `httpx.AsyncClient`，导致：



1. **握手延迟剧增**：每次请求都要重新走 TCP 三次握手和 TLS 协商（访问海外 OpenAI/DeepSeek 节点，额外开销通常在 100ms ~ 300ms 以上）。
2. **端口耗尽风险**：高并发下频繁创建和销毁连接，会导致大量的 Socket 处于 `TIME_WAIT` 状态，拖垮服务器。

要解决这个问题，需要从 **“TCP 连接池”** 与 **“LangChain 对象模型”** 的解耦入手。以下是工业级落地的核心方案：



### 方案一：共享底层的 `httpx.AsyncClient`（最底层、最高效的解法）

很多开发者不知道的是：**TCP 连接池只认 `Host:Port`，根本不在乎 `api_key` 和 `model_name`。**



- 用户的 `api_key` 只是 HTTP 请求头中的 `Authorization: Bearer <key>`。
- 用户的 `model_name` 只是 POST Body 里的一个 JSON 字段。
- 只要目标 `base_url` 相同（例如都是 `[https://api.deepseek.com/v1](https://api.deepseek.com/v1)`），无论多少个用户、使用多少个不同的 Key，**它们都可以安全地复用同一批底层 TCP/TLS 长连接**。

`ChatOpenAI` 允许传入自定义的 `http_async_client`。因此，你可以**全局维护一个 `httpx.AsyncClient` 实例，在请求到来时轻量级创建 `ChatOpenAI`**：



Python

```
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx
from langchain_openai import ChatOpenAI

# 1. 全局连接池（生命周期跟随服务）
shared_client: httpx.AsyncClient | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global shared_client
    # 配置全局连接池参数：保持长连接，设置最大连接数
    shared_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=200),
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    yield
    await shared_client.aclose()

app = FastAPI(lifespan=lifespan)

# 2. 运行时动态构造轻量级包装器
def get_user_chat_model(
    api_key: str, 
    base_url: str, 
    model_name: str
) -> ChatOpenAI:
    # 构造 ChatOpenAI 本身只是微秒级的 Python 对象实例化，真正的“重开销”（TCP/TLS）全在 shared_client 里
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        http_async_client=shared_client, # 共享连接池！
        temperature=0.7,
    )
```

> **原理**：`ChatOpenAI` 内部会将请求委托给 `openai.AsyncOpenAI`，后者把具体网络请求派发给 `shared_client`。由于 `shared_client` 内部维护了 `httpcore.ConnectionPool`，请求发往相同域名时会自动重用空闲 TCP 连接。

### 方案二：基于配置指纹的 LRU / TTL 实例缓存（业务层解法）

在多用户对话系统中，同一个用户的操作往往具有**时间局部性**（一个用户在几分钟内会连续发送数条消息，或者一个 Agent 任务内部会执行 5~20 次 LLM 调用）。



如果不想每次都重新生成 `ChatOpenAI` 对象，可以维护一个 **带有容量与过期时间的实例池（TTL + LRU）**：



Python

```
import hashlib
from cachetools import TTLCache
from langchain_openai import ChatOpenAI

# 缓存最近活跃的 1000 个配置实例，闲置 10 分钟自动过期释放
llm_cache = TTLCache(maxsize=1000, ttl=600)

def get_cached_chat_model(api_key: str, base_url: str, model_name: str) -> ChatOpenAI:
    # 用配置特征生成唯一缓存键
    raw_key = f"{base_url}|{api_key}|{model_name}"
    cache_key = hashlib.sha256(raw_key.encode()).hexdigest()
    
    if cache_key in llm_cache:
        return llm_cache[cache_key]
    
    # 未命中则创建并缓存
    instance = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
    )
    llm_cache[cache_key] = instance
    return instance
```

### 方案三：连接与模型解耦（针对同 Key 切换模型）

如果同一个用户只是频繁切换 `model_name`（例如对话用 `deepseek-chat`，写代码切 `deepseek-coder`），不要为此建立不同的实例：



Python

```
# 缓存仅按 (base_url, api_key) 维度
base_llm = get_cached_client(base_url=user_base_url, api_key=user_api_key)

# 动态绑定具体 model，完全零初始化开销
response = await base_llm.bind(model=user_model).ainvoke(messages)
```

### 生产环境必须防范的两个隐患

1. **针对自定义 `base_url` 的 SSRF 攻击**：

   如果允许终端用户自定义 `base_url`，恶意用户可能会填入内网地址（如 `[http://192.168.1.1](http://192.168.1.1)`、AWS 元数据地址 `[http://169.254.169.254](http://169.254.169.254)`）借你的服务器发起探测。必须在构建请求前校验域名，屏蔽内网保留 IP 网段。

2. **连接泄露问题**：

   如果使用字典做本地缓存，千万不要使用无界的 `dict`，否则用户长期累积会导致海量废弃 client 堆积在内存中，最终导致连接泄漏和内存溢出。一定要用 `TTLCache` 或 `LRUCache` 限制上限并自动驱逐。

### 问：嗯，对于方案一，我感觉很巧妙。不过这应该是基于一个前提，那就是每次ChatOpenAI(...) 重新初始化的开销之所以大，其实是因为内部TCP连接池的建立，ChatOpenAI(...) 其它代码部分其实开销很小。因此只要自己维护一个全局TCP连接池，然后ChatOpenAI初始化时将我们维护的传入即可

你的理解**完全正确，且抓住了最本质的架构分工**。



这个方案之所以成立，本质原因正是：**Python 对象的内存分配与字段校验开销在微秒（$\mu s$）级别，而网络协议栈与传输层的连接开销在毫秒（$ms$）级别，二者相差上千倍。**



我们可以拆开看这两部分的具体开销与内部机制：



### 一、轻量部分：`ChatOpenAI(...)` 实例化到底在做什么？

当你执行 `ChatOpenAI(model=..., api_key=..., http_async_client=...)` 时，它做的事情非常纯粹：



1. **Pydantic 字段验证**：`ChatOpenAI` 继承自 Pydantic 的 `BaseModel`，初始化仅涉及属性类型校验、默认值填充（如 `temperature=0.7`）、将 `api_key` 包装为 `SecretStr`。
2. **OpenAI SDK 客户端包装**：在内部实例化一个 `openai.AsyncOpenAI` 对象。如果你传入了现成的 `http_async_client`，SDK 内部只会执行一行简单的引用赋值：`self._client = http_async_client`，而**不会去初始化新的网络传输器（Transport）**。

这一系列操作纯粹是内存分配与 CPU 运算：



- **耗时**：通常在 **0.1 ms ~ 0.5 ms（100~500 微秒）** 左右。
- **资源释放**：请求结束后，`ChatOpenAI` 实例随着局部变量作用域结束被 Python 垃圾回收（GC）。因为它没有持有底层的 Socket 文件描述符（File Descriptor），GC 回收极快，毫无负担。

### 二、重量部分：为什么默认的底层连接开销极其昂贵？

如果**不传入**全局连接池，每次实例化都会让 SDK 内部去 `new` 一个 `httpx.AsyncClient()`，代价非常高昂：



```
[无共享连接池]
用户请求 ──> 初始化 httpx.Client ──> DNS 解析 ──> TCP 三次握手 (1 RTT) ──> TLS 1.3 协商 (1~2 RTT) ──> 发送 Prompt ──> 销毁 Client ──> TCP FIN/ACK ──> Socket 进入 TIME_WAIT
```

- **延迟惩罚**：如果你的服务在国内或香港，而 LLM 节点（如 OpenAI、DeepSeek 海外节点）在美西或欧洲，单次 RTT 就在 100ms ~ 200ms 左右。一次完整的 TCP + TLS 握手，直接让首字延迟（TTFT）凭空增加 **200ms ~ 500ms**。
- **系统资源惩罚**：连接关闭后，操作系统会将端口保持在 `TIME_WAIT` 状态（通常持续 60 秒）。高并发下，频繁新建销毁连接会迅速耗尽系统的可用本地端口（Ephemeral Ports），导致报 `Cannot assign requested address`（连接池耗尽）错误。

### 三、底层连接池是如何处理“多租户 / 多域名”的？

你可能会有疑问：*“如果用户 A 访问的是 DeepSeek，用户 B 访问的是 OpenAI，用户 C 填的是自己的中转代理，同一个全局连接池能搞定吗？”*



答案是：**完全可以，而且原生支持。**



`httpx.AsyncClient` 底层依赖的是 `httpcore.AsyncConnectionPool`，其内部的连接组织逻辑是：



1. **按 `(scheme, host, port)` 划分子池**：
   - 连接池内部并不是一条管子，而是一个哈希映射表。
   - 发往 `api.deepseek.com:443` 的请求会自动从 DeepSeek 的长连接队列中取连接复用；发往 `api.openai.com:443` 的会自动复用 OpenAI 的长连接。
2. **连接级别无状态，请求级别有状态**：
   - 用户的 `api_key`（HTTP Header）和 `model_name`（JSON Body）都是**在发送具体的 HTTP Request 时动态挂载的**。
   - 底层的 TLS/TCP 连接只是一条安全的“数据管道”，管道本身不记录、也不绑定任何用户的鉴权身份，完全不存在多租户之间的串号或凭证泄漏风险。

### 四、最佳实践提醒：全局连接池的正确初始化姿势

为了让全局连接池能无缝服务于所有自定义域名的用户，**创建全局 Client 时切记保持“纯净”**：



Python

```
# 正确：只配置通用网络行为（连接数、Keep-Alive 时间、网络超时）
global_http_client = httpx.AsyncClient(
    limits=httpx.Limits(
        max_keepalive_connections=100, # 允许保持的长连接总数
        max_connections=500,           # 最大并发连接数
        keepalive_expiry=30.0          # 空闲连接保持 30 秒
    ),
    timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
    # 绝对不要在此处传 base_url 或 headers，保持完全无状态
)
```

这样，`ChatOpenAI` 每次轻量化创建时，只需负责提供 **`api_key`、`base_url`、`model`** 即可，重型的连接协商与长连接保活完全由底层的 `global_http_client` 默默处理。既获得了动态多租户的灵活性，又吃满了长连接池的性能红利。

### 问：

```
import asyncio

from langchain_deepseek import ChatDeepSeek
from app.core.config import settings
from langchain_core.messages import HumanMessage

class ModelClient:
    def __init__(
            self,
            base_url: str,
            model_name: str,
            api_key: str
    ):
        self.base_url = base_url
        self.model_name = model_name
        self.api_key = api_key
        self._model: ChatDeepSeek | None = None

    async def startup(self) -> None:
        if self._model is None:
            self._model = ChatDeepSeek(
                model_name=self.model_name,
                api_key=self.api_key,
                base_url=self.base_url
            )

    @property
    def model(self) -> ChatDeepSeek:
        if self._model is None:
            raise RuntimeError(f"LLM Model has not been initialized")
        return self._model


model_client = ModelClient(settings.base_url, settings.model_name, settings.api_key)

async def main():
    await model_client.startup()
    model = model_client.model
    response = await model.ainvoke([HumanMessage(content="你好")])
    print(response)

if __name__ == "__main__":
    asyncio.run(main())
async def call_model(state: OverAllState, config: RunnableConfig):
    messages = state["messages"]
    model_with_tools = model_client.model.bind_tools(tools)
    response = await model_with_tools.ainvoke(messages, config)
    return {"messages": [response]}

```

### 这是我当前管理ChatDeepSeek对象实例的代码，因为之前只考虑到了所有用户共享系统提供的模型，如果改成支持用户自定义模型api key, model_name,url（当前是只考虑ChatDeepSeek），是不是就得改成维护httpx.AsyncClient 连接池了？

**是的，完全正确。**



你现有的 `ModelClient` 是典型的“单套静态配置”单例模式，把特定的 `api_key`、`base_url`、`model_name` 与模型实例深度绑定了。一旦要支持多租户自定义，它就不能再作为持有单一模型实例的全局单例存在。



最优雅、性能最优的改造方式是：**将单例持有的对象从“具体的 `ChatDeepSeek`”下沉为“全局 `httpx.AsyncClient` 连接池”，并将 `ModelClient` 改造为一个轻量的“模型工厂（Factory）”。**



### 改造后的代码实现

配合你代码中出现的 `RunnableConfig`（LangChain / LangGraph 针对多用户动态传参的标准机制），整体重构如下：



Python

```
import asyncio
from typing import Optional
import httpx
from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from app.core.config import settings

# -------------------------------------------------------------
# 1. 全局连接池与模型管理器（单例）
# -------------------------------------------------------------
class LLMClientManager:
    def __init__(self):
        self._async_client: Optional[httpx.AsyncClient] = None

    async def startup(self) -> None:
        """应用启动时调用，初始化全局底层 TCP 连接池"""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=50,  # 允许保持的长连接数
                    max_connections=200,          # 最大并发连接数
                    keepalive_expiry=30.0         # 连接空闲超时时间（秒）
                ),
                timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
            )

    async def shutdown(self) -> None:
        """应用退出时调用，优雅关闭连接池"""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()

    def get_model(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
    ) -> ChatDeepSeek:
        """
        轻量级工厂方法：
        传入用户自定义参数；若用户未提供，则回退到系统默认 settings。
        通过挂载全局 _async_client，完全省去新建 TCP/TLS 握手的开销。
        """
        if self._async_client is None:
            raise RuntimeError("LLMClientManager has not been started! Call startup() first.")

        return ChatDeepSeek(
            model=model_name or settings.model_name,
            api_key=api_key or settings.api_key,
            base_url=base_url or settings.base_url,
            http_async_client=self._async_client,  # <--- 核心：挂载全局连接池
            temperature=temperature,
        )

# 全局单例管理器
client_manager = LLMClientManager()


# -------------------------------------------------------------
# 2. 与 LangGraph / RunnableConfig 结合的 call_model
# -------------------------------------------------------------
# 在多租户系统中，用户的凭证通常从请求头/上下文提取，
# 并通过 ainvoke(..., config={"configurable": {...}}) 传入。

async def call_model(state: dict, config: RunnableConfig):
    messages = state["messages"]
    
    # 动态从 config 中获取当前用户的个性化配置（若无则取 None，走系统默认）
    configurable = config.get("configurable", {})
    user_api_key = configurable.get("api_key")
    user_base_url = configurable.get("base_url")
    user_model_name = configurable.get("model_name")

    # 毫秒级构造复用长连接的实例
    dynamic_model = client_manager.get_model(
        api_key=user_api_key,
        base_url=user_base_url,
        model_name=user_model_name,
    )
    
    # 绑定工具并调用
    # model_with_tools = dynamic_model.bind_tools(tools)
    response = await dynamic_model.ainvoke(messages, config)
    return {"messages": [response]}


# -------------------------------------------------------------
# 3. 示例执行验证
# -------------------------------------------------------------
async def main():
    # 模拟应用启动
    await client_manager.startup()

    try:
        # 场景 A: 走系统默认配置
        default_model = client_manager.get_model()
        res1 = await default_model.ainvoke([HumanMessage(content="你好")])
        print("默认配置响应:", res1.content)

        # 场景 B: 走用户自定义的配置（多租户调用）
        # 即使这里传入了第三方代理 URL 或专属 Key，底层连接池也会自动按 Host 分流并复用
        user_config: RunnableConfig = {
            "configurable": {
                "api_key": "sk-user-custom-key",
                "base_url": "https://api.deepseek.com",
                "model_name": "deepseek-chat"
            }
        }
        res2 = await call_model({"messages": [HumanMessage(content="测试自定义用户调用")]}, user_config)
        print("自定义配置响应:", res2)

    finally:
        # 模拟应用关闭
        await client_manager.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

### 为什么这样做最合理？

1. **生命周期对齐**：网络套接字（Socket）的生命周期与 **Web 进程（如 FastAPI lifespan）** 对齐，而不是与请求或用户对齐。
2. **零握手惩罚**：`client_manager.get_model(...)` 只是实例化一个 Python 对象，耗时小于 0.2ms；由于复用了 `_async_client`，真正的 HTTP 请求不会重复走 TCP 三次握手和 TLS 协商。
3. **架构无缝接入 LangChain 生态**：利用 `RunnableConfig["configurable"]` 是 LangChain 官方推荐的向 Graph 内部注入每请求元数据（用户上下文、隔离凭证）的标准范式，无需改动全局状态或修改图的输入 State 结构。

### 问：在 LangChain 流式输出 (Streaming) 场景下，使用共享 httpx.AsyncClient 连接池时，如果客户端中途断开连接，如何防止连接泄漏？

在流式输出（Streaming / SSE）场景下，使用共享 `httpx.AsyncClient` 连接池时，如果客户端突然断开（如用户关掉网页、取消请求），极易引发连接未归还连接池、Socket 处于半关闭状态、最终导致连接池耗尽（Pool Exhaustion）的问题。



### 一、泄漏的本质原因

在 `httpx` 的底层机制中：



1. **流式响应会独占连接**：发起流式请求时，`httpx` 会从连接池借出一条 Socket。
2. **连接归还的先决条件**：这条连接**只有在以下两种情况才会归还连接池（或彻底关闭）**：
   - 数据流被**完整消费完毕**；
   - 显式触发了底层响应的 `await response.aclose()`（通常由退出 `async with client.stream(...)` 上下文触发）。
3. **泄漏是如何发生的**：如果客户端中途断开，而后端继续向死连接发送数据，或者异常处理不当导致外层没有通知 LangChain 的生成器退出，底层的 `httpx.Response` 就无法正常执行退出逻辑，导致该 Socket 永远处于“被借出”状态。

### 二、工业级防护体系与代码实现

要彻底杜绝连接泄漏，需从 **代码生成器控制**、**FastAPI/ASGI 任务取消感知**、**反向代理透传** 以及 **httpx 参数兜底** 四个维度协同处理。



#### 1. 代码层：主动检测断开并触发清理（FastAPI 示例）

在处理流式请求时，必须在消费生成器的循环中主动检测客户端状态，并在断开时**显式退出循环**。当从 `async for` 中 `break` 时，Python 会自动调用生成器的 `aclose()`，进而触发底层 `httpx.Response` 的关闭流程。



Python

```
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
# 假设 client_manager 是之前维护全局 httpx.AsyncClient 的管理器

app = FastAPI()

@app.post("/chat/stream")
async def chat_stream(request: Request):
    # 1. 获取复用连接池的模型实例
    model: ChatOpenAI = client_manager.get_model()

    async def event_generator():
        # 获取 LangChain 的底层异步迭代器
        stream_iter = model.astream([HumanMessage(content="讲一个长故事")])
        
        try:
            async for chunk in stream_iter:
                # 核心点 1：每次推流前主动检查客户端是否已断开连接
                if await request.is_disconnected():
                    # 主动退出循环！
                    # 这一步会隐式触发 stream_iter 的 aclose()，释放底层的 httpx 连接
                    break
                
                yield f"data: {chunk.content}\n\n"
                
        except asyncio.CancelledError:
            # 核心点 2：ASGI 服务器感知到客户端断开时，通常会取消当前协程任务
            # 必须允许 CancelledError 正常传播，千万不要捕获后吞掉
            raise
        finally:
            # 核心点 3：确保收尾逻辑
            # 如果是自定义的流，也可以显式调用 await stream_iter.aclose()
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 告知 Nginx 不要缓冲，让断连信号能迅速传递
        },
    )
```

### 三、防止泄漏的四大关键防护点

#### 1. 绝不能吞掉 `asyncio.CancelledError`

在 Python 3.8+ 中，`CancelledError` 继承自 `BaseException` 而不是 `Exception`。



- **反模式**：写出 `except BaseException:` 或在 `except:` 中吞掉了异常，导致协程无法终止。
- **正确做法**：如果捕获了 `CancelledError`，只做必要的日志记录，然后必须将其重新 `raise` 出去，让 Python 运行时的清理机制（包括上下文管理器 `__aexit__`）完整走完。

#### 2. 配置合理且强制的 `httpx.Timeout`

即便代码逻辑严密，网络瞬断也可能导致连接挂死。全局连接池必须设置严格的超时阈值：



Python

```
httpx.AsyncClient(
    limits=httpx.Limits(
        max_keepalive_connections=50,
        max_connections=200,
        keepalive_expiry=30.0,
    ),
    timeout=httpx.Timeout(
        connect=5.0,     # 建连超时
        read=60.0,       # 单次 chunk 读取超时（防止大模型服务无响应卡死连接）
        write=5.0,
        pool=5.0,        # 从连接池等待获取连接的最大超时（极重要！防止无可用连接时协程无限卡死）
    )
)
```

> **注意 `pool` 超时**：如果没有设置 `pool` 超时（默认是 `None`），当连接池耗尽时，新进来的请求会永远阻塞等待空闲连接，最终拖垮整台 Web 服务器。设置 `pool=5.0` 可以在发生泄漏时及时抛出错误、熔断并报警。

#### 3. 关闭反向代理（如 Nginx）的输出缓冲

在前后端架构中，客户端断开往往发生在与 Nginx 之间：



- 如果 Nginx 开启了响应缓冲（`proxy_buffering on`），客户端断开时，Nginx 并不会立刻通知上游的 FastAPI，而是继续把 FastAPI 发送的内容读完存入缓冲区。

- 这会导致后端即使在客户端已关闭的情况下，仍继续跑完整个 LLM 生成，期间一直占用连接池。

- **解决方案**：在 Nginx 配置中针对 SSE 接口关闭缓冲：

  Nginx

  ```
  proxy_buffering off;
  proxy_cache off;
  proxy_read_timeout 600s;
  ```

  或者在代码返回头中加入 `X-Accel-Buffering: no`。

#### 4. 避免隐式无界生成器（Orphaned Generator）

如果直接在后台 Task 中运行 `model.astream(...)`，而没有通过 FastAPI 响应通道消费：



- 切勿让后台任务脱离生命周期管理。
- 如果要在后台任务中处理生成器，务必将流式消费包裹在 `try...finally` 中，并在任务需要停止时显式调用 `task.cancel()`。