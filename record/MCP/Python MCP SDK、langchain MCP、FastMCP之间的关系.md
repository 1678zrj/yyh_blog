### 问：我最近正在学习MCP，但是我有点搞不清楚Python的mcp SDK、FastMCP、langchain的MCP之间的关系

可以用一句话理清三者的核心定位：**官方 `mcp` SDK 是地基规范，FastMCP 是用来极速写 Server 的“高级框架”，而 LangChain 的 MCP 适配器是让 Agent 消费调用这些服务的“客户端桥梁”。**   



### 用 Web 开发做个直观类比

| **概念**             | **类比 Web 开发生态**       | **角色定位**                                 |
| -------------------- | --------------------------- | -------------------------------------------- |
| **Python `mcp` SDK** | **ASGI / WSGI 底层库**      | 官方核心协议与数据模型实现                   |
| **FastMCP**          | **FastAPI / Flask**         | 基于官方协议的高层开发框架（负责“提供接口”） |
| **LangChain MCP**    | **Requests / 前端业务框架** | 客户端适配器（负责“调用接口并融入 Agent”）   |

### 三者的详细关系与分工

#### 1. Python 官方 `mcp` SDK (`pip install mcp`)

- **定位**：Anthropic 官方维护的 MCP 参考实现库。
- **负责的事**：定义了 MCP 协议的底层通信细节（JSON-RPC 2.0 封包）、传输协议实现（Stdio、SSE、Streamable HTTP）、以及完整的 Pydantic 数据模型（如 `Tool`、`CallToolResult`、`TextContent` 等）。   
- **痛点**：由于它是底层基础设施，直接用它手写 Server 需要处理大量底层生命周期管理和类型解析样板代码（Boilerplate）。

#### 2. FastMCP（服务端的“语法糖”）

- **定位**：基于官方协议的高层服务端框架，设计灵感来自 FastAPI。
- **与官方 SDK 的关系**：FastMCP 最初由社区（Prefect 团队）开发，因体验极佳，其核心实现**已被官方吸收并合并进了官方 SDK**（即 `from mcp.server.fastmcp import FastMCP`），同时作为独立的高级包（FastMCP 2.0+）持续迭代。   
- **负责的事**：它将繁琐的协议细节隐藏，让你用极其简单的 Python 装饰器（`@mcp.tool()`）快速声明工具、资源和提示词。它会自动提取函数的类型注解（Type Hints）生成标准 JSON Schema，极大降低写 MCP Server 的门槛。   

#### 3. LangChain 的 MCP（客户端的“转换插头”）   

- **主要包**：`langchain-mcp-adapters`   
- **定位**：LangChain 生态与 MCP 生态的**粘合剂（Client 适配层）**。   
- **负责的事**：MCP 是一套开放协议，其他平台（如 Claude Desktop、Cursor、Cline）都可以作为 Client 连接它。如果你要在自己的 LangChain / LangGraph 应用中使用 MCP 服务，这个库负责连接 MCP Server，并将 MCP 格式的 Tool 自动转为 LangChain 原生的 `BaseTool`。这样，你的 LangChain Agent 就能像调用普通工具一样无缝调用任意 MCP 服务。   

### 完整的交互链路

在实际项目中，它们通常是上下游的协作关系：



Plaintext

```
[ 服务端开发 ]
开发者使用 FastMCP 编写工具函数
      │ (底层依赖 Python mcp SDK 处理协议与传输)
      ▼
启动 MCP Server (暴露 Stdio / SSE / HTTP 接口)
      ▲
      │ 遵循 MCP 标准协议通信
      ▼
[ 客户端消费 ]
LangChain MCP Adapter 作为 Client 连接 MCP Server
      │ (将 MCP 工具转换为 LangChain BaseTool)
      ▼
LangChain / LangGraph Agent (由 LLM 决定何时调用该工具)
```

### 开发选型建议

- **如果你要自己写一个 MCP Server（提供工具/数据源）**：

  直接使用 **FastMCP**（推荐从 `mcp.server.fastmcp` 或独立的 `fastmcp` 包起步），这是目前 Python 生态中最省心、最现代化的写法。除非需要深度定制协议交互，否则不需要直接操作官方 SDK 的底层类。

