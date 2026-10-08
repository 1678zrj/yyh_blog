### 问：调用大模型API重试的指数退避算法参数如何设置？

设置大模型 API 的指数退避（Exponential Backoff）重试策略时，核心目标是在**避免加剧服务端限流/雪崩（惊群效应）**与**保障调用端延迟体验**之间取得平衡。

---

### 一、 核心数学模型与关键参数

标准的指数退避等待时间计算公式为：

$$t_i = \min\left(t_{\max},\; t_{\text{base}} \times b^i\right)$$

在实际工程中，必须引入 **随机抖动（Jitter）**，推荐采用 AWS 提出的 **Full Jitter（全随机抖动）**：

$$\text{Delay}_i = \text{Uniform}\left(0,\; t_i\right)$$

* **$t_{\text{base}}$（初始等待时间 / `initial_delay`）**：首次重试前的休眠基准时间。
* **$b$（底数 / `multiplier`）**：退避乘数，业内标准通常为 $2.0$（即每次重试间隔翻倍）。
* **$t_{\max}$（最大等待时间 / `max_delay`）**：单次重试等待的硬上限，防止因指数爆炸产生数分钟的无意义等待。
* **`max_retries`（最大重试次数）**：达到后触发降级或向调用方抛出异常。

---

### 二、 典型场景的参数推荐配置

不同场景对“延迟”与“吞吐”的容忍度不同，参数需区别对待：

| 业务场景                    | $t_{\text{base}}$ | 乘数 $b$ | $t_{\max}$ | `max_retries` | 抖动策略 | 预估最大累计耗时 |
| --------------------------- | ----------------- | -------- | ---------- | ------------- | -------- | ---------------- |
| **实时交互 / C 端对话**<br> |                   |          |            |               |          |                  |

<br>*(Web UI、SSE 流式打字机)* | 0.5s – 1.0s | 2.0 | 4s – 6s | **2 – 3 次** | Full Jitter 或 50% Jitter | $\le 10\text{s}$（避免用户直接关闭页面） |
| **复杂 Agent / 链式调用**<br>

<br>*(ReAct 工作流、多工具调用)* | 1.0s | 2.0 | 10s – 15s | **3 – 4 次** | Full Jitter | $\approx 20\text{s} - 30\text{s}$（保持上下文生命周期稳定） |
| **离线批量 / 数据处理**<br>

<br>*(RAG 数据切片向量化、批量评测)* | 1.0s – 2.0s | 2.0 | 30s – 60s | **5 – 8 次** | Full Jitter | $1\text{min} - 3\text{min}$（优先跑完，不丢任务） |

---

### 三、 大模型 API 场景下的 4 个关键工程细节

#### 1. 优先服从服务端的 `Retry-After` 响应头

主流大模型厂商（如 OpenAI、Anthropic、Google）在返回 HTTP `429 Too Many Requests` 时，通常会返回 `Retry-After`（秒数）或 `retry-after-ms`（毫秒）响应头：

* 该值是服务端根据当前 Token 桶/请求桶恢复计算出的精确时间。
* **策略**：若响应头存在 `Retry-After`，**跳过客户端的指数退避计算**，直接以 `Retry-After + 随机抖动(0~500ms)` 作为等待时长。

#### 2. 精确过滤“可重试”错误（严禁盲目重试）

只有瞬态故障才值得重试，业务或配置类错误重试不仅无效还会加速耗尽配额：

* **必须重试**：
* HTTP `429` 且子错误为 `rate_limit_exceeded` / `slow_down`
* HTTP `500`、`502`、`503`（Service Unavailable）、`504`（Gateway Timeout）
* 网络层错误：连接超时（`ConnectTimeout`）、读取超时（`ReadTimeout`）


* **绝对不要重试**：
* HTTP `400`（Bad Request，请求体格式错误、Schema 校验不通过）
* HTTP `401` / `403`（API Key 无效或模型权限不足）
* HTTP `404`（模型名称不存在）
* HTTP `429` 但错误类型为 `insufficient_quota`（**账号欠费或月度限额用尽**，重试 100 次也无法恢复）



#### 3. 必须设置全局超时预算（Deadline / Context Timeout）

单次请求每次重试都有其等待时间，重试多次会导致总延迟拉长。上游调用链若已超时（例如 Nginx 反向代理通常为 60s），本地若还在做第 4 次重试等待，会直接造成无意义的孤儿任务与并发协程堆积。

