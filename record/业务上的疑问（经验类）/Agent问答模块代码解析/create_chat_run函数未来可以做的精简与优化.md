```
# ============================================================
# app/api/routes/runs.py
# ============================================================
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.core.database import get_session
from app.core.lock import RedisDistributedLock
from app.core.redis import redis_client
from app.core.security import get_current_user_id
from app.models import (
    Message,
    MessageRole,
    MessageStatus,
    Run,
    RunStatus,
    Thread,
    ThreadStatus,
)
from app.schemas import RunCreateRequest, RunCreateResponse
from app.tasks.agent import execute_agent_run
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Runs"])


# ============================================================
# 常量
# ============================================================

# 活跃 Run 的状态集合。
#
# ⚠️ 关键一致性要求
# ------------------------------------------------------------
# 这个集合必须与 PostgreSQL 部分唯一索引
# `uq_runs_thread_active_run` 的 WHERE 子句 100% 一致。
#
# Migration 中必须存在：
#
#     CREATE UNIQUE INDEX uq_runs_thread_active_run
#     ON runs(thread_id)
#     WHERE status IN ('queued', 'in_progress', 'requires_action');
#
# 任何一方的改动都必须同步另一方，否则会出现：
#   - 索引比集合宽 → DB 允许创建，业务认为冲突（并发下产生重复 Run）
#   - 集合比索引宽 → 业务认为冲突，DB 允许（用户被误拦）
#
# 建议：在测试中断言该索引定义与下面的集合一致。
#
ACTIVE_RUN_STATUSES: tuple[str, ...] = (
    RunStatus.QUEUED.value,
    RunStatus.IN_PROGRESS.value,
    RunStatus.REQUIRES_ACTION.value,
)

# 分布式锁的 TTL（秒）。
# 需显著大于临界区的最坏耗时（DB 查询 + 一次 INSERT + commit）。
THREAD_LOCK_TTL_SECONDS = 30


# ============================================================
# 端点：在指定 Thread 下创建一次 Agent Run
# ============================================================
@router.post(
    "/threads/{thread_id}/runs",
    response_model=RunCreateResponse,
    # 装饰器上声明为 201，但函数内部会针对"幂等重放"覆盖为 200，
    # 让客户端能区分"新建" vs "重放已有"。
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    thread_id: uuid.UUID,
    req: RunCreateRequest,
    response: Response,
    user_id: int = Depends(get_current_user_id),
):
    """
    在已有 Thread 中创建一次 Agent Run。

    这是整个 Agent 执行的入口。

    ============================================================
    并发保障的"三层防御"
    ============================================================

    第一层：Redis 分布式锁（快速失败，性能优化，非权威）
        - 作用：把绝大多数并发请求挡在临界区外，减少 DB 冲突。
        - 局限：TTL 到期、Redis 抖动、网络分区等都可能导致锁失效，
                所以它只是"减少冲突"的优化，不能作为正确性依据。

    第二层：业务层"是否已有 active Run"检查（事务内快照）
        - 作用：给常规请求一个清晰的 409 错误。
        - 局限：读快照不是原子操作，无法在真正并发下保证唯一性。

    第三层：DB 部分唯一索引 + IntegrityError 分流（权威兜底）
        - 作用：数据库级强一致，任何并发都拦得住。
        - 两个关键索引：
            * `uq_runs_idempotency_key`     → 同 (user, key) 幂等防重
            * `uq_runs_thread_active_run`   → 同一 Thread 最多一个活跃 Run

    ============================================================
    鉴权顺序（重要）
    ============================================================

        先鉴权（无锁） → 再抢锁 → 再进入临界区

    原因：
      - 锁 key 是 `lock:thread:{thread_id}`，只按资源命名，
        不带 user_id。若先抢锁后鉴权，任何持有合法 JWT 的用户
        只要知道别人的 thread_id，都能抢到这把锁并阻塞 owner
        （跨用户锁干扰，乃至轻量 DoS）。
      - 把 owner 校验前移到锁外，非法请求根本进不到锁竞争阶段，
        既不消耗锁资源，也不占用临界区窗口。

    ============================================================
    执行流程
    ============================================================

        阶段一（无锁）：查 thread，校验 owner
        阶段二：获取 `lock:thread:{thread_id}`
        阶段三（临界区）：
            1. 重新读 thread，校验 status
            2. 幂等检查
            3. active Run 检查
            4. INSERT Run + Message
            5. commit（IntegrityError 三段分流）
            6. TaskIQ 入队；失败则标记 Run 为 failed
        阶段四：释放锁
    """

    # ============================================================
    # 阶段一：无锁鉴权（前置）
    # ============================================================
    #
    # 目的：
    #   1. 非法请求在这里就被拒，根本进不到锁竞争；
    #   2. 顺带确认 thread 存在，避免为一个不存在的 thread 抢锁。
    #
    # 注意：
    #   - 这里读到的 thread 是"鉴权视图"，用于判断 owner。
    #     owner 是不变量，所以即使并发修改其他字段也安全。
    #   - 404 与 403 统一返回 404，避免向未授权用户泄露
    #     "该 thread 是否存在" 这一事实。
    #
    async with get_session() as db:
        thread = await db.get(Thread, thread_id)

        if thread is None or thread.user_id != user_id:
            # 故意合并 404 与 403：不泄露 thread 存在性。
            # 对客户端来说，"不存在"和"不属于你"是同一件事。
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "THREAD_NOT_FOUND",
                    "message": "Thread 不存在",
                },
            )

    # ============================================================
    # 阶段二：获取 Thread 级分布式锁
    # ============================================================
    #
    # 锁 key 只由"被保护的资源"决定，不带 user_id：
    #   - 一个 thread 只会属于一个 user，user_id 在这里是冗余维度；
    #   - 锁的语义是"保护这个 thread 的写入"，key 应反映资源本身。
    #
    # ⚠️ 前提：RedisDistributedLock 必须满足以下两点：
    #   1. acquire 时写入一个随机 token（每次获取唯一）；
    #   2. release 用 Lua 脚本做 check-and-delete：
    #
    #      if redis.call("GET", KEYS[1]) == ARGV[1] then
    #          return redis.call("DEL", KEYS[1])
    #      else
    #          return 0
    #      end
    #
    #   没有第 2 点，锁因 TTL 过期被别的请求抢到后，
    #   我们的 finally 会把别人的锁删掉（经典坑）。
    #
    lock = RedisDistributedLock(
        redis_client,
        key=f"lock:thread:{thread_id}",
        ttl=THREAD_LOCK_TTL_SECONDS,
    )

    if not await lock.acquire():
        # 用 429 而不是 409：表示"短时间内可重试"，
        # 与"已有 active Run"的 409 语义区分开。
        # 客户端据此可做退避重试，而不是提示用户"不能操作"。
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "THREAD_LOCK_BUSY",
                "message": "当前会话正在处理其他请求，请稍后重试",
            },
        )

    try:
        # ============================================================
        # 阶段三：临界区
        # ============================================================
        async with get_session() as db:

            # --------------------------------------------------------
            # 3.1 重新读 thread，校验业务状态
            # --------------------------------------------------------
            #
            # 阶段一读过一次 thread，为什么这里再读？
            #   - 阶段一读的是"鉴权视图"，判断 owner（不变量）；
            #   - 这里读的是"业务视图"，要判断 status 等可变字段。
            #   - status 可能被其他请求并发修改，必须在锁内读取
            #     才能获得一致快照。
            #
            thread = await db.get(Thread, thread_id)

            # 理论上到了这一步 thread 一定存在（阶段一校验过），
            # 但如果并发删除 thread，这里可能为 None。
            # 保险起见再判一次，避免 None 访问。
            if thread is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "THREAD_NOT_FOUND",
                        "message": "Thread 不存在",
                    },
                )

            if thread.status != ThreadStatus.ACTIVE.value:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "THREAD_NOT_ACTIVE",
                        "message": "Thread 当前不可执行",
                    },
                )

            # --------------------------------------------------------
            # 3.2 幂等检查
            # --------------------------------------------------------
            #
            # 查询条件必须与唯一索引 `uq_runs_idempotency_key` 的列一致：
            #
            #     UNIQUE (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL
            #
            # 因此这里同时匹配 user_id + idempotency_key。
            # 如果只按 idempotency_key 查（单列）会漏掉"其他用户
            # 用了同 key"的情况，导致后续 IntegrityError 分流误判。
            #
            existing_run: Optional[Run] = None
            if req.idempotency_key:
                existing_run = (
                    await db.exec(
                        select(Run).where(
                            Run.user_id == user_id,
                            Run.idempotency_key == req.idempotency_key,
                        )
                    )
                ).first()

            if existing_run is not None:
                # ---- 幂等重放分支 ----

                # 客户端误用 key：同一 key 指到了不同 Thread。
                # 必须明确拒绝，否则会把 A thread 的 run 返回给 B thread。
                if existing_run.thread_id != thread_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "IDEMPOTENCY_KEY_MISMATCH",
                            "message": (
                                "idempotency_key 已被其他 Thread 使用，"
                                "请为新的请求生成新的 idempotency_key"
                            ),
                        },
                    )

                # 幂等重放返回 200（覆盖装饰器上的 201），
                # 让客户端能区分"新建成功" vs "重放已有"。
                response.status_code = status.HTTP_200_OK
                return _build_run_response(existing_run)

            # --------------------------------------------------------
            # 3.3 active Run 检查（事务内快照）
            # --------------------------------------------------------
            #
            # 这是"业务层防护"，不保证并发唯一，只提供友好错误信息。
            # 真正的唯一性由 DB 部分唯一索引兜底。
            #
            active_run = (
                await db.exec(
                    select(Run).where(
                        Run.thread_id == thread_id,
                        Run.status.in_(ACTIVE_RUN_STATUSES),
                    )
                )
            ).first()

            if active_run is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "THREAD_HAS_ACTIVE_RUN",
                        "message": "当前 Thread 已存在正在执行的 Run",
                        "run_id": str(active_run.id),
                        "run_status": active_run.status,
                    },
                )

            # --------------------------------------------------------
            # 3.4 创建 Run 与 User Message
            # --------------------------------------------------------
            run = Run(
                thread_id=thread_id,
                user_id=user_id,
                idempotency_key=req.idempotency_key,
                scope=req.scope,
                scope_id=req.scope_id,
                status=RunStatus.QUEUED.value,
                trigger_type="user_prompt",
            )
            db.add(run)

            # flush 让我们拿到 run.id，以便在 Message 里引用。
            # ⚠️ flush 只是把 SQL 发到 DB，事务尚未 commit，
            #    并发场景下另一个事务看不到这条记录。
            await db.flush()

            user_message = Message(
                thread_id=thread_id,
                run_id=run.id,
                role=MessageRole.USER.value,
                content=req.prompt,
                status=MessageStatus.SUCCESS.value,
            )
            db.add(user_message)

            # 更新 Thread.updated_at
            thread.updated_at = utc_now()

            # --------------------------------------------------------
            # 3.5 DB Commit + IntegrityError 三段分流
            # --------------------------------------------------------
            #
            # 需要分流的三种情况：
            #
            #   (A) 同 key 并发 → `uq_runs_idempotency_key` 触发
            #       → 另一并发请求已成功，返回它的 Run（幂等重放）。
            #
            #   (B) 不同 key 但同 Thread 已有活跃 Run
            #       → `uq_runs_thread_active_run` 触发
            #       → 返回 409。
            #
            #   (C) 其他完整性错误（外键、CHECK、未来新增约束等）
            #       → 不能吞，必须 re-raise，方便排查。
            #
            # 判定顺序：(A) → (B) → (C)。
            # 先查 (A) 是因为同 key 并发时也可能同时触发 (B)，
            # 但业务语义上应优先视为幂等重放。
            #
            try:
                await db.commit()

            except IntegrityError as exc:
                # rollback 必须立刻执行，否则 session 不可用。
                await db.rollback()

                # ---------- (A) 同 key 并发 → 幂等返回 ----------
                existing_run = None
                if req.idempotency_key:
                    existing_run = (
                        await db.exec(
                            select(Run).where(
                                Run.user_id == user_id,
                                Run.idempotency_key == req.idempotency_key,
                            )
                        )
                    ).first()

                if existing_run is not None:
                    if existing_run.thread_id != thread_id:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail={
                                "code": "IDEMPOTENCY_KEY_MISMATCH",
                                "message": "idempotency_key 已属于其他 Thread",
                            },
                        )
                    response.status_code = status.HTTP_200_OK
                    return _build_run_response(existing_run)

                # ---------- (B) 同 Thread 已有活跃 Run ----------
                active_run = (
                    await db.exec(
                        select(Run).where(
                            Run.thread_id == thread_id,
                            Run.status.in_(ACTIVE_RUN_STATUSES),
                        )
                    )
                ).first()

                if active_run is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "THREAD_HAS_ACTIVE_RUN",
                            "message": "当前 Thread 已存在正在执行的 Run",
                            "run_id": str(active_run.id),
                            "run_status": active_run.status,
                        },
                    )

                # ---------- (C) 其他 IntegrityError ----------
                # 例如外键失败、NOT NULL、CHECK、未来新增的唯一索引。
                # 这不是业务冲突，不能吞成 409，必须向上抛让监控报警。
                logger.exception(
                    "Unexpected IntegrityError when creating run. "
                    "thread_id=%s user_id=%s idempotency_key=%s",
                    thread_id,
                    user_id,
                    req.idempotency_key,
                )
                raise

            # --------------------------------------------------------
            # 3.6 TaskIQ 入队
            # --------------------------------------------------------
            #
            # 到这里 Run 已落库为 queued。
            # 如果入队失败，不能删除 Run（它已是业务事实），
            # 而应把它标记为 failed，让用户看到错误。
            #
            # ⚠️ 遗留风险：
            #   客户端在收到 503 后用同一个 idempotency_key 重试时，
            #   会命中幂等分支，拿到这个 failed 的 Run。
            #   建议客户端在 503 时换新 key 重试，
            #   或改用 Outbox 模式异步投递（见文末说明）。
            #
            try:
                task = await execute_agent_run.kiq(
                    run_id=str(run.id),
                    thread_id=str(thread_id),
                    resume=False,
                    resolution=None,
                )

                run.task_id = task.task_id
                await db.commit()

            except Exception as exc:
                logger.exception(
                    "Failed to enqueue Agent Run: run_id=%s", run.id
                )

                # 用一个新的短事务标记失败，避免污染已 commit 的状态。
                run.status = RunStatus.FAILED.value
                run.error_code = "TASK_ENQUEUE_FAILED"
                run.error_message = str(exc)
                run.finished_at = utc_now()
                await db.commit()

                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "TASK_ENQUEUE_FAILED",
                        "message": "Agent 任务提交失败，请稍后重试",
                    },
                )

            # --------------------------------------------------------
            # 3.7 返回
            # --------------------------------------------------------
            return _build_run_response(run)

    finally:
        # ============================================================
        # 阶段四：释放锁
        # ============================================================
        #
        # ⚠️ 前提：RedisDistributedLock.release() 必须使用 Lua
        #    check-and-delete，避免误删别人的锁（见阶段二的注释）。
        #
        await lock.release()


# ============================================================
# 辅助：构造统一响应
# ============================================================
def _build_run_response(run: Run) -> RunCreateResponse:
    """
    把 Run ORM 对象转换成对外响应结构。

    单独抽出来，是为了：
      - 幂等重放分支与正常分支共用同一份逻辑，避免字段不一致；
      - stream_url 拼接只在一处维护。
    """
    return RunCreateResponse(
        thread_id=run.thread_id,
        run_id=run.id,
        status=run.status,
        stream_url=(
            f"/api/threads/{run.thread_id}/runs/{run.id}/stream"
        ),
    )
```

