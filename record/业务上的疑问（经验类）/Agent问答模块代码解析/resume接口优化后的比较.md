```
@router.post(
    "/threads/{thread_id}/runs/{run_id}/resume",
    response_model=ResumeResponse,
)async def resume_run(
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    req: ResumeRequest,
):
    """
    恢复一个 requires_action 状态的 Run。
    """
    user_id = get_current_user_id()

    lock = RedisDistributedLock(
        redis_client,
        key=f"lock:thread:{thread_id}",
        ttl=30,
    )

    if not await lock.acquire():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前会话正在执行其他操作",
        )

    try:
        from app.core.database import get_session

        async with get_session() as db:

            # ------------------------------------------------
            # 1. Thread 鉴权
            # ------------------------------------------------

            thread = await db.get(
                Thread,
                thread_id,
            )

            if thread is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Thread 不存在",
                )

            if thread.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="无权访问该 Thread",
                )

            # ------------------------------------------------
            # 2. 查询 Run
            # ------------------------------------------------

            run = await db.get(
                Run,
                run_id,
            )

            if run is None or run.thread_id != thread_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Run 不存在",
                )

            if run.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="无权访问该 Run",
                )

            if run.status != RunStatus.REQUIRES_ACTION.value:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"当前 Run 状态为 {run.status}，"
                        "不能执行 resume"
                    ),
                )

            # ------------------------------------------------
            # 3. 原子解决 pending Interrupt
            # ------------------------------------------------

            now = datetime.now(timezone.utc)

            result = await db.exec(
                update(Interrupt)
                .where(
                    Interrupt.run_id == run_id,
                    Interrupt.thread_id == thread_id,
                    Interrupt.status
                    == InterruptStatus.PENDING.value,
                )
                .values(
                    status=InterruptStatus.RESOLVED.value,
                    resolution=req.resolution,
                    resolved_by=user_id,
                    resolved_at=now,
                )
            )

            if result.rowcount != 1:
                await db.rollback()

                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="该 Interrupt 已经被处理",
                )

            # ------------------------------------------------
            # 4. 将 human action 写入 assistant message parts
            # ------------------------------------------------

            assistant_result = await db.exec(
                select(Message).where(
                    Message.run_id == run_id,
                    Message.role == MessageRole.ASSISTANT.value,
                )
            )

            assistant_message = assistant_result.first()

            if assistant_message is None:
                assistant_message = Message(
                    thread_id=thread_id,
                    run_id=run_id,
                    role=MessageRole.ASSISTANT.value,
                    content="",
                    parts=[],
                    status=MessageStatus.PENDING.value,
                )

                db.add(assistant_message)

            if assistant_message.parts is None:
                assistant_message.parts = []

            assistant_message.parts.append(
                {
                    "type": "human_action",
                    "action": req.resolution,
                    "created_at": now.isoformat(),
                }
            )

            # ------------------------------------------------
            # 5. Run 重新进入 queued
            # ------------------------------------------------

            run.status = RunStatus.QUEUED.value
            run.error_code = None
            run.error_message = None
            run.finished_at = None

            await db.commit()

            # ------------------------------------------------
            # 6. 重新提交 TaskIQ
            # ------------------------------------------------

            try:
                task = await execute_agent_run.kiq(
                    run_id=str(run_id),
                    thread_id=str(thread_id),
                    resume=True,
                    resolution=req.resolution,
                )

                run.task_id = task.task_id

                await db.commit()

            except Exception as exc:
                logger.exception(
                    "Failed to enqueue resumed Run: %s",
                    run_id,
                )

                run.status = RunStatus.FAILED.value
                run.error_code = "RESUME_ENQUEUE_FAILED"
                run.error_message = str(exc)
                run.finished_at = datetime.now(timezone.utc)

                await db.commit()

                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="恢复任务提交失败",
                )

            # ------------------------------------------------
            # 7. Redis Stream event
            # ------------------------------------------------

            await redis_client.xadd(
                f"agent:stream:{run_id}",
                {
                    "type": "human_action",
                    "run_id": str(run_id),
                    "data": json.dumps(
                        req.resolution,
                        ensure_ascii=False,
                    ),
                },
            )

            return ResumeResponse(
                thread_id=thread_id,
                run_id=run_id,
                status=run.status,
            )

    finally:
        await lock.release()
上面这段代码和下面这段代码
from datetime import datetime, timezone
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select, update

from app.core.database import get_session
from app.core.redis import redis_client
from app.models.chat import Interrupt, Run
from app.models.enums import InterruptStatus, RunStatus
from app.schemas.run import ResumeRequest, ResumeResponse
from app.tasks.agent import execute_agent_run
from app.utils.auth import get_current_user_id
from app.utils.lock import RedisDistributedLock

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post(
    "/threads/{thread_id}/runs/{run_id}/resume",
    response_model=ResumeResponse,
)
async def resume_run(
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    req: ResumeRequest,
):
    """
    恢复一个处于 requires_action 状态的 Run。
    职责说明：
    - API 仅负责控制面（权限校验、会话互斥加锁、更新 Run/Interrupt 状态、下发异步任务）。
    - 数据面（将输入回填为 Tool 结果或生成新消息）交由 Worker 恢复运行时单点写入，避免数据重复。
    """
    user_id = get_current_user_id()
    now = datetime.now(timezone.utc)

    # ------------------------------------------------------------
    # 1. 锁前预检（只读）：静态归属鉴权，防未授权恶意请求抢占会话锁（防 DoS）
    # ------------------------------------------------------------
    async with get_session() as db:
        run = await db.get(Run, run_id)
        if not run or run.thread_id != thread_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Run 不存在",
            )
        if run.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权操作该资源",
            )

    # ------------------------------------------------------------
    # 2. 会话级分布式互斥锁
    # ------------------------------------------------------------
    lock = RedisDistributedLock(
        redis_client,
        key=f"lock:thread:{thread_id}",
        ttl=30,
    )
    if not await lock.acquire():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前会话正在执行其他操作",
        )

    try:
        # --------------------------------------------------------
        # 3. 核心事务：权威状态校验 + CAS 跃迁 + 纯更新 Interrupt 表
        # --------------------------------------------------------
        async with get_session() as db:
            async with db.begin():
                # 3.1 锁内读取权威动态状态，杜绝 TOCTOU 竞态
                current_run = await db.get(Run, run_id)
                if current_run.status != RunStatus.REQUIRES_ACTION.value:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"当前 Run 状态为 {current_run.status}，不可执行 resume 操作",
                    )

                # 3.2 乐观锁 CAS 跃迁状态 (REQUIRES_ACTION -> QUEUED)
                run_res = await db.exec(
                    update(Run)
                    .where(
                        Run.id == run_id,
                        Run.status == RunStatus.REQUIRES_ACTION.value,
                    )
                    .values(
                        status=RunStatus.QUEUED.value,
                        error_code=None,
                        error_message=None,
                        finished_at=None,
                    )
                )
                if run_res.rowcount != 1:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Run 状态已被并发修改",
                    )

                # 3.3 锁定并标记挂起的中断实体
                interrupt_stmt = select(Interrupt).where(
                    Interrupt.run_id == run_id,
                    Interrupt.thread_id == thread_id,
                    Interrupt.status == InterruptStatus.PENDING.value,
                )
                interrupt = (await db.exec(interrupt_stmt)).first()
                if not interrupt:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="未找到挂起状态的中断操作（可能已被处理）",
                    )

                interrupt.status = InterruptStatus.RESOLVED.value
                interrupt.resolution = req.resolution
                interrupt.resolved_by = user_id
                interrupt.resolved_at = now

                interrupt_id_str = str(interrupt.id)

        # --------------------------------------------------------
        # 4. 事务提交后执行外部副作用（网络 I/O 隔离） + 异常补偿机制
        # --------------------------------------------------------
        try:
            task = await execute_agent_run.kiq(
                run_id=str(run_id),
                thread_id=str(thread_id),
                resume=True,
                resolution=req.resolution,
            )

            # 回填任务 ID
            async with get_session() as db:
                await db.exec(
                    update(Run)
                    .where(Run.id == run_id)
                    .values(task_id=task.task_id)
                )
                await db.commit()

        except Exception as exc:
            logger.exception("Failed to enqueue resumed Run: %s", run_id)
            # 补偿：防止任务下发失败导致 Run 永久僵死在 QUEUED 状态
            async with get_session() as db:
                await db.exec(
                    update(Run)
                    .where(Run.id == run_id)
                    .values(
                        status=RunStatus.FAILED.value,
                        error_code="RESUME_ENQUEUE_FAILED",
                        error_message=str(exc),
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()

            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="恢复任务提交失败，已标记为失败状态",
            )

        # --------------------------------------------------------
        # 5. 事件流广播（前端 UI 接收中断恢复响应）
        # --------------------------------------------------------
        await redis_client.xadd(
            f"agent:stream:{run_id}",
            {
                "type": "human_action",
                "run_id": str(run_id),
                "interrupt_id": interrupt_id_str,
                "data": json.dumps(req.resolution, ensure_ascii=False),
            },
        )

        return ResumeResponse(
            thread_id=thread_id,
            run_id=run_id,
            status=RunStatus.QUEUED.value,
        )

    finally:
        await lock.release()

```

