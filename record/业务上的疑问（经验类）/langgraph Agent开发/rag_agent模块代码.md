```
# app/tasks/agent_worker.py

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)
from langgraph.types import Command
from sqlalchemy import update
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from app.core.lock import RedisDistributedLock
from app.models.agent import (
    Interrupt,
    InterruptStatus,
    Message,
    MessageRole,
    MessageStatus,
    Run,
    RunStatus,
    Thread,
)
from app.core.redis import redis_client
from app.graph.agent_graph import get_graph
from app.services.rag import get_rag_service


logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_CHARS = 10_000
DB_THROTTLE_FLUSH_INTERVAL = 2.5
CANCEL_CHECK_INTERVAL = 10
LOCK_TTL = 30


# ============================================================
# Utils
# ============================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def publish_event(
    run_id: uuid.UUID,
    event_type: str,
    data: Optional[dict[str, Any]] = None,
) -> None:
    """
    写入 Redis Stream。
    """
    fields = {
        "type": event_type,
        "run_id": str(run_id),
    }

    if data is not None:
        fields["data"] = json.dumps(
            data,
            ensure_ascii=False,
        )

    await redis_client.xadd(
        f"agent:stream:{run_id}",
        fields,
    )


def _get_cancelled_message_status() -> str:
    """
    安全获取 MessageStatus 的取消状态枚举（若未定义则回退为 FAILED）。
    """
    return getattr(MessageStatus, "CANCELLED", getattr(MessageStatus, "FAILED", MessageStatus.PENDING)).value


# ============================================================
# Execution Accumulator
# ============================================================

class RunExecutionAccumulator:
    """
    一个 Run 执行期间的中间结果累积器。

    主要保存：
        parts
        tool_refs
        citation_map

    同时用于：
        1. Redis Stream 增量事件
        2. DB 节流持久化
        3. interrupt 后恢复
    """

    def __init__(
        self,
        initial_parts: Optional[list[dict[str, Any]]] = None,
    ):
        self.parts: list[dict[str, Any]] = (
            list(initial_parts)
            if initial_parts
            else []
        )

        self.tool_refs: dict[str, dict[str, Any]] = {}
        self.citation_map: dict[str, dict[str, Any]] = {}
        self._text_buffer: list[str] = []
        self._thought_buffer: list[str] = []

        # 从历史 parts 逆向恢复 tool_refs，避免 resume 时状态被抹除覆盖
        if initial_parts:
            self._restore_tool_refs_from_parts(initial_parts)

    def _restore_tool_refs_from_parts(self, parts: list[dict[str, Any]]) -> None:
        for part in parts:
            p_type = part.get("type")
            tid = part.get("tool_id")
            if not tid:
                continue

            if p_type == "tool_call":
                self.tool_refs[tid] = {
                    "id": tid,
                    "name": part.get("name"),
                    "args": part.get("args", {}),
                    "status": part.get("status", "running"),
                }
            elif p_type == "tool_result":
                status = part.get("status", "completed")
                content = part.get("content")
                if tid in self.tool_refs:
                    self.tool_refs[tid]["status"] = status
                    self.tool_refs[tid]["output"] = content
                else:
                    self.tool_refs[tid] = {
                        "id": tid,
                        "name": None,
                        "args": {},
                        "status": status,
                        "output": content,
                    }

    # --------------------------------------------------------
    # Thought
    # --------------------------------------------------------

    def add_thought_delta(
        self,
        delta: str,
    ) -> None:
        if not delta:
            return

        self._thought_buffer.append(delta)

        if (
            self.parts
            and self.parts[-1].get("type") == "thought"
        ):
            self.parts[-1]["content"] = (
                self.parts[-1].get("content", "")
                + delta
            )
        else:
            self.parts.append(
                {
                    "type": "thought",
                    "content": delta,
                }
            )

    # --------------------------------------------------------
    # Text
    # --------------------------------------------------------

    def add_text_delta(
        self,
        delta: str,
    ) -> None:
        if not delta:
            return

        self._text_buffer.append(delta)

        if (
            self.parts
            and self.parts[-1].get("type") == "text"
        ):
            self.parts[-1]["content"] = (
                self.parts[-1].get("content", "")
                + delta
            )
        else:
            self.parts.append(
                {
                    "type": "text",
                    "content": delta,
                }
            )

    # --------------------------------------------------------
    # Tool
    # --------------------------------------------------------

    def register_tool_call(
        self,
        tool_call: dict[str, Any],
    ) -> bool:
        """
        幂等注册工具调用。
        如果 tool_id 已存在（已注册或已完成），直接跳过并返回 False，
        防止破坏已有状态、在 parts 中产生重复项或重复向 Stream 发送事件。
        """
        tool_id = tool_call.get("id")
        if not tool_id:
            tool_id = str(uuid.uuid4())
            tool_call["id"] = tool_id

        if tool_id in self.tool_refs:
            return False

        tool_ref = {
            "id": tool_id,
            "name": tool_call.get("name"),
            "args": tool_call.get("args", {}),
            "status": "running",
        }

        self.tool_refs[tool_id] = tool_ref

        self.parts.append(
            {
                "type": "tool_call",
                "tool_id": tool_id,
                "name": tool_ref["name"],
                "args": tool_ref["args"],
                "status": "running",
            }
        )
        return True

    def complete_tool_call(
        self,
        tool_message: ToolMessage,
    ) -> tuple[bool, str]:
        """
        幂等完成工具调用。
        返回元组: (is_newly_completed, status)
        如果该工具已处于终态，跳过追加并返回 (False, current_status)。
        """
        tool_id = getattr(
            tool_message,
            "tool_call_id",
            None,
        )

        if not tool_id:
            return False, "completed"

        # 幂等拦截：已记录最终结果则不重复追加 parts
        if (
            tool_id in self.tool_refs
            and self.tool_refs[tool_id].get("status") in ("completed", "failed")
        ):
            return False, self.tool_refs[tool_id]["status"]

        # 识别 LangChain 原生工具报错标记
        msg_status = getattr(tool_message, "status", None)
        final_status = "failed" if msg_status == "error" else "completed"

        content = tool_message.content
        if isinstance(content, str):
            content_str = content[:MAX_TOOL_OUTPUT_CHARS]
        else:
            content_str = str(content)[:MAX_TOOL_OUTPUT_CHARS]

        # 1. 更新 tool_refs
        if tool_id in self.tool_refs:
            self.tool_refs[tool_id]["status"] = final_status
            self.tool_refs[tool_id]["output"] = content_str
        else:
            self.tool_refs[tool_id] = {
                "id": tool_id,
                "name": None,
                "args": {},
                "status": final_status,
                "output": content_str,
            }

        # 2. 同步回写 parts 中对应的 tool_call 项状态，避免前端渲染卡在 running
        for part in reversed(self.parts):
            if part.get("type") == "tool_call" and part.get("tool_id") == tool_id:
                part["status"] = final_status
                break

        # 3. 追加 tool_result part
        self.parts.append(
            {
                "type": "tool_result",
                "tool_id": tool_id,
                "content": content_str,
                "status": final_status,
            }
        )
        return True, final_status

    def mark_unfinished_tools(self, status: str = "failed") -> None:
        """
        当 Run 中断、取消或异常退出时，将依然挂起为 running 的工具置为终态。
        """
        for ref in self.tool_refs.values():
            if ref.get("status") == "running":
                ref["status"] = status

        for part in self.parts:
            if part.get("type") == "tool_call" and part.get("status") == "running":
                part["status"] = status

    # --------------------------------------------------------
    # Final Properties
    # --------------------------------------------------------

    @property
    def content(self) -> str:
        return "".join(
            part.get("content", "")
            for part in self.parts
            if part.get("type") == "text"
        )

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return list(
            self.tool_refs.values()
        )

    @property
    def citations(self) -> list[dict[str, Any]]:
        return list(
            self.citation_map.values()
        )


# ============================================================
# DB Persistence
# ============================================================

async def flush_to_db(
    run_id: uuid.UUID,
    thread_id: uuid.UUID,
    accumulator: RunExecutionAccumulator,
    status: str = MessageStatus.PENDING.value,
) -> None:
    """
    将当前 assistant execution snapshot 持久化到 DB。
    """
    from app.core.database import get_session

    async with get_session() as db:
        result = await db.exec(
            select(Message).where(
                Message.run_id == run_id,
                Message.role == MessageRole.ASSISTANT.value,
            )
        )

        message = result.first()

        if message is None:
            message = Message(
                thread_id=thread_id,
                run_id=run_id,
                role=MessageRole.ASSISTANT.value,
                content=accumulator.content,
                parts=accumulator.parts,
                tool_calls=accumulator.tool_calls,
                citations=accumulator.citations,
                status=status,
            )
            db.add(message)
        else:
            message.content = accumulator.content
            message.parts = list(accumulator.parts)
            message.tool_calls = list(accumulator.tool_calls)
            message.citations = list(accumulator.citations)
            message.status = status

            flag_modified(message, "parts")
            flag_modified(message, "tool_calls")
            flag_modified(message, "citations")

        await db.commit()


# ============================================================
# Cancel
# ============================================================

async def is_cancel_requested(
    run_id: uuid.UUID,
) -> bool:
    return bool(
        await redis_client.exists(
            f"run:cancel:{run_id}"
        )
    )


async def mark_run_cancelled(
    run_id: uuid.UUID,
) -> None:
    """
    取消指定的 Run，支持 QUEUED、IN_PROGRESS 以及处于挂起态的 REQUIRES_ACTION。
    级联清理该 Run 下未完成的 Interrupt 与挂起的 Message。
    """
    from app.core.database import get_session

    async with get_session() as db:
        # 1. 原子 CAS 更新 Run 状态
        result = await db.exec(
            update(Run)
            .where(
                Run.id == run_id,
                Run.status.in_(
                    [
                        RunStatus.QUEUED.value,
                        RunStatus.IN_PROGRESS.value,
                        RunStatus.REQUIRES_ACTION.value,
                    ]
                ),
            )
            .values(
                status=RunStatus.CANCELLED.value,
                finished_at=utc_now(),
            )
        )

        if result.rowcount == 1:
            # 2. 级联取消挂起的 Interrupt 记录，防止幽灵中断残留
            cancel_enum = getattr(
                InterruptStatus,
                "CANCELLED",
                getattr(InterruptStatus, "DISMISSED", None),
            )
            interrupt_cancel_status = cancel_enum.value if cancel_enum else "cancelled"

            await db.exec(
                update(Interrupt)
                .where(
                    Interrupt.run_id == run_id,
                    Interrupt.status == InterruptStatus.PENDING.value,
                )
                .values(
                    status=interrupt_cancel_status,
                )
            )

            # 3. 将处于 PENDING 的 Assistant 消息同步置为终态
            await db.exec(
                update(Message)
                .where(
                    Message.run_id == run_id,
                    Message.role == MessageRole.ASSISTANT.value,
                    Message.status == MessageStatus.PENDING.value,
                )
                .values(
                    status=_get_cancelled_message_status(),
                )
            )

            await db.commit()

            # 4. 发布取消事件
            await publish_event(
                run_id,
                "cancelled",
            )


# ============================================================
# Main Worker
# ============================================================

async def execute_agent_run(
    run_id: str,
    thread_id: str,
    resume: bool = False,
    resolution: Optional[dict[str, Any]] = None,
):
    """
    TaskIQ Agent Worker。

    核心流程：
        1. Run 锁竞争
        2. 校验 Run / Thread
        3. queued / requires_action -> in_progress (CAS 原子转换)
        4. 加载历史 assistant parts 并反向恢复 tool_refs
        5. 构建 LangGraph config
        6. 执行 graph 流式事件
        7. 处理 interrupt (requires_action)
        8. 完成 / 失败 / 取消
    """
    run_uuid = uuid.UUID(run_id)
    thread_uuid = uuid.UUID(thread_id)

    worker_lock = RedisDistributedLock(
        redis_client,
        key=f"lock:worker:run:{run_id}",
        ttl=LOCK_TTL,
    )

    acquired = await worker_lock.acquire()

    if not acquired:
        logger.warning(
            "Run worker already exists: %s",
            run_id,
        )
        return

    stream_key = f"agent:stream:{run_id}"

    try:
        from app.core.database import get_session

        # ====================================================
        # 1. 查询 Run / Thread
        # ====================================================

        async with get_session() as db:
            run = await db.get(
                Run,
                run_uuid,
            )

            if run is None:
                logger.error(
                    "Run not found: %s",
                    run_id,
                )
                return

            if run.thread_id != thread_uuid:
                logger.error(
                    "Run/thread mismatch: run=%s thread=%s expected=%s",
                    run_id,
                    run.thread_id,
                    thread_id,
                )
                return

            thread = await db.get(
                Thread,
                thread_uuid,
            )

            if thread is None:
                logger.error(
                    "Thread not found: %s",
                    thread_id,
                )
                return

            # =================================================
            # 2. 原子状态转换
            # =================================================

            allowed_statuses = (
                [RunStatus.REQUIRES_ACTION.value]
                if resume
                else [RunStatus.QUEUED.value]
            )

            result = await db.exec(
                update(Run)
                .where(
                    Run.id == run_uuid,
                    Run.thread_id == thread_uuid,
                    Run.status.in_(allowed_statuses),
                )
                .values(
                    status=RunStatus.IN_PROGRESS.value,
                    started_at=(run.started_at or utc_now()),
                )
            )

            if result.rowcount != 1:
                logger.warning(
                    "Run status transition rejected: run=%s resume=%s",
                    run_id,
                    resume,
                )
                return

            await db.commit()

            run_scope = run.scope
            run_scope_id = run.scope_id

            # =================================================
            # 3. Resume 时加载已有 assistant parts
            # =================================================

            initial_parts: list[dict[str, Any]] = []

            if resume:
                assistant_result = await db.exec(
                    select(Message).where(
                        Message.run_id == run_uuid,
                        Message.role == MessageRole.ASSISTANT.value,
                    )
                )

                assistant_message = assistant_result.first()

                if assistant_message is not None and assistant_message.parts:
                    initial_parts = list(assistant_message.parts)

        # ====================================================
        # 4. 初始化 accumulator
        # ====================================================

        accumulator = RunExecutionAccumulator(
            initial_parts=initial_parts,
        )

        await flush_to_db(
            run_id=run_uuid,
            thread_id=thread_uuid,
            accumulator=accumulator,
            status=MessageStatus.PENDING.value,
        )

        await publish_event(
            run_uuid,
            "in_progress",
        )

        # ====================================================
        # 5. 创建 Graph
        # ====================================================

        graph = get_graph()
        rag_service = get_rag_service()

        config = {
            "configurable": {
                "thread_id": str(thread_uuid),
                "scope": run_scope,
                "scope_id": run_scope_id,
                "rag_service": rag_service,
            }
        }

        # ====================================================
        # 6. 构造输入
        # ====================================================

        if resume:
            graph_input = Command(
                resume=resolution or {}
            )
        else:
            from app.core.database import get_session

            async with get_session() as db:
                result = await db.exec(
                    select(Message)
                    .where(
                        Message.run_id == run_uuid,
                        Message.thread_id == thread_uuid,
                        Message.role == MessageRole.USER.value,
                    )
                    .order_by(Message.created_at.desc())
                )

                user_message = result.first()

                if user_message is None:
                    raise RuntimeError(
                        f"User message not found for run {run_id}"
                    )

                graph_input = [
                    HumanMessage(content=user_message.content)
                ]

        # ====================================================
        # 7. LangGraph Streaming
        # ====================================================

        chunk_count = 0
        last_db_flush = asyncio.get_running_loop().time()

        async for chunk in graph.astream(
            graph_input,
            config=config,
            stream_mode=[
                "messages",
                "updates",
            ],
            version="v2",
        ):
            chunk_count += 1

            # ------------------------------------------------
            # Cancel check
            # ------------------------------------------------

            if chunk_count % CANCEL_CHECK_INTERVAL == 0:
                if await is_cancel_requested(run_uuid):
                    accumulator.mark_unfinished_tools(status="cancelled")

                    await flush_to_db(
                        run_id=run_uuid,
                        thread_id=thread_uuid,
                        accumulator=accumulator,
                        status=_get_cancelled_message_status(),
                    )

                    await mark_run_cancelled(run_uuid)
                    return

            # ------------------------------------------------
            # messages stream
            # ------------------------------------------------

            if chunk.get("type") == "messages":
                data = chunk.get("data")
                if not data:
                    continue

                if isinstance(data, tuple):
                    message = data[0]
                else:
                    message = data

                if isinstance(message, AIMessage):
                    # Reasoning 内容 (如 DeepSeek / QwQ)
                    reasoning_content = message.additional_kwargs.get(
                        "reasoning_content"
                    )

                    if reasoning_content:
                        accumulator.add_thought_delta(reasoning_content)
                        await publish_event(
                            run_uuid,
                            "thought_delta",
                            {"content": reasoning_content},
                        )

                    # 正文流式内容
                    content = message.content
                    if isinstance(content, str) and content:
                        accumulator.add_text_delta(content)
                        await publish_event(
                            run_uuid,
                            "text_delta",
                            {"content": content},
                        )

            # ------------------------------------------------
            # updates stream
            # ------------------------------------------------

            elif chunk.get("type") == "updates":
                updates = chunk.get("data")
                if not isinstance(updates, dict):
                    continue

                for _, update_data in updates.items():
                    if not isinstance(update_data, dict):
                        continue

                    messages = update_data.get("messages", [])
                    if not isinstance(messages, list):
                        continue

                    for message in messages:
                        # ------------------------------------
                        # AI tool calls
                        # ------------------------------------
                        if isinstance(message, AIMessage):
                            tool_calls = message.tool_calls
                            for tool_call in tool_calls:
                                if accumulator.register_tool_call(tool_call):
                                    await publish_event(
                                        run_uuid,
                                        "tool_call",
                                        {"tool_call": tool_call},
                                    )

                        # ------------------------------------
                        # Tool result
                        # ------------------------------------
                        elif isinstance(message, ToolMessage):
                            is_new, tool_status = accumulator.complete_tool_call(message)
                            if is_new:
                                tool_call_id = getattr(
                                    message,
                                    "tool_call_id",
                                    None,
                                )
                                await publish_event(
                                    run_uuid,
                                    "tool_result",
                                    {
                                        "tool_call_id": tool_call_id,
                                        "status": tool_status,
                                        "content": str(message.content)[
                                            :MAX_TOOL_OUTPUT_CHARS
                                        ],
                                    },
                                )

            # ------------------------------------------------
            # DB throttle flush
            # ------------------------------------------------

            now = asyncio.get_running_loop().time()
            if now - last_db_flush >= DB_THROTTLE_FLUSH_INTERVAL:
                await flush_to_db(
                    run_id=run_uuid,
                    thread_id=thread_uuid,
                    accumulator=accumulator,
                    status=MessageStatus.PENDING.value,
                )
                last_db_flush = now

        # ====================================================
        # 8. Graph 执行完成后检查 interrupt
        # ====================================================

        state = await graph.aget_state(config)
        active_interrupts = []

        for task in state.tasks:
            interrupts = getattr(task, "interrupts", None)
            if interrupts:
                active_interrupts.extend(interrupts)

        # ====================================================
        # 9. requires_action (HITL 人机协同中断)
        # ====================================================

        if active_interrupts:
            interrupt = active_interrupts[0]
            payload = getattr(interrupt, "value", {})

            if not isinstance(payload, dict):
                payload = {"value": payload}

            from app.core.database import get_session

            async with get_session() as db:
                existing_result = await db.exec(
                    select(Interrupt).where(
                        Interrupt.run_id == run_uuid,
                        Interrupt.status == InterruptStatus.PENDING.value,
                    )
                )
                existing = existing_result.first()

                if existing is None:
                    interrupt_record = Interrupt(
                        run_id=run_uuid,
                        thread_id=thread_uuid,
                        action_type=payload.get("action_type", "clarify"),
                        status=InterruptStatus.PENDING.value,
                        payload=payload,
                    )
                    db.add(interrupt_record)

                await db.exec(
                    update(Run)
                    .where(
                        Run.id == run_uuid,
                        Run.status == RunStatus.IN_PROGRESS.value,
                    )
                    .values(
                        status=RunStatus.REQUIRES_ACTION.value,
                    )
                )
                await db.commit()

            await flush_to_db(
                run_id=run_uuid,
                thread_id=thread_uuid,
                accumulator=accumulator,
                status=MessageStatus.PENDING.value,
            )

            await publish_event(
                run_uuid,
                "requires_action",
                {"payload": payload},
            )
            return

        # ====================================================
        # 10. Completed
        # ====================================================

        await flush_to_db(
            run_id=run_uuid,
            thread_id=thread_uuid,
            accumulator=accumulator,
            status=MessageStatus.SUCCESS.value,
        )

        from app.core.database import get_session

        async with get_session() as db:
            await db.exec(
                update(Run)
                .where(
                    Run.id == run_uuid,
                    Run.status == RunStatus.IN_PROGRESS.value,
                )
                .values(
                    status=RunStatus.COMPLETED.value,
                    finished_at=utc_now(),
                )
            )
            await db.commit()

        await publish_event(
            run_uuid,
            "completed",
        )

    except Exception as exc:
        logger.exception("Agent Run failed: %s", run_id)

        try:
            if "accumulator" in locals():
                accumulator.mark_unfinished_tools(status="failed")
                await flush_to_db(
                    run_id=run_uuid,
                    thread_id=thread_uuid,
                    accumulator=accumulator,
                    status=MessageStatus.FAILED.value,
                )
        except Exception:
            logger.exception("Failed to flush failed Run: %s", run_id)

        try:
            from app.core.database import get_session

            async with get_session() as db:
                await db.exec(
                    update(Run)
                    .where(
                        Run.id == run_uuid,
                        Run.status.in_(
                            [
                                RunStatus.QUEUED.value,
                                RunStatus.IN_PROGRESS.value,
                            ]
                        ),
                    )
                    .values(
                        status=RunStatus.FAILED.value,
                        error_code="AGENT_EXECUTION_FAILED",
                        error_message=str(exc),
                        finished_at=utc_now(),
                    )
                )
                await db.commit()

            await publish_event(
                run_uuid,
                "failed",
                {"error": str(exc)},
            )
        except Exception:
            logger.exception("Failed to mark Run failed: %s", run_id)

        raise

    finally:
        # Redis Stream 设置 24h 兜底过期
        try:
            await redis_client.expire(stream_key, 86400)
        except Exception:
            logger.warning("Failed to expire stream: %s", stream_key)

        # 释放分布式锁
        await worker_lock.release()
这段代码怎么样
```