#### 4. 配合 Fallback（降级）策略

当指数退避重试到达上限依然失败时：

* 切换同厂商备用模型（如主模型 `gpt-4o` 限流降级为 `gpt-4o-mini`，或高规格模型降级为轻量模型）。
* 切换同生态的多区域/多平台 Endpoint（如直连官方 API 限流时切换至 Azure OpenAI 或 AWS Bedrock 镜像路由）。

---

### 四、 Python (Tenacity) 生产环境参考实现

利用 Python 的 `tenacity` 库实现带随机抖动、动态捕获特定异常的重试逻辑：

```python
import httpx
from openai import (
    APIConnectionError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

client = OpenAI()


def is_retryable_exception(exc: BaseException) -> bool:
    """判断是否为可重试的异常"""
    if isinstance(exc, RateLimitError):
        # 排除欠费导致的情况 (insufficient_quota)
        error_code = getattr(exc, "code", None)
        if error_code == "insufficient_quota":
            return False
        return True
    if isinstance(exc, (InternalServerError, APIConnectionError)):
        return True
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True
    return False


@retry(
    # 指数退避 + Full Jitter: 初始 1 秒，每次 2^n 翻倍，上限 10 秒
    wait=wait_random_exponential(multiplier=1, min=1, max=10),
    # 最多重试 3 次 (连同初次调用共 4 次)
    stop=stop_after_attempt(4),
    retry=retry_if_exception(is_retryable_exception),
    reraise=True,
)
def chat_with_llm(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        timeout=15.0,  # 单次请求超时设置
    )
    return response.choices[0].message.content

```

### 问：展示在 Python 中如何解析大模型 API 的 429 Retry-After 响应头，并动态覆盖指数退避等待时间的完整实现

在生产环境中，大模型服务商（如 OpenAI、Anthropic、Azure）通常会在 HTTP `429 Too Many Requests` 的响应头中携带恢复时间。标准头为 `Retry-After`（支持秒数或 HTTP 日期格式），部分网关或服务还会返回毫秒级的 `retry-after-ms`。



结合 `tenacity` 库，可以通过继承 `wait_base` 自定义等待策略：**优先提取响应头中的重试等待时间（并附加微量 Jitter 避免瞬时并发冲撞）；若响应头未提供，则自动退化为指数退避（Full Jitter）**。



### 1. `Retry-After` 解析与动态退避策略实现

Python

```
import email.utils
import random
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from openai import (
    APIConnectionError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_base,
)


def parse_retry_after(headers: httpx.Headers) -> Optional[float]:
    """从 HTTP 响应头中解析重试等待时间（秒）。

    兼容:
    1. retry-after-ms (毫秒整数/浮点数)
    2. retry-after (秒数，如 "3" 或 "1.5")
    3. retry-after (RFC 1123 日期格式，如 "Wed, 21 Oct 2026 07:28:00 GMT")
    """
    if not headers:
        return None

    # 1. 优先尝试毫秒级头 (如 Anthropic / 某些网关)
    if "retry-after-ms" in headers:
        try:
            return max(0.0, float(headers["retry-after-ms"]) / 1000.0)
        except (ValueError, TypeError):
            pass

    # 2. 尝试标准 Retry-After
    raw_val = headers.get("retry-after")
    if not raw_val:
        return None

    # 格式 A: 纯秒数
    try:
        return max(0.0, float(raw_val))
    except ValueError:
        pass

    # 格式 B: RFC 1123 HTTP-Date
    try:
        target_date = email.utils.parsedate_to_datetime(raw_val)
        if target_date.tzinfo is None:
            target_date = target_date.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delay = (target_date - now).total_seconds()
        return max(0.0, delay)
    except Exception:
        pass

    return None


class wait_retry_after_or_exponential(wait_base):
    """动态等待策略：优先服从 Retry-After，其次使用指数退避 + Full Jitter。"""

    def __init__(
        self,
        initial_delay: float = 1.0,
        multiplier: float = 2.0,
        max_delay: float = 20.0,
        header_jitter_range: tuple[float, float] = (0.1, 0.5),
    ):
        self.initial_delay = initial_delay
        self.multiplier = multiplier
        self.max_delay = max_delay
        self.header_jitter_min, self.header_jitter_max = header_jitter_range

    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None

        # 1. 尝试从异常关联的 HTTP 响应头提取 Retry-After
        if exc and hasattr(exc, "response") and exc.response is not None:
            headers = getattr(exc.response, "headers", None)
            if isinstance(headers, httpx.Headers):
                delay = parse_retry_after(headers)
                if delay is not None:
                    # 即使服务端给出了具体秒数，仍需叠加 100~500ms 的轻微 Jitter，
                    # 避免同个并发批次在解封瞬间发起二次“惊群冲击”
                    jitter = random.uniform(
                        self.header_jitter_min, self.header_jitter_max
                    )
                    final_delay = min(delay + jitter, self.max_delay)
                    return final_delay

        # 2. 回退机制：标准 Full Jitter 指数退避 (Delay = Uniform(0, min(max_delay, initial * multiplier^n)))
        attempt = retry_state.attempt_number - 1
        calculated_backoff = self.initial_delay * (self.multiplier**attempt)
        capped_backoff = min(calculated_backoff, self.max_delay)
        return random.uniform(0.0, capped_backoff)
```