### 问：我感觉这段代码中存在很多重复性检测，也就是check then act的代码很多，似乎没有必要，通过分布式锁加上数据库唯一索引兜底即可。不知道我感觉对不对

你的直觉非常敏锐。**这段代码确实存在明显的“过度设计（Over-Engineering）”和冗余的 DB Roundtrip**，出现了典型的“既要悲观 Check、又要锁、又要唯一索引、失败后还回查 DB 猜测原因”的防御套娃。



一个健壮的后端并发设计应该讲求**职责单一**和**网络往返（RTT）最小化**。



### 哪些检查确实多余（应当精简）

**1. `IntegrityError` 捕获后的“反查数据库猜原因”完全多余**



- **现状**：代码在 `commit()` 报错后，分别执行了两次 `db.exec(select(...))` 去尝试命中到底是哪个约束冲突。
- **弊端**：白白增加了 1~2 次数据库网络往返。
- **解法**：PostgreSQL 会在异常中明确抛出违反的约束名（Constraint Name）。在 `IntegrityError.orig`（无论是 asyncpg 还是 psycopg）中直接读取 `constraint_name` 即可精确判断是 `uq_runs_idempotency_key` 还是 `uq_runs_thread_active_run`，根本不需要再次发 SQL 查询。

**2. 活跃 Run 的前置检查（3.3）与 DB 部分索引职责重叠**