### 问：哪个更好？

**第二段代码明显更好**。它在架构设计、并发安全、防御性编程和事务隔离上都远优于第一段代码。



### 核心差异对比

| **维度**          | **第一段代码**                                           | **第二段代码（更好）**                                       |
| ----------------- | -------------------------------------------------------- | ------------------------------------------------------------ |
| **架构职责边界**  | **混乱**：API 强行介入数据面，手动拼接 `Message.parts`。 | **清晰**：API 仅做控制面流转，具体消息回填交由 Worker 处理。 |
| **防 DoS 锁竞争** | **差**：未鉴权直接抢 Redis 锁，易被未授权请求恶意挂锁。  | **优**：锁前只读预检，拦截未授权恶意请求。                   |
| **并发事务处理**  | 手动 `commit()` 多次，容易出现半提交状态。               | 严格使用 `async with db.begin()` 配合 CAS 原子跃迁。         |
| **潜在 Bug 隐患** | 直接 `parts.append()` 存在 SQLAlchemy 脏追踪失效问题。   | 不在 API 侧触碰 JSON 字段变更，杜绝脏写。                    |
| **可观测性**      | Stream 仅下发 resolution。                               | Stream 携带 `interrupt_id`，前端状态对齐更精确。             |

### 为什么第二段代码更好