### 2. 异常过滤器与调用集成

Python

```
def is_retryable_exception(exc: BaseException) -> bool:
    """过滤可重试异常：剔除欠费、权限不足或客户端语法错误。"""
    if isinstance(exc, RateLimitError):
        # 排除欠费导致的情况 (insufficient_quota 重试无效)
        error_code = getattr(exc, "code", None)
        return error_code != "insufficient_quota"

    if isinstance(exc, (InternalServerError, APIConnectionError)):
        return True

    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True

    return False


client = OpenAI()


@retry(
    # 使用自定义的复合等待逻辑
    wait=wait_retry_after_or_exponential(
        initial_delay=1.0, multiplier=2.0, max_delay=30.0
    ),
    stop=stop_after_attempt(4),
    retry=retry_if_exception(is_retryable_exception),
    reraise=True,
)
def completion_with_dynamic_backoff(prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        timeout=20.0,
    )
    return response.choices[0].message.content
```

### 3. 异步（AsyncIO）版本适配

如果使用的是 `AsyncOpenAI` 或 `httpx.AsyncClient`，`tenacity` 的 `wait` 策略完全通用，只需将重试装饰器挂载在 `async def` 上即可：



Python

```
from openai import AsyncOpenAI

async_client = AsyncOpenAI()


@retry(
    wait=wait_retry_after_or_exponential(
        initial_delay=1.0, multiplier=2.0, max_delay=30.0
    ),
    stop=stop_after_attempt(4),
    retry=retry_if_exception(is_retryable_exception),
    reraise=True,
)
async def async_completion_with_dynamic_backoff(prompt: str) -> str:
    response = await async_client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        timeout=20.0,
    )
    return response.choices[0].message.content
```

### 4. 关键设计细节

- **避免直接信任裸延迟**：即便解析出的 `Retry-After` 为 `2.0` 秒，多个并发请求若同时等待整整 `2.0` 秒并在同一毫秒苏醒，会再次击穿服务端的配额窗口。附加 `random.uniform(0.1, 0.5)` 可以打散请求波峰。
- **边界保护（`max_delay` 截断）**：极端情况下（如服务商封禁或配额重置在数天后），`Retry-After` 可能会返回几个小时的等待。必须用 `min(delay + jitter, self.max_delay)` 进行硬截断，超过单次可承受上限后尽早熔断报错。
- **Header 类型安全**：`exc.response.headers` 在 `httpx`（OpenAI v1+ 底层客户端）中是大小写不敏感字典，使用 `.get()` 和 `in` 操作符无需手动转换 `lower()`。

### 问：展示如何在大模型重试达到上限后，自动无缝切换到备用模型或备用服务商的工程实现方案

在生产环境中，大模型故障转移（Failover / Fallback）的标准范式是 **“双层防御架构”**：



1. **第一层（节点内重试）**：单模型进行快速、低次数（如 2~3 次）的指数退避重试，处理网络抖动或微量配额波动。
2. **第二层（跨模型/跨厂商降级）**：单模型重试耗尽或遭遇不可恢复错误（如 `insufficient_quota` 欠费、硬性限流、持续 5xx 宕机）时，立即跳出重试循环，沿备用链路按优先级降级。

为了避免“已宕机厂商导致后续请求持续变慢”，还需要在降级架构中引入**轻量级熔断/冷却机制（Cooldown Circuit Breaker）**。



### 一、 生产级降级路由架构设计