### 问：这段代码怎么样？

整体架构设计非常成熟，具备工业级 Agent 系统的工程水准。你采用的 **FastAPI + SQLModel (PostgreSQL JSONB) + TaskIQ + Redis Stream + LangGraph (HITL)** 方案，彻底解耦了长耗时 LLM 任务与 HTTP 请求生命周期，且具备断线重连、时序快照追加与中断恢复能力。



以下从架构亮点、关键缺陷（含并发/锁隐患）以及优化建议三个维度进行深度评审：



### 一、 核心架构亮点

1. **时序块（Parts）高保真设计**：

   在 `messages.parts` 中统一记录 `thought`、`text`、`tool_call` 和 `human_action`，配合 `RunExecutionAccumulator` 与 `flag_modified`，解决了复杂 Agent 中多模态/推理流与工具链交织的时序丢失问题，前端刷新后能完整还原当时的执行流。

2. **状态与流广播解耦**：

   Worker 负责推进图状态并写入 Redis Stream；API 层的 SSE 仅做流式分发并支持 `Last-Event-ID` 回放，避免了长连接直接挂在 Worker 进程导致的资源耗尽。

3. **严谨的数据库约束**：

   `uq_pending_interrupt_per_run` 部分唯一索引（Partial Index: `status = 'pending'`）设计极佳，在 DB 层面物理阻断了同一个 Run 产生多个悬挂中断的问题。