- **现状**：在锁内先执行一次 `select(Run).where(Run.status.in_(ACTIVE_RUN_STATUSES))`，如果报错再走索引兜底。
- **弊端**：有了分布式锁的保护，绝大多数正常请求进入临界区时根本没有并发冲突；即使分布式锁偶发失效，PostgreSQL 的部分唯一索引 `uq_runs_thread_active_run` 也是硬性物理约束。
- **解法**：直接信任 DB 的部分唯一索引。省掉这次 SELECT，让 DB 在 `commit` 时通过约束报错，捕获后直接按冲突名抛出 409。

**3. `await db.flush()` 完全可以消灭**



- **现状**：为了拿到 `run.id` 给 `user_message.run_id` 赋值，强行执行了一次 `flush()`。
- **解法**：`Run.id` 如果是 UUID，完全可以在 Python 端直接生成（`run_id = uuid.uuid4()`），赋值给两个对象后一次性 `add` 并 `commit`，直接砍掉一次 DB RTT。

### 哪些检查必须保留（不能无脑全删）

如果把所有 Check 都删掉、完全只靠“加锁 + INSERT 唯一索引”，会踩入以下两个陷阱：



- **阶段一的“无锁越权检查”必须保留（防 Lock-DoS）**
  - 如果删掉无锁查库，直接拿 `thread_id` 抢锁，恶意攻击者只需遍历/随机生成 UUID 请求接口，就能抢占任意 Thread 的 Redis 锁，让合法所有者被 429 阻断（跨用户拒绝服务攻击）。
