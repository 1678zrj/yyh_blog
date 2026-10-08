### 问：langgraph中@tool定义的工具函数能否获取到图的全局状态？

**可以**。在 LangGraph 中，通过 `langgraph.prebuilt.InjectedState` 注解，`@tool` 工具函数可以直接读取图的全局状态（或指定字段），同时该参数会被自动从发送给 LLM 的 Schema 中剔除，无需模型显式传入。



**读取整个 State 或指定字段**



你可以通过 `Annotated` 将参数标记为 `InjectedState`：



Python

```
from typing import Annotated, TypedDict
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState, ToolNode

# 1. 定义图状态
class AgentState(TypedDict):
    messages: list
    user_id: str
    auth_token: str

# 2. 方式 A：注入整个全局状态
@tool
def check_permission(
    action: str,
    state: Annotated[AgentState, InjectedState]
) -> str:
    """检查当前用户是否有执行某操作的权限。"""
    user_id = state.get("user_id")
    return f"User {user_id} has permission for {action}."

# 3. 方式 B：仅注入指定字段（推荐，解耦且更清晰）
@tool
def fetch_account_data(
    category: str,
    token: Annotated[str, InjectedState("auth_token")]
) -> str:
    """获取账户特定类别的数据。"""
    return f"Fetching {category} using token {token}."
```

**工作原理与运行机制**



- **模型侧不可见**：生成工具调用结构体（JSON Schema）传给 LLM 时，LangGraph 会自动过滤掉带有 `InjectedState` 的参数。LLM 只需要推断生成 `action` 或 `category`。
- **执行侧自动注入**：工具执行节点（如 `ToolNode`）在调用函数前，会从当前图的状态字典中提取对应值，填入相应参数。

**关键注意事项**



- **依赖 `ToolNode` 执行**：自动注入仅在通过 LangGraph 的预构建组件 `ToolNode`（或实现了参数注入逻辑的节点）运行时生效。如果手动在普通 Python 代码中直接调用该工具函数，需要自行传入该参数。

- **获取运行时上下文（如 `thread_id`）**：如果需要获取当前会话 ID、用户配置等运行元数据，而非图状态，可在参数中直接注入 `RunnableConfig`：

  Python

  ```
  from langchain_core.runnables import RunnableConfig
  
  @tool
  def search_db(query: str, config: RunnableConfig) -> str:
      thread_id = config.get("configurable", {}).get("thread_id")
      return f"Searching in thread {thread_id} for {query}"
  ```

- **工具是否能直接“修改”全局状态**：

  - `@tool` 默认通过返回值作为 ToolMessage 更新消息历史。
  - 如果需要工具直接写入/覆盖图状态的其他字段，可以在工具函数中返回 `Command(update={"key": "new_value"})`（需使用 LangGraph 0.2+ 的 `Command` 对象）。

### 问：好吧，我不用ToolNode，那可能只能通过RunnableConfig对象获取了

不用 `ToolNode` 完全可以，但需要注意一个核心前提：**图的全局状态（State）默认并不在 `RunnableConfig` 里面**。



`RunnableConfig` 默认只包含运行元数据（如 `thread_id`、`checkpoint_ns`、`callbacks`、`tags` 等）。如果你想通过它或者其他方式在自定义节点中给 `@tool` 传递状态，有以下两种最直接的做法：



**方式一：通过 `RunnableConfig` 传递（你的思路）**



在自定义节点调用工具时，将当前节点的 `state` 手动塞入 `config["configurable"]`：



Python

```
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

# 1. 定义工具：从 config 中取出自定义注入的 state
@tool
def my_tool(query: str, config: RunnableConfig) -> str:
    """搜索或执行某些操作"""
    # 从 configurable 中提取
    state = config.get("configurable", {}).get("state", {})
    user_id = state.get("user_id")
    return f"Result for {query}, User: {user_id}"

# 2. 自定义工具执行节点
def custom_tools_node(state: dict, config: RunnableConfig):
    messages = state["messages"]
    last_message = messages[-1]
    
    # 构造携带 state 的 config
    configurable = {**config.get("configurable", {}), "state": state}
    custom_config = {**config, "configurable": configurable}
    
    results = []
    for tool_call in last_message.tool_calls:
        # 执行工具时传入 custom_config
        res = my_tool.invoke(tool_call["args"], config=custom_config)
        results.append(res)
        
    return {"messages": results}
```

