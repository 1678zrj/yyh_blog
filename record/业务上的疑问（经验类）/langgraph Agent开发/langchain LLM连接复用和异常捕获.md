```python
import asyncio
import httpx
from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from app.core.config import settings
from langchain_core.messages import HumanMessage
from httpx import AsyncClient
from enum import Enum

class LLMProvider(str, Enum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    CUSTOM = "custom"





"""
    进行连接池优化
    ChatDeepSeek不需要全局单例，
    真正需要全局单例的是底层维护的TCP连接池，因为ChatDeepSeek初始化的主要开销其实在TCP连接池上
    这样一来每次ChatDeepSeek初始化，都可以复用全局维护的TCP连接池，开销可以忽略不记
    就可以针对多用户的情况使用用户自己的model_name, api_key, base_url了，
    实现用户配置和TCP连接的解耦
"""
class LLMClientManager:
    def __init__(self):
        self._async_client: AsyncClient | None = None
        self._default_model_name = settings.model_name
        self._default_base_url = settings.base_url
        self._default_api_key = settings.api_key

    async def startup(self):
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=50,  # 允许报错的长连接数
                    max_connections=200,  # 最大并发连接数
                    keepalive_expiry=30.0  # 连接空闲超时时长
                ),
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=120.0,
                    write=10.0,
                    pool=10.0
                )
            )

    async def shutdown(self):
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()

    def get_model(
            self,
            provider: str | None = None,
            api_key: str | None = None,
            base_url: str | None = None,
            model_name: str | None = None
    ) -> BaseChatModel:
        """
            轻量级工厂方法：
            传入用户自定义参数；若用户未提供，则回退到系统默认 settings。
            通过挂载全局 _async_client，完全省去新建 TCP/TLS 握手的开销。
        """
        if self._async_client is None:
            raise RuntimeError("LLMClientManager has not been started. Call startup() first")
        if provider and api_key and base_url and model_name:
            if provider == LLMProvider.DEEPSEEK.value:
                return ChatDeepSeek(
                    model_name=model_name,
                    api_key=api_key,
                    base_url=base_url,
                    http_async_client=self._async_client
                )
            elif provider == LLMProvider.OPENAI.value:
                return ChatOpenAI(
                    model_name=model_name,
                    api_key=api_key,
                    base_url=base_url,
                    http_async_client=self._async_client
                )
            elif provider == LLMProvider.CUSTOM.value:
                return ChatOpenAI(
                    model_name=model_name,
                    api_key=api_key,
                    base_url=base_url,
                    http_async_client=self._async_client
                )
            else:
                raise RuntimeError(f"Unsupported provider: {provider}")
        else:
            return ChatDeepSeek(
                model_name=self._default_model_name,
                api_key=self._default_api_key,
                base_url=self._default_base_url,
                http_async_client=self._async_client # 核心：挂在全局连接池
            )


# model_container = ModelContainer(settings.base_url, settings.model_name, settings.api_key)
llm_client_manager = LLMClientManager()
```

### 问：我当前的提供多provider配置的模型代码是这样的，ChatOpenAI、ChatDeepSeek这些底层应该能够共享同一AsyncClient实例吧？

**可以，完全能够共享同一个 `AsyncClient` 实例。**



这种在应用层维护一个全局长连接池、将模型对象做成轻量级工厂的做法，是高并发 LLM 服务（如 FastAPI 网关）中非常标准的优化实践。



### 一、 为什么它们可以安全共享？

底层能安全共享的关键在于 **HTTP 客户端的无状态性** 和 **OpenAI SDK / LangChain 的请求封装机制**：



1. **按 Origin 自动隔离连接池**

   `httpx.AsyncClient` 内部根据 `(scheme, host, port)` 维度维护连接池。当请求发往 `api.deepseek.com` 和 `api.openai.com` 时，`httpx` 会自动在内部路由到各自独立的 Host 连接池，彼此互不干扰。

2. **认证信息与 Base URL 是请求级（Per-request）绑定的**

   你在创建全局 `AsyncClient` 时**没有**硬编码全局的 `base_url` 和 `headers`（这是完全正确的）。`ChatOpenAI` 和 `ChatDeepSeek` 底层依赖的 OpenAI Python SDK，在每次发起请求时，都会把对应模型的 `base_url` 拼装成完整 URL，并将该次调用的 `api_key` 放入单个请求的 `Authorization: Bearer <key>` Header 中。多用户、多 Provider 之间**绝对不会发生鉴权串号或配置污染**。