- **幂等 key 的前置查询建议保留（服务正常重试）**
  - 客户端网络抖动后的“合法幂等重试”（比如用户断网重发），查到已有 Run 直接返回 200。如果直接依赖 DB INSERT 抛冲突再处理，会导致每次正常重试都经历一次事务中断与回滚，污染事务指标并消耗序列/写资源。

### 精简后的网络交互对比

| **阶段**           | **原始代码 DB/Redis 交互**                           | **精简后优化方案**                             |
| ------------------ | ---------------------------------------------------- | ---------------------------------------------- |
| **阶段 1：鉴权**   | 1 次 DB 查询 (`get`)                                 | 1 次 DB 查询 (`get`)                           |
| **阶段 2：加锁**   | 1 次 Redis `SET NX`                                  | 1 次 Redis `SET NX`                            |
| **阶段 3：临界区** | 4 次 DB 交互 (读 Thread + 查幂等 + 查活跃 + `flush`) | **1 次 DB 查询** (查幂等，可顺带带出 Run)      |
| **阶段 4：提交**   | 1 次 `commit()`；冲突时追加 **2 次回查 SELECT**      | 1 次 `commit()`；冲突时**仅解析异常元数据**    |
| **总 DB 往返**     | **5 ~ 7 次**                                         | **2 次**（大幅降低高并发下的连接池排队与延迟） |