**方式二：参数合并后直接调用（更简单直接）**



既然工具执行节点完全由你手写，你拥有对 `tool_call["args"]` 的完全控制权。你可以让参数对 LLM 隐藏，而在节点内部直接补全参数：



Python

```
from typing import Annotated
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

# 1. 使用 InjectedState 隐藏该参数，避免被 LLM 看到
@tool
def my_tool(query: str, state: Annotated[dict, InjectedState]) -> str:
    """搜索工具"""
    return f"Query: {query}, User: {state.get('user_id')}"

# 2. 自定义节点中直接合并参数
def custom_tools_node(state: dict):
    tool_map = {"my_tool": my_tool}
    last_message = state["messages"][-1]
    
    outputs = []
    for tc in last_message.tool_calls:
        t = tool_map[tc["name"]]
        
        # 核心：直接调用底层原始函数 .func，或合并入 args
        # 方式 A：直接调 Python 原生函数，绕过 LangChain 包装器检验
        result = t.func(**tc["args"], state=state)
        
        # 方式 B：用 invoke 传入补充参数
        # result = t.invoke({**tc["args"], "state": state})
        
        outputs.append(result)
    return {"messages": outputs}
```

**方式选择建议**



- **选用 `RunnableConfig`**：适合工具本身需要同时读取 `thread_id`、`callbacks`，且不希望工具函数签名中显式多写一个 `state` 参数。
- **选用参数合并 / `.func`**：适合纯业务工具，签名清晰、易于单独写单元测试，也不用在 `dict` 里层层解包 `configurable`。

### 问：是不是通过RunnableConfig传递更加合适？

从架构设计和工程实践来看，**并不推荐**将 `state` 塞进 `RunnableConfig` 中传递。虽然它能跑通，但本质上是一种破坏框架约定的变通手段（Workaround）。



### 为什么不推荐通过 `RunnableConfig` 传递 State

| **维度**           | **RunnableConfig 方案**                                      | **潜在问题**                                                 |
| ------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **语义职责**       | `config` 的设计初衷是存放**运行元数据与基础设施配置**（如 `thread_id`、`callbacks`、`tags`、超时时间）。 | 把动态变化的业务状态（`state`）强行塞入，造成职责混乱。      |
| **Tracing / 日志** | `configurable` 中的内容会被完整记录到 LangSmith / 追踪日志的元数据中。 | 如果 `state` 包含较多上下文（如长聊天记录、大对象），会导致链路日志严重膨胀，甚至触发序列化异常。 |
| **类型安全与提示** | 业务代码中出现类似 `config.get("configurable", {}).get("state", {}).get("key")`。 | 缺乏类型注解、无 IDE 自动补全，极易因拼写错误引发运行时 `KeyError` 或 `NoneType` 异常。 |
| **单元测试成本**   | 工具函数强耦合了 LangChain 的底层数据结构。                  | 单测时必须手造一个复杂的 `RunnableConfig` 嵌套字典，无法像普通函数一样直接传参验证。 |

### 何时适合使用 `RunnableConfig`？

只有当工具需要读取**真正的运行配置**时，使用 `RunnableConfig` 才是标准做法：



- 获取持久化所需的 `thread_id`（用于区分会话上下文）。
- 获取自定义的系统凭证（如运行时指定的子租户 `tenant_id`、不同的 API Base URL）。
- 传递自定义的 `callbacks` 用于流式输出或打点。

### 不用 `ToolNode` 时的推荐实践

既然工具执行节点完全由你自主编写，最解耦、最健壮的做法是将状态解构逻辑收敛在**节点层**，让工具保持为**纯粹的业务函数**：



**1. 工具只声明自己真正需要的业务参数**

工具无需感知 LangGraph、无需感知全局 `state` 的存在：