**1. 职责解耦：控制面与数据面分离**



- **第一段代码的问题**：在 API 中直接获取 `assistant_message` 并往 `parts` 插入 `human_action`。这导致 API 承担了本属于 Agent 运行时的数据组装逻辑。一旦后续支持不同类型的中断（如表单审批、多工具调用），API 逻辑会迅速腐化膨胀。
- **第二段代码的优势**：API 只负责验证权限、标记 Interrupt 已解决、更新 Run 状态为 `QUEUED` 并提交 TaskIQ。Worker 消费任务启动时，才是唯一有权写入对话上下文（Conversation History）的地方，避免了 API 与 Worker 的并发写冲突。

**2. 规避了 SQLAlchemy JSON 字段的静默更新 Bug**



- 第一段代码中的 `assistant_message.parts.append(...)` 是典型的 SQLAlchemy 反模式。
- 对于 PostgreSQL 的 `JSON` 或 `JSONB` 字段，直接 `append` 内存列表**不会触发 ORM 的属性变更追踪（Dirty Flag）**。在很多情况下，后面的 `db.commit()` 根本不会把新增的 part 写回数据库，除非显式使用 `flag_modified(assistant_message, "parts")` 或重新赋值 `parts = [*parts, new_part]`。

**3. 锁前预检防范锁耗尽（Anti-DoS）**



- **第一段**：只要有请求打入，先加 Redis 锁，再查数据库鉴权。攻击者或无权客户端传入真实的 `thread_id` 和任意 `run_id`，就能将该会话锁死 30 秒，导致正常用户无法操作。
- **第二段**：先进行无锁只读查询确认归属权，通过后再抢占分布式锁，有效保护了临界资源。

**4. 事务隔离与异常补偿更规范**



- 第二段代码将数据库状态变更封装在 `async with db.begin()` 块中，并在事务彻底提交后再向 TaskIQ 提交任务，实现了**数据库 I/O 与网络/消息队列 I/O 的隔离**。
- 在 TaskIQ 投递失败时，第二段代码在独立的 session 中将 Run 兜底标记为 `FAILED`，避免了任务既未进队列、数据库又停留在 `QUEUED` 的死锁僵尸状态。

### 第二段代码可进一步微调的细节

第二段代码整体结构很优秀，但存在一处轻微的性能冗余：



Python

```
# 3.1 已经通过 db.get 读取了 current_run
current_run = await db.get(Run, run_id)
if current_run.status != RunStatus.REQUIRES_ACTION.value:
    ...

# 3.2 紧接着又执行了一次带条件的 update CAS
run_res = await db.exec(
    update(Run)
    .where(
        Run.id == run_id,
        Run.status == RunStatus.REQUIRES_ACTION.value,
    )
    .values(...)
)
```

**建议**：在持有分布式锁的前提下，直接执行 3.2 的 CAS `update`。根据 `run_res.rowcount == 0` 直接判断并抛出 409 异常，可省去 3.1 的一次 `SELECT` 往返。