```
[请求入口]
    │
    ▼
[Fallback Router]
    │── 1. 过滤处于“冷却中 (Cooldown)”的节点
    │
    ├──▶ [Primary: OpenAI gpt-4o] ──(重试 2 次均 429/5xx)──┐
    │                                                      │ 失败 / 触发熔断
    │                                                      ▼
    ├──▶ [Backup 1: Azure OpenAI gpt-4o] ──(成功)──────── 响应返回
    │                                                      │ (若失败)
    │                                                      ▼
    └──▶ [Backup 2: DeepSeek-V3 / Qwen] ──────────────── 最终保底
```

### 二、 完整异步实现代码

基于 `AsyncOpenAI`（兼容所有遵循 OpenAI 标准接口的厂商，如 DeepSeek、Azure、vLLM、DashScope）与 `tenacity`：



Python

```
import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

import httpx
from openai import (
    APIConnectionError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_base,
)

logger = logging.getLogger("LLMFallback")


# ----------------------------
# 1. 节点配置与熔断健康状态
# ----------------------------
@dataclass
class ModelEndpoint:
    name: str  # 标识符，如 "primary-openai", "backup-azure"
    model: str  # 模型名，如 "gpt-4o", "deepseek-chat"
    client: AsyncOpenAI
    max_retries: int = 2  # 单节点内重试次数
    timeout: float = 15.0
    cooldown_seconds: float = 60.0  # 失败后熔断冷却时长
    _cooldown_until: float = field(default=0.0, init=False)

    @property
    def is_available(self) -> bool:
        """检查节点是否已脱离熔断冷却期"""
        return time.time() >= self._cooldown_until

    def mark_failure(self):
        """节点调用彻底失败，打入冷却池"""
        self._cooldown_until = time.time() + self.cooldown_seconds
        logger.warning(
            f"Endpoint [{self.name}] 触发熔断，冷却 {self.cooldown_seconds}s"
        )

    def mark_success(self):
        """成功后解除冷却"""
        self._cooldown_until = 0.0


# ----------------------------
# 2. 退避策略与异常判断
# ----------------------------
def is_retryable_exception(exc: BaseException) -> bool:
    """仅对瞬态网络故障或服务繁忙重试；欠费或权限错误直接抛出进入 Fallback"""
    if isinstance(exc, RateLimitError):
        # 欠费 (insufficient_quota) 立即跳出重试，由 fallback 接管
        return getattr(exc, "code", None) != "insufficient_quota"
    return isinstance(
        exc,
        (
            InternalServerError,
            APIConnectionError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
        ),
    )


class DynamicJitterWait(wait_base):
    """提取 Retry-After，否则使用指数退避 + Full Jitter"""

    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if exc and hasattr(exc, "response") and exc.response is not None:
            retry_after = exc.response.headers.get("retry-after")
            if retry_after:
                try:
                    return float(retry_after) + random.uniform(0.1, 0.4)
                except ValueError:
                    pass
        # 默认指数退避：1s, 2s, 4s...
        delay = 1.0 * (2.0 ** (retry_state.attempt_number - 1))
        return random.uniform(0.0, min(delay, 8.0))


# ----------------------------
# 3. 降级调度器核心实现
# ----------------------------
class LLMFallbackRouter:

    def __init__(self, endpoints: List[ModelEndpoint]):
        if not endpoints:
            raise ValueError("至少需要配置一个 ModelEndpoint")
        self.endpoints = endpoints

    async def _execute_single_endpoint(
        self, endpoint: ModelEndpoint, **kwargs
    ) -> Any:
        """单节点执行器：包含内部的 tenacity 指数退避重试"""
        async for attempt in AsyncRetrying(
            wait=DynamicJitterWait(),
            stop=stop_after_attempt(endpoint.max_retries),
            retry=retry_if_exception(is_retryable_exception),
            reraise=True,
        ):
            with attempt:
                logger.info(
                    f"正在尝试节点: [{endpoint.name}], 模型: {endpoint.model} (第 {attempt.retry_state.attempt_number} 次)"
                )
                return await endpoint.client.chat.completions.create(
                    model=endpoint.model,
                    timeout=endpoint.timeout,
                    **kwargs,
                )

    async def chat_completion(self, **kwargs) -> tuple[Any, str]:
        """对外统一入口：顺次尝试可用节点，自动 Failover。

        返回: (response, used_endpoint_name)
        """
        last_exception = None

        for endpoint in self.endpoints:
            # 跳过正处于熔断冷却中的节点
            if not endpoint.is_available:
                remaining = int(endpoint._cooldown_until - time.time())
                logger.debug(
                    f"跳过熔断中的节点: [{endpoint.name}] (剩余冷却: {remaining}s)"
                )
                continue

            try:
                response = await self._execute_single_endpoint(
                    endpoint, **kwargs
                )
                endpoint.mark_success()
                return response, endpoint.name

            except Exception as e:
                last_exception = e
                endpoint.mark_failure()
                logger.error(
                    f"节点 [{endpoint.name}] 彻底失败: {type(e).__name__} - {e}. 正在切换至下一个备用节点..."
                )

        # 所有候选节点全部失效
        raise RuntimeError(
            f"所有大模型节点均调用失败，最后捕获异常: {last_exception}"
        ) from last_exception
```