Python

```
from langchain_core.tools import tool

@tool
def process_order(product_id: str, user_id: str) -> str:
    """处理用户订单。"""
    # 纯净的入参，单测极其容易：process_order.func("p1", "u123")
    return f"Order {product_id} processed for user {user_id}"
```

**2. 在自定义节点中做参数补全（依赖注入）**

LLM 只生成它知道的 `product_id`，节点负责从 `state` 提取 `user_id` 并补入调用：



Python

```
def custom_tools_node(state: dict):
    tool_map = {"process_order": process_order}
    last_message = state["messages"][-1]
    
    outputs = []
    for tc in last_message.tool_calls:
        tool_instance = tool_map[tc["name"]]
        args = dict(tc["args"])
        
        # 节点根据业务逻辑，将 state 中的字段注入工具调用
        if "user_id" not in args:
            args["user_id"] = state.get("user_id")
            
        result = tool_instance.invoke(args)
        outputs.append(result)
        
    return {"messages": outputs}
```

这种模式让**节点负责编排与数据路由**，让**工具专注业务本身**，不仅完全绕开了对 `ToolNode` 的依赖，也避免了污染 `RunnableConfig`。

### 问：可是@tool注解之后，Agent进行工具调用不是必须得输入user_id的吗

你提到了一个非常关键的问题。



如果直接像上面那样写 `user_id: str`，LangChain 在通过 `@tool` 自动生成 JSON Schema 时，**确实会将其标记为必填字段暴露给 LLM**，导致 LLM 去瞎猜、幻觉或主动向用户索要 `user_id`。



要想既不让 LLM 看到这个参数，又能在自定义节点中手动传参，标准的解法是使用 **`InjectedToolArg`**（或 `InjectedState`）。



### 核心机制：Schema 隐藏并不依赖 `ToolNode`

很多人误以为参数隐藏是 `ToolNode` 做的，**其实是 `@tool` 装饰器本身负责的**：



- 只要参数被标记为 `InjectedToolArg`（或其子类 `InjectedState`），`@tool` 生成供 LLM 读取的 Schema 时就会**自动剔除该字段**。
- `ToolNode` 的唯一作用是**帮你在执行时自动注入**。
- 如果你不用 `ToolNode`，你仍然可以用它来**向 LLM 隐藏参数**，然后在自定义节点里**手动注入**。

### 代码实现

#### 1. 定义工具（使用 `InjectedToolArg` 隐藏字段）

Python

```
from typing import Annotated
from langchain_core.tools import tool, InjectedToolArg

@tool
def process_order(
    product_id: str,
    user_id: Annotated[str, InjectedToolArg]  # 对 LLM 隐藏此参数
) -> str:
    """处理用户订单。"""
    return f"Order {product_id} processed for user {user_id}"
```

你可以打印工具的参数 Schema 验证：



Python

```
print(process_order.args)
# 输出: {'product_id': {'title': 'Product Id', 'type': 'string'}}
# 可以看到 user_id 已经被彻底隐藏，LLM 不会知道它的存在。
```

#### 2. 自定义节点中手动补充参数

由于 LLM 返回的 `tool_call["args"]` 只有 `product_id`，你在执行前将 `state` 里的数据合并进去即可：



Python

```
def custom_tools_node(state: dict):
    tool_map = {"process_order": process_order}
    last_message = state["messages"][-1]
    
    outputs = []
    for tc in last_message.tool_calls:
        tool_instance = tool_map[tc["name"]]
        
        # 1. 复制 LLM 生成的参数
        final_args = dict(tc["args"])
        
        # 2. 手动将 state 注入隐藏参数中
        final_args["user_id"] = state["user_id"]
        
        # 3. 正常调用 invoke（或者调用 tool_instance.func(**final_args)）
        result = tool_instance.invoke(final_args)
        outputs.append(result)
        
    return {"messages": outputs}
```

### 权责划分

- **`@tool` + `InjectedToolArg`**：负责 **屏蔽 Schema**，确保模型看不到 `user_id`。
- **自定义节点**：负责 **填充数据**，从全局状态中取出值塞入调用参数。