### 优化后的核心实现范式

通过**Python 端预生成 UUID** + **精简临界区查询** + **解析 PostgreSQL 约束名**，代码可压缩得非常干练：



Python

```
# 1. 阶段一：前置鉴权（防御跨用户抢锁 DoS）
async with get_session() as db:
    thread = await db.get(Thread, thread_id)
    if not thread or thread.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "THREAD_NOT_FOUND", "message": "Thread 不存在"},
        )
    if thread.status != ThreadStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "THREAD_NOT_ACTIVE", "message": "Thread 当前不可执行"},
        )

# 2. 阶段二：抢锁
lock = RedisDistributedLock(redis_client, key=f"lock:thread:{thread_id}", ttl=30)
if not await lock.acquire():
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "THREAD_LOCK_BUSY", "message": "当前会话正在处理其他请求"},
    )

try:
    async with get_session() as db:
        # 3.1 仅针对带幂等 key 的请求查重（纯正幂等分支）
        if req.idempotency_key:
            stmt = select(Run).where(
                Run.user_id == user_id, Run.idempotency_key == req.idempotency_key
            )
            existing_run = (await db.exec(stmt)).first()
            if existing_run:
                if existing_run.thread_id != thread_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={"code": "IDEMPOTENCY_KEY_MISMATCH", "message": "Key 已属于其他 Thread"},
                    )
                response.status_code = status.HTTP_200_OK
                return _build_run_response(existing_run)

        # 3.2 本地生成 UUID，一次性组装对象，去掉 flush() 和 active_run 的多余查询
        new_run_id = uuid.uuid4()
        run = Run(
            id=new_run_id,
            thread_id=thread_id,
            user_id=user_id,
            idempotency_key=req.idempotency_key,
            scope=req.scope,
            scope_id=req.scope_id,
            status=RunStatus.QUEUED.value,
            trigger_type="user_prompt",
        )
        user_message = Message(
            thread_id=thread_id,
            run_id=new_run_id,
            role=MessageRole.USER.value,
            content=req.prompt,
            status=MessageStatus.SUCCESS.value,
        )
        db.add(run)
        db.add(user_message)

        # 3.3 Commit 并通过约束名精准分流
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            # 获取 PostgreSQL 原生约束名（适配 asyncpg / psycopg）
            diag = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or str(exc.orig)

            if "uq_runs_thread_active_run" in diag:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "THREAD_HAS_ACTIVE_RUN", "message": "当前 Thread 已存在活跃的 Run"},
                )
            if "uq_runs_idempotency_key" in diag:
                # 极端锁失效下偶发的并发同 key 写入
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "CONCURRENT_REQUEST", "message": "请求冲突，请稍后重试"},
                )
            logger.exception("Unexpected IntegrityError on create_run: %s", exc)
            raise

        # 3.4 任务投递
        # ... 后续 TaskIQ 入队逻辑 ...
finally:
    await lock.release()
```