3. **模型生命周期不会误关全局连接池**

   OpenAI SDK 在检测到传入了外部自定义的 `http_client` 时，会标记 `self._custom_client = True`。当临时创建的 `ChatOpenAI` 实例被 Python 垃圾回收（GC）时，它**不会**去调用 `http_client.aclose()`，全局连接池依然安然无恙。

### 二、 实际落地必须注意的 4 个关键细节

虽然底层架构支持，但在当前代码的实际运行中，有几个隐患需要调整：



#### 1. 长流式输出（Streaming）与并发连接数上限

LLM 通常使用 SSE 流式输出，**一个流式响应会独占一条 TCP 连接数十秒甚至上百秒**。



- 你设置的 `max_connections=200` 意味着整个服务同时最多只能支撑 200 个并发生成任务。
- 如果达到 200 并发，第 201 个请求会在连接池等待 `pool=10.0` 秒，超时后会直接抛出 `httpx.PoolTimeout`。
- **建议**：根据你的业务并发量，适当调高 `max_connections`（如 500+），并将 `pool` timeout 放宽（如 30.0s），防止瞬间并发打满导致大量请求直接异常退出。

#### 2. 深度思考模型（如 DeepSeek-R1）的 Read 超时

`read=120.0` 对于普通闲聊足够，但面对 DeepSeek-R1、o1 等思考模型时，如果在高峰期或复杂推理场景下，首字延迟（TTFT）或单次生成极耗时，120 秒可能偶发超时中断。建议将 `read` 超时提升至 `300.0` 秒以上。



#### 3. 仅对异步方法生效（`ainvoke` / `astream`）

你只挂载了 `http_async_client`。如果业务代码中无意调用了同步方法（如 `model.invoke()`），LangChain 会退回到其默认机制重新创建同步客户端，无法享受该连接池加速。请确保业务链路统一使用 `ainvoke`、`astream` 等异步调用。



#### 4. Event Loop 绑定陷阱

`httpx.AsyncClient` 的底层连接和 Transport 是绑定在初始化时的那个 `asyncio.EventLoop` 上的。



- **正确做法**：在 FastAPI 的 `lifespan` 或异步启动入口（即同一个主事件循环内）调用 `startup()`。
- **错误做法**：在多线程中跨线程使用、或者在不同的 `asyncio.run(...)` 之间共享同一个 client 实例，会导致 `RuntimeError: Event loop is closed`。

### 三、 优化后的代码实现

在原代码基础上，解决了**参数全部必须提供才能生效的逻辑缺陷**（支持单项参数局部覆盖并回退默认值），同时增加了超时与异常保护：

Python

```
from enum import Enum
import httpx
from httpx import AsyncClient
from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI
from app.core.config import settings

class LLMProvider(str, Enum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    CUSTOM = "custom"


class LLMClientManager:
    def __init__(self):
        self._async_client: AsyncClient | None = None
        self._default_provider = LLMProvider.DEEPSEEK.value
        self._default_model_name = settings.model_name
        self._default_base_url = settings.base_url
        self._default_api_key = settings.api_key

    async def startup(self):
        """服务启动时调用（如 FastAPI 的 lifespan context）"""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=100,  # 适度增加空闲保活连接
                    max_connections=500,            # 应对并发流式调用
                    keepalive_expiry=60.0
                ),
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=300.0,  # 适配长思考模型（如 R1）的首字生成与大输出
                    write=10.0,
                    pool=30.0    # 避免突发并发时连接池排队瞬间爆掉
                )
            )

    async def shutdown(self):
        """服务停机时关闭全局连接池"""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()

    def get_model(
        self,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model_name: str | None = None,
        temperature: float = 0.7,
        **kwargs
    ) -> BaseChatModel:
        """
        轻量级工厂：
        支持单参数局部覆盖；未传入的参数平滑回退到 settings 默认配置。
        统一复用全局持久 TCP 连接池。
        """
        if self._async_client is None or self._async_client.is_closed:
            raise RuntimeError("LLMClientManager尚未启动，请在应用生命周期中先调用 startup()")

        target_provider = (provider or self._default_provider).lower()
        target_api_key = api_key or self._default_api_key
        target_base_url = base_url or self._default_base_url
        target_model_name = model_name or self._default_model_name

        common_kwargs = {
            "model_name": target_model_name,
            "api_key": target_api_key,
            "base_url": target_base_url,
            "temperature": temperature,
            "http_async_client": self._async_client,  # 共享长连接池
            **kwargs
        }

        if target_provider == LLMProvider.DEEPSEEK.value:
            return ChatDeepSeek(**common_kwargs)
        elif target_provider in (LLMProvider.OPENAI.value, LLMProvider.CUSTOM.value):
            return ChatOpenAI(**common_kwargs)
        else:
            raise ValueError(f"Unsupported LLM provider: {target_provider}")


llm_client_manager = LLMClientManager()
```