### 三、 使用示例（多厂商容灾）

配置主服务（OpenAI）、镜像备用（Azure）、保底高可用国产服务（DeepSeek）：



Python

```
import os

# 1. 组装三级容灾管线
router = LLMFallbackRouter(
    endpoints=[
        # 优先级 1: OpenAI 官方主账号
        ModelEndpoint(
            name="openai-primary",
            model="gpt-4o",
            client=AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")),
            max_retries=2,
            timeout=15.0,
        ),
        # 优先级 2: Azure OpenAI (不同地域/不同配额池的相同能力模型)
        ModelEndpoint(
            name="azure-secondary",
            model="gpt-4o",
            client=AsyncOpenAI(
                api_key=os.getenv("AZURE_OPENAI_KEY"),
                base_url="https://your-resource.openai.azure.com/openai/deployments/gpt-4o",
            ),
            max_retries=2,
            timeout=20.0,
        ),
        # 优先级 3: 备用厂商保底
        ModelEndpoint(
            name="deepseek-fallback",
            model="deepseek-chat",
            client=AsyncOpenAI(
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url="https://api.deepseek.com",
            ),
            max_retries=3,
            timeout=30.0,
        ),
    ]
)


# 2. 业务调用
async def main():
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "解释分布式系统的 CAP 定理"},
    ]

    try:
        response, used_provider = await router.chat_completion(
            messages=messages,
            temperature=0.7,
        )
        print(f"✅ 请求成功！实际响应节点: [{used_provider}]")
        print(f"内容:\n{response.choices[0].message.content}")

    except RuntimeError as fatal_err:
        print(f"❌ 链路全崩溃: {fatal_err}")


if __name__ == "__main__":
    asyncio.run(main())
```

### 四、 避坑与进阶考量

1. **结构化输出（JSON Schema / Function Calling）兼容性**：
   - 如果开启了 `response_format={"type": "json_object"}` 或绑定了复杂的 `tools`，备用模型必须具备相同的 Tool-calling 解析能力。
   - 若备选模型对 `tools` 支持不一致，应在 Fallback 捕获到参数错误时对 `kwargs` 进行动态修剪（如移除 tools 并将 Schema 嵌入 System Prompt 作为纯文本兜底）。
2. **上下文窗口（Context Window）断崖**：
   - 主模型若是 128k 上下文，降级模型若是 32k 或 16k，直接透传长 prompt 会引发不可恢复的 `400 Bad Request`。降级前需检查 `token_count`，必要时裁剪历史对话消息。
3. **指标埋点（Observability）**：
   - 必须在降级触发点打入 Metrics（例如 Prometheus Counter `llm_fallback_triggered_total{from="openai", to="azure"}`），以便监控主力配额消耗趋势并及时触发人工告警。

### 问：展示如何使用 Redis 实现跨多进程/跨容器共享的大模型节点熔断状态与分布式限流方案

在多进程（如 Gunicorn / Celery / TaskIQ 多 Worker）或跨容器（Docker / K8s）部署大模型服务时，单机内存的熔断与限流状态无法共享，会导致**流量倾轧**与**重复踩坑**。



利用 Redis 的原子操作（Lua 脚本与分布式锁），可以构建集中式的高可用防护层，包含两个核心机制：



1. **原子滑动窗口限流器（Sliding Window Rate Limiter）**：严格控制 RPM（每分钟请求数）与 TPM（每分钟 Token 数）。
2. **三态分布式熔断器（Distributed 3-State Circuit Breaker）**：跨节点共享 `CLOSED`、`OPEN`、`HALF_OPEN` 状态，并利用分布式锁防止半开恢复时的“惊群探测”。

### 一、 核心架构与状态机流转