这样既不需要依赖预构建的 `ToolNode`，也不会破坏 `RunnableConfig` 的语义。

### 问：好家伙，确实可以，这是否是企业级项目规范开发常用写法，查询确认一下

**是的，这不仅是常用写法，而且是企业级 LLM Agent 开发中的行业标准规范（甚至是安全红线）。**



在严肃的企业级生产环境中，**绝不应该允许 LLM 自行决定或填写身份凭证、租户 ID 等上下文参数**。使用 `InjectedToolArg` 屏蔽模型可见性 + 在服务端运行时手动注入，契合了多项关键的企业架构原则：



### 为什么这是企业级规范？

**1. 抵御权限越权与注入攻击（IDOR 防御）**



- 如果把 `user_id` 或 `tenant_id` 暴露在 Tool Schema 中，一旦发生 Prompt Injection（提示词注入）或模型幻觉，攻击者可能诱导模型生成属于其他用户的 `user_id`，造成越权访问。
- **企业安全准则（Zero-Trust LLM）**：**LLM 是不可信的执行意图生成者，不可充当鉴权凭据的发起者**。用户身份只能从经过 JWT 鉴权后的网关/会话状态中由服务端自动补入。

**2. 降低 Token 开销与模型决策噪声**



- 工具入参越臃肿，LLM 解析和遵循指令的准确率就越低。
- 剔除基础设施和上下文参数，让 Schema 仅保留业务决策参数（如 `product_id`、`date_range`），既节省 Token，又显著降低模型报错率。

**3. 纯净的单元测试与可维护性**



- 工具函数使用标准的 Python 类型标注，不侵入 LangGraph 框架的黑盒对象。
- 单测时只需要调用 `process_order.func(product_id="P1", user_id="U1")`，无需 Mock 整个 LangGraph 运行时。

### 企业级项目中的工程化进阶写法

在实际企业代码中，如果有几十个工具都要注入参数，在自定义节点里写一堆 `if "user_id" not in args` 会显得硬编码。企业通常会采用**通用上下文注入中间件模式**：



Python

```
import inspect
from typing import Annotated, Any
from langchain_core.tools import tool, InjectedToolArg

# 1. 业务工具：按需声明系统级参数（加上 InjectedToolArg）
@tool
def process_order(
    product_id: str,
    user_id: Annotated[str, InjectedToolArg],
    tenant_id: Annotated[str, InjectedToolArg],
) -> str:
    """提交用户订单"""
    return f"Order {product_id} processed for {user_id} in {tenant_id}"

# 2. 企业级通用执行器：基于反射自动补全 State 中的匹配字段
def custom_tools_node(state: dict[str, Any]):
    tool_map = {"process_order": process_order}
    last_message = state["messages"][-1]
    
    outputs = []
    for tc in last_message.tool_calls:
        selected_tool = tool_map[tc["name"]]
        final_args = dict(tc["args"])
        
        # 核心：检查工具签名，凡是工具需要的参数且当前在 state 中存在的，自动注入
        # 这样无需为每个工具手写特定字段的赋值逻辑
        sig = inspect.signature(selected_tool.func)
        for param_name in sig.parameters:
            if param_name not in final_args and param_name in state:
                final_args[param_name] = state[param_name]
        
        # 校验并执行
        result = selected_tool.invoke(final_args)
        outputs.append(result)
        
    return {"messages": outputs}
```

这种方案将**数据安全性（服务端绝对控制身份）**、灵活性（不强依赖官方预构建组件）**与**代码可维护性（反射自动解耦）做到了平衡，是目前自研 Agent 工作流中最稳健的落地方案。

### 问：我的关注点主要是通过config传入还是通过InjectedToolArg传入的分歧上

在 LangChain / LangGraph 的企业级设计中，关于 **`config (RunnableConfig)`** 与 **`InjectedToolArg`** 的选型分歧，本质上是“隐式环境上下文（Ambient Context）”**与**“显式依赖注入（Dependency Injection）”的架构之争。



