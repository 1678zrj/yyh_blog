### 问：langgraph的中断是什么类型的，如何获取中断的数据

LangGraph 的中断（Human-in-the-loop）在底层基于可恢复异常（`GraphInterrupt`）**与**检查点持久化（Checkpointer）实现，在图运行到断点时自动挂起并持久化上下文状态。



从使用形态与颗粒度来看，主要分为两类中断：



**中断的类型**  



- **动态中断（Dynamic Interrupt / 节点内部中断）**：

  在节点内部任意位置通过调用 `interrupt(payload)` 触发。它会在代码执行到该行时抛出中断信号并暂存图状态。恢复时外部可以通过 `Command(resume=...)` 将外部输入直接传回该断点作为该函数的返回值。  

- **静态中断（Static Interrupt / 节点边界中断）**：

  在编译图时声明，如 `graph.compile(checkpointer=..., interrupt_before=[...], interrupt_after=[...])`。无需改动节点内部代码，由图调度器在进入节点前或执行完节点后硬性拦截挂起。  

**如何获取中断的数据**  



根据运行模式（同步调用、流式传输或离线查询状态），获取中断数据主要有以下三种方式：



**1. 通过 `get_state()` 查询状态（最常用、最推荐）**



无论工作流何时被挂起，都可以通过线程的 `config` 随时拉取检查点状态：



Python

```
config = {"configurable": {"thread_id": "thread-1"}}
state = graph.get_state(config)

# 方式 A：直接读取便捷属性 state.interrupts（获取动态中断抛出的 payload）
if state.interrupts:
    for intr in state.interrupts:
        print("中断携带的数据:", intr.value)

# 方式 B：从当前待执行的 task 中获取中断详情
for task in state.tasks:
    if task.interrupts:
        for intr in task.interrupts:
            print("任务中断数据:", intr.value)

# 获取整个图在中断时的上下文状态（适用于静态/动态中断）
current_graph_data = state.values
next_node = state.next  # 下一个待执行的节点名称
```

**2. 从 `invoke()` 的返回值中获取**



当使用 `graph.invoke()` 触发动态中断暂停时，返回的字典中会直接暴露特殊的 `__interrupt__` 键：  



Python

```
result = graph.invoke(inputs, config=config)

if "__interrupt__" in result:
    # 包含了 interrupt(payload) 传入的数据元组/列表
    interrupt_data = result["__interrupt__"]
    print("中断数据:", interrupt_data)
```

**3. 从 `stream()` 事件流中捕获**



在使用流式输出时，图遇到中断会正常终止迭代，最后发出的 chunk 会携带中断事件：



Python

```
for chunk in graph.stream(inputs, config=config, stream_mode="updates"):
    if "__interrupt__" in chunk:
        interrupt_info = chunk["__interrupt__"]
        print("流式中断捕获:", interrupt_info)
```

**恢复执行的处理方式**



获取并处理完人工干预数据后，恢复执行的方式取决于中断类型：  



- **动态中断**：使用 `Command(resume=...)` 传回外部处理结果：  

  Python

  ```
  from langgraph.types import Command
  
  graph.invoke(Command(resume={"action": "approve"}), config=config)
  ```

- **静态中断**：通过 `update_state` 修改数据（若需要），然后传入 `None` 继续驱动：

  Python

  ```
  graph.update_state(config, {"reviewed_data": "correct_value"})
  graph.invoke(None, config=config)
  ```

### 问：方式A和方式B你推荐哪个？

日常业务开发中，绝大多数场景首选 **方式 A (`state.interrupts`)**。



`state.interrupts` 是 LangGraph 针对 `StateSnapshot` 封装的高层级只读属性，它自动将底层所有待处理任务中的中断进行了扁平化收集。两者适用边界如下：



**优先选方式 A (`state.interrupts`) 的理由**



- **API 最精简**：无需双重嵌套循环遍历 `tasks`，直接 `state.interrupts[0].value` 即可拿到 payload。
- **业务语义聚焦**：前端展示“待审批详情”或 API 接口下发中断表单时，业务层通常只关心“中断带了什么数据”，不关心底层调度任务的元信息。
- **适用 90% 的工作流**：凡是标准的串行图、单分支审批流、单节点挂起场景，方式 A 都是最简洁、可读性最好的写法。

**必须换用方式 B (`state.tasks`) 的场景**



当图结构较为复杂时，方式 A 会丢失**任务维度的上下文**，这时需要深入到 `state.tasks`：



- **并行分支并发中断**：若图中两条并行分支同时调用了 `interrupt()`，`state.interrupts` 只是扁平的列表；而遍历 `state.tasks` 可以通过 `task.name` 明确知道**哪个节点**产生了哪个中断。
- **子图嵌套（Subgraphs）追踪**：通过 `task.path` 可以精准判断中断发生在根图还是某一级深层嵌套的子图中。
- **需要获取 `task.id` 进行精准调度**：当配合高级并发控制或底层审计系统时，`task.id` 是唯一定位该次执行碎片的凭证。

**选型速查表**

| **维度**         | **方式 A (state.interrupts)**        | **方式 B (state.tasks)**                   |
| ---------------- | ------------------------------------ | ------------------------------------------ |
| **层级定位**     | 高层便捷属性（Syntactic Sugar）      | 运行时底层任务元数据                       |
| **代码复杂度**   | 单层访问，代码干净                   | 双层遍历，样板代码较多                     |
| **节点归属信息** | 无法直接对应具体节点名称             | 可读取 `task.name`、`task.path`、`task.id` |
| **推荐场景**     | 串行审批流、Web API 统一返回中断数据 | 并行节点中断、多 Agent 协作、子图嵌套调试  |