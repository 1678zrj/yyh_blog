![](https://p3-piu.byteimg.com/tos-cn-i-8jisjyls3a/b37ce6cd3dfa46f699d8fc9c7c888f2f~tplv-8jisjyls3a-3:0:0:q75.png)

## 前端学AI：LangChain、LangGraph和LangSmith的核心区别及定位

> 本文主要介绍 `LangChain` 生态系统中不同层级的组件：LangChain、LangGraph 和 LangSmith，介绍其核心区别及定位。
> 
> 供自己以后查漏补缺，也欢迎同道朋友交流学习。

### 引言

看了 LangChain 的文档，发现 LangChain 有三个组件：`LangChain`、`LangGraph` 和 `LangSmith`。之前不知道他们的区别，查询了下资料，这边做个记录。

`LangChain`、`LangGraph` 和 `LangSmith` 是 LangChain 生态系统中不同层级的组件，分别服务于不同的开发需求。

以下是它们的核心区别及定位：

### LangChain（核心框架）

-   **定位**：大语言模型（`LLM`）应用开发的`基础框架`，提供`模块化构建块`和`全生命周期`支持。
    
-   **核心功能**：
    

1.  **任务链（Chains）**：通过链式结构串联多步骤任务（如提示生成→模型调用→结果解析），支持简单顺序或复杂条件分支。
2.  **工具集成**：通过 `Agents` 协调外部工具（如数据库、API、Python 执行器）与模型的交互。
3.  **检索增强生成（RAG）**：结合`文档加载`、`向量化存储`和`语义检索`，提升回答的准确性和事实性。
4.  **模块化设计**：包含 `langchain-core`（基础接口）、`langchain-community`（社区扩展集成）等子库。

-   **典型场景**：构建`对话系统`、`问答机器人`、`自动化流程`等。

#### LangGraph（复杂任务编排框架）

-   **定位**：LangChain 的`扩展库`，专注于有状态、多参与者的`复杂任务流编排`。
    
-   **核心功能**：
    

1.  **图结构任务流**：通过节点（`Nodes`）、边（`Edges`）和状态（`State`）定义任务图（`DAG`），支持并行执行和依赖管理。
2.  **持久化与恢复**：保存任务状态检查点，允许中断后恢复执行，适用于长时任务和人工介入场景。
3.  **条件分支控制**：基于 `LLM` 推理结果动态选择执行路径（如 ReAct 架构的推理→行动→观察循环）。
4.  **多代理协作**：构建`多智能体`系统（如`代码生成`→`测试`→`反馈循环`）。

-   **典型场景**：开发`多代理协作`系统、需复杂流程控制的`自动化任务`（如数据分析、代码生成）。

#### LangSmith（开发者辅助平台）

-   **定位**：面向生产环境的开发运维（`DevOps`）工具，提供调试、监控和优化支持。
    
-   **核心功能**：
    

1.  **调试与测试**：跟踪任务链执行过程，`定位错误`或`性能瓶颈`。
2.  **Prompt 管理**：优化提示模板，对比不同版本的效果。
3.  **性能监控**：记录模型调用耗时、成本及输出质量，生成可视化报告。
4.  **协作支持**：支持团队共享配置、注释和测试用例。

-   **典型场景**：企业级应用部署前的`调试优化`，生产环境`性能监控`。

#### 对比总结

| `组件` | `层级` | `核心能力差异` | `典型应用案例` |
| --- | --- | --- | --- |
| **LangChain** | `基础框架层` | 模块化`任务链`构建、`工具集成`、`RAG` | 简单问答机器人、文档摘要 |
| **LangGraph** | `扩展编排层` | `图结构任务流`、`状态持久化`、`多代理`协作 | `多智能体系统`、复杂数据分析流程 |
| **LangSmith** | `运维工具层` | `调试`、`监控`、`Prompt 优化` | 生产环境性能调优、团队协作开发 |

#### 协作关系示例

开发一个多代理协作系统时：

1.  用 **LangChain** 构建单个代理的基础功能（如调用模型、访问数据库）；
2.  用 **LangGraph** 编排代理间的协作流程（如任务分配→执行→结果聚合）；
3.  通过 **LangSmith** 监控系统性能，优化提示模板和任务链逻辑。

### 推荐资料

#### 官方资源

-   [LangChain.js官方文档](https://link.juejin.cn/?target=https%3A%2F%2Fjs.langchain.com%2F "https://js.langchain.com/")。
-   GitHub仓库：[langchain-js](https://link.juejin.cn/?target=https%3A%2F%2Fgithub.com%2Flangchain-ai%2Flangchainjs "https://github.com/langchain-ai/langchainjs")（含代码示例）。

#### 学习平台

-   **Deeplearning.ai短期课程**：[LCEL与RAG技术实战](https://link.juejin.cn/?target=https%3A%2F%2Flearn.deeplearning.ai%2Fcourses%2Ffunctions-tools-agents-langchain "https://learn.deeplearning.ai/courses/functions-tools-agents-langchain")
-   **Scrimba交互教程**：[Learn LangChain.js](https://link.juejin.cn/?target=https%3A%2F%2Fscrimba.com%2Flearn-langchainjs-c02t "https://scrimba.com/learn-langchainjs-c02t")。

#### 社区与案例

-   **GitHub示例库**：[langchain-examples](https://link.juejin.cn/?target=https%3A%2F%2Fgithub.com%2Falphasecio%2Flangchain-examples "https://github.com/alphasecio/langchain-examples")。
-   **Stack Overflow**：搜索`langchain.js`标签解决常见问题。

#### 扩展阅读

-   [小白入门大模型：LangChain](https://link.juejin.cn/?target=https%3A%2F%2Fwww.zhihu.com%2Ftardis%2Fbd%2Fart%2F656646499%3Fsource_id%3D1001 "https://www.zhihu.com/tardis/bd/art/656646499?source_id=1001")

### 专栏系列

-   [前端学AI：我的学习路径规划](https://juejin.cn/post/7475504940652789760 "https://juejin.cn/post/7475504940652789760")
-   [前端学AI：AI相关概念介绍](https://juejin.cn/post/7475651690369482761 "https://juejin.cn/post/7475651690369482761")
-   [前端学AI：AI相关平台和工具](https://juejin.cn/post/7475754150791315490 "https://juejin.cn/post/7475754150791315490")
-   [前端学AI：如何写好提示词（prompt）](https://juejin.cn/post/7478185513460482048 "https://juejin.cn/post/7478185513460482048")
-   [前端学AI：基于Node.js的LangChain开发-知识概念](https://juejin.cn/post/7478503126426337315 "https://juejin.cn/post/7478503126426337315")
-   [前端学AI：基于Node.js的Langchain开发-简单实战应用](https://juejin.cn/post/7478585166497595407 "https://juejin.cn/post/7478585166497595407")
-   [前端学AI：LangChain、LangGraph和LangSmith的核心区别及定位](https://juejin.cn/spost/7478620961517207586 "https://juejin.cn/spost/7478620961517207586")

本文收录于以下专栏

![cover](https://p26-juejin-sign.byteimg.com/tos-cn-i-k3u1fbpfcp/95414745836549ce9143753e2a30facd~tplv-k3u1fbpfcp-jj-mark-v1:0:0:0:0:5o6Y6YeR5oqA5pyv56S-5Yy6IEAg54mb5aW2:q75.awebp?rk3s=f64ab15b&x-expires=1753703553&x-signature=vViQvbCvuKQLFCXtb%2BlulWPAW%2F0%3D)

上一篇

前端学AI：基于Node.js的Langchain开发-简单实战应用