4. **两级并发防护**：

   API 层对 `Thread` 加锁防串包，Worker 层对 `Run` 加锁防重复调度，配合看门狗机制，并发控制逻辑完整。

### 二、 必须修复的 Bug 与并发风险

#### 1. Worker 在 Resume 场景下的竞态判断漏洞

在 `app/tasks/agent_worker.py` 中：



Python

```
valid_statuses = ["queued", "requires_action"] if resume else ["queued"]
stmt = (
    update(Run)
    .where(Run.id == run_id, Run.status.in_(valid_statuses))
    .values(status="in_progress", started_at=utc_now())
)
result = await db.exec(stmt)
await db.commit()
if result.rowcount != 1 and not resume:  # <--- 隐患在此
    return
```

- **问题**：如果 `resume=True`，当两个重复的 Resume 任务并发投递到 TaskIQ，第二个 Worker 执行该 SQL 时 `result.rowcount` 为 0（因为状态已被第一个 Worker 翻为 `in_progress`）。但由于 `not resume` 为 `False`，该条件判定为 `False`，**第二个 Worker 不会退出，依然会继续向下执行 LangGraph**。
- **修复方案**：无论是否为 Resume，状态更新失败均说明状态已被抢占或前置条件失效，必须直接返回：

Python

```
if result.rowcount != 1:
    logger.warning("Run 状态已被流转或抢占，退出执行: run_id=%s, resume=%s", run_id, resume)
    return
```

#### 2. 取消信号在排队状态（Queued）下的检测盲区

在 `app/api/routes/agent.py` 的 `/cancel` 中，仅执行了：



Python

```
await redis_client.set(f"run:cancel:{run_id}", "1", ex=3600)
```

而 Worker 中仅在 `chunk_step % 10 == 0` 时才检查取消标志。



- **问题**：如果用户在任务排队（TaskIQ Queue）时点击取消，Worker 拉到任务后会直接将 `Run` 置为 `in_progress` 并启动 `graph.astream`，直到跑了 10 个 chunk 才感知取消；若模型正在进行耗时较长的第一次工具调用或首包等待，任务会被无意义地执行。
- **修复方案**：在 Worker 启动图流式执行前，立刻做一次取消前置校验：

Python

```
if await redis_client.exists(f"run:cancel:{run_id}"):
    await handle_cancel(run_id)
    return
```

#### 3. 看门狗静默退出后的“脑裂（Split-Brain）”风险

在 `app/core/lock.py` 中：



Python

```
if consecutive_failures >= max_retries:
    logger.error("看门狗重试超限，自动退出: key=%s", self.key)
    break
```

- **问题**：如果 Redis 发生瞬时网络闪烁导致续期失败（或 key 被外力清掉），看门狗退出循环。此时锁将在原 TTL 过期后释放，另一个 Worker 或 API 可能重新拿到该锁，但原 Worker 仍然在继续写入数据库和执行图操作，破坏互斥语义。
- **建议方案**：给 `RedisDistributedLock` 暴露一个 `is_lost` 标志或 `asyncio.Event`，当看门狗异常退出时置位，执行引擎可以在每个 step 检测该标志并主动抛出异常中止任务。

#### 4. Redis Stream `maxlen=2000` 导致思考模型 Token 丢弃

在 DeepSeek-R1 或 o1 类长思考模型下，单次请求可能吐出 3000~8000+ 个思考 token（每个 token 触发一次 `publish_event`）。