```
                      [请求进入]
                          │
                          ▼
            [1. 检查 Redis 熔断状态]
             ┌────────────┴────────────┐
             ▼                         ▼
      [OPEN 熔断中]             [CLOSED / HALF_OPEN]
     (快速 Failover)                   │
                                       ▼
                             [2. Redis 滑动窗口限流]
                                       │ 超过 RPM/TPM 限额
                              ┌────────┴────────┐
                              ▼                 ▼
                         [等待或降级]       [通过限流]
                                                │
                                                ▼
                                         [调用大模型 API]
                                                │
                               ┌────────────────┴────────────────┐
                               ▼                                 ▼
                           [调用成功]                        [连续调用失败]
                               │                                 │
                   (半开转闭合 / 清零失败计数)             (达到阈值触发 OPEN，全集群生效)
```

### 二、 Redis 原子滑动窗口限流器（Lua 实现）

使用 ZSet（有序集合）记录时间戳，并通过原子 Lua 脚本完成 **“过期指标清除 $\rightarrow$ 容量核验 $\rightarrow$ 写入当前请求 $\rightarrow$ 刷新 Key 存活”**，彻底避免并发竞争。



Python

```
import time
from typing import Optional
import redis.asyncio as aioredis

# Lua 脚本：原子滑动窗口限流
# KEYS[1]: 限流 key (如 "llm:ratelimit:openai:rpm")
# ARGV[1]: 当前毫秒时间戳
# ARGV[2]: 时间窗口大小（毫秒，如 60000 代表 1 分钟）
# ARGV[3]: 窗口内最大允许容量
# ARGV[4]: 消耗配额（请求次数传 1，Token 限制传 token_count）
SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local max_capacity = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local clear_before = now - window
redis.call('ZREMRANGEBYSCORE', key, '-inf', clear_before)

-- 统计当前窗口内消耗总量
local current_entries = redis.call('ZRANGEBYSCORE', key, clear_before, '+inf')
local current_load = 0
for _, entry in ipairs(current_entries) do
    local entry_cost = tonumber(string.match(entry, ":(%d+)$") or 1)
    current_load = current_load + entry_cost
end

if current_load + cost <= max_capacity then
    -- 写入当前消耗 (member 拼接唯一标识防止覆盖: timestamp_uuid:cost)
    local member = now .. "_" .. redis.call('INCR', key .. ':seq') .. ":" .. cost
    redis.call('ZADD', key, now, member)
    redis.call('PEXPIRE', key, window)
    return 1  -- 允许通行
else
    return 0  -- 限流拦截
end
"""


class RedisRateLimiter:

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self._script = self.redis.register_script(SLIDING_WINDOW_LUA)

    async def acquire(
        self,
        name: str,
        limit_type: str,
        max_capacity: int,
        window_ms: int = 60000,
        cost: int = 1,
    ) -> bool:
        """申请配额：limit_type 可为 'rpm' (cost=1) 或 'tpm' (cost=estimated_tokens)"""
        key = f"llm:ratelimit:{name}:{limit_type}"
        now_ms = int(time.time() * 1000)
        res = await self._script(
            keys=[key], args=[now_ms, window_ms, max_capacity, cost]
        )
        return bool(res == 1)
```

### 三、 跨进程三态分布式熔断器

熔断器包含三种状态：



- **`CLOSED`（闭合）**：正常状态。失败次数写入 Redis Counter 并设置滑动窗口 TTL，达到 `failure_threshold` 触发熔断。
- **`OPEN`（开启）**：阻断状态。所有进程均直接阻断该模型请求，持续 `cooldown_seconds`。
- **`HALF_OPEN`（半开）**：冷却期过后。**通过 Redis 分布式排他锁（`SET NX`）仅放行一个探测请求**，其余并发请求继续快速失败走 Fallback。探测成功则恢复 `CLOSED`，探测失败重新打入 `OPEN`。

Python