保留防 DoS 的前置鉴权与正常幂等分支，去除内存快照的重复检查与异常后的盲查 SQL，整体复杂度与数据库连接负担可减少约 60%。

### 问：还有一块是这是我的数据库表定义，它似乎本来就能自动生成UUID

```python

# 一个会话窗口可以有多轮对话
class Run(SQLModel, table=True):
    """
    Agent 的一次具体执行
    scope / scope_id 属于 Run, 而不是 Thread
    原因：
        一个 Thread 可以连续产生多个 Run
        每个 Run 可以选择不同的知识库
    例如：
        Thread T1
            Run R1 -> KB-A
            Run R2 -> KB-B
            Run R3 -> KB-A
    """

    __tablename__ = "run"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # 当前运行和会话窗口关联
    thread_id: uuid.UUID = Field(foreign_key="thread.id", index=True)
    idempotency_key: str | None = Field(default_factory=None, max_length=128, unique=True, index=True)
    user_id: int = Field(index=True)
    # 本次Run使用的 数据域 / 知识库
    scope: str = Field(index=True, max_length=32)
    scope_id: str = Field(index=True, max_length=64)
    # 运行状态，有queued / in_progress / requires_action / completed / failed / cancelled
    status: str = Field(default="queued", index=True)
    trigger_type: str = Field(default="user_prompt", max_length=32)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = None
    token_usage: dict[str, Any] = Field(default=None, sa_column=Column(JSONB))
    created_at: AwareCreatedAt
    started_at: AwareNullableDateTime = None
    finished_at: AwareNullableDateTime = None
    # 用户进行一次run的请求面临两层并发安全
    # 1、同一幂等键请求的并发重复
    # 2、不同幂等键请求的并发重复
    __table_args__ = (
        Index("idx_runs_thread_status", "thread_id", "status"),
        Index("idx_runs_scope", "scope", "scope_id"),
        # 幂等唯一约束。
        #
        # 同一个用户：
        # idempotency_key = X
        #
        # 只能对应一个 Run。
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_runs_user_idempotency_key",
        ),

        # 一个 Thread 同一时间最多允许一个 active Run。
        #
        # 这是最终 DB 兜底。
        #
        # Redis thread lock 用于降低并发冲突，
        # DB partial unique index 用于保证最终正确性。
        Index(
            "uq_active_run_per_thread",
            "thread_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'in_progress', 'requires_action')"
            ),
        ),
    )

```