### 问：这个架构在后续应该也能支持更多模型provider吧，就是不知道langchain底层对这些provider客户端的异常返回抛出适配是否统一？

对于后续扩展更多 Provider 以及 LangChain 底层的异常处理，结论可以直接概括为两句话：



1. **架构扩展性**：对于 **OpenAI 兼容生态**（Moonshot、硅基流动、Qwen、Groq、vLLM 等）能够无缝复用全局连接池；但对于非 OpenAI 系原生 SDK（如 Google GenAI），连接池注入机制并不统一。
2. **异常适配统一性**：**非常遗憾，LangChain 并没有做完全统一的异常封装，底层异常泄露非常严重。**

### 一、 LangChain 底层异常适配现状：不统一且严重泄漏

LangChain 的设计哲学倾向于“轻量包装厂商原生 SDK”。这意味着当大模型调用失败时，**各个 Provider 抛出的通常是厂商 SDK 自带的私有异常，或者是底层网络穿透的异常，并不会被统一翻译成标准的 `LangChainException`**。



#### 1. 常见 Provider 的异常形态差异

不同 Provider 遇到相同问题（如欠费、限流、超长）时，抛出的 Exception 类层级完全不同：



| **错误场景**        | **OpenAI / DeepSeek (openai SDK)**             | **Anthropic (anthropic SDK)**   | **Google (google-genai)**                        | **本地/通用 HTTP 穿透** |
| ------------------- | ---------------------------------------------- | ------------------------------- | ------------------------------------------------ | ----------------------- |
| **鉴权/Key 无效**   | `openai.AuthenticationError` (401)             | `anthropic.AuthenticationError` | `google.api_core.exceptions.Unauthenticated`     | `httpx.HTTPStatusError` |
| **超频/限流 (429)** | `openai.RateLimitError`                        | `anthropic.RateLimitError`      | `google.api_core.exceptions.ResourceExhausted`   | 429 状态码              |
| **上下文超长**      | `openai.BadRequestError` (内含 context_length) | `anthropic.BadRequestError`     | `google.api_core.exceptions.InvalidArgument`     | 400 状态码              |
| **服务宕机 (5xx)**  | `openai.InternalServerError`                   | `anthropic.InternalServerError` | `google.api_core.exceptions.InternalServerError` | 502/503/504 错误        |
| **连接池打满/超时** | `httpx.PoolTimeout` / `openai.APITimeoutError` | `httpx.PoolTimeout`             | gRPC Deadline Exceeded                           | `httpx.ReadTimeout`     |

#### 2. 流式传输（`astream`）中的异常截断

如果使用流式输出，异常往往不是在 `ainvoke()` 起始阶段报出，而是在异步迭代器 `async for chunk in model.astream(...)` 生成到一半时突发网络中断或厂商 500。此时异常会从生成器中直接暴跌抛出，更难依赖外部简单的单一 `try...except` 捕捉。



### 二、 后续扩展更多 Provider 时的连接池兼容性

你在 `LLMClientManager` 维护的 `httpx.AsyncClient`，向后兼容大致分为两类：



- **完全兼容（零成本复用）**：
  - **所有 OpenAI 兼容协议厂商**：包括 DeepSeek、Kimi (Moonshot)、阿里通义千问 (DashScope OpenAI 兼容端)、硅基流动 (SiliconFlow)、Groq、TogetherAI、本地部署的 vLLM / Ollama 等。
  - 这些全部可以直接使用 `ChatOpenAI`，只需替换 `base_url`、`api_key` 和 `model_name`，都可以**无缝共享同一套 `http_async_client`**。
