关于并行工具调用中各种类型工具和审核过程中各种情况处理的方案与当前公示

想像一个批次的工具调用[ReadA, ReadB, EditA, EditB, Bash, ReadB]

可以分成多个批次：
[ReadA, ReadB]

[EditA, EditB, Bash]

[ReadB]

其中ReadA，ReadB可以并发执行

然后到了EditA, EditB, Bash，需要串行执行，

那么会有如下几个问题

1、已知EditA, EditB, Bash这三个工具都需要审批，那么这些工具的审批是一次性全部交给用户决定，还是审批通过执行完一个，再进行下一个的审批执行流程

2、已知EditA审批通过并执行完毕，EditB审批后被拒绝执行，那么后续的Bash，ReadB是继续按照规则执行，还是全部拒绝，并人工填补消息？还有EditA的执行是否要回滚？

当前共识：

对于问题1，决定是审批通过执行完一个，再进行下一个的审批执行流程

对于问题2，EditB审批后被拒绝执行，后续的Bash，ReadB也不再执行，为了维护消息列表的正确性，人工填补消息。并且EditA的执行已经发生，不需要回滚

### 问：请提供一份基于 Python 的生产级工具分发器（Dispatcher）实现代码，包含参数预检、异步并发执行与级联熔断逻辑。