是的，你的判断完全准确！



在 SQLModel / Pydantic 中，只要声明了 `default_factory=uuid.uuid4`，**UUID 是在 Python 内存实例化这一瞬间由客户端生成的**，根本不需要数据库来分配。



在原代码中：



Python

```
run = Run(...)          # 此时 run.id 已经在 Python 内存中生成好了！
db.add(run)
await db.flush()        # 👈 完全多此一举，纯属白白增加一次 DB 网络往返
user_message = Message(run_id=run.id, ...)
```

直接写成如下方式即可：



Python

```
run = Run(
    thread_id=thread_id,
    user_id=user_id,
    idempotency_key=req.idempotency_key,
    scope=req.scope,
    scope_id=req.scope_id,
    status=RunStatus.QUEUED.value,
    trigger_type="user_prompt",
)
user_message = Message(
    thread_id=thread_id,
    run_id=run.id,  # 直接读取，已有值
    role=MessageRole.USER.value,
    content=req.prompt,
    status=MessageStatus.SUCCESS.value,
)
db.add_all([run, user_message])
await db.commit()  # 无需 flush，直接提交
```

顺着你的表结构定义，还有 **两个非常致命的隐患** 需要立即纠正：



### 1. `idempotency_key` 存在单列全局唯一与联合唯一的冲突

看这一行定义：



Python

```
idempotency_key: str | None = Field(default_factory=None, max_length=128, unique=True, index=True)
```

这里潜伏了两个问题：



- **语法隐患**：`default_factory` 接收的是可调用对象（如 `uuid.uuid4`），传 `None` 属于误用，默认值应写为 `default=None`。
- **逻辑冲突（严重）**：你在字段声明了 `unique=True`，同时又在 `__table_args__` 中声明了 `UniqueConstraint("user_id", "idempotency_key")`。
  - `Field(unique=True)` 会在数据库生成针对 `idempotency_key` **单列的全局唯一约束**。
  - 这会导致：如果不同用户（用户 A 和用户 B）前端生成了重名的 key（例如 UUID 碰撞或简单的业务流水号），就会报唯一键冲突。
  - **修复方式**：去掉 `Field` 里的 `unique=True`，只保留 `__table_args__` 里的 `(user_id, idempotency_key)` 联合唯一约束。

### 2. 约束名与路由层捕获字符串不一致

如果后续要精简代码、通过捕获异常中的约束名直接分流错误，必须保证 **模型中的约束名** 与 **代码中匹配的字符串** 严格对齐：



| **项目**              | **原路由代码中假定的名字**  | **当前模型实际定义的名字**     | **影响**                            |
| --------------------- | --------------------------- | ------------------------------ | ----------------------------------- |
| **幂等唯一约束**      | `uq_runs_idempotency_key`   | `uq_runs_user_idempotency_key` | 字符串匹配失败，落入 `raise` 报 500 |
| **活跃 Run 部分索引** | `uq_runs_thread_active_run` | `uq_active_run_per_thread`     | 字符串匹配失败，用户收不到友好 409  |

### 建议修正后的 Model 定义

Python

```
from __future__ import annotations

import uuid
from typing import Any
from sqlalchemy import Column, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Run(SQLModel, table=True):
    __tablename__ = "run"

    # Python 内存层自动生成 UUID，无需 flush
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    thread_id: uuid.UUID = Field(foreign_key="thread.id", index=True)
    
    # 修正：改用 default=None，去掉单列 unique=True，唯一性由联合约束兜底
    idempotency_key: str | None = Field(default=None, max_length=128, index=True)
    user_id: int = Field(index=True)
    scope: str = Field(index=True, max_length=32)
    scope_id: str = Field(index=True, max_length=64)
    status: str = Field(default="queued", index=True)
    trigger_type: str = Field(default="user_prompt", max_length=32)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = None
    token_usage: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))
    
    # 时间字段按你的实际类型定义
    created_at: AwareCreatedAt
    started_at: AwareNullableDateTime = None
    finished_at: AwareNullableDateTime = None

    __table_args__ = (
        Index("idx_runs_thread_status", "thread_id", "status"),
        Index("idx_runs_scope", "scope", "scope_id"),
        # (user_id, idempotency_key) 联合唯一
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_runs_user_idempotency_key",
        ),
        # 一个 Thread 仅允许一个活跃状态的 Run
        Index(
            "uq_runs_thread_active_run",  # 建议名字与业务语义、路由代码保持完全一致
            "thread_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'in_progress', 'requires_action')"
            ),
        ),
    )
```