- **问题**：`maxlen=2000` 采用近似截断时，前端若中途发生断网并使用 `Last-Event-ID="0-0"` 重新拉取流，最早的 `thought_delta` 甚至 `tool_start` 事件已被丢弃。
- **建议**：
  - 将 `maxlen` 上调至 `10000`。
  - 或者，断线重连时前端优先向 `/threads/{thread_id}/messages` 拉取已持久化的完整 parts，SSE 仅用于追加最新流。

### 三、 架构与细节优化建议

#### 1. 锁粒度冗余（空锁）

在 `create_chat_run` 中：



Python

```
target_thread_id = req.thread_id or uuid.uuid4()
async with RedisDistributedLock(redis_client, f"lock:thread:{target_thread_id}", ttl=15):
```

- 当 `req.thread_id` 为 `None` 时，`target_thread_id` 是全新生成的 UUID，去 Redis 里对它加锁没有任何并发冲突风险，属于无效的 Redis RTT 消耗。
- **优化**：仅在 `req.thread_id` 存在时才进分布式锁上下文。

#### 2. SSE 重播时 `requires_action` 的竞态拦截

在 `stream_agent_events` 中：



Python

```
if status_val == "requires_action":
    async with async_session_maker() as verify_db:
        curr_run = await verify_db.get(Run, run_id)
        if curr_run and curr_run.status == "requires_action":
            return
```

- **逻辑评价**：这处设计考虑到了“历史回放”与“已恢复”的时序差异，做得很细致。
- **潜在风险**：如果客户端建立连接很慢，用户点击 Resume 时，DB 中的 `Run.status` 刚被置为 `queued`，此时 `curr_run.status == "queued"`，SSE 会继续监听而不是退出。这是正确的行为（继续等待恢复后的流）。但前端必须确保在中断解决后，不盲目关闭现存 SSE 连接，而是继续消费后续的 `human_action` 与恢复输出。

#### 3. 工具调用的超时熔断

在 `tasks/agent_worker.py` 的执行循环中，如果某个 Tool（例如网络爬虫或外部系统集成）卡死且本身未设超时，Worker 的 `async for chunk in graph.astream(...)` 会一直阻塞，导致看门狗可能虽然活着（因为事件循环仍有空隙），但任务永久停滞。建议对单步调用施加整体超时约束或在 Tool 节点内强制封装 `asyncio.wait_for`。



#### 4. 幂等结果缓存数据陈旧

在 `create_chat_run` 命中幂等键时：



Python

```
cached = await redis_client.get(f"idempotency:run:{req.idempotency_key}")
if cached:
    return json.loads(cached)
```

- 缓存里存的是初始响应（`status: queued`）。如果客户端在任务完成后由于超时重试相同请求，拿到的依然是 `queued`。虽然带了 `stream_url`，但更标准的做法是命中缓存后，以缓存的 `run_id` 为准去查一次 DB 获取真实的最新状态返回。

### 总结

代码结构规整，命名规范，异常处理边界（事务 rollback、SSE ping、flag_modified 脏更新）把握得非常精准。修复上述 **Worker Resume 并发更新判定** 与 **任务排队取消** 的边界条件后，这套系统完全足以支撑高可用、支持人机协同交互的生产级 Agent 任务中枢。

```
class RunExecutionAccumulator:
    """
    一个 Run 执行期间的中间结果累积器。
    精简后仅保留核心的 parts 与 tool_refs 状态管理。
    """

    def __init__(
        self,
        initial_parts: Optional[list[dict[str, Any]]] = None,
    ):
        self.parts: list[dict[str, Any]] = (
            list(initial_parts) if initial_parts else []
        )
        self.tool_refs: dict[str, dict[str, Any]] = {}

        # 从历史 parts 逆向恢复 tool_refs，避免 resume 时状态被抹除覆盖
        if initial_parts:
            self._restore_tool_refs_from_parts(initial_parts)

    def _restore_tool_refs_from_parts(self, parts: list[dict[str, Any]]) -> None:
        for part in parts:
            p_type = part.get("type")
            tid = part.get("tool_id")
            if not tid:
                continue

            if p_type == "tool_call":
                self.tool_refs[tid] = {
                    "id": tid,
                    "name": part.get("name"),
                    "args": part.get("args", {}),
                    "status": part.get("status", "running"),
                }
            elif p_type == "tool_result":
                status = part.get("status", "completed")
                content = part.get("content")
                if tid in self.tool_refs:
                    self.tool_refs[tid]["status"] = status
                    self.tool_refs[tid]["output"] = content
                else:
                    self.tool_refs[tid] = {
                        "id": tid,
                        "name": None,
                        "args": {},
                        "status": status,
                        "output": content,
                    }

    def add_thought_delta(self, delta: str) -> None:
        if not delta:
            return

        if self.parts and self.parts[-1].get("type") == "thought":
            self.parts[-1]["content"] = (
                self.parts[-1].get("content", "") + delta
            )
        else:
            self.parts.append({"type": "thought", "content": delta})

    def add_text_delta(self, delta: str) -> None:
        if not delta:
            return

        if self.parts and self.parts[-1].get("type") == "text":
            self.parts[-1]["content"] = (
                self.parts[-1].get("content", "") + delta
            )
        else:
            self.parts.append({"type": "text", "content": delta})

    def register_tool_call(self, tool_call: dict[str, Any]) -> bool:
        tool_id = tool_call.get("id")
        if not tool_id:
            logger.warning("Tool call missing id from upstream: %s", tool_call)
            return False

        if tool_id in self.tool_refs:
            return False

        tool_ref = {
            "id": tool_id,
            "name": tool_call.get("name"),
            "args": tool_call.get("args", {}),
            "status": "running",
        }

        self.tool_refs[tool_id] = tool_ref
        self.parts.append(
            {
                "type": "tool_call",
                "tool_id": tool_id,
                "name": tool_ref["name"],
                "args": tool_ref["args"],
                "status": "running",
            }
        )
        return True

    def complete_tool_call(
        self, tool_message: ToolMessage
    ) -> tuple[bool, str]:
        tool_id = getattr(tool_message, "tool_call_id", None)
        if not tool_id:
            return False, "completed"

        if (
            tool_id in self.tool_refs
            and self.tool_refs[tool_id].get("status") in ("completed", "failed")
        ):
            return False, self.tool_refs[tool_id]["status"]

        msg_status = getattr(tool_message, "status", None)
        final_status = "failed" if msg_status == "error" else "completed"

        content = tool_message.content
        content_str = (
            content[:MAX_TOOL_OUTPUT_CHARS]
            if isinstance(content, str)
            else str(content)[:MAX_TOOL_OUTPUT_CHARS]
        )

        if tool_id in self.tool_refs:
            self.tool_refs[tool_id]["status"] = final_status
            self.tool_refs[tool_id]["output"] = content_str
        else:
            self.tool_refs[tool_id] = {
                "id": tool_id,
                "name": None,
                "args": {},
                "status": final_status,
                "output": content_str,
            }

        for part in reversed(self.parts):
            if part.get("type") == "tool_call" and part.get("tool_id") == tool_id:
                part["status"] = final_status
                break

        self.parts.append(
            {
                "type": "tool_result",
                "tool_id": tool_id,
                "content": content_str,
                "status": final_status,
            }
        )
        return True, final_status

    def mark_unfinished_tools(self, status: str = "failed") -> None:
        for ref in self.tool_refs.values():
            if ref.get("status") == "running":
                ref["status"] = status

        for part in self.parts:
            if part.get("type") == "tool_call" and part.get("status") == "running":
                part["status"] = status

    @property
    def content(self) -> str:
        return "".join(
            part.get("content", "")
            for part in self.parts
            if part.get("type") == "text"
        )

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return list(self.tool_refs.values())
```







````
### app/models/upload.py

```python
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, Index, Text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MergeStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


class OutboxStatus(str, Enum):
    INIT = "init"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"


class FileRecord(SQLModel, table=True):
    __tablename__ = "file_record"

    id: Optional[int] = Field(default=None, primary_key=True)
    file_ext: Optional[str] = Field(default=None, description="文件扩展名")
    mime_type: Optional[str] = Field(default=None, description="文件 MIME 类型")
    storage_type: str = Field(description="存储类型：local / minio")
    storage_key: str = Field(description="最终文件物理路径 / Object Storage Key")
    total_size: int = Field(sa_column=Column(BigInteger, nullable=False), description="文件总字节数")
    file_hash: str = Field(unique=True, index=True, description="文件完整 Hash")
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class MergeTaskRecord(SQLModel, table=True):
    __tablename__ = "merge_tasks"

    id: Optional[int] = Field(default=None, primary_key=True)
    upload_id: str = Field(unique=True, index=True, description="唯一上传会话 ID")
    user_id: int = Field(index=True)
    file_hash: str = Field(index=True)
    status: MergeStatus = Field(default=MergeStatus.PENDING, index=True)

    # Fencing Token
    epoch: int = Field(default=1)
    retry_count: int = Field(default=0)

    # Worker Lease
    worker_id: Optional[str] = Field(default=None, index=True)
    heartbeat_at: Optional[datetime] = Field(
        default=None,
        index=True,
        sa_column=Column(DateTime(timezone=True)),
    )
    lease_expires_at: Optional[datetime] = Field(
        default=None,
        index=True,
        sa_column=Column(DateTime(timezone=True)),
    )

    # 关联产物
    file_record_id: Optional[int] = Field(
        default=None,
        foreign_key="file_record.id",
        index=True,
    )
    error_msg: Optional[str] = Field(default=None, sa_column=Column(Text))

    # 生命周期时间戳
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        index=True,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
    )
    finished_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True)),
    )


class OutboxEvent(SQLModel, table=True):
    __tablename__ = "outbox_events"

    __table_args__ = (
        Index(
            "ix_outbox_poll",
            "status",
            "next_retry_at",
            "updated_at",
            "created_at",
        ),
        Index(
            "ix_outbox_dedupe",
            "event_key",
            unique=True,
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    event_key: str = Field(unique=True, index=True, description="同一 epoch 的 merge event 唯一键")
    event_type: str = Field(default="FILE_MERGE")
    aggregate_id: str = Field(index=True, description="MergeTaskRecord.upload_id")
    payload: str = Field(sa_column=Column(Text, nullable=False), description="JSON payload")
    status: OutboxStatus = Field(default=OutboxStatus.INIT, index=True)
    retry_count: int = Field(default=0)
    last_error: Optional[str] = Field(default=None, sa_column=Column(Text))

    next_retry_at: datetime = Field(
        default_factory=utc_now,
        index=True,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        index=True,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

```