```
import asyncio
import logging
from enum import Enum

logger = logging.getLogger("DistributedCircuitBreaker")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class RedisCircuitBreaker:

    def __init__(
        self,
        redis_client: aioredis.Redis,
        endpoint_name: str,
        failure_threshold: int = 5,  # 触发熔断的失败次数
        failure_window_sec: int = 30,  # 失败计数窗口
        cooldown_seconds: int = 60,  # 熔断冷却恢复时间
    ):
        self.redis = redis_client
        self.name = endpoint_name
        self.failure_threshold = failure_threshold
        self.failure_window_sec = failure_window_sec
        self.cooldown_seconds = cooldown_seconds

        # Redis 键命名规范
        self.key_state = f"llm:cb:{endpoint_name}:state"
        self.key_failures = f"llm:cb:{endpoint_name}:failures"
        self.key_probe_lock = f"llm:cb:{endpoint_name}:probe_lock"

    async def can_execute(self) -> bool:
        """判断当前节点是否可被调度执行"""
        state = await self.redis.get(self.key_state)

        # 1. 默认为 CLOSED 状态
        if not state or state.decode() == CircuitState.CLOSED:
            return True

        # 2. 如果是 OPEN 状态，由于带有 EXPIRE，若该键还在，说明冷却期未过
        if state.decode() == CircuitState.OPEN:
            return False

        # 3. 如果是 HALF_OPEN 状态，竞争探测权
        if state.decode() == CircuitState.HALF_OPEN:
            return await self._try_acquire_probe_lock()

        return True

    async def _try_acquire_probe_lock(self) -> bool:
        """分布式原子锁：只允许集群中的单个请求进行半开探针测试"""
        acquired = await self.redis.set(
            self.key_probe_lock,
            "locked",
            ex=10,  # 10s 探针锁，防止 Worker 挂掉导致永远锁死
            nx=True,
        )
        return bool(acquired)

    async def record_success(self):
        """调用成功回调"""
        state = await self.redis.get(self.key_state)
        current_state = state.decode() if state else CircuitState.CLOSED

        if current_state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            logger.info(
                f"[{self.name}] 探测请求成功，集群熔断恢复至 CLOSED 状态"
            )
            pipe = self.redis.pipeline()
            pipe.set(self.key_state, CircuitState.CLOSED)
            pipe.delete(self.key_failures)
            pipe.delete(self.key_probe_lock)
            await pipe.execute()
        else:
            # 闭合状态下清空失败计数
            await self.redis.delete(self.key_failures)

    async def record_failure(self):
        """调用失败回调"""
        state = await self.redis.get(self.key_state)
        current_state = state.decode() if state else CircuitState.CLOSED

        # 若在 HALF_OPEN 探测期失败，立即重新打回 OPEN 熔断状态
        if current_state == CircuitState.HALF_OPEN:
            logger.warning(
                f"[{self.name}] 探测请求失败，重新熔断 {self.cooldown_seconds}s"
            )
            await self._trip_open()
            return

        # 闭合状态：累加失败计数
        pipe = self.redis.pipeline()
        pipe.incr(self.key_failures)
        pipe.expire(self.key_failures, self.failure_window_sec)
        results = await pipe.execute()
        failures = results[0]

        if failures >= self.failure_threshold:
            logger.error(
                f"[{self.name}] 窗口期失败累计达 {failures} 次，触发集群熔断 {self.cooldown_seconds}s"
            )
            await self._trip_open()

    async def _trip_open(self):
        """状态跃迁为 OPEN，并设置到期后自动转换为 HALF_OPEN 的键生命周期"""
        pipe = self.redis.pipeline()
        # 设置 OPEN 状态，TTL 到期后自动消失
        pipe.set(self.key_state, CircuitState.OPEN, ex=self.cooldown_seconds)
        pipe.delete(self.key_probe_lock)
        await pipe.execute()

        # 异步启动或监听：当 OPEN 键自然过期消失时，通过读操作默认识别为冷却结束，将其升级为 HALF_OPEN
        asyncio.create_task(self._schedule_half_open_transition())

    async def _schedule_half_open_transition(self):
        """后台轻量协程：在冷却结束时刻，将 Redis 状态标记为 HALF_OPEN"""
        await asyncio.sleep(self.cooldown_seconds)
        # CAS 机制：只有当前还是空或者 OPEN 状态时才更新，防止被新的状态覆盖
        await self.redis.set(self.key_state, CircuitState.HALF_OPEN)
```

### 四、 整合调度：多模型降级网关实现

将限流器、分布式熔断器与上一轮的 Fallback 机制打通：



Python