行业沉淀出的统一划分标准是：**业务数据用 `InjectedToolArg`，基础设施元数据用 `RunnableConfig`。**



### 方案多维对比

| **维度**                 | **InjectedToolArg（推荐用于业务/状态）**                     | **RunnableConfig（用于基础设施/配置）**                      |
| ------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **设计定位**             | **业务参数依赖注入**  工具的函数签名显式表达业务依赖。       | **执行运行时上下文**  跨调用链传递基础设施控制参数。         |
| **代码可读性与自解释性** | **极高**。看函数签名就知道此工具依赖 `user_id`、`order_id`。 | **极差**。入参只有一个看不出业务含义的 `config: RunnableConfig`。 |
| **类型安全与静态检查**   | **完全支持**。Mypy、Pyright 和 IDE 补全均能正常工作。        | **弱**。深层字典取值 `config.get("configurable", {}).get(...)`，无静态检查。 |
| **单元测试成本**         | **零成本**。直接当原生函数调用：  `func(item="A", user_id="U1")`。 | **高**。测试业务逻辑必须伪造一个复杂的 LangChain `RunnableConfig` 对象。 |
| **LangSmith 追踪影响**   | **干净**。参数记录在 Tool Run 的 Inputs 中，属于明确的调用参数。 | **容易污染**。`configurable` 属于 Run Metadata，塞入大对象会导致链路日志体积暴增。 |
| **LLM Schema 过滤**      | 装饰器自动剔除，模型感知不到。                               | 原生忽略，模型感知不到。                                     |

### 两者的严谨边界划分

企业架构中通常遵循以下边界界定：



**必须/推荐使用 `InjectedToolArg` 的场景（业务与状态相关）：**



- **用户/业务上下文**：`user_id`、`tenant_id`、`account_number`。
- **业务状态数据**：`cart_items`、`previous_turn_summary`、`order_status`。
- **规则**：凡是**业务逻辑直接依赖、在没有 LangChain 框架时它依然是此函数的一个参数**的值，全部通过 `InjectedToolArg` 声明。

**必须/推荐使用 `RunnableConfig` 的场景（技术与基础设施相关）：**



- **链路追踪与会话标识**：`thread_id`（用于检查点 Checkpoint 寻址）、`trace_id`。
- **网络与客户端凭证**：统一的鉴权 Header、Bearer Token、下游 RPC 超时时间。
- **运行时扩展**：动态注入的 LangChain Callbacks、动态注入的并发参数（`max_concurrency`）。
- **规则**：凡是非业务语义、只关乎“系统怎么跑”而非“业务算什么”的值，才放入 `RunnableConfig`。

### 为什么把 State 塞入 Config 会被视作反模式（Anti-Pattern）？

在 LangChain 早期（0.0.x ~ 0.1.x 前期），由于当时官方没有提供对模型隐藏参数的标准机制，很多开发者习惯把上下文一股脑塞进 `config["configurable"]` 中。随着项目变大，这种做法暴露了典型的“上帝对象（God Object）”缺陷：



1. **破窗效应**：一旦允许将部分 State 写入 `config`，团队成员就会开始将聊天记录、文件 Base64、数据库连接甚至整个全局图状态全部往 `config` 里面塞，导致该字典变为黑盒，无人敢动。
2. **重构噩梦**：某个工具修改了需要的字段名称，IDE 无法全局重构，只能全局文本搜索字符串 key，极易在运行时引发线上 `KeyError` 或静默失败。
3. **框架绑架业务**：工具函数本应是最纯粹的业务资产（哪怕未来项目不用 LangGraph 改用原生 FastAPI 或其它 Agent 框架，这些业务函数都应该能直接复用）。一旦函数体内写满了 `config.get("configurable", ...)`，业务逻辑就与 LangChain 的运行时生命周期发生了无法解耦的强绑定。

如果关注的是**代码规范度、团队维护成本与架构纯洁性**，用 `InjectedToolArg` 定义签名并在执行节点组装参数，是更符合现代软件工程原则的选择。

### 问：很好，我当前想要先快速实现功能，未来再改成用InjectedToolArg注入的方式，请你总结一下我们上述对话的精华内容还有输出未来优化方案

