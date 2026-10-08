```python
# 该函数是为了修复因worker因中断或断电等中途崩溃事件而导致的错误消息队列
def sanitize_and_prepare_messages(
        history_messages: list[BaseMessage],
        new_user_content: str
) -> list[BaseMessage]:
    """
        1、可能在用户输入消息之后，call_model节点还没有返回最新响应就崩溃了
        2、可能call_model返回了最新响应，但是工具调用还没执行就崩溃了，或者考虑到有多个工具节点
        只执行了部分就结束了
        针对第一个问题，修复方案比较简单，添加上人工设置的AIMessage即可
        判断是否是第一个问题，只需要看消息列表中的最后一条消息是否是HumanMessage即可
        针对第二个问题，先补齐缺失的ToolMessage，再补上AIMessage
        判断是否是第二个问题，
        1、消息列表的最后一条消息是带有toolcall的AIMessage
        2、消息列表的最后一条消息是ToolMessage
        已知两个问题出现时都需要补上AIMessage，因此先修复ToolMessage缺失的问题，再补上AIMessage缺失的问题
    """
    # 在没有历史消息的情况下用户发送第一条请求就失败了
    if not history_messages:
        return [HumanMessage(content=new_user_content)]
    repaired_patch: list[BaseMessage] = []
    existing_tool_call_ids = set()
    last_ai_message = None
    for message in reversed(history_messages):
        # 符合第一种情况，还没获取模型响应便报错结束
        if isinstance(message, HumanMessage):
            break
        # 可能是遍历到AIMessage，也可能最后一条消息是AIMessage
        elif isinstance(message, AIMessage):
            last_ai_message = message
            break
        # 保存已经执行了的工具的id，这样就不需要构造了
        elif isinstance(message, ToolMessage):
            existing_tool_call_ids.add(message.tool_call_id)
    # 只要last_ai_message不为空，就肯定是第二种情况，而不是第一种
    if last_ai_message is not None:
        # AIMessage有工具调用，增加一层判定是为了防止call_model节点在条件边判断响应没工具调用时是连接的不是END
        if last_ai_message.tool_calls:
            missing_tool_calls = [
                tool_call
                for tool_call in last_ai_message.tool_calls
                if tool_call["id"] not in existing_tool_call_ids
            ]
            # 补上缺失的ToolMessage
            for tc in missing_tool_calls:
                repaired_patch.append(
                    ToolMessage(
                        tool_call_id=tc["id"],
                        content="[System Note]: Tool execution aborted due to unexpected worker interruption.",
                        status="error"
                    )
                )
            # 再补上AIMessage
            repaired_patch.append(
                AIMessage(
                    content="[前序任务已中断，已切换至您的最新输入。]"
                )
            )
        # 一条纯文本AIMessage，不需要补上ToolMessage
        else:
            pass
    # last_ai_message为空，说明是第一种情况
    else:
        # 第一种情况只需要补上AIMessage
        repaired_patch.append(
            AIMessage(
                content="[前序响应中断，已切换至您的最新输入。]"
            )
        )
    # 用户新的输入，必须加上
    repaired_patch.append(HumanMessage(content=new_user_content))
    return repaired_patch

```

### 问:新的逻辑我改成这样了

这次的修改**非常精准且干练**，直接命中了核心问题。



你通过 `if last_ai_message.tool_calls:` 这一层判断，巧妙地区分了“包含工具调用的中间态”**与**“纯文本回答的完结态”，用极小的代码改动彻底消除了连续 `AIMessage` 引发 400 的隐患。



### 全场景推演验证

我们可以把所有可能的崩溃断点代入你当前的逻辑走一遍，可以看到所有路径都已经**完全闭环且合规**：



| **崩溃发生的时机**                     | **历史消息尾部**   | **last_ai_message** | **tool_calls 状态**               | **补丁列表 (repaired_patch)** | **最终生效的消息拓扑**     | **协议合规性**              |
| -------------------------------------- | ------------------ | ------------------- | --------------------------------- | ----------------------------- | -------------------------- | --------------------------- |
| **场景 1：模型未输出就崩溃**           | `Human`            | `None`              | -                                 | `[AI, Human]`                 | `User -> AI(补丁) -> User` | ✅ 完美交替                  |
| **场景 2：有工具调用，执行中崩溃**     | `AI(tc)` 或 `Tool` | 找到该 `AI`         | **非空** (`True`)                 | `[缺失Tool..., AI, Human]`    | `Tool -> AI(补丁) -> User` | ✅ 工具闭合，角色交替        |
| **场景 3：工具全跑完，总结前崩溃**     | `Tool`             | 找到该 `AI`         | **非空** (`True`)  *(缺失数为 0)* | `[AI, Human]`                 | `Tool -> AI(补丁) -> User` | ✅ 避免 Tool 直连 User       |
| **场景 4：纯文本回复后在下游节点崩溃** | `AI(纯文本)`       | 找到该 `AI`         | **空/None** (`False`)             | `[Human]` *(进入 else: pass)* | `AI(原回答) -> User`       | ✅ **消除重复 AI，自然交替** |

### 生产级防御细节建议

代码在逻辑上已经无懈可击，仅提供一个细微的**工程防御性写法**：



在某些特定的 LangChain 版本或自定义序列化（例如从 Redis/Postgres 反序列化）时，如果模型没有触发工具调用，`last_ai_message.tool_calls` 偶尔会是 `None`，或者极端情况下属性不存在：



Python

```
# 建议：使用 getattr 提高防御性
tool_calls = getattr(last_ai_message, "tool_calls", None) or []
if tool_calls:
    missing_tool_calls = [
        tc for tc in tool_calls
        if tc["id"] not in existing_tool_call_ids
    ]
    # ...
```

这样可以完全避免 `AttributeError` 或 `TypeError: 'NoneType' object is not iterable` 的边界异常。



这段代码现在已经是一套兼顾**简洁性、容错性与多供应商协议合规性**的优秀工业级实现了。