```
import os
from openai import AsyncOpenAI


class ManagedEndpoint:

    def __init__(
        self,
        name: str,
        model: str,
        client: AsyncOpenAI,
        limiter: RedisRateLimiter,
        circuit_breaker: RedisCircuitBreaker,
        rpm_limit: int,
    ):
        self.name = name
        self.model = model
        self.client = client
        self.limiter = limiter
        self.cb = circuit_breaker
        self.rpm_limit = rpm_limit

    async def execute(self, **kwargs):
        # 1. 熔断判定（跨容器共享）
        if not await self.cb.can_execute():
            raise RuntimeError(f"Endpoint [{self.name}] 熔断中或未获取到探测权")

        # 2. 限流判定（跨容器共享）
        allowed = await self.limiter.acquire(
            name=self.name,
            limit_type="rpm",
            max_capacity=self.rpm_limit,
            window_ms=60000,
            cost=1,
        )
        if not allowed:
            raise RuntimeError(
                f"Endpoint [{self.name}] 触发分布式 RPM 限额 ({self.rpm_limit})"
            )

        # 3. 实际发起 API 请求
        try:
            resp = await self.client.chat.completions.create(
                model=self.model, **kwargs
            )
            await self.cb.record_success()
            return resp
        except Exception as e:
            # 记录不可恢复错误，可能触发跨机熔断
            await self.cb.record_failure()
            raise e


class DistributedLLMRouter:

    def __init__(self, endpoints: list[ManagedEndpoint]):
        self.endpoints = endpoints

    async def completion_with_failover(self, **kwargs):
        last_error = None
        for ep in self.endpoints:
            try:
                logger.info(f"正在尝试可用节点: {ep.name}")
                response = await ep.execute(**kwargs)
                return response, ep.name
            except Exception as e:
                last_error = e
                logger.warning(f"节点 {ep.name} 跳过/调用失败: {e}，尝试下一备选")
                continue

        raise RuntimeError(
            f"所有集群节点均不可用或已被限流熔断: {last_error}"
        )
```

### 五、 接入与启动代码

Python

```
async def main():
    # 初始化异步 Redis 连接池 (兼容多进程 fork 安全)
    redis_pool = aioredis.ConnectionPool.from_url(
        "redis://localhost:6379/0", max_connections=20
    )
    redis_client = aioredis.Redis(connection_pool=redis_pool)

    limiter = RedisRateLimiter(redis_client)

    # 声明各节点的分布式熔断与限流配置
    primary_ep = ManagedEndpoint(
        name="openai-main",
        model="gpt-4o",
        client=AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")),
        limiter=limiter,
        circuit_breaker=RedisCircuitBreaker(
            redis_client, "openai-main", failure_threshold=3, cooldown_seconds=30
        ),
        rpm_limit=500,  # 共享 500 RPM
    )

    backup_ep = ManagedEndpoint(
        name="deepseek-backup",
        model="deepseek-chat",
        client=AsyncOpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com",
        ),
        limiter=limiter,
        circuit_breaker=RedisCircuitBreaker(
            redis_client,
            "deepseek-backup",
            failure_threshold=5,
            cooldown_seconds=45,
        ),
        rpm_limit=1000,
    )

    router = DistributedLLMRouter([primary_ep, backup_ep])

    # 执行调用
    resp, provider = await router.completion_with_failover(
        messages=[{"role": "user", "content": "Ping"}],
        timeout=10.0,
    )
    print(f"调用成功, 响应提供方: {provider}")


if __name__ == "__main__":
    asyncio.run(main())
```

### 六、 核心边界处理

1. **Redis 故障容灾（Fail-Open 策略）**：
   - 在微服务架构中，**限流/熔断组件本身不能成为单点阻塞故障**。如果 Redis 出现主从切换、网络抖动或超时，应在代码外层捕获 `aioredis.RedisError`，打出告警后执行 **Fail-Open（降级放行）**，直接将请求透传至模型 API，避免 Redis 宕机拖垮全站 AI 业务。
2. **多节点时钟偏斜（Clock Skew）**：
   - 滑动窗口强依赖时间戳。在 K8s 集群中，不同物理宿主机若存在秒级时钟不同步，会导致滑动窗口统计偏差。生产环境推荐统一调用 Redis 服务端的时钟：`time_tuple = await redis.time()`，以此作为全局时间基准。
3. **Token 限流预扣与补正（Two-Phase TPM）**：
   - RPM 可以预先判定（每个请求 cost=1），但 TPM（每分钟 Token 数）在请求结束前无法获知真实数值。
   - **工程实践**：请求前按输入 Prompt 估算 Token 并加上预设 Max Tokens 进行预扣（`cost = len(prompt)/3 + max_tokens`）；请求完成后拿到官方响应体中的 `usage.total_tokens`，调用 Lua 脚本将差额以负值补偿回窗口集合中。