- **如果你在开发一个 Agent，需要调用现成的 MCP 生态（如 GitHub、数据库等工具）**：

  使用 **`langchain-mcp-adapters`** 将 MCP Server 引入你的 LangChain/LangGraph 编排流程中。   

### 问：好吧，我现在暂时不需要开发mcp server，那我应该是使用langchain-mcp-adapters或者Python的mcp sdk了，langchain-mcp-adapters应该也是基于Python的mcp sdk开发的吧？

**完全正确，你的判断非常敏锐。**



`langchain-mcp-adapters` 底层就是强依赖官方的 `mcp` Python SDK。



### 它在底层是如何基于官方 SDK 工作的？

在底层，`langchain-mcp-adapters` 并没有重新实现一套网络协议，它扮演的是转换器（Adapter）的角色：



1. **连接与通信（交由官方 SDK）**：

   它直接调用官方 SDK 的客户端通信模块（如 `mcp.client.stdio.stdio_client` 或 `mcp.client.sse.sse_client`），通过 `ClientSession` 与目标 MCP Server 握手并拉取工具列表。

2. **协议对象转换（Adapter 的核心价值）**：

   官方 SDK 返回的工具是标准的 `mcp.types.Tool`（包含 MCP 标准的 JSON Schema）。适配器会将这些工具自动包装转换成 LangChain 认识的 `BaseTool` / `StructuredTool`。

3. **调用转发**：

   当你的 LangChain Agent 决定调用某个工具时，适配器会在后台调用官方 SDK 的 `session.call_tool(...)` 发送请求，并将结果格式化后交回给 LLM。

### 作为“调用方”，你该选哪一个？

既然只是做 Client 调用，选型逻辑取决于你的**上层技术栈**：



#### 方案 A：使用 `langchain-mcp-adapters`（强烈推荐）

- **适用场景**：你的项目已经在使用 LangChain、LangGraph，或者习惯用 LangChain 的 LLM 封装（如 `ChatOpenAI`, `ChatAnthropic`）。
- **优势**：
  - **开箱即用**：几行代码就能把 MCP Server 的工具直接挂到 Agent 的 `tools=[...]` 列表中。
  - **天然契合**：不需要手动处理 JSON-RPC 消息体和返回格式解析，它会自动融入 LangChain 的执行图、Callbacks 和流式输出中。

Python

```
# 核心逻辑：直接把 MCP 服务转换成 LangChain Tools
from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "fetch_service": {
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "transport": "stdio",
    }
}) as client:
    # 这一步拿到的已经是 LangChain 标准的 BaseTool 列表
    tools = client.get_tools()
    
    # 直接绑定给你的模型或 Agent
    llm_with_tools = model.bind_tools(tools)
```

#### 方案 B：直接使用官方 `mcp` SDK (`mcp.client`)

- **适用场景**：你**完全不用 LangChain**，而是用原生代码、纯 `openai` / `anthropic` SDK，或者自己维护 Agent 循环。
- **代价**：你需要自己写代码维护 `ClientSession` 的异步生命周期，自己把 MCP 工具定义转成 OpenAI 接受的 `tools=[{"type": "function", ...}]` 格式，并在模型返回 `tool_calls` 时手动编写分发和调用逻辑。

如果你当前的系统基于 LangChain / LangGraph 编排，**直接选 `langchain-mcp-adapters` 是最省心、最标准的选择**。

### 问：langchain-mcp-adapters要在生产环境使用的话，MultiServerMCPClient是否应该复用呢？

**在生产环境中，强烈建议将 `MultiServerMCPClient` 作为长生命周期对象进行复用（例如通过单例模式或 Web 框架的 Lifespan 管理），而不是在每个请求（Per-Request）中频繁创建和销毁。**



### 为什么必须复用？

#### 1. 规避进程启停的致命性能损耗（针对 Stdio 模式）

如果你的 MCP Server 是通过命令行命令（Stdio 传输方式，如 `uvx`、`python server.py`、`npx`）启动的：



- **每次新建 Client** 意味着系统要进行一次 `fork/exec`，拉起一个独立的 Python 或 Node.js 子进程。
- 进程启动、加载依赖包可能耗时数百毫秒甚至数秒，请求结束还要等待子进程回收。
- 在并发或频繁请求下，每个请求起一个进程会导致 **CPU 飙升、接口延迟激增，甚至引发僵尸进程或内存耗尽**。复用 Client 能让服务子进程常驻后台，随调随用。