---

### app/schemas/upload_schema.py

```python
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MergeStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


class InitUploadRequest(BaseModel):
    file_name: str
    file_hash: str
    file_ext: str
    mime_type: str
    total_size: int = Field(gt=0)
    chunk_size: int = Field(gt=0)
    total_chunks: int = Field(gt=0)


class InitUploadResponse(BaseModel):
    instant_upload: bool
    file_record_id: Optional[int] = None
    upload_id: Optional[str] = None
    uploaded_chunks: list[int] = []


class UploadStatusResponse(BaseModel):
    upload_id: str
    uploaded_chunks: list[int]
    total_chunks: int
    merge_status: MergeStatus
    file_record_id: Optional[int] = None
    error_msg: Optional[str] = None
    retry_count: int = 0
    epoch: int = 1


class MergeTriggerResponse(BaseModel):
    upload_id: str
    status: MergeStatus
    task_id: Optional[str] = None
    epoch: Optional[int] = None

```

---

### app/file/session_manager.py

```python
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Callable

from fastapi import Depends
from redis.asyncio import Redis

from app.redis.redis_client import get_redis


@dataclass
class UploadSession:
    user_id: int
    upload_id: str
    file_name: str
    file_hash: str
    file_ext: str
    mime_type: str
    total_size: int
    chunk_size: int
    total_chunks: int
    uploaded_chunks: set[int] = field(default_factory=set)
    updated_at: float = 0.0


class SessionManager:
    TTL = 86400
    MUTATION_LOCK_TTL = 30

    ADD_CHUNK_LUA = """
    local meta_key = KEYS[1]
    local chunks_key = KEYS[2]

    local chunk_index = tonumber(ARGV[1])
    local ttl = tonumber(ARGV[2])

    if redis.call('EXISTS', meta_key) == 0 then
        return -1
    end

    local added = redis.call('SADD', chunks_key, chunk_index)
    redis.call('EXPIRE', meta_key, ttl)
    redis.call('EXPIRE', chunks_key, ttl)

    return added
    """

    DEL_IF_EQUALS_LUA = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    else
        return 0
    end
    """

    def __init__(
        self,
        redis_client: Redis,
        ttl: int = TTL,
    ):
        self.redis = redis_client
        self.ttl = ttl
        self.prefix = "upload"

    def _meta_key(self, upload_id: str) -> str:
        return f"{self.prefix}:{{{upload_id}}}:meta"

    def _chunks_key(self, upload_id: str) -> str:
        return f"{self.prefix}:{{{upload_id}}}:chunks"

    def _mutation_lock_key(self, upload_id: str) -> str:
        return f"{self.prefix}:{{{upload_id}}}:mutation-lock"

    def _user_hash_key(self, user_id: int, file_hash: str) -> str:
        return f"{self.prefix}:user_idx:{user_id}:{file_hash}"

    async def get_or_create_session(
        self,
        user_id: int,
        upload_id_generator: Callable[[], str],
        file_data: dict,
        max_retries: int = 5,
    ) -> tuple[UploadSession, bool]:
        user_hash_key = self._user_hash_key(user_id, file_data["file_hash"])

        for attempt in range(max_retries):
            existing_upload_id = await self.redis.get(user_hash_key)

            if existing_upload_id:
                if isinstance(existing_upload_id, bytes):
                    existing_upload_id = existing_upload_id.decode("utf-8")

                meta_key = self._meta_key(existing_upload_id)
                chunks_key = self._chunks_key(existing_upload_id)

                async with self.redis.pipeline(transaction=False) as pipe:
                    pipe.get(meta_key)
                    pipe.smembers(chunks_key)
                    raw_meta, chunks = await pipe.execute()

                if raw_meta:
                    if isinstance(raw_meta, bytes):
                        raw_meta = raw_meta.decode("utf-8")

                    meta_dict = json.loads(raw_meta)
                    uploaded_chunks = {int(x) for x in chunks}

                    async with self.redis.pipeline(transaction=False) as pipe:
                        pipe.expire(user_hash_key, self.ttl)
                        pipe.expire(meta_key, self.ttl)
                        pipe.expire(chunks_key, self.ttl)
                        await pipe.execute()

                    return (
                        UploadSession(
                            **meta_dict,
                            uploaded_chunks=uploaded_chunks,
                        ),
                        False,
                    )

                # 索引失效清理
                await self.redis.delete(user_hash_key)

            upload_id = upload_id_generator()
            meta_payload = {
                "user_id": user_id,
                "upload_id": upload_id,
                "updated_at": time.time(),
                **file_data,
            }

            meta_key = self._meta_key(upload_id)

            await self.redis.set(
                meta_key,
                json.dumps(meta_payload, ensure_ascii=False),
                ex=self.ttl,
            )

            acquired = await self.redis.set(
                user_hash_key,
                upload_id,
                nx=True,
                ex=self.ttl,
            )

            if acquired:
                return (
                    UploadSession(**meta_payload, uploaded_chunks=set()),
                    True,
                )

            await self.redis.delete(meta_key)
            await asyncio.sleep(0.02 * (attempt + 1))

        raise RuntimeError("Failed to initialize upload session.")

    def get_mutation_lock(self, upload_id: str):
        return self.redis.lock(
            self._mutation_lock_key(upload_id),
            timeout=self.MUTATION_LOCK_TTL,
            blocking_timeout=5,
        )

    async def add_uploaded_chunk(
        self,
        upload_id: str,
        chunk_index: int,
        user_id: int,
        file_hash: str,
    ) -> bool:
        result = await self.redis.eval(
            self.ADD_CHUNK_LUA,
            2,
            self._meta_key(upload_id),
            self._chunks_key(upload_id),
            chunk_index,
            self.ttl,
        )

        if result == -1:
            raise ValueError(f"Upload session expired: {upload_id}")

        await self.redis.expire(
            self._user_hash_key(user_id, file_hash),
            self.ttl,
        )
        return result == 1

    async def get_session(self, upload_id: str) -> UploadSession:
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.get(self._meta_key(upload_id))
            pipe.smembers(self._chunks_key(upload_id))
            raw_meta, chunks = await pipe.execute()

        if not raw_meta:
            raise ValueError(f"Unknown upload session: {upload_id}")

        if isinstance(raw_meta, bytes):
            raw_meta = raw_meta.decode("utf-8")

        meta = json.loads(raw_meta)
        return UploadSession(
            **meta,
            uploaded_chunks={int(c) for c in chunks},
        )

    async def clear_session(
        self,
        upload_id: str,
        user_id: int,
        file_hash: str,
    ):
        meta_key = self._meta_key(upload_id)
        chunks_key = self._chunks_key(upload_id)
        user_hash_key = self._user_hash_key(user_id, file_hash)

        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.delete(meta_key)
            pipe.delete(chunks_key)
            await pipe.execute()

        # Lua 原子 CAS 清除指向当前 upload_id 的用户索引
        await self.redis.eval(
            self.DEL_IF_EQUALS_LUA,
            1,
            user_hash_key,
            upload_id,
        )


async def get_session_manager(
    redis: Redis = Depends(get_redis),
) -> SessionManager:
    return SessionManager(redis)

```

---

### app/services/upload_service.py

