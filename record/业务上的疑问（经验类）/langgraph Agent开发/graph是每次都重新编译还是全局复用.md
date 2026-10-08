### 问：我在学习langgraph Agent开发，我想了解的是langgraph构建的graph是每次都重新编译还是全局复用。主要每次重新编译的写法似乎更加优雅，但是却比全局复用多了重新编译的损耗

在生产和工程实践中，标准的做法是**全局编译一次、多请求并发复用（Compile Once, Run Many）**。



LangGraph 的 `CompiledStateGraph` 本质上是一个**无状态、线程安全（Thread-safe）且可重入的执行蓝图（Pregel Runner）**。每次请求重新编译看似局部封装更干净，但在架构上通常是反模式。



### 为什么不建议每次请求重新编译？

#### 1. 编译开销不仅是 CPU 耗时，更是资源生命周期问题

调用 `workflow.compile()` 时，LangGraph 会执行：



- 节点、边、条件边的拓扑合法性验证与环路检测。
- 状态通道（Channels）、Managed Values 及 Reducer 的映射绑定。
- **Checkpointer（状态持久化）与 Store（记忆库）的初始化与绑定**。

如果你的 Agent 接入了生产级持久化介质（例如 `PostgresSaver` 或 `RedisSaver`），每次重新编译意味着频繁重复绑定甚至重新创建连接池，极易导致连接泄露或数据库连接耗尽。



#### 2. CompiledGraph 本身不存储运行期状态

很多开发者误以为“全局复用会导致并发请求之间的对话状态混乱”，但事实上：



- **静态结构与动态上下文解耦**：图的节点定义、逻辑流是固定的。
- **运行期状态属于请求上下文**：每次执行 `app.invoke()` 或 `app.astream()` 时，状态是通过 `thread_id` 和当前入参动态加载的。不同用户的请求在内存和持久层中完全通过 `thread_id` 物理隔离，并发执行互不干扰。

### 为什么很多人觉得“每次编译写法更优雅”？

通常是因为遇到以下两个痛点，误以为只有“每次新建图”才能解决：



1. **动态参数传参（如用户 Token、当前用户 ID、动态 API Key）**
2. **避免模块级全局变量带来的副作用**

在 LangGraph 中，这两个问题都有原生、优雅且不破坏全局复用的解决方案：



#### 方案 1：使用 `RunnableConfig` 传递请求级动态上下文

不要把动态数据写死在节点闭包里，而是通过 `configurable` 传入。所有节点都可以直接在入参中拿到 `config`：



Python

```
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from typing import TypedDict

class AgentState(TypedDict):
    input: str
    output: str

# 1. 节点声明直接接收 config
def my_node(state: AgentState, config: RunnableConfig):
    # 动态获取请求级别的参数（如用户身份、独立客户端实例等）
    user_id = config.get("configurable", {}).get("user_id")
    api_token = config.get("configurable", {}).get("api_token")
    return {"output": f"Processed for user {user_id}"}

builder = StateGraph(AgentState)
builder.add_node("process", my_node)
builder.add_edge(START, "process")
builder.add_edge("process", END)

# 2. 全局/服务启动时编译一次
app = builder.compile()

# 3. 运行期每次请求传入不同的 config
async def handle_request(user_input: str, user_id: str, token: str):
    config = {
        "configurable": {
            "thread_id": f"session_{user_id}",  # 隔离对话状态
            "user_id": user_id,                 # 隔离运行时参数
            "api_token": token
        }
    }
    return await app.ainvoke({"input": user_input}, config=config)
```

#### 方案 2：利用框架生命周期（如 FastAPI Lifespan）管理

若不想在模块顶层写全局变量，可以用工厂函数配合 Web 框架的生命周期管理器，将编译好的 `app` 挂载在应用上下文上：



Python

```
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

def create_agent_graph(checkpointer):
    builder = StateGraph(AgentState)
    # ... 构建节点与边
    return builder.compile(checkpointer=checkpointer)

@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    # 启动时初始化连接池并编译图
    async with AsyncPostgresSaver.from_conn_string("postgresql://...") as checkpointer:
        await checkpointer.setup()
        fastapi_app.state.agent = create_agent_graph(checkpointer)
        yield
    # 关闭时资源自动清理

api = FastAPI(lifespan=lifespan)

@api.post("/chat")
async def chat(request: Request, user_msg: str, session_id: str):
    agent = request.app.state.agent  # 复用单例编译图
    result = await agent.ainvoke(
        {"input": user_msg}, 
        config={"configurable": {"thread_id": session_id}}
    )
    return result
```

### 什么时候才需要动态/重新编译？

只有在**图的拓扑结构（Topology）本身需要随请求改变**时才考虑重新编译，例如：



- **插件式架构**：不同租户启用了完全不同的自选节点或分支流程。
- **元编程生成图**：由大模型自身实时规划并生成了一张全新的任务图（Dynamic Plan-and-Solve）。

即使是多租户拓扑差异，常见优化策略也是使用租户级缓存池（LRU Cache）缓存编译后的图，而非无脑每次重建。