### 问：不需要check then act操作进行一次过滤是吗？虽然不靠谱

**完全不需要在锁内单独写一次 `SELECT` 去做“Check-then-Act”，直接用一条带条件的原子 `UPDATE`（CAS）替代是更成熟、高效的做法。**



这里需要把代码里的两种“检查”拆开来看：



### 1. 锁内的状态过滤（Step 3.1）为什么可以坚决干掉？

你在锁内写的：



Python

```
# Check
current_run = await db.get(Run, run_id)
if current_run.status != RunStatus.REQUIRES_ACTION.value:
    raise ...

# Act
await db.exec(update(Run).where(Run.status == ...).values(...))
```

这种做法有两个弊端：



- **额外的网络往返（Round-trip Time）**：为了查一个状态，平白多了一次数据库 `SELECT` 的 I/O 延迟。
- **语义重复**：SQL 自身的 `UPDATE ... WHERE status = 'REQUIRES_ACTION'` **本身就是一个在数据库行锁级完成的原子的 Check-and-Set (CAS)**。数据库在执行这一行时，会自动判断并加行写锁。

#### 优雅的平替方案：CAS 驱动 + 失败后按需补查

很多人之所以喜欢先 `SELECT`，是为了在报错时告诉用户当前具体是啥状态（例如：“当前是 RUNNING 状态，无法恢复”）。这可以通过**正常路径 1 次 IO，异常路径才补查**来兼顾性能与可读性：



Python

```
# 直接原子跃迁（既是 Check，也是 Act）
result = await db.exec(
    update(Run)
    .where(
        Run.id == run_id,
        Run.status == RunStatus.REQUIRES_ACTION.value,
    )
    .values(status=RunStatus.QUEUED.value, ...)
)

# 99.9% 的正常请求到这里直接过，省掉一次 SELECT
if result.rowcount == 0:
    # 仅在发生冲突/非法状态时，才补查一次原因用于报错
    current_run = await db.get(Run, run_id)
    actual_status = current_run.status if current_run else "UNKNOWN"
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Run 状态已被修改（当前为 {actual_status}），不可执行 resume",
    )
```

### 2. 锁前的静态预检（Step 1）为什么“虽然不靠谱但必须留”？

你提到的“不靠谱”，通常指的是**锁前检查存在 TOCTOU（时间差竞态）**：锁前查出来状态是对的，等拿到分布式锁后，状态可能早就被别人改了。



确实如此，但 **锁前预检的目的从来不是为了保证状态权威性，而是为了“快速失败（Fail-Fast）”与“防 DoS”**：



- **防恶意占锁**：Redis 分布式锁是一个高竞争的临界资源。如果没有锁前过滤，黑客可以用脚本拿随机 UUID 或别的用户的 `thread_id` 疯狂发请求，瞬间占满 Redis 锁 30 秒，导致正常用户无法操作。
- **业务静态属性几乎不变**：`run.user_id`（归属人）和 `run.thread_id`（父子级）是不可变的只读元数据，锁前查和锁内查结果完全一致，在这层做鉴权百分之百靠谱。

- **锁前预检**：必须留，用来拦截非法身份和恶意请求，保护 Redis 锁；
- **锁内状态校验**：坚决剔除，直接交由 SQL `WHERE` 条件原子完成。

### 问：可是先 SELECT的话可以避免后续的update了

直觉上“先查一下能省掉一次写操作”，但在高并发与数据库性能工程中，这个逻辑恰好是相反的。



核心原因在于**网络往返开销（RTT）\**以及\**数据库对“零匹配 UPDATE”的真实处理成本**：



### 1. 热路径优先原则（Happy Path）

在正常业务中，99% 以上的请求都是合法的。



- **先 `SELECT` 后 `UPDATE`**：
  - **合法请求（99%）**：产生 **2 次网络往返（RTT）**（1 次查 + 1 次写），延迟翻倍，数据库连接池占用时间翻倍。
  - **非法请求（<1%）**：产生 1 次查。
- **直接原子 `UPDATE`**：
  - **合法请求（99%）**：仅需 **1 次网络往返（RTT）**，延迟直接减半。
  - **非法请求（<1%）**：仅需 1 次尝试写入，未命中直接返回 `rowcount = 0`。

为了节约不到 1% 的非法请求开销，让 99% 的正常请求承受双倍的数据库往返延迟，在架构上是不划算的。



### 2. 没匹配到的 `UPDATE`，数据库根本不会真正“写盘”

很多开发者误以为只要发了 `UPDATE`，数据库就会执行沉重的写盘和日志刷盘。实际上：