**核心要点提炼**



- **隐藏机制独立于 `ToolNode`**：`@tool` 生成供 LLM 解析的 JSON Schema 时，会自动剔除被 `InjectedToolArg`（或 `InjectedState`）标记的参数。参数隐藏是装饰器本身的职能，即使脱离 `ToolNode`，Schema 过滤依然完全生效。
- **分歧的本质是职责边界**：
  - `RunnableConfig`：定位为**基础设施运行时环境**（`thread_id`、`callbacks`、超时控制等）。放入业务状态虽能临时跑通，但会导致链路追踪（LangSmith）元数据膨胀，且缺乏类型提示与编译期检查。
  - `InjectedToolArg`：定位为**业务依赖注入**。保留了工具函数的纯粹性（易于单测），显式声明业务依赖，同时在协议层将敏感字段对模型屏蔽。
- **零信任（Zero-Trust）安全原则**：像 `user_id`、`tenant_id` 这类鉴权关键字段，绝不能暴露给不可信的 LLM 推理生成，必须在服务端运行时由经过认证的 State 强行补充。

**当前阶段：快速过渡实现**



在当前不修改工具签名、快速上线的前提下，可通过 `custom_tools_node` 将当前 `state` 挂载至 `config` 的 `configurable` 中下发：



Python

```
# 1. 业务工具（当前临时写法：从 config 中取）
@tool
def get_user_profile(config: RunnableConfig) -> str:
    """获取用户信息"""
    user_id = config.get("configurable", {}).get("state", {}).get("user_id")
    return f"User profile for {user_id}"

# 2. 自定义节点（组装 config 并调用）
def custom_tools_node(state: dict, config: RunnableConfig):
    # 将 state 写入 configurable
    run_config = {
        **config,
        "configurable": {
            **config.get("configurable", {}),
            "state": state
        }
    }
    
    outputs = []
    for tc in state["messages"][-1].tool_calls:
        # 传递包含 state 的 config
        res = tool_map[tc["name"]].invoke(tc["args"], config=run_config)
        outputs.append(res)
    return {"messages": outputs}
```

**未来演进：`InjectedToolArg` 平滑重构方案**



未来演进无需推倒重来，只需按以下三步解耦业务与基础设施：



**第 1 步：工具层彻底解耦（剥离 `config`）**

将工具函数改写为纯业务签名，敏感上下文使用 `InjectedToolArg` 声明。此时函数可脱离 LangChain 单独做单元测试：



Python

```
from typing import Annotated
from langchain_core.tools import tool, InjectedToolArg

@tool
def get_user_profile(user_id: Annotated[str, InjectedToolArg]) -> str:
    """获取用户信息"""
    return f"User profile for {user_id}"

# 单测时无需构造复杂对象，直接调原生函数：
# assert get_user_profile.func(user_id="U1001") == "User profile for U1001"
```

**第 2 步：节点层升级为反射自动注入（避免硬编码）**

在自定义执行节点中，通过 Python 的 `inspect` 模块自动扫描工具签名，若发现未由 LLM 提供的入参存在于 `state` 中，则自动注水补充：



Python

```
import inspect

def custom_tools_node(state: dict, config: RunnableConfig):
    outputs = []
    for tc in state["messages"][-1].tool_calls:
        tool_obj = tool_map[tc["name"]]
        call_args = dict(tc["args"])
        
        # 核心反射逻辑：检查函数签名，从 state 中按参数名自动匹配注入
        sig = inspect.signature(tool_obj.func)
        for param_name in sig.parameters:
            if param_name not in call_args and param_name in state:
                call_args[param_name] = state[param_name]
                
        # 纯净的业务参数 + 框架的原生 config 分开传递
        result = tool_obj.invoke(call_args, config=config)
        outputs.append(result)
        
    return {"messages": outputs}
```

**第 3 步：类型注解与防御性断言**

为自定义节点增加缺参校验。如果某个被标注为 `InjectedToolArg` 的参数在 `state` 中缺失，在执行前抛出明确的异常，避免运行时静默失败。