```python
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Optional

import aiofiles.os
from fastapi import Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crud.upload_crud import upload_crud
from app.file.base_storage import BaseStorage
from app.file.key_builder import build_tmp_path
from app.file.session_manager import (
    SessionManager,
    UploadSession,
    get_session_manager,
)
from app.file.storage_factory import get_storage
from app.models.upload import (
    FileRecord,
    MergeStatus,
    MergeTaskRecord,
    OutboxEvent,
    OutboxStatus,
)
from app.tasks.merge_tasks import merge_file


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def asyncio_remove(path: Path):
    try:
        await aiofiles.os.remove(path)
    except FileNotFoundError:
        pass


class UploadService:
    def __init__(
        self,
        storage: BaseStorage = Depends(get_storage),
        session_manager: SessionManager = Depends(get_session_manager),
    ):
        self.storage = storage
        self.session_manager = session_manager

    async def init_file_upload(
        self,
        user_id: int,
        file_name: str,
        file_hash: str,
        file_ext: str,
        mime_type: str,
        total_size: int,
        chunk_size: int,
        total_chunks: int,
        db: AsyncSession,
    ) -> tuple[Optional[UploadSession], Optional[FileRecord]]:
        file_name = Path(file_name).name

        existing = await upload_crud.get_file_record_by_hash(db, file_hash)
        if existing:
            return None, existing

        session, _ = await self.session_manager.get_or_create_session(
            user_id=user_id,
            upload_id_generator=lambda: uuid.uuid4().hex,
            file_data={
                "file_name": file_name,
                "file_hash": file_hash,
                "file_ext": file_ext,
                "mime_type": mime_type,
                "total_size": total_size,
                "chunk_size": chunk_size,
                "total_chunks": total_chunks,
            },
        )

        stmt = select(MergeTaskRecord).where(
            MergeTaskRecord.upload_id == session.upload_id
        )
        task = (await db.exec(stmt)).first()

        if task is None:
            try:
                async with db.begin_nested():
                    db.add(
                        MergeTaskRecord(
                            upload_id=session.upload_id,
                            user_id=user_id,
                            file_hash=session.file_hash,
                            status=MergeStatus.PENDING,
                            epoch=1,
                        )
                    )
                    await db.flush()
            except IntegrityError:
                pass

        await db.commit()
        return session, None

    async def upload_file_chunk(
        self,
        upload_id: str,
        user_id: int,
        chunk_index: int,
        chunk_file: AsyncGenerator[bytes, None],
    ):
        session = await self._get_session_with_permission(upload_id, user_id)

        if chunk_index < 0 or chunk_index >= session.total_chunks:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid chunk_index={chunk_index}, total_chunks={session.total_chunks}",
            )

        # 快速幂等检查
        if chunk_index in session.uploaded_chunks:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Chunk {chunk_index} already uploaded.",
            )

        canonical_path = Path(
            build_tmp_path(
                upload_id=upload_id,
                chunk_index=chunk_index,
            )
        )
        await aiofiles.os.makedirs(canonical_path.parent, exist_ok=True)
        tmp_path = canonical_path.with_name(
            f"{canonical_path.name}.{uuid.uuid4().hex}.tmp"
        )

        try:
            await self.storage.upload_chunk(
                chunk_path=tmp_path,
                file=chunk_file,
            )

            # 原子替换，消除全局锁竞争
            await aiofiles.os.replace(tmp_path, canonical_path)

            await self.session_manager.add_uploaded_chunk(
                upload_id=upload_id,
                chunk_index=chunk_index,
                user_id=user_id,
                file_hash=session.file_hash,
            )
        except HTTPException:
            await asyncio_remove(tmp_path)
            raise
        except Exception as exc:
            await asyncio_remove(tmp_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload chunk: {exc}",
            )

    async def trigger_merge(
        self,
        upload_id: str,
        user_id: int,
        db: AsyncSession,
    ) -> tuple[MergeStatus, Optional[str], Optional[int]]:
        lock = self.session_manager.get_mutation_lock(upload_id)
        if not await lock.acquire():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Upload session is busy.",
            )

        outbox_payload: Optional[dict] = None
        outbox_id: Optional[int] = None

        try:
            session = await self._get_session_with_permission(upload_id, user_id)
            expected_chunks = set(range(session.total_chunks))
            missing = expected_chunks - session.uploaded_chunks

            if missing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Chunks incomplete. Missing: {sorted(missing)}",
                )

            stmt = (
                select(MergeTaskRecord)
                .where(MergeTaskRecord.upload_id == upload_id)
                .with_for_update()
            )
            task = (await db.exec(stmt)).first()

            if task is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Merge task record does not exist.",
                )

            if task.status == MergeStatus.COMPLETED:
                return MergeStatus.COMPLETED, None, task.epoch

            if task.status == MergeStatus.MERGING:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Merge task is already being executed.",
                )

            if task.status == MergeStatus.QUEUED:
                return MergeStatus.QUEUED, None, task.epoch

            if task.status == MergeStatus.FAILED:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Merge task has failed. Use retry endpoint.",
                )

            task.status = MergeStatus.QUEUED
            task.updated_at = utc_now()
            task.error_msg = None

            outbox_payload = self._build_merge_payload(session=session, task=task)
            event = OutboxEvent(
                event_key=f"FILE_MERGE:{upload_id}:{task.epoch}",
                event_type="FILE_MERGE",
                aggregate_id=upload_id,
                payload=json.dumps(outbox_payload, ensure_ascii=False),
                status=OutboxStatus.INIT,
            )
            db.add(event)
            await db.flush()
            outbox_id = event.id

            await db.commit()
        finally:
            await lock.release()

        # Fast Path 加速投递
        if outbox_id is not None and outbox_payload is not None:
            try:
                task_result = await merge_file.kiq(payload=outbox_payload)

                async with db.begin():
                    stmt = (
                        select(OutboxEvent)
                        .where(OutboxEvent.id == outbox_id)
                        .with_for_update()
                    )
                    current = (await db.exec(stmt)).first()

                    if current and current.status == OutboxStatus.INIT:
                        current.status = OutboxStatus.PUBLISHED
                        current.updated_at = utc_now()

                return (
                    MergeStatus.QUEUED,
                    str(task_result.task_id),
                    outbox_payload["epoch"],
                )
            except Exception:
                return (
                    MergeStatus.QUEUED,
                    None,
                    outbox_payload["epoch"],
                )

        return (
            MergeStatus.QUEUED,
            None,
            outbox_payload["epoch"] if outbox_payload else None,
        )

    async def retry_merge(
        self,
        upload_id: str,
        user_id: int,
        db: AsyncSession,
    ) -> tuple[MergeStatus, Optional[str], Optional[int]]:
        lock = self.session_manager.get_mutation_lock(upload_id)
        if not await lock.acquire():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Upload session is busy.",
            )

        outbox_payload = None
        outbox_id = None

        try:
            session = await self._get_session_with_permission(upload_id, user_id)
            expected_chunks = set(range(session.total_chunks))
            missing = expected_chunks - session.uploaded_chunks

            if missing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Chunks incomplete. Missing: {sorted(missing)}",
                )

            stmt = (
                select(MergeTaskRecord)
                .where(MergeTaskRecord.upload_id == upload_id)
                .with_for_update()
            )
            task = (await db.exec(stmt)).first()

            if task is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Merge task not found.",
                )

            if task.status == MergeStatus.COMPLETED:
                return MergeStatus.COMPLETED, None, task.epoch

            if task.status == MergeStatus.MERGING:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Merge worker is still active. Retry only after lease recovery.",
                )

            task.epoch += 1
            task.retry_count += 1
            task.status = MergeStatus.QUEUED

            task.worker_id = None
            task.heartbeat_at = None
            task.lease_expires_at = None
            task.started_at = None
            task.finished_at = None
            task.file_record_id = None
            task.error_msg = None
            task.updated_at = utc_now()

            outbox_payload = self._build_merge_payload(session=session, task=task)
            event = OutboxEvent(
                event_key=f"FILE_MERGE:{upload_id}:{task.epoch}",
                event_type="FILE_MERGE",
                aggregate_id=upload_id,
                payload=json.dumps(outbox_payload, ensure_ascii=False),
                status=OutboxStatus.INIT,
            )
            db.add(event)
            await db.flush()
            outbox_id = event.id

            await db.commit()
        finally:
            await lock.release()

        try:
            result = await merge_file.kiq(payload=outbox_payload)

            async with db.begin():
                stmt = (
                    select(OutboxEvent)
                    .where(OutboxEvent.id == outbox_id)
                    .with_for_update()
                )
                event = (await db.exec(stmt)).first()

                if event and event.status == OutboxStatus.INIT:
                    event.status = OutboxStatus.PUBLISHED
                    event.updated_at = utc_now()

            return (
                MergeStatus.QUEUED,
                str(result.task_id),
                outbox_payload["epoch"],
            )
        except Exception:
            return (
                MergeStatus.QUEUED,
                None,
                outbox_payload["epoch"],
            )

    async def get_status(
        self,
        upload_id: str,
        user_id: int,
        db: AsyncSession,
    ) -> dict:
        session = await self._get_session_with_permission(upload_id, user_id)

        stmt = select(MergeTaskRecord).where(
            MergeTaskRecord.upload_id == upload_id
        )
        task = (await db.exec(stmt)).first()

        if task is None:
            merge_status = MergeStatus.PENDING
            file_record_id = None
            error_msg = None
            retry_count = 0
            epoch = 1
        else:
            merge_status = task.status
            file_record_id = task.file_record_id
            error_msg = task.error_msg
            retry_count = task.retry_count
            epoch = task.epoch

        return {
            "upload_id": upload_id,
            "uploaded_chunks": sorted(session.uploaded_chunks),
            "total_chunks": session.total_chunks,
            "merge_status": merge_status,
            "file_record_id": file_record_id,
            "error_msg": error_msg,
            "retry_count": retry_count,
            "epoch": epoch,
        }

    async def _get_session_with_permission(
        self,
        upload_id: str,
        user_id: int,
    ) -> UploadSession:
        try:
            session = await self.session_manager.get_session(upload_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown upload session: {upload_id}",
            )

        if int(session.user_id) != int(user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied.",
            )

        return session

    @staticmethod
    def _build_merge_payload(
        session: UploadSession,
        task: MergeTaskRecord,
    ) -> dict:
        return {
            "upload_id": session.upload_id,
            "user_id": session.user_id,
            "file_name": session.file_name,
            "file_hash": session.file_hash,
            "file_ext": session.file_ext,
            "mime_type": session.mime_type,
            "total_size": session.total_size,
            "total_chunks": session.total_chunks,
            "epoch": task.epoch,
        }

```