#### 2. 消除协议握手与 Schema 解析延迟

MCP 基于标准的 JSON-RPC 2.0 握手协议。每次创建连接并获取工具时，客户端必须经历：



1. 发送 `initialize` 请求（协商协议版本与能力集）。
2. 发送 `notifications/initialized` 确认握手。
3. 发送 `tools/list` 获取工具元数据并解析 JSON Schema。   

如果连接了多个 Server，这种网络/IPC 往返耗时会线性累加。复用 Client 允许在服务启动阶段一次性完成工具拉取，请求到来时直接调用，零握手等待。   



#### 3. 连接复用（针对 SSE / Streamable HTTP 模式）   

如果 MCP Server 部署为远端微服务（SSE 或 HTTP 传输）：



- 复用 Client 能够复用底层的 TCP/TLS 连接池和长连接通道，避免每次请求都产生 3 次握手与 TLS 协商。

### 生产推荐实践：结合 FastAPI 的 Lifespan

在生产级 Web 服务中，最标准的做法是**将 Client 的生命周期挂载到应用的全局生命周期上**：

Python

```
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

# 存放全局单例对象
app_state = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 应用启动：初始化 MCP 客户端并建立长连接
    client = MultiServerMCPClient({
        "remote_weather": {
            "url": "http://weather-service:8000/sse",
            "transport": "sse",
        }
    })
    
    # 2. 一次性获取工具定义
    tools = await client.get_tools()
    
    # 3. 预构建 Agent
    llm = ChatOpenAI(model="gpt-4o")
    agent = create_react_agent(llm, tools)
    
    app_state["mcp_client"] = client
    app_state["agent"] = agent
    
    yield  # 服务运行中，处理请求...
    
    # 4. 应用停止：优雅关闭连接与子进程
    await client.close()

app = FastAPI(lifespan=lifespan)

@app.post("/chat")
async def chat(prompt: str):
    # 直接复用预加载好的 Agent
    agent = app_state["agent"]
    result = await agent.ainvoke({"messages": [("user", prompt)]})
    return {"reply": result["messages"][-1].content}
```

### 复用时的注意事项与避坑指南

| **关注维度**           | **潜在问题**                                                 | **生产应对方案**                                             |
| ---------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **传输选型**           | Stdio 模式受限于单机资源与单进程能力，难以横向扩容。         | **生产环境强烈建议将 MCP Server 部署为独立的 HTTP/SSE 服务**，Client 仅作为远程网络客户端连接。 |
| **连接断开与健康检查** | 长连接可能因网络波动或服务端重启而失效。                     | 配置连接重试机制（Retry Policy），并在遇到特定通信异常时触发重连或重新初始化。 |
| **多租户与用户隔离**   | 某些 MCP 工具依赖当前登录用户的 OAuth Token（如用户的私有 GitHub 仓库）。 | **系统级公共工具**采用全局单例复用；**用户专属工具**建议维护带有 TTL 的 Client 缓存池，或优先采用支持请求时透传用户上下文（Header/Params）的服务端架构。 |
| **并发与调用排队**     | 单个 MCP Server 实例若是同步阻塞的，并发调用会被排队。       | 确保 MCP Server 是异步非阻塞实现的，或者在服务端前配置负载均衡。 |

### 问：如果不是后端web应用，而是本地CLI应用呢

在本地 CLI 应用中，核心逻辑依然是“在整个会话生命周期内保持复用”。不过，具体策略取决于 CLI 的运行模式是一次性的还是交互式的。



### 两种 CLI 模式下的复用策略

#### 1. 交互式 CLI / REPL 模式（极力推荐复用）

如果你的 CLI 类似 Claude Code、Cursor 终端或多轮对话助手（用户启动一次程序，随后在终端连续输入多条指令）：



- **生命周期策略**：**在 CLI 启动时初始化一次，整个会话期间复用，退出时统一销毁。**
- **为什么**：用户在终端交互时对响应延迟非常敏感。如果用户每输入一句话你都重新启动一次子进程、协商协议并获取工具，终端会产生长达 1~3 秒的卡顿，交互体验会非常差。