- **部分兼容（参数名不同但底层同构）**：
  - **Anthropic (`langchain-anthropic`)**：底层同样使用 `httpx`，但参数名可能需要确认是 `async_client=self._async_client` 还是 `http_async_client`。
- **不兼容（需独立处理）**：
  - **Google Gemini (`langchain-google-genai`)**：底层默认走 Google 自家的传输协议（如 gRPC 或其私有封装 HTTP 层），无法直接挂载你的 `httpx.AsyncClient` 实例。

### 三、 架构改进方案：在 Manager 层构建统一异常拦截

为了不让上层业务代码去分别 `except openai.RateLimitError`、`except anthropic.RateLimitError`、`except httpx.PoolTimeout`，**推荐在 `LLMClientManager` 中提供一个统一的包装执行器或异常转换适配器**。



#### 1. 定义统一的业务语义异常

Python

```
class LLMBaseException(Exception):
    """LLM 统一异常基类"""
    def __init__(self, message: str, provider: str, status_code: int | None = None, raw_error: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.status_code = status_code
        self.raw_error = raw_error

class LLMAuthError(LLMBaseException): pass        # 401, 403 无效 key
class LLMRateLimitError(LLMBaseException): pass   # 429 限流 / 欠费
class LLMContextWindowExceededError(LLMBaseException): pass # 上下文超长
class LLMTimeoutError(LLMBaseException): pass     # 各种网络超时、连接池打满
class LLMServiceUnavailableError(LLMBaseException): pass # 5xx 宕机
```

#### 2. 在 Manager 中增加统一异常映射器（Exception Translator）

利用 Python 的上下文管理器对调用进行包装，不管底层是 OpenAI 还是其他 Provider，抛出给业务层（Controller / Service）的都是统一异常：



Python

```
from contextlib import asynccontextmanager
import httpx
import openai

class LLMClientManager:
    # ... 省略前文的 startup, shutdown, get_model ...

    @staticmethod
    @asynccontextmanager
    async def handle_exceptions(provider_name: str = "unknown"):
        """统一拦截各种 SDK 原始异常并标准化向外抛出"""
        try:
            yield
        except openai.AuthenticationError as e:
            raise LLMAuthError(f"API Key 无效或未授权: {e}", provider=provider_name, status_code=401, raw_error=e)
        except openai.RateLimitError as e:
            raise LLMRateLimitError(f"请求过于频繁或配额耗尽: {e}", provider=provider_name, status_code=429, raw_error=e)
        except openai.BadRequestError as e:
            err_msg = str(e).lower()
            if "context_length" in err_msg or "token" in err_msg:
                raise LLMContextWindowExceededError(f"Token 上下文超长: {e}", provider=provider_name, status_code=400, raw_error=e)
            raise LLMBaseException(f"请求格式/参数错误: {e}", provider=provider_name, status_code=400, raw_error=e)
        except (httpx.PoolTimeout, httpx.ConnectTimeout, httpx.ReadTimeout, openai.APITimeoutError) as e:
            # 捕获连接池满、握手超时、模型生成超时
            raise LLMTimeoutError(f"上游模型响应超时或连接池饱和: {e}", provider=provider_name, raw_error=e)
        except (openai.InternalServerError, httpx.HTTPStatusError) as e:
            raise LLMServiceUnavailableError(f"上游模型供应商服务不可用(5xx): {e}", provider=provider_name, raw_error=e)
        except Exception as e:
            raise LLMBaseException(f"未知的大模型调用异常: {str(e)}", provider=provider_name, raw_error=e)
```

#### 3. 上层业务端的使用方式

业务代码不再关心底层到底是 DeepSeek 还是 OpenAI 还是未来接入的第三方，统一捕获：



Python

```
model = llm_client_manager.get_model(provider="deepseek", ...)

try:
    async with llm_client_manager.handle_exceptions(provider_name="deepseek"):
        response = await model.ainvoke([HumanMessage(content="Hello")])
except LLMRateLimitError:
    # 统一处理限流，例如降级切换 Provider、通知前端重试
    ...
except LLMTimeoutError:
    # 统一记录连接超时日志或报警
    ...
except LLMBaseException as e:
    # 统一处理通用异常
    ...
```

通过这种方式，既能保留利用 `httpx.AsyncClient` 共享连接池的高吞吐性能，又抹平了 LangChain 底层异常不统一、到处泄漏 Vendor SDK 异常的问题。