```
import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Type
from pydantic import BaseModel, ValidationError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ToolDispatcher")


# =====================================================================
# 1. 领域模型与异常体系
# =====================================================================

class ToolExecutionStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class ToolCall:
    """来自 LLM 的单次工具调用结构体"""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolMessage:
    """对齐 OpenAI / Anthropic 协议标准的返回消息"""
    tool_call_id: str
    name: str
    status: ToolExecutionStatus
    content: str

    def to_openai_format(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "content": self.content,
        }


class AgentToolError(Exception):
    """工具异常基类"""
    def __init__(self, message: str, tool_name: str):
        super().__init__(message)
        self.tool_name = tool_name


class TransientToolError(AgentToolError):
    """系统级可重试异常（网络抖动、限流等）"""
    pass


class ExecutionToolError(AgentToolError):
    """LLM 级可自愈业务异常（命令退出码非 0、文件不存在等）"""
    pass


class FatalToolError(AgentToolError):
    """致命基础设施异常（沙盒崩溃、物理磁盘损坏等）"""
    pass


@dataclass
class ToolDefinition:
    """工具元信息定义"""
    name: str
    schema: Type[BaseModel]
    handler: Callable[..., Awaitable[str]]
    is_read_only: bool = False
    requires_approval: bool = True
    max_retries: int = 2


# =====================================================================
# 2. 生产级工具调度器 (ToolDispatcher)
# =====================================================================

class ToolDispatcher:
    def __init__(self):
        self._registry: dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition):
        self._registry[tool_def.name] = tool_def

    async def dispatch(
        self,
        tool_calls: list[ToolCall],
        approval_hook: Callable[[ToolCall], Awaitable[bool]]
    ) -> list[ToolMessage]:
        """
        分发执行入口：
        阶段 1：全量静态 Schema 预检（Pre-flight）
        阶段 2：读写分组并发/串行调度 + HITL 审批 + 级联熔断
        阶段 3：上下文对齐与合成消息生成
        """
        if not tool_calls:
            return []

        # -------------------------------------------------------------
        # 阶段 1：全批次静态预检 (Pre-flight Validation)
        # -------------------------------------------------------------
        validated_args: dict[str, BaseModel] = {}
        for idx, call in enumerate(tool_calls):
            tool_def = self._registry.get(call.name)
            if not tool_def:
                logger.warning(f"预检拦截: 未注册的工具 [{call.name}]")
                return self._abort_remaining(
                    tool_calls=tool_calls,
                    from_index=idx,
                    failed_status=ToolExecutionStatus.ERROR,
                    failed_msg=f"Error: Tool [{call.name}] not found in system registry.",
                    cancel_reason=f"aborted because preceding tool [{call.name}] was not found."
                )

            try:
                # Pydantic 严格校验
                validated_model = tool_def.schema.model_validate(call.arguments)
                validated_args[call.id] = validated_model
            except ValidationError as e:
                logger.warning(f"预检拦截: 工具 [{call.name}] 参数格式不合法: {e.errors()}")
                error_details = json.dumps(e.errors(include_url=False), ensure_ascii=False)
                return self._abort_remaining(
                    tool_calls=tool_calls,
                    from_index=idx,
                    failed_status=ToolExecutionStatus.ERROR,
                    failed_msg=f"Error: Parameter validation failed for [{call.name}]: {error_details}",
                    cancel_reason=f"aborted because preceding tool [{call.name}] failed parameter validation."
                )

        # -------------------------------------------------------------
        # 阶段 2：执行调度与级联熔断机制
        # -------------------------------------------------------------
        results: list[ToolMessage] = []
        aborted = False
        abort_trigger_tool = ""
        abort_cause_msg = ""

        # 将工具列表按“连续读”和“串行写”切片
        batches = self._partition_batches(tool_calls)

        for batch in batches:
            if aborted:
                for call in batch:
                    results.append(ToolMessage(
                        tool_call_id=call.id,
                        name=call.name,
                        status=ToolExecutionStatus.CANCELLED,
                        content=f"Cancelled: Skipped due to previous failure/rejection in [{abort_trigger_tool}]."
                    ))
                continue

            # 分支 A：并发只读批次 (Concurrent Reads)
            if batch[0] and self._registry[batch[0].name].is_read_only:
                tasks = [
                    self._execute_single_tool(call, validated_args[call.id])
                    for call in batch
                ]
                batch_results = await asyncio.gather(*tasks)
                results.extend(batch_results)

                # 检查只读批次是否有异常（只读异常会阻断后续串行写）
                for res in batch_results:
                    if res.status == ToolExecutionStatus.ERROR:
                        aborted = True
                        abort_trigger_tool = res.name
                        abort_cause_msg = res.content
                        break

            # 分支 B：串行副作用批次 (Sequential Writes with HITL)
            else:
                for call in batch:
                    tool_def = self._registry[call.name]

                    # 1. 人机协同审批 Gate
                    if tool_def.requires_approval:
                        logger.info(f"触发人工审批: [{call.name}], 参数: {call.arguments}")
                        approved = await approval_hook(call)
                        if not approved:
                            logger.info(f"审批拒绝: 用户驳回了 [{call.name}]")
                            results.append(ToolMessage(
                                tool_call_id=call.id,
                                name=call.name,
                                status=ToolExecutionStatus.REJECTED,
                                content=f"Error: Tool execution was rejected by the user."
                            ))
                            aborted = True
                            abort_trigger_tool = call.name
                            break

                    # 2. 真实执行与单工具异常捕获
                    res = await self._execute_single_tool(call, validated_args[call.id])
                    results.append(res)

                    if res.status == ToolExecutionStatus.ERROR:
                        aborted = True
                        abort_trigger_tool = call.name
                        break

        return results

    # -------------------------------------------------------------
    # 3. 单工具执行防线与瞬态重试 (Resilience)
    # -------------------------------------------------------------
    async def _execute_single_tool(self, call: ToolCall, model_args: BaseModel) -> ToolMessage:
        tool_def = self._registry[call.name]
        attempts = 0
        raw_kwargs = model_args.model_dump()

        while attempts <= tool_def.max_retries:
            attempts += 1
            try:
                # 真实调用工具 handler
                output = await tool_def.handler(**raw_kwargs)
                return ToolMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    status=ToolExecutionStatus.SUCCESS,
                    content=output
                )
            except TransientToolError as e:
                # 瞬态错误：系统静默重试（指数退避）
                if attempts <= tool_def.max_retries:
                    wait_time = 0.5 * (2 ** (attempts - 1))
                    logger.warning(f"[{call.name}] 触发瞬态异常, 等待 {wait_time}s 进行第 {attempts} 次重试...")
                    await asyncio.sleep(wait_time)
                else:
                    return ToolMessage(
                        tool_call_id=call.id,
                        name=call.name,
                        status=ToolExecutionStatus.ERROR,
                        content=f"Error: Tool failed due to persistent network/timeout: {str(e)}"
                    )
            except ExecutionToolError as e:
                # 业务/运行时错误：不重试，直接抛回给 LLM 自愈
                return ToolMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    status=ToolExecutionStatus.ERROR,
                    content=f"Error: Tool execution failed: {str(e)}"
                )
            except FatalToolError as e:
                # 致命错误：阻断 Agent 事件循环并上报
                logger.critical(f"捕获致命基础设施故障: {e}")
                raise
            except Exception as e:
                # 未知未捕获异常兜底（Catch-All）
                logger.error(f"工具执行崩溃未处理异常: {e}", exc_info=True)
                return ToolMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    status=ToolExecutionStatus.ERROR,
                    content=f"Error: Unexpected runtime crash ({type(e).__name__}): {str(e)}"
                )

    # -------------------------------------------------------------
    # 4. 辅助函数：批次切分与预检熔断合成
    # -------------------------------------------------------------
    def _partition_batches(self, tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
        """将 [ReadA, ReadB, EditA, EditB, ReadC] 切分为 [[ReadA, ReadB], [EditA], [EditB], [ReadC]]"""
        batches: list[list[ToolCall]] = []
        current_read_group: list[ToolCall] = []

        for call in tool_calls:
            tool_def = self._registry[call.name]
            if tool_def.is_read_only:
                current_read_group.append(call)
            else:
                if current_read_group:
                    batches.append(current_read_group)
                    current_read_group = []
                batches.append([call])  # 写工具单独作为一个串行批次

        if current_read_group:
            batches.append(current_read_group)

        return batches

    def _abort_remaining(
        self,
        tool_calls: list[ToolCall],
        from_index: int,
        failed_status: ToolExecutionStatus,
        failed_msg: str,
        cancel_reason: str
    ) -> list[ToolMessage]:
        """预检失败或致命中断时，构造符合 OpenAI 协议的全量回填消息"""
        messages: list[ToolMessage] = []
        for i, call in enumerate(tool_calls):
            if i < from_index:
                messages.append(ToolMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    status=ToolExecutionStatus.CANCELLED,
                    content=f"Cancelled: Batch pre-flight check aborted execution."
                ))
            elif i == from_index:
                messages.append(ToolMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    status=failed_status,
                    content=failed_msg
                ))
            else:
                messages.append(ToolMessage(
                    tool_call_id=call.id,
                    name=call.name,
                    status=ToolExecutionStatus.CANCELLED,
                    content=f"Cancelled: Tool execution was {cancel_reason}"
                ))
        return messages


# =====================================================================
# 5. 场景验证与演示
# =====================================================================

# 1. 定义工具参数 Pydantic Schema
class ReadFileSchema(BaseModel):
    filepath: str

class EditFileSchema(BaseModel):
    filepath: str
    content: str

class BashSchema(BaseModel):
    command: str

# 2. 模拟工具真实实现
async def mock_read_file(filepath: str) -> str:
    await asyncio.sleep(0.1)  # 模拟 IO
    return f"Content of {filepath}: Hello World"

async def mock_edit_file(filepath: str, content: str) -> str:
    await asyncio.sleep(0.1)
    if "forbidden" in filepath:
        raise ExecutionToolError("Permission denied: writing to system root.", "EditFile")
    return f"Success: File {filepath} updated."

async def mock_bash(command: str) -> str:
    await asyncio.sleep(0.1)
    return f"Executed `{command}` successfully. ReturnCode: 0"

async def mock_user_approval_reject_edit_b(call: ToolCall) -> bool:
    """模拟审批 Hook：批准 EditA，拒绝 EditB"""
    if call.arguments.get("filepath") == "/app/b.py":
        return False
    return True


async def main():
    dispatcher = ToolDispatcher()
    dispatcher.register(ToolDefinition("ReadA", ReadFileSchema, mock_read_file, is_read_only=True))
    dispatcher.register(ToolDefinition("ReadB", ReadFileSchema, mock_read_file, is_read_only=True))
    dispatcher.register(ToolDefinition("EditA", EditFileSchema, mock_edit_file, is_read_only=False, requires_approval=True))
    dispatcher.register(ToolDefinition("EditB", EditFileSchema, mock_edit_file, is_read_only=False, requires_approval=True))
    dispatcher.register(ToolDefinition("Bash", BashSchema, mock_bash, is_read_only=False, requires_approval=True))

    print("\n" + "=" * 60)
    print("场景 1: EditB 被用户拒绝，触发级联熔断，协议严格对齐")
    print("=" * 60)
    batch_1 = [
        ToolCall("call_1", "ReadA", {"filepath": "/app/a.py"}),
        ToolCall("call_2", "ReadB", {"filepath": "/app/b.py"}),
        ToolCall("call_3", "EditA", {"filepath": "/app/a.py", "content": "updated"}),
        ToolCall("call_4", "EditB", {"filepath": "/app/b.py", "content": "updated"}),  # 被拒
        ToolCall("call_5", "Bash", {"command": "pytest tests/"}),
        ToolCall("call_6", "ReadB", {"filepath": "/app/b.py"}),
    ]
    results_1 = await dispatcher.dispatch(batch_1, mock_user_approval_reject_edit_b)
    for res in results_1:
        print(f"[{res.status.value.upper():<9}] ID: {res.tool_call_id} | Name: {res.name:<5} | Msg: {res.content}")

    print("\n" + "=" * 60)
    print("场景 2: 静态预检拦截（EditA 缺少必填参数 content），全批次未执行直接熔断")
    print("=" * 60)
    batch_2 = [
        ToolCall("call_1", "ReadA", {"filepath": "/app/a.py"}),
        ToolCall("call_3", "EditA", {"filepath": "/app/a.py"}),  # 缺少必填项 'content'
        ToolCall("call_5", "Bash", {"command": "pytest"}),
    ]
    results_2 = await dispatcher.dispatch(batch_2, lambda call: asyncio.sleep(0, result=True))
    for res in results_2:
        print(f"[{res.status.value.upper():<9}] ID: {res.tool_call_id} | Name: {res.name:<5} | Msg: {res.content}")


if __name__ == "__main__":
    asyncio.run(main())
```