Python

```
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

async def interactive_cli():
    # 1. 启动会话前，初始化 Client（连接通常为本地 Stdio）
    async with MultiServerMCPClient({
        "fetch_server": {
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "transport": "stdio",
        }
    }) as client:
        # 2. 一次性获取工具并构建 Agent
        tools = await client.get_tools()
        llm = ChatOpenAI(model="gpt-4o")
        agent = create_react_agent(llm, tools)
        
        print("MCP Agent 已就绪，输入 'exit' 退出。")

        # 3. 循环复用同一个 Agent 与 MCP 连接
        while True:
            try:
                # 终端异步获取输入
                user_input = await asyncio.to_thread(input, "\n你: ")
                if user_input.strip().lower() in ("exit", "quit"):
                    break
                if not user_input.strip():
                    continue

                result = await agent.ainvoke({"messages": [("user", user_input)]})
                print(f"助手: {result['messages'][-1].content}")

            except (KeyboardInterrupt, asyncio.CancelledError):
                break
                
    # 4. 跳出 context manager 时自动杀死子进程，不留孤儿进程
    print("\n已退出会话并清理进程。")

if __name__ == "__main__":
    asyncio.run(interactive_cli())
```

#### 2. 单次执行命令（One-shot CLI 模式）

如果你的 CLI 类似 `git commit` 或 `curl`（如 `my-cli run --prompt "分析当前目录"`，执行完一个任务就立刻退出进程）：



- **生命周期策略**：**无需跨进程复用，用完即毁。**
- **实现方式**：每个命令作为独立的 OS 进程运行，直接使用 `async with MultiServerMCPClient(...)`，任务执行完毕后依靠上下文管理器优雅关闭连接并退出进程。

### 本地 CLI 场景与 Web 场景的关键区别

| **对比维度**     | **Web 后端应用**                       | **本地 CLI 应用**                         |
| ---------------- | -------------------------------------- | ----------------------------------------- |
| **首选传输协议** | **SSE / HTTP**（解耦部署、易水平扩容） | **Stdio**（首选，本地即起即用，不占端口） |
| **复用范围**     | 跨所有用户请求的**全局单例**           | 单个用户的**终端会话级别**                |
| **资源限制**     | 必须严格限制并发进程数与常驻内存       | 单机运行，主要关注用户当前的机器资源占用  |
| **首要技术陷阱** | 高并发击垮 Server、连接池耗尽          | **Ctrl+C 导致的孤儿进程遗留**             |

### CLI 开发的两个关键体验细节

#### 1. 严防“孤儿子进程”

在 Stdio 模式下，Client 会通过管道拉起子进程（例如 `npx` 或 Python 解释器）。

CLI 用户非常习惯直接按 **`Ctrl+C` (SIGINT)** 中断程序。如果你的代码没有通过 `try...finally` 或 `async with` 捕获异常并执行清理逻辑，Python 主进程退出了，**后台的 MCP 子进程可能会变成孤儿进程持续霸占系统内存或端口**。使用 `async with` 或显式注册信号处理函数能确保退出时发送 `SIGTERM` 杀掉子进程。



#### 2. 优化启动阶段的终端交互

Stdio 启动子进程并完成握手通常需要 0.5~1.5 秒。在交互式 CLI 初始化阶段，可以在拉取工具时给用户展示一个简单的 Spinner（如结合 `rich` 库的 `console.status(...)`），避免用户在纯黑屏下以为终端卡死。

### 问：好家伙，我还是有些疑问，首先是就不能不采用async with MultiServerMCPClient的写法吗？还有就是如果本地cli应用也要获取远程MCP服务呢？

针对这两个问题，答案都是肯定的。下面逐一拆解实现方式与背后的工程原理。



### 一、能不能不用 `async with MultiServerMCPClient`？

**完全可以。**



之所以官方文档喜欢写 `async with`，是因为底层涉及子进程管道和网络 Stream 的释放，用上下文管理器最不容易写出“进程/连接泄漏”的 Bug。但这种写法会导致整个 CLI 逻辑都被迫缩进在一层代码块里。