这样改完后，不仅 Python 端实例化即有 `id`，而且数据库层的约束逻辑与异常拦截也能精确闭环。

### 问：还有idempotency_key应不应该设置可为空？

**数据库层面应该设置为可为空（`Nullable`），但在 API 协议层面根据你的业务场景决定是否必填。**



把“数据库字段可空”与“接口参数必填”解耦，是分布式系统中最稳妥的设计。



### 为什么数据库层面必须允许 `NULL`？

- **多触发源解耦（`trigger_type`）**

  表中已有 `trigger_type` 字段（当前是 `"user_prompt"`）。未来系统演进很可能会引入系统调度（`scheduled`）、Webhook 触发、工作流子任务执行或管理员后台重放。这些内部调用往往不需要客户端的业务幂等键；如果 DB 强制 `NOT NULL`，会迫使内部逻辑伪造无意义的垃圾 Key。

- **开发与排查友好**

  在本地开发、联调、Swagger / cURL 手动发起一次性测试时，强制传 Key 会增加调用阻力。

- **PostgreSQL 对 `NULL` 的唯一性处理很成熟**

  在 PostgreSQL 默认规则下，`NULL != NULL`。同一个用户的多条 `idempotency_key = NULL` 记录不会触发唯一性冲突。

### ⚠️ 一个绝对不要踩的反模式：服务端兜底生成 UUID

有些设计为了在 DB 维持 `NOT NULL`，会写成：



Python

```
# ❌ 错误做法：没有起到任何防重作用
idempotency_key = req.idempotency_key or str(uuid.uuid4())
```

这是自欺欺人的“伪幂等”：如果前端因为超时重试了一次未带 key 的请求，服务端两次都会生成不同的 UUID，依然会并发创建出两个重复的 Run。**没有就是没有，落库记录为 `NULL` 是最真实的状态。**



### 索引层面的最佳实践：使用部分唯一索引（Partial Index）

如果在模型中直接写普通的 `UniqueConstraint("user_id", "idempotency_key")`，PostgreSQL 虽然允许存在多个 NULL，但仍会把这些 NULL 值作为索引项记入 B-Tree。



如果未来大量请求不带 key，会导致索引膨胀。**最优雅的解法是在数据库创建部分唯一索引（Partial Unique Index）：**



Python

```
# 在 Run 模型的 __table_args__ 中替代原有的 UniqueConstraint:

Index(
    "uq_runs_user_idempotency_key",
    "user_id",
    "idempotency_key",
    unique=True,
    postgresql_where=text("idempotency_key IS NOT NULL"),  # 👈 仅对非 NULL 值做唯一约束
),
```

这样不仅完全消除了 NULL 带来的索引开销，而且语义非常纯粹：**只要传了 key 就必须保证用户下唯一；没传 key 则不进入防重索引。**



### 业务层（API）如何决定是否必填？

数据库放开为 `Nullable` 之后，在 Pydantic 的 `RunCreateRequest` 中做控制：



- **如果应用属于对成本极敏感、严格杜绝重复消费的场景（如涉及扣费、耗时长的大模型推理）：**

  在 `RunCreateRequest` 中将其设为必填（`idempotency_key: str`），要求前端 SDK（如 Axios 拦截器）每次发请求前自动生成一个 UUIDv4 放到 Header 或 Body。

- **如果属于通用开发接口（对标 OpenAI / Stripe 标准）：**

  设为可选（`idempotency_key: str | None = None`）。客户端传了就提供并发去重与 200 重放保护，不传就走普通的创建流程。