---

### app/api/routes/upload.py

```python
from fastapi import (
    APIRouter,
    Depends,
    Path as RequestPath,
    Query,
    Request,
    status,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.session import get_session
from app.models.base import User
from app.rbac.dependencies import get_current_user
from app.schemas.upload_schema import (
    InitUploadRequest,
    InitUploadResponse,
    MergeTriggerResponse,
    UploadStatusResponse,
)
from app.services.upload_service import UploadService

router = APIRouter()


@router.post("/init", response_model=InitUploadResponse)
async def init_upload(
    upload_info: InitUploadRequest,
    upload_service: UploadService = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    session, file_record = await upload_service.init_file_upload(
        user_id=current_user.id,
        file_name=upload_info.file_name,
        file_hash=upload_info.file_hash,
        file_ext=upload_info.file_ext,
        mime_type=upload_info.mime_type,
        total_size=upload_info.total_size,
        chunk_size=upload_info.chunk_size,
        total_chunks=upload_info.total_chunks,
        db=db,
    )

    if file_record:
        return InitUploadResponse(
            instant_upload=True,
            file_record_id=file_record.id,
            upload_id=None,
            uploaded_chunks=[],
        )

    return InitUploadResponse(
        instant_upload=False,
        file_record_id=None,
        upload_id=session.upload_id,
        uploaded_chunks=sorted(session.uploaded_chunks),
    )


@router.post("/{upload_id}/chunk")
async def upload_chunk(
    request: Request,
    upload_id: str = RequestPath(...),
    chunk_index: int = Query(...),
    upload_service: UploadService = Depends(),
    current_user: User = Depends(get_current_user),
):
    await upload_service.upload_file_chunk(
        upload_id=upload_id,
        user_id=current_user.id,
        chunk_index=chunk_index,
        chunk_file=request.stream(),
    )
    return {
        "upload_id": upload_id,
        "chunk_index": chunk_index,
    }


@router.get("/{upload_id}/status", response_model=UploadStatusResponse)
async def get_upload_status(
    upload_id: str = RequestPath(...),
    upload_service: UploadService = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    result = await upload_service.get_status(
        upload_id=upload_id,
        user_id=current_user.id,
        db=db,
    )
    return UploadStatusResponse(**result)


@router.post(
    "/{upload_id}/merge",
    response_model=MergeTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def merge_chunks(
    upload_id: str = RequestPath(...),
    upload_service: UploadService = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    merge_status, task_id, epoch = await upload_service.trigger_merge(
        upload_id=upload_id,
        user_id=current_user.id,
        db=db,
    )
    return MergeTriggerResponse(
        upload_id=upload_id,
        status=merge_status,
        task_id=task_id,
        epoch=epoch,
    )


@router.post(
    "/{upload_id}/retry",
    response_model=MergeTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_merge(
    upload_id: str = RequestPath(...),
    upload_service: UploadService = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    merge_status, task_id, epoch = await upload_service.retry_merge(
        upload_id=upload_id,
        user_id=current_user.id,
        db=db,
    )
    return MergeTriggerResponse(
        upload_id=upload_id,
        status=merge_status,
        task_id=task_id,
        epoch=epoch,
    )

```

---

### app/tasks/merge_tasks.py

