![](https://csdnimg.cn/release/blogv2/dist/pc/img/original.png)

[伤心美眉](https://blog.csdn.net/weixin_67551828 "伤心美眉") ![](https://csdnimg.cn/release/blogv2/dist/pc/img/newCurrentTime2.png) 于 2025-06-15 20:19:51 发布

LangGraph 是 LangChain 生态系统的一部分，用于构建状态驱动的[工作流](https://so.csdn.net/so/search?q=%E5%B7%A5%E4%BD%9C%E6%B5%81&spm=1001.2101.3001.7020)和智能代理。它支持开发者创建结构化的、精确控制的流程或动态的智能助手。本文将比较 LangGraph 的两种实现方式：基于 `StateGraph` 的自定义工作流和基于 `create_react_agent` 的 [ReAct](https://so.csdn.net/so/search?q=ReAct&spm=1001.2101.3001.7020) 代理，分析它们的区别、原理和使用场景，用通俗的语言结合 LangGraph 文档进行说明。

___

### 1\. 基于 StateGraph 的自定义工作流

#### 概述

`StateGraph` 工作流就像一条“工厂流水线”，你需要手动设计每个步骤（节点）和步骤之间的连接（边）。它适合需要严格控制多步骤流程的任务，比如生成笑话或文章。

#### 工作原理

-   **状态定义**：使用 `TypedDict` 定义状态（`State`），比如包含 `topic`（主题）、`joke`（初始笑话）、`improved_joke`（改进笑话）等字段，记录流程中的数据。
-   **节点（Nodes）**：每个节点是一个函数，执行特定任务，比如调用大模型（LLM）生成笑话、检查笑话是否有 punchline（笑点）或优化笑话。
-   **边（Edges）**：定义节点之间的跳转逻辑，比如从 `START` 到生成笑话，再根据检查结果决定是结束（`END`）还是继续优化。
-   **条件边**：通过 `add_conditional_edges` 实现动态跳转，比如如果笑话没有 punchline，就进入优化节点。
-   **执行**：通过 `workflow.compile()` 生成可运行的 `chain`，然后用 `chain.invoke({"topic": "cats"})` 执行，输出初始笑话、改进笑话（如果有）等。

#### 特点

-   **高度结构化**：开发者明确定义每一步和跳转逻辑，适合固定、可预测的任务。
-   **状态传递**：状态在节点间传递，比如从生成笑话到优化的过程中，`State` 不断更新。
-   **代码量较多**：需要手动定义状态、节点和边，适合复杂但明确的任务。

#### 示例

**场景**：自动生成一篇关于“AI 医疗”的文章，步骤为：

1.  生成文章大纲。
2.  检查大纲是否包含引言、正文、结论。
3.  如果不完整，补充缺失部分；如果完整，写文章。
4.  最后润色文章，添加吸引人的标题。

**代码**（简化版）：

```python
from typing_extensions import TypedDict from langgraph.graph import StateGraph, START, END class State(TypedDict): topic: str outline: str article: str polished_article: str def generate_outline(state: State): outline = llm.invoke(f"生成关于{state['topic']}的文章大纲") return {"outline": outline.content} def check_outline(state: State): if "引言" in state["outline"] and "结论" in state["outline"]: return "Pass" return "Fail" def improve_outline(state: State): improved = llm.invoke(f"补充这个大纲的缺失部分: {state['outline']}") return {"outline": improved.content} def write_article(state: State): article = llm.invoke(f"根据这个大纲写文章: {state['outline']}") return {"article": article.content} def polish_article(state: State): polished = llm.invoke(f"润色这篇文章并添加吸引人的标题: {state['article']}") return {"polished_article": polished.content} workflow = StateGraph(State) workflow.add_node("generate_outline", generate_outline) workflow.add_node("improve_outline", improve_outline) workflow.add_node("write_article", write_article) workflow.add_node("polish_article", polish_article) workflow.add_edge(START, "generate_outline") workflow.add_conditional_edges("generate_outline", check_outline, {"Pass": "write_article", "Fail": "improve_outline"}) workflow.add_edge("improve_outline", "write_article") workflow.add_edge("write_article", "polish_article") workflow.add_edge("polish_article", END) chain = workflow.compile() result = chain.invoke({"topic": "AI在医疗中的应用"}) print(result["polished_article"])
```

**为什么用 StateGraph**：

-   任务有明确的步骤（生成 → 检查 → 补充 → 写作 → 润色）。
-   需要状态在步骤间传递，比如大纲传递到写作节点。
-   适合结构化的内容生成任务。

___

### 2\. 基于 create\_react\_agent 的 ReAct 代理

#### 概述

ReAct 代理就像一个“[智能助手](https://so.csdn.net/so/search?q=%E6%99%BA%E8%83%BD%E5%8A%A9%E6%89%8B&spm=1001.2101.3001.7020)”，你给它工具（比如查天气的函数），它根据用户输入动态决定如何处理，比如是直接回答还是调用工具。ReAct 是“推理（Reasoning）+行动（Acting）”的缩写，适合处理不确定的用户输入。

#### 工作原理

-   **代理结构**：使用 `langgraph.prebuilt.create_react_agent` 创建，集成了大模型（比如 Claude-3.7）和工具（比如 `get_weather`）。
-   **ReAct 逻辑**：代理接收用户输入（比如“旧金山的天气”），先推理：
    -   如果能直接回答，就用模型知识回复。
    -   如果需要工具（比如查天气），就调用工具并整合结果。
-   **状态管理**：内部也是一个 `StateGraph`，状态包括用户消息、工具调用历史等，代理通过循环（推理 → 行动 → 观察）更新状态，直到得出最终回答。
-   **执行**：通过 `agent.invoke({"messages": [...]})` 执行，代理动态处理输入，比如调用 `get_weather("sf")` 返回“旧金山总是晴天！”。

#### 特点

-   **动态性强**：根据输入自动决定行动路径，适合开放性任务。
-   **简单易用**：使用预构建模板，开发者只需提供模型和工具，省去定义节点和边的麻烦。
-   **适合交互**：能处理多种用户输入，比如问天气、翻译或计算。

#### 示例

**场景**：构建一个能回答天气、翻译等多种问题的助手，用户输入不确定。

**代码**：

```python
from langgraph.prebuilt import create_react_agent def get_weather(city: str) -> str: return f"{city}总是晴天！" def translate_text(text: str, target_lang: str) -> str: return f"将'{text}'翻译成{target_lang}：[模拟翻译]" agent = create_react_agent( model="anthropic:claude-3-7-sonnet-latest", tools=[get_weather, translate_text], prompt="你是一个能回答问题和使用工具的助手" ) # 用户问天气 result = agent.invoke({"messages": [{"role": "user", "content": "东京的天气如何？"}]}) print(result["messages"][-1]["content"]) # 输出：东京总是晴天！ # 用户问翻译 result = agent.invoke({"messages": [{"role": "user", "content": "把'hello'翻译成西班牙语"}]}) print(result["messages"][-1]["content"]) # 输出：将'hello'翻译成西班牙语：[模拟翻译]
```

**为什么用 ReAct 代理**：

-   用户输入多样（天气、翻译等），代理动态决定调用哪个工具。
-   预构建模板省去手动定义流程的麻烦。
-   适合快速开发交互式应用，比如聊天机器人。

___

### 3\. 两者区别

| 特性 | StateGraph 工作流 | ReAct 代理 |
| --- | --- | --- |
| **结构** | 固定流水线，开发者定义节点和边 | 动态推理，框架控制逻辑 |
| **控制程度** | 高度可控，适合定制化任务 | 逻辑由 ReAct 框架管理，适合通用任务 |
| **开发复杂度** | 需手动定义状态、节点、边，代码较多 | 预构建模板，代码简单 |
| **动态性** | 逻辑固定，适合可预测任务 | 动态处理输入，适合开放性任务 |
| **状态管理** | 状态在节点间显式传递 | 状态由框架管理，包含消息和工具历史 |

**核心区别**：

-   **StateGraph 工作流**：像搭积木，手动设计每一步，适合固定流程，比如生成笑话（生成 → 检查 → 优化）。
-   **ReAct 代理**：像智能助手，给它工具，它自己决定怎么用，适合处理用户问天气、翻译等动态任务。

___

### 4\. 哪个是真正的 LangGraph？

两者都基于 LangGraph！LangGraph 是一个基于有向图的框架，核心是 `StateGraph`，通过状态、节点和边管理复杂逻辑。

-   **StateGraph 工作流**：直接使用 LangGraph 的核心功能，手动定义状态、节点和边，适合高度定制化场景。比如，笑话生成中，状态从 `{"topic": "cats"}` 更新到包含 `joke`、`improved_joke` 等。
-   **ReAct 代理**：使用 LangGraph 的预构建模板（`create_react_agent`），内部也是 `StateGraph`，但逻辑由框架预定义，开发者只需提供工具和提示，适合快速开发。

___

### 5\. 使用场景

#### StateGraph 工作流

-   **适用场景**：
    -   需要严格控制多步骤任务，比如内容生成（笑话、文章）、数据处理流水线。
    -   有明确步骤和条件分支，比如“如果数据不完整就补充”。
    -   需要状态持久化，传递中间结果。
-   **例子**：
    -   营销文案生成：生成草稿 → 检查关键词 → 优化语气 → 添加行动号召。
    -   数据清洗：读取数据 → 检查缺失值 → 填充缺失值 → 保存。

#### ReAct 代理

-   **适用场景**：
    -   处理开放性用户输入，比如聊天机器人、问答系统。
    -   需要动态调用外部工具（API、数据库、计算器等）。
    -   快速原型开发，省去手动定义复杂流程。
-   **例子**：
    -   智能助手：回答天气、翻译、搜索网页。
    -   客户服务机器人：根据用户问题调用订单查询 API 或回答常见问题。

___

### 6\. 总结

-   **StateGraph 工作流**：像“工厂流水线”，你自己设计每条传送带，适合固定、复杂的任务，比如生成笑话或文章。
-   **ReAct 代理**：像“智能助手”，给它工具，它自己判断怎么用，适合回答用户各种问题，比如查天气。
-   **选择建议**：
    -   如果需要精确控制每一步，用 StateGraph（比如笑话生成）。
    -   如果需要快速建一个能处理多种问题的助手，用 ReAct 代理（比如天气查询机器人）。