Python 的 `async with obj:` 本质上只是在进入时调用 `await obj.__aenter__()`，退出时调用 `await obj.__aexit__(...)`。要跳出缩进地狱，**最佳实践是将其封装成一个独立的 Manager 类，显式提供 `start()` 和 `close()` 生命周期方法**。



#### 面向对象封装示例（无需 `async with` 缩进）：

Python

```
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

class AgentCLIService:
    def __init__(self, server_config: dict):
        self.server_config = server_config
        self.client = MultiServerMCPClient(server_config)
        self.agent = None

    async def start(self):
        """显式初始化，替代 __aenter__"""
        # 手动驱动进入上下文，建立与各 MCP Server 的连接
        await self.client.__aenter__()
        
        # 拉取工具并组装 Agent
        tools = await self.client.get_tools()
        llm = ChatOpenAI(model="gpt-4o")
        self.agent = create_react_agent(llm, tools)
        print("MCP 连接已建立，Agent 初始化完成。")

    async def execute_task(self, query: str):
        """执行具体任务"""
        if not self.agent:
            raise RuntimeError("服务未启动，请先调用 start()")
        return await self.agent.ainvoke({"messages": [("user", query)]})

    async def close(self):
        """显式销毁，替代 __aexit__"""
        # 退出上下文，自动杀掉子进程、断开网络连接
        await self.client.__aexit__(None, None, None)
        print("MCP 连接已安全关闭。")


async def main():
    service = AgentCLIService({
        "fetch_server": {
            "command": "uvx",
            "args": ["mcp-server-fetch"],
            "transport": "stdio",
        }
    })

    try:
        await service.start()
        
        # 你的主业务循环在最外层，扁平无多余缩进
        while True:
            cmd = await asyncio.to_thread(input, ">> ")
            if cmd in ("exit", "quit"):
                break
            res = await service.execute_task(cmd)
            print(res["messages"][-1].content)
            
    finally:
        # 保证无论程序是正常退出还是异常崩溃，都能清理子进程
        await service.close()

if __name__ == "__main__":
    asyncio.run(main())
```

> **提示**：如果项目很复杂、需要动态挂载多个不同的 MCP Server，还可以使用 Python 标准库的 `contextlib.AsyncExitStack` 进行动态注册与统一收口清理。

### 二、本地 CLI 应用要获取远程 MCP 服务怎么办？

**这正是 MCP 协议最大的优势之一：Client 根本不在乎工具运行在本地还是千里之外。**



CLI 应用不需要本地安装工具所依赖的环境（比如 Node.js、特定的 Python 虚拟环境、数据库客户端），只要远程服务器通过 **SSE（Server-Sent Events）** 或 **HTTP** 暴露了 MCP 服务，本地 CLI 就能直接配置连接。



甚至，**一个 CLI 可以同时混用“本地工具”和“远程微服务”**。



#### 远程 + 本地混合配置示例：

Python

```
client_config = {
    # 1. 远程 MCP 服务（例如部署在内网云服务器的知识库或企业微服务）
    "remote_company_rag": {
        "url": "https://mcp.internal.yourcompany.com/sse",
        "transport": "sse",
        "headers": {
            # 远程服务通常需要鉴权
            "Authorization": "Bearer your-user-jwt-or-api-key",
            "X-Tenant-ID": "dev-env"
        }
    },
    
    # 2. 本地 MCP 服务（依然保留一些本地专属能力，如读写当前终端所在目录的文件）
    "local_filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
        "transport": "stdio",
    }
}

client = MultiServerMCPClient(client_config)
```

#### 本地 CLI 接入远程 MCP 服务的实际收益与考量：

1. **环境极简（Thin Client）**：

   本地用户的机器上只需一个纯 Python 脚本，不需要装几十个依赖和环境，重型依赖（如向量库驱动、浏览器自动化无头环境等）全跑在远程服务器上。

2. **免去子进程管理负担**：

   对于远程服务，本地 CLI **不需要 fork 任何 OS 子进程**，仅仅是一个轻量级 `aiohttp` / `httpx` 网络请求，完全不用担心 Ctrl+C 导致本地残留僵尸进程。

3. **唯一的注意点是网络超时**：

   调用远程 MCP 时可能会受公网波动影响。如果远程工具执行耗时较长（比如远程跑抓取或大模型分析），需要在 Client 配置中适当调大连接与读取的超时时间（Timeout）。