- 当执行 `UPDATE run SET status = ... WHERE id = ? AND status = 'REQUIRES_ACTION'` 时，数据库通过主键索引找到该行后，发现 `status` 条件不满足，**立刻直接返回更新行数为 0**。
- 此时**不会生成数据行的变更日志（WAL/Redo Log）**，**不会标记数据页为脏页（Dirty Buffer）**，更不会有磁盘 I/O。
- 它的实际消耗和一次只读的 `SELECT` 几乎没有本质区别。

### 3. 两种方案成本直观对比

| **场景**                        | **先 SELECT 后 UPDATE**       | **直接原子 UPDATE（CAS）**     |
| ------------------------------- | ----------------------------- | ------------------------------ |
| **正常恢复请求（绝大多数）**    | 2 次 DB IO（SELECT + UPDATE） | **1 次 DB IO（仅 UPDATE）**    |
| **非法/并发重复请求（极少数）** | 1 次 DB IO（SELECT 拦截）     | 1 次 DB IO（UPDATE 命中 0 行） |
| **高并发锁竞争**                | 查与改之间存在微小时间缝隙    | 单语句行级原子判定，无缝隙     |

只要建立了合适的索引，直接用带 `WHERE` 条件的 `UPDATE` 代替“先查后改”，永远是兼顾代码简洁性与极端并发性能的最优解。

### 问：网络开销比数据库执行开销还大吗

**是的。对于绝大多数经过索引优化的点查和单行更新，网络开销（RTT）往往是数据库自身执行耗时的 10 到 100 倍。**



在典型的云原生架构（如 Kubernetes、AWS RDS、阿里云 VPC）中，应用服务与数据库通常位于不同的主机或可用区。两者的耗时量级完全不在一个维度上：



### 耗时量级对比

| **阶段**            | **实际操作**                                               | **耗时量级**                     | **耗时占比** |
| ------------------- | ---------------------------------------------------------- | -------------------------------- | ------------ |
| **数据库内部执行**  | 主键索引 B+ 树查找、内存页比对条件、返回行计数             | **0.01 ~ 0.05 ms** (10 ~ 50 µs)  | **< 5%**     |
| **网络往返（RTT）** | TCP 协议栈、序列化/反序列化、网卡收发、跨机器/跨可用区延迟 | **0.5 ~ 2.0 ms** (500 ~ 2000 µs) | **> 95%**    |

对于 `WHERE id = ?` 这种走主键的简单语句，数据库的行级比对全在内存（Buffer Pool）中以纳秒到微秒级完成。从客户端发出的 SQL 耗时，**绝大部分时间都花在网线和网络协议栈的往返等待上**。



### 为什么网络往返会成为主因？

- **主键查找本质是纯内存操作**：活跃数据（如正在执行的 Run）基本 100% 命中数据库内存缓存。一次索引二分查找比对只要几十微秒，甚至比应用服务器 Python 解释器构造一个对象的耗时还要低。
- **网络往返包含了大量非执行损耗**：
  - 应用层 SQL 拼接与参数绑定；
  - 数据包在 TCP/IP 栈封装与解包；
  - Linux 系统的软中断与上下文切换；
  - 跨交换机、跨宿主机、甚至跨可用区（Cross-AZ 通常在 1ms 左右）的物理光纤传播延迟。
- **连接池阻塞效应（更严重的并发隐患）**：
  - 应用与数据库交互采用请求-响应模型。
  - 每多一次往返（`SELECT` 查一次，再 `UPDATE` 改一次），数据库连接被独占的时间直接**翻倍**。
  - 当高并发流量涌入时，连接池里的连接不是因为数据库 CPU 跑满而耗尽，而是全卡在“等待网络数据包传输”的闲置状态，导致系统吞吐量断崖式下跌。

### 何时“数据库开销”才会超过网络开销？

只有在以下重型负载下，数据库自身的耗时才会显著大于网络耗时：



- **全表扫描 / 未命中索引**：扫描上百万行数据，引发大量磁盘冷读取。
- **复杂联表与大聚合**：多表 `JOIN`、窗口函数、复杂的排序与分组运算。
- **等待行锁冲突**：多个并发事务死锁争抢同一行，排队等待锁释放。
- **慢磁盘刷盘**：机械硬盘环境下每次 `COMMIT` 导致的物理 WAL 写入阻塞。

在基于主键进行状态流转（CAS）的业务场景中，数据库计算开销几乎可以忽略不计，**减少网络交互次数（RTT）永远是收益最高的优化手段**。