```python
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiofiles.os
from sqlalchemy.exc import IntegrityError
from sqlmodel import select, update

from app.core.config import settings
from app.crud.upload_crud import upload_crud
from app.db.session import get_db_session_context
from app.file.key_builder import build_final_path, build_tmp_path
from app.file.session_manager import SessionManager
from app.file.storage_factory import get_storage_instance
from app.models.upload import (
    FileRecord,
    MergeStatus,
    MergeTaskRecord,
    OutboxEvent,
    OutboxStatus,
)
from app.redis.redis_client import get_redis_client
from app.tasks.broker import broker

logger = logging.getLogger(__name__)

MERGE_LEASE_SECONDS = 180
HEARTBEAT_INTERVAL_SECONDS = 20
OUTBOX_PROCESSING_TIMEOUT = 90
OUTBOX_MAX_RETRY = 10


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def calculate_file_hash_sync(file_path: Path, expected_hash_len: int) -> str:
    hasher = (
        hashlib.md5() if expected_hash_len == 32 else hashlib.sha256()
    )
    with open(file_path, "rb", buffering=2 * 1024 * 1024) as f:
        while chunk := f.read(2 * 1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def worker_identity() -> str:
    return f"{platform.node()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def safe_remove(path: Path):
    try:
        await aiofiles.os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("Failed to remove file: %s", path)


def _clean_orphans_sync(tmp_root: Path, cutoff: float):
    """自底向上扫描物理碎片并清理过期空目录，回收物理磁盘 Inode 与存储空间。"""
    if not tmp_root.exists():
        return

    for root, dirs, files in os.walk(tmp_root, topdown=False):
        for f in files:
            file_path = Path(root) / f
            try:
                if file_path.stat().st_mtime < cutoff:
                    file_path.unlink(missing_ok=True)
            except Exception:
                pass

        for d in dirs:
            dir_path = Path(root) / d
            try:
                # 目录若为空则直接回收，规避海量 upload_id 空目录积压
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
            except Exception:
                pass


@broker.task
async def merge_file(payload: dict):
    upload_id = payload["upload_id"]
    task_epoch = int(payload["epoch"])
    file_name = Path(payload["file_name"]).name
    file_hash = payload["file_hash"]
    total_chunks = int(payload["total_chunks"])
    total_size = int(payload["total_size"])
    file_ext = payload["file_ext"]
    mime_type = payload["mime_type"]

    worker_id = worker_identity()
    storage = get_storage_instance()
    redis = get_redis_client()
    session_mgr = SessionManager(redis)

    target_path = Path(build_final_path(upload_id, file_name))
    await aiofiles.os.makedirs(target_path.parent, exist_ok=True)

    temp_target = target_path.with_name(
        f"{target_path.name}.epoch-{task_epoch}.{uuid.uuid4().hex}.tmp"
    )

    source_paths = [
        Path(build_tmp_path(upload_id, index))
        for index in range(total_chunks)
    ]

    heartbeat_stop = asyncio.Event()

    async def heartbeat():
        while not heartbeat_stop.is_set():
            try:
                await asyncio.wait_for(
                    heartbeat_stop.wait(),
                    timeout=HEARTBEAT_INTERVAL_SECONDS,
                )
                return
            except asyncio.TimeoutError:
                pass

            now = utc_now()
            lease_until = now + timedelta(seconds=MERGE_LEASE_SECONDS)

            try:
                async with get_db_session_context() as db:
                    result = await db.exec(
                        update(MergeTaskRecord)
                        .where(MergeTaskRecord.upload_id == upload_id)
                        .where(MergeTaskRecord.epoch == task_epoch)
                        .where(MergeTaskRecord.status == MergeStatus.MERGING)
                        .values(
                            worker_id=worker_id,
                            heartbeat_at=now,
                            lease_expires_at=lease_until,
                            updated_at=now,
                        )
                    )
                    await db.commit()

                    if result.rowcount != 1:
                        logger.warning(
                            "Lease lost: upload_id=%s epoch=%s",
                            upload_id,
                            task_epoch,
                        )
                        return
            except Exception:
                logger.exception(
                    "Heartbeat failed: upload_id=%s epoch=%s",
                    upload_id,
                    task_epoch,
                )

    heartbeat_task = asyncio.create_task(heartbeat())

    try:
        # 1. Claim 任务
        now = utc_now()
        lease_until = now + timedelta(seconds=MERGE_LEASE_SECONDS)

        async with get_db_session_context() as db:
            result = await db.exec(
                update(MergeTaskRecord)
                .where(MergeTaskRecord.upload_id == upload_id)
                .where(MergeTaskRecord.epoch == task_epoch)
                .where(MergeTaskRecord.status == MergeStatus.QUEUED)
                .values(
                    status=MergeStatus.MERGING,
                    worker_id=worker_id,
                    heartbeat_at=now,
                    lease_expires_at=lease_until,
                    started_at=now,
                    updated_at=now,
                    error_msg=None,
                )
            )
            await db.commit()

            if result.rowcount != 1:
                logger.info(
                    "Duplicate/stale merge task ignored: upload_id=%s epoch=%s",
                    upload_id,
                    task_epoch,
                )
                return

        # 2. 校验所有 Chunk 文件
        missing_files = [str(p) for p in source_paths if not p.exists()]
        if missing_files:
            raise RuntimeError(
                f"Missing chunk files: {', '.join(missing_files[:20])}"
            )

        # 3. 合并文件
        await asyncio.to_thread(
            storage.merge_chunks,
            source_paths=source_paths,
            target_path=temp_target,
        )

        # 4. 校验哈希一致性
        computed_hash = await asyncio.to_thread(
            calculate_file_hash_sync,
            temp_target,
            len(file_hash),
        )
        if computed_hash.lower() != file_hash.lower():
            raise ValueError(
                f"File integrity check failed: expected={file_hash}, actual={computed_hash}"
            )

        # 5. DB 与存储原子提交 (带 Fencing 校验)
        durable_file_record_id = None

        async with get_db_session_context() as db:
            async with db.begin():
                stmt = (
                    select(MergeTaskRecord)
                    .where(MergeTaskRecord.upload_id == upload_id)
                    .with_for_update()
                )
                current = (await db.exec(stmt)).first()

                if (
                    current is None
                    or current.epoch != task_epoch
                    or current.status != MergeStatus.MERGING
                ):
                    logger.warning(
                        "Stale worker blocked at finalization: upload_id=%s epoch=%s",
                        upload_id,
                        task_epoch,
                    )
                    return

                existing = await upload_crud.get_file_record_by_hash(db, file_hash)

                if existing:
                    durable_file_record_id = existing.id
                    await safe_remove(temp_target)
                else:
                    await aiofiles.os.replace(temp_target, target_path)

                    try:
                        async with db.begin_nested():
                            record = FileRecord(
                                storage_type=settings.STORAGE_TYPE,
                                storage_key=str(target_path),
                                total_size=total_size,
                                file_hash=file_hash,
                                file_ext=file_ext,
                                mime_type=mime_type,
                            )
                            db.add(record)
                            await db.flush()
                            durable_file_record_id = record.id
                    except IntegrityError:
                        conflict = await upload_crud.get_file_record_by_hash(db, file_hash)
                        if conflict:
                            durable_file_record_id = conflict.id
                            if conflict.storage_key != str(target_path):
                                await safe_remove(target_path)
                        else:
                            raise

                current.status = MergeStatus.COMPLETED
                current.file_record_id = durable_file_record_id
                current.worker_id = None
                current.heartbeat_at = None
                current.lease_expires_at = None
                current.error_msg = None
                current.finished_at = utc_now()
                current.updated_at = utc_now()

        # 6. 清理分片与会话
        for path in source_paths:
            await safe_remove(path)

        await session_mgr.clear_session(
            upload_id=upload_id,
            user_id=int(payload["user_id"]),
            file_hash=file_hash,
        )

    except BaseException as exc:
        heartbeat_stop.set()
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task

        try:
            async with get_db_session_context() as db:
                result = await db.exec(
                    update(MergeTaskRecord)
                    .where(MergeTaskRecord.upload_id == upload_id)
                    .where(MergeTaskRecord.epoch == task_epoch)
                    .where(MergeTaskRecord.status == MergeStatus.MERGING)
                    .values(
                        status=MergeStatus.FAILED,
                        worker_id=None,
                        heartbeat_at=None,
                        lease_expires_at=None,
                        error_msg=str(exc)[:4000],
                        finished_at=utc_now(),
                        updated_at=utc_now(),
                    )
                )
                await db.commit()

                if result.rowcount == 0:
                    logger.warning(
                        "Worker failed but lost fencing token: upload_id=%s epoch=%s",
                        upload_id,
                        task_epoch,
                    )
        except Exception:
            logger.exception(
                "Failed to persist merge failure: upload_id=%s epoch=%s",
                upload_id,
                task_epoch,
            )

        raise
    finally:
        if not heartbeat_stop.is_set():
            heartbeat_stop.set()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

        await safe_remove(temp_target)


@broker.task(schedule=[{"cron": "*/1 * * * *"}])
async def outbox_relay_task():
    now = utc_now()
    process_before = now - timedelta(seconds=OUTBOX_PROCESSING_TIMEOUT)
    event_rows: list[tuple[int, str, int]] = []

    async with get_db_session_context() as db:
        async with db.begin():
            stmt = (
                select(OutboxEvent)
                .where(
                    (
                        (OutboxEvent.status == OutboxStatus.INIT)
                        & (OutboxEvent.next_retry_at <= now)
                    )
                    | (
                        (OutboxEvent.status == OutboxStatus.PROCESSING)
                        & (OutboxEvent.updated_at < process_before)
                    )
                )
                .where(OutboxEvent.retry_count < OUTBOX_MAX_RETRY)
                .order_by(OutboxEvent.created_at.asc())
                .limit(50)
                .with_for_update(skip_locked=True)
            )

            events = (await db.exec(stmt)).all()
            for event in events:
                event.status = OutboxStatus.PROCESSING
                event.retry_count += 1
                event.updated_at = now
                event_rows.append((event.id, event.payload, event.retry_count))

    if not event_rows:
        return

    published: list[int] = []
    failed: list[tuple[int, int, str]] = []

    for event_id, payload_str, retry_count in event_rows:
        try:
            payload = json.loads(payload_str)
            await merge_file.kiq(payload=payload)
            published.append(event_id)
        except Exception as exc:
            failed.append((event_id, retry_count, str(exc)[:4000]))

    async with get_db_session_context() as db:
        async with db.begin():
            now = utc_now()

            for event_id in published:
                await db.exec(
                    update(OutboxEvent)
                    .where(OutboxEvent.id == event_id)
                    .where(OutboxEvent.status == OutboxStatus.PROCESSING)
                    .values(
                        status=OutboxStatus.PUBLISHED,
                        updated_at=now,
                        last_error=None,
                    )
                )

            for event_id, retry_count, error in failed:
                if retry_count >= OUTBOX_MAX_RETRY:
                    await db.exec(
                        update(OutboxEvent)
                        .where(OutboxEvent.id == event_id)
                        .where(OutboxEvent.status == OutboxStatus.PROCESSING)
                        .values(
                            status=OutboxStatus.FAILED,
                            updated_at=now,
                            last_error=error,
                        )
                    )
                else:
                    delay = min(300, 2 ** min(retry_count, 8))
                    next_retry = now + timedelta(seconds=delay)
                    await db.exec(
                        update(OutboxEvent)
                        .where(OutboxEvent.id == event_id)
                        .where(OutboxEvent.status == OutboxStatus.PROCESSING)
                        .values(
                            status=OutboxStatus.INIT,
                            updated_at=now,
                            next_retry_at=next_retry,
                            last_error=error,
                        )
                    )


@broker.task(schedule=[{"cron": "*/1 * * * *"}])
async def merge_recovery_task():
    now = utc_now()
    redis = get_redis_client()
    session_mgr = SessionManager(redis)

    async with get_db_session_context() as db:
        async with db.begin():
            stmt = (
                select(MergeTaskRecord)
                .where(MergeTaskRecord.status == MergeStatus.MERGING)
                .where(MergeTaskRecord.lease_expires_at != None)
                .where(MergeTaskRecord.lease_expires_at < now)
                .order_by(MergeTaskRecord.updated_at.asc())
                .limit(50)
                .with_for_update(skip_locked=True)
            )
            tasks = (await db.exec(stmt)).all()

            for task in tasks:
                lock = session_mgr.get_mutation_lock(task.upload_id)
                acquired = await lock.acquire()
                if not acquired:
                    continue

                try:
                    task.epoch += 1
                    task.retry_count += 1
                    task.status = MergeStatus.QUEUED

                    task.worker_id = None
                    task.heartbeat_at = None
                    task.lease_expires_at = None
                    task.started_at = None
                    task.finished_at = None
                    task.file_record_id = None
                    task.error_msg = (
                        "Previous worker lease expired; task automatically recovered."
                    )
                    task.updated_at = now

                    old_event_stmt = (
                        select(OutboxEvent)
                        .where(OutboxEvent.aggregate_id == task.upload_id)
                        .order_by(OutboxEvent.id.desc())
                        .limit(1)
                    )
                    old_event = (await db.exec(old_event_stmt)).first()

                    if old_event:
                        payload = json.loads(old_event.payload)
                        payload["epoch"] = task.epoch
                    else:
                        payload = {
                            "upload_id": task.upload_id,
                            "user_id": task.user_id,
                            "file_hash": task.file_hash,
                            "epoch": task.epoch,
                        }

                    event = OutboxEvent(
                        event_key=f"FILE_MERGE:{task.upload_id}:{task.epoch}",
                        event_type="FILE_MERGE",
                        aggregate_id=task.upload_id,
                        payload=json.dumps(payload, ensure_ascii=False),
                        status=OutboxStatus.INIT,
                    )
                    db.add(event)
                finally:
                    await lock.release()


@broker.task(schedule=[{"cron": "0 3 * * *"}])
async def disk_janitor_reap_orphans():
    """凌晨 3 点定时清理 24 小时前修改的未合并物理碎片及残余空目录。"""
    tmp_upload_dir = getattr(settings, "TMP_UPLOAD_DIR", "/tmp/uploads")
    tmp_root = Path(tmp_upload_dir)
    cutoff = time.time() - 86400
    await asyncio.to_thread(_clean_orphans_sync, tmp_root, cutoff)

```
````

