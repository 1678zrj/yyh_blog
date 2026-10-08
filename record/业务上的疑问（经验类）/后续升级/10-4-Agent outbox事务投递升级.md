### 问：

```python
# 每次请求实际上都是进行一次Agent Run
@router.post(
    "/threads/{thread_id}/run",
    response_model=RunCreateResponse
)
async def create_chat_run(
        thread_id: uuid.UUID,
        req: RunCreateRequest,
        db: AsyncSession = Depends(get_session),
        redis: Redis = Depends(get_redis),
        current_user: User = Depends(get_current_user)
):
    # 1 进行鉴权
    # 鉴权放在分布式锁前面是因为分布式锁锁的是thread_id
    # 防止别的用户抢占属于当前用户的thread的锁
    existing_thread = await thread_crud.get_thread_by_id(db, thread_id)
    if existing_thread is None or existing_thread.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread不存在"
        )
    # 2获取 Thread 级分布式锁
    # 锁的粒度选择是Thread
    # 若选择 run， 则粒度太细， 无法阻止同一个 Thread 两个 Run 并发
    # 若选择 user, 则粒度太粗， 导致同一用户无法在两个或多个Thread中一起跑
    lock = RedisDistributedLock(
        redis,
        key=f"lock:thread:{thread_id}",
        ttl=30
    )
    # 两个几乎同时的并发请求过来枪锁，它们可能是同一请求，带有同一幂等键，也可能不同
    acquired = await lock.acquire()
    # 没有抢到锁的一方
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="当前会话正在处理其它请求，请稍后重试"
        )
    try:
        # 抢到锁的一方，先进行权限校验
        # 首先是Thread级别的校验
        existing_thread = await thread_crud.get_thread_by_id(db, thread_id)
        # 之前通过鉴权的时候还存在，但极小概率现在又被删除了
        if existing_thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread不存在"
            )
        # 当前Thread的状态，如果是活跃状态，则通过，反之如果是归档、删除状态，则报错
        if existing_thread.status != ThreadStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Thread 当前不可执行"
            )
        # Thread校验完毕后，开始对run进行校验
        # 首先是check then act 校验，这层校验无法兜底
        existing_run = await run_crud.get_run_by_user_idempotency_key(
            db,
            current_user.id,
            req.idempotency_key
        )
        # 当前已经有run存在了，说明已经有相同请求创建了run（因为幂等键相同）
        if existing_run is not None:
            # 这个run属于当前用户，并且和当前请求是统一幂等键
            # 但还有变数，这个run的thread_id相同和不同
            # 不同的情况说明当前用户的相同请求也发给了另一个会话窗口
            # 客户端误用 key：同一 key 指到了不同 Thread。
            # 必须明确拒绝，否则会把 A thread 的 run 返回给 B thread。
            if existing_run.thread_id != thread_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="当前请求已被其它会话Thread使用，请为新的请求生成新的幂等键"
                )
            # 运行到这里，说明当前已经有相同请求在该thread创建了run
            # 那么可以直接返回结果
            return _build_run_response(existing_run)
        # 运行到这里，说明当前幂等键对应的run还不存在
        # 我们的目的创建run
        # 但是在创建run之前，还得保证当前thread没有其它幂等键对应的活跃的run
        active_run = await run_crud.get_active_run_by_thread_id(db, thread_id)
        # 当前thread已经有正在运行的run了
        if active_run is not None:
            # 这个活跃的run可能是同一幂等键请求创建的，也可能是不同幂等键请求创建的
            # 需要进行区分
            if active_run.idempotency_key == req.idempotency_key:
                return _build_run_response(active_run)
            # 不是同一幂等键，那么就是同一用户的不同请求了
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "THREAD_HAS_ACTIVE_RUN",
                    "message": "当前 Thread 已存在正在执行的 Run",
                    "run_id": str(active_run.id),
                    "run_status": active_run.status,
                },
            )
        # 运行到这里，说明当前thread还不存在正在运行的run
        # 可以进行创建
        # 但是众所周知，以上操作都是check then act操作，并不靠谱
        # 因此还需要数据库进行兜底
        run_dict = {
            "thread_id": thread_id,
            "user_id": current_user.id,
            "idempotency_key": req.idempotency_key,
            "scope": req.scope,
            "scope_id": req.scope_id,
            "status": RunStatus.QUEUED.value,
            "trigger_type": "user_prompt"
        }
        new_run = await run_crud.create_run(db, run_dict)
        message_dict = {
            "thread_id":thread_id,
            "run_id": new_run.id,
            "role": MessageRole.USER.value,
            "content": req.prompt,
            "status": MessageStatus.SUCCESS.value
        }
        new_message = await message_crud.create_message(db, message_dict)
        existing_thread.updated_at = utc_now()
        try:
            await db.commit()
        except Exception as e:
            # rollback 必须立刻执行，否则 session 不可用。
            await db.rollback()

            # ---------- (A) 同 key 并发 → 幂等返回 ----------
            existing_run = await run_crud.get_run_by_user_idempotency_key(
                db,
                current_user.id,
                req.idempotency_key
            )
            if existing_run is not None:
                if existing_run.thread_id != thread_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "IDEMPOTENCY_KEY_MISMATCH",
                            "message": "idempotency_key 已属于其他 Thread",
                        },
                    )
                return _build_run_response(existing_run)

            # ---------- (B) 同 Thread 已有活跃 Run ----------
            active_run = await run_crud.get_active_run_by_thread_id(db, thread_id)
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
            #   客户端在收到 503 后用同一个 idempotency_key 重试时，
            #   会命中幂等分支，拿到这个 failed 的 Run。
            #   建议客户端在 503 时换新 key 重试，
            #   或改用 Outbox 模式异步投递（见文末说明）。
            #
        try:
            task = await execute_agent_run.kiq(
                run_id=str(new_run.id),
                thread_id=str(thread_id),
                resume=False,
                resolution=None,
            )

            # new_run.task_id = task.task_id
            # await db.commit()

        except Exception as exc:

            # 用一个新的短事务标记失败，避免污染已 commit 的状态。
            new_run.status = RunStatus.FAILED.value
            new_run.error_code = "TASK_ENQUEUE_FAILED"
            new_run.error_message = str(exc)
            new_run.finished_at = utc_now()
            await db.commit()

            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "TASK_ENQUEUE_FAILED",
                    "message": "Agent 任务提交失败，请稍后重试",
                },
            )
        return _build_run_response(new_run)

    finally:
        # 不管是执行异常还是代码正常执行，锁都要正常释放
        await lock.release()
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
    idempotency_key: str | None = Field(default=None, max_length=128, index=True)
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

### 问：这段代码和数据库表设计是不是有点过度防御了？主要是在idempotency_key属性和与它相关的代码上，似乎不需要它也可以

你看得非常准，上一轮我把这一行直接定性为“死代码”确实**武断且欠缺推敲**了。在并发场景（Check-then-Act 的时间窗口）下，**这个分支不仅可能被触发，而且是一个真实存在的时序拦截点**。



### 一、这个分支会在什么场景下被触发？

代码之所以设计了双重防御（Redis 锁 + DB 唯一约束），本身就默认了“Redis 锁不是 100% 可靠的屏障”（比如因慢查询/GC/网络抖动导致 30s TTL 超时失效，或者锁发生漂移）。



在数据库默认的 **Read Committed**（读已提交）隔离级别下，每个 `SELECT` 语句都会生成一个独立的数据快照。一旦发生并发，就会出现以下时序：



Plaintext

```
客户端并发发送两个完全相同请求：Req A 和 Req B（相同 thread_id, 相同 idempotency_key）

  时间线               Req A                               Req B
    │
    │   1. 查 key：不存在
    │                                       1. 查 key：不存在 (此时 A 还没提交)
    │   2. 查 active run：不存在
    │   3. INSERT run + COMMIT
    │      (A 已经成功落库并提交)
    │                                       2. 查 active run：
    │                                          (由于 Read Committed，B 查到了 A 刚提交的 Run！)
    │                                       3. 检查 active_run.idempotency_key == req.idempotency_key
    ▼                                          👉 命中 True！直接返回 A 的 Run
```

如果没有这一行判断，Req B 在查到 `active_run` 时，会直接走到下面的逻辑：



Python

```
raise HTTPException(
    status_code=409,
    detail={"code": "THREAD_HAS_ACTIVE_RUN", ...}
)
```

这就导致：**同一个请求重试，本该享受幂等返回，结果因为踩中了这一微小的时间差，被当成了“另一个冲突的活跃 Run”返回了 409 报错**。



所以，这一行代码不是死代码，而是一个**针对“查 key”与“查 active”之间缝隙的补漏拦截**。



### 二、既然有用，为什么初看会觉得它“多余”？

之所以容易产生“死代码”的直觉，是因为在**单线程逻辑**或**分布式锁绝对生效**的前提下：



1. `Run` 属于当前 `thread_id`；
2. 如果存在，第 1 步的 `get_run_by_user_idempotency_key` 就能查到；
3. 第 1 步查不到，后面再查同一个 Thread 的 active run，自然就不可能含有这个 key。

但 Check-then-Act 的本质就是：**在步骤 1 和步骤 2 之间，外部世界随时可能发生 Commit**。



### 三、这段防御逻辑的真正定位与权衡

虽然它能拦住上述时序，但从架构严谨性的角度来看，这种 Check-then-Act 防御依然是“尽力而为（Best-effort）”的，无法实现理论上的 100% 覆盖：



1. **更靠后的缝隙依然拦不住**：

   如果 Req A 提交的时机不在“B 的步骤 1 和步骤 2 之间”，而是在“B 的步骤 2 之后、B 执行 `commit` 之前”，那么 B 的代码检查依然会全部溜过去，最终只能靠下面的 `try ... except IntegrityError` 数据库唯一约束来兜底。

2. **它的实际价值是“提前止损”**：

   既然数据库层面已经有了 `try ... commit ... except` 兜底，为什么还要在上面写这层检查？

   - **减少异常风暴与事务回滚**：能在上层用一个简单的 `SELECT` 和 `if` 拦截掉并平滑返回，就尽量不让事务走到 `IntegrityError` 和 `db.rollback()`，减轻 DB 的死锁检测和回滚压力。
   - **用户体验更平滑**：在锁漂移的瞬态并发下，尽量让重试请求拿到 200 而不是报错。

### 结论

你的质疑完全正确：



- 在 **Read Committed** 事务隔离级别 + **并发重试（锁失效或穿透）** 的情况下，`active_run.idempotency_key == req.idempotency_key` **绝对不是死代码**，它真实地覆盖了两个查询之间的竞态窗口。
- 保留这行代码是合理的，它是在 DB 唯一索引兜底之前，应用层做的一道有效、廉价的**前置漏斗过滤**。

### 问：不过当前创建Agent Run仍然存在问题，主要在数据库状态和消息投递状态无法保证一致上。如果改成outbox模式，应该怎么做呢

采用 **Transactional Outbox（发件箱）模式** 的核心逻辑是：**将「业务状态写库」和「消息投递记录」合并到同一个本地数据库事务中**，将原本跨系统的分布式一致性问题，降级为单数据库的 ACID 事务。消息再由专门的 Relay（中继服务）异步投递到消息队列。



落地针对 Agent Run 场景的 Outbox 改造，标准实现分为以下四个部分：



### 1. 表结构设计

除了原有的 `agent_runs` 业务表，需要新增一张 `outbox_events` 表：



SQL

```
-- 1. Agent Run 业务表
CREATE TABLE agent_runs (
    id VARCHAR(64) PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL, -- 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED'
    params JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. 事务发件箱表
CREATE TABLE outbox_events (
    id BIGSERIAL PRIMARY KEY,
    aggregate_type VARCHAR(64) NOT NULL, -- 固定为 'agent_run'
    aggregate_id VARCHAR(64) NOT NULL,   -- run_id
    event_type VARCHAR(64) NOT NULL,     -- 'run.created'
    payload JSONB NOT NULL,              -- 派发给 Worker 必需的上下文数据
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'PUBLISHED', 'FAILED'
    retry_count INT NOT NULL DEFAULT 0,
    locked_until TIMESTAMP WITH TIME ZONE, -- 防并发拉取锁
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_outbox_pending ON outbox_events (status, created_at) 
WHERE status = 'PENDING';
```

### 2. 写入流程：本地事务双写

当用户发起 `CreateRun` 请求时，在同一个 DB 事务中同时写入 Run 和 Outbox 事件。**不再直接在 Web 接口层向 MQ 或 Redis 发消息。**



Python

```
def create_agent_run(agent_id: str, run_params: dict) -> str:
    run_id = generate_uuid()
    
    with db_engine.begin() as txn:
        # 1. 写入业务表，初始状态置为 PENDING
        txn.execute(
            """
            INSERT INTO agent_runs (id, agent_id, status, params)
            VALUES (:id, :agent_id, 'PENDING', :params)
            """,
            {"id": run_id, "agent_id": agent_id, "params": json.dumps(run_params)}
        )
        
        # 2. 写入发件箱
        txn.execute(
            """
            INSERT INTO outbox_events (aggregate_type, aggregate_id, event_type, payload, status)
            VALUES ('agent_run', :run_id, 'run.created', :payload, 'PENDING')
            """,
            {
                "run_id": run_id,
                "payload": json.dumps({"run_id": run_id, "agent_id": agent_id, "params": run_params})
            }
        )
    
    # 事务提交成功后，直接返回 run_id 给客户端
    return run_id
```

此时即便系统崩溃、断电，两张表的数据一定是强一致的：要么都有，要么都没有。



### 3. 消息中继（Outbox Relay）投递方案

后台独立进程将 `outbox_events` 中的记录取出并推送到消息中枢。在实践中有两种主流策略：



#### 方案 A：轻量轮询 + 行锁（适合中小规模、快速上线）

使用独立的轮询 Worker 定期扫描发件箱。利用 `FOR UPDATE SKIP LOCKED` 避免多个 Relay 实例冲突：



SQL

```
-- 锁定一批待发送的记录
SELECT id, aggregate_id, payload 
FROM outbox_events
WHERE status = 'PENDING'
ORDER BY id ASC
LIMIT 100
FOR UPDATE SKIP LOCKED;
```

**处理逻辑：**



1. 获取事件记录后，将消息发送到 MQ / Redis Stream / Celery。
2. MQ 回执 ACK 后，更新 `status = 'PUBLISHED'`（或在量大时直接执行 `DELETE` 避免发件箱无限膨胀）。
3. 发生错误时增加 `retry_count`，若连续失败达到阈值则置为 `FAILED` 并触发报警。

#### 方案 B：CDC（Change Data Capture）无轮询（适合高吞吐场景）

使用 Debezium / Canal 监听数据库的 WAL / Binlog 日志，流式捕获 `outbox_events` 的 INSERT 事件，自动由 Connector 投递到 Kafka。



- **优点**：对数据库主库完全无轮询开销，端到端延迟低（通常 < 100ms）。
- **代价**：需引入额外的中间件与运维成本。

### 4. 关键演进：解决 Agent 响应延迟（Fast-Path 机制）

传统的 Outbox 依赖定时间隔轮询（如 500ms~1s），对交互式 Agent 而言冷启动过慢。工业级实践通常采用 **Fast-Path 混合加速**：



Plaintext

```
Client API 
    │
    ├─ 1. DB 事务提交 (agent_runs + outbox_events) ───► 成功
    │
    ├─ 2. [Fast-Path] 内存异步事件触发 ───► 立刻投递 MQ ───► 标记 Outbox 为 PUBLISHED (耗时 5~15ms)
    │
    └─ 3. [Slow-Path / 保底] 后台 Relay 轮询 (只补偿 Fast-Path 崩溃未处理的事件)
```

即使第 2 步的内存信号在进程崩溃时丢失，第 3 步的轮询保底机制也会在秒级内捞出并重新投递，兼顾了低延迟与绝对可靠性。



### 5. 消费端必须具备：幂等控制

Outbox 保证的是 **至少一次投递（At-least-once）**，极端情况下（如 Relay 发送到 MQ 成功但未及写回 `PUBLISHED` 时崩溃），同一条 Run 消息会被投递多次。因此执行 Worker 必须实现状态机幂等：



SQL

```
-- Worker 认领任务时的原子状态跃迁
UPDATE agent_runs 
SET status = 'RUNNING', started_at = NOW() 
WHERE id = :run_id AND status = 'PENDING';
```

- **受影响行数 = 1**：成功抢占，启动 Agent 运行循环（LLM Call、Tool Use 等）。
- **受影响行数 = 0**：说明该 Run 已经被其它 Worker 消费或已处于终态，直接忽略，丢弃该重复消息。

### 问：看来我当前要做消息中继（Outbox Relay）投递只能选择方案A了，那么我有几个疑问，首先是使用独立的轮询 Worker 定期扫描发件箱，那么单个worker能否对多个不同的业务进行消息投递？

**完全可以，而且在中小规模和统一技术栈下，由单个 Worker 统一投递是业界的标准做法。**



但要做到这一点，核心前提是必须严守职责边界：**Outbox Worker 只能当“纯搬运工（快递员）”，绝对不能在里面写任何业务处理逻辑。** 它的唯一职责就是将 DB 里的事件拿出来，投递到消息队列（如 TaskIQ、Redis Stream、RabbitMQ），之后立刻交差。



### 一、通用 Worker 的两种实现模式

由于不同业务投递的目的地（Queue/Topic）或序列化方式可能不同，通常有两种组织方式：



#### 模式 1：注册表分发模式（Registry / Dispatcher）

如果你的不同业务依赖不同的 TaskIQ 任务或投递方法，可以在 Worker 内部维护一个轻量的**事件分发器**。



Python

```
from typing import Callable, Awaitable

# 1. 业务分发注册表：将 event_type 映射到具体的投递动作
EVENT_DISPATCHERS: dict[str, Callable[[dict], Awaitable[None]]] = {}

def register_handler(event_type: str):
    def decorator(fn):
        EVENT_DISPATCHERS[event_type] = fn
        return fn
    return decorator

# 业务 A: Agent 启动任务
@register_handler("agent.run.created")
async def dispatch_agent_run(payload: dict):
    await execute_agent_run.kiq(
        run_id=payload["run_id"],
        thread_id=payload["thread_id"],
    )

# 业务 B: 账单/Token 扣费任务
@register_handler("billing.token.deduct")
async def dispatch_billing(payload: dict):
    await deduct_user_balance.kiq(
        user_id=payload["user_id"],
        tokens=payload["token_count"],
    )

# 2. Worker 核心轮询循环（完全通用的搬运逻辑）
async def poll_and_dispatch(db: AsyncSession):
    # 锁定一批待处理事件
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.status == "PENDING")
        .order_by(OutboxEvent.id.asc())
        .limit(50)
        .with_for_update(skip_locked=True)
    )
    events = (await db.execute(stmt)).scalars().all()

    for event in events:
        dispatcher = EVENT_DISPATCHERS.get(event.event_type)
        if not dispatcher:
            event.status = "FAILED"
            event.error_message = f"Unknown event_type: {event.event_type}"
            continue

        try:
            await dispatcher(event.payload)
            event.status = "PUBLISHED"
            event.published_at = utc_now()
        except Exception as exc:
            event.retry_count += 1
            if event.retry_count >= 5:
                event.status = "FAILED"
            event.error_message = str(exc)

    await db.commit()
```

#### 模式 2：纯基础设施路由（完全无感知搬运）

在 `outbox_events` 表中直接记录目标消息队列的名字（如 `task_name` 或 `queue_name`）：



SQL

```
ALTER TABLE outbox_events ADD COLUMN queue_name VARCHAR(64);
ALTER TABLE outbox_events ADD COLUMN routing_key VARCHAR(128);
```

Worker 甚至不需要知道 payload 里面装了什么，直接根据字段将 payload 裸发到对应的 Queue 即可，新业务接入时**Worker 代码一行都不需要改**。



### 二、单 Worker 承载多业务时必须防范的 3 个隐患

虽然技术上完全可行，但在并发和高频场景下，需要注意以下隔离问题：



| **隐患**                          | **现象**                                                     | **应对策略**                                                 |
| --------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **1. 队头阻塞（Noisy Neighbor）** | 某个低优先级的批量业务（如批量同步文档产生 10,000 条事件）占满了扫描窗口，导致高优先级的交互式 `agent.run.created` 排在后面得不到投递。 | **引入优先级或分流**：  • 在表里加 `priority` 字段，`ORDER BY priority DESC, id ASC`；  • 或者按 `aggregate_type` 划分独立的 Worker 实例，专人专事。 |
| **2. 毒丸事件（Poison Pill）**    | 某一条异常数据（如 payload 格式错误）导致 MQ 客户端抛错，若逻辑处理不当导致不断重试，会拖慢整个 Worker 的吞吐。 | **严格重试上限 + 死信状态**：  捕获单条异常，增加 `retry_count`，达到阈值（如 3~5 次）直接标记为 `FAILED` 并报警，绝不能让单条失败阻断整个批次提交。 |
| **3. 吞吐瓶颈**                   | 业务量增长后，单 Worker 轮询的 DB I/O 或网络 I/O 成为瓶颈。  | **无缝横向扩展**：  因为查询使用了 `FOR UPDATE SKIP LOCKED`，可以直接启动 3~5 个相同的 Worker 进程同时跑，数据库会自动互斥分配行记录，天然支持水平扩容。 |

### 三、选型建议

1. **初期阶段（当前）**：
   - 使用 **单个 Outbox 表 + 单个/少量 Relay Worker 进程**。
   - 采用上述的**注册表模式（Registry）**，集中管理 Agent Run 以及后续其他业务（如审计日志、消息归档、扣费结算）的投递。
2. **后期演进（如遇瓶颈）**：
   - 只有当出现明显**不同 SLA 诉求**时才需要拆分：比如将秒级强实时的“交互类任务”（Agent Run）与分钟级延迟无所谓的“分析/日志类任务”拆分成两个独立的 Worker 进程分别拉取不同条件的数据。

### 问：负责投递任务的worker在生产环境如何持久化运行呢

在生产环境中，Outbox Relay Worker 的持久化运行核心需要解决三件事：**进程崩溃自愈与守护**、**系统停止时的优雅关机（Graceful Shutdown）**、以及**无缝横向扩容**。



根据你的部署架构（容器化 K8s / Docker 还是传统虚拟机），推荐以下标准实践：



### 一、代码层：实现优雅关机（基础前置）

无论用什么守护工具，Worker 脚本本身必须监听系统的 `SIGTERM` 和 `SIGINT` 信号。否则容器重启或发版时，进程会被粗暴 `kill -9`，导致正在投递或写库的事务处于未决状态。



Python

```
# outbox_worker.py
import asyncio
import signal
import sys
from loguru import logger

class OutboxWorker:
    def __init__(self):
        self.is_running = True

    def stop(self):
        logger.info("收到终止信号，正在准备停止 Worker...")
        self.is_running = False

    async def run(self):
        logger.info("Outbox Relay Worker 启动运行中...")
        while self.is_running:
            try:
                # 执行单批次拉取与投递
                processed_count = await poll_and_dispatch_batch()
                
                # 如果当前队列为空，短暂休眠降低 DB 压力；有数据则立即进入下一轮
                if processed_count == 0:
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"轮询异常: {e}", exc_info=True)
                await asyncio.sleep(2)  # 发生异常时退避，避免刷屏打满日志

        logger.info("Worker 循环已安全退出，资源已释放。")

async def main():
    worker = OutboxWorker()
    loop = asyncio.get_running_loop()

    # 注册系统退出信号（K8s 缩容/重启或 systemd stop 会发送 SIGTERM）
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)

    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### 二、运行方案 A：容器化 / Kubernetes（现代架构首选）

在云原生环境中，Worker 直接作为与 FastAPI 平级的 **独立 Deployment** 部署。



因为底层 SQL 使用了 `FOR UPDATE SKIP LOCKED`，你甚至可以直接把副本数（`replicas`）设为 2 或更多，实现**天然的高可用与并发投递**，无需任何分布式协调组件。



YAML

```
# outbox-worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-outbox-relay
  labels:
    app: agent-outbox-relay
spec:
  replicas: 2  # 多实例并发消费，一个挂掉另一个继续跑
  selector:
    matchLabels:
      app: agent-outbox-relay
  template:
    metadata:
      labels:
        app: agent-outbox-relay
    spec:
      # 给 Worker 充足的优雅退出时间完成当前批次 DB 提交
      terminationGracePeriodSeconds: 30
      containers:
      - name: worker
        image: your-registry/agent-service:v1.0.0
        command: ["python", "-m", "app.workers.outbox_worker"]
        envFrom:
        - configMapRef:
            name: app-config
        - secretRef:
            name: app-secrets
        resources:
          requests:
            cpu: "100m"
            memory: "128Mi"
          limits:
            cpu: "500m"
            memory: "512Mi"
```

如果是单机 Docker 环境，配置 `docker-compose.yml`：



YAML

```
services:
  outbox-worker:
    image: your-registry/agent-service:v1.0.0
    command: python -m app.workers.outbox_worker
    restart: always  # 崩溃或机器重启后自动拉起
    stop_grace_period: 30s
    environment:
      - DATABASE_URL=postgresql+asyncpg://...
```

### 三、运行方案 B：Linux 虚拟机 / Systemd（轻量稳健）

如果部署在独立的 Linux 服务器上，使用系统的 **systemd** 是最稳定可靠的方式（优于 Python 层面的 Supervisor）。



1. **创建服务配置文件** `/etc/systemd/system/outbox-worker.service`：

Ini, TOML

```
[Unit]
Description=Agent Outbox Relay Worker
After=network.target postgresql.service

[Service]
Type=simple
User=appuser
Group=appuser
WorkingDirectory=/opt/agent-service
Environment="PATH=/opt/agent-service/.venv/bin"
EnvironmentFile=/opt/agent-service/.env
ExecStart=/opt/agent-service/.venv/bin/python -m app.workers.outbox_worker

# 核心守护策略
Restart=always
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=30s

# 日志输出到系统日志 (journalctl 可查)
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

1. **启用并启动服务**：

Bash

```
sudo systemctl daemon-reload
sudo systemctl enable outbox-worker
sudo systemctl start outbox-worker

# 查看实时日志
sudo journalctl -u outbox-worker -f
```

### 四、生产落地的 3 个关键细节

1. **连接池独立配置**：
   - Worker 是长连接持续占用的进程。
   - 为 Worker 单独配置 SQLAlchemy 的连接池（例如 `pool_size=5`, `max_overflow=0`），避免 Worker 占死数据库所有的连接数导致 Web 接口无连接可用。
2. **空闲退避（Backoff）策略**：
   - 队列无事件时，务必加上 `sleep(0.5)` ~ `sleep(1)`，防止 Worker 进入死循环（Busy Loop）打爆数据库 CPU 和产生海量无意义的只读日志。
3. **死信报警（Dead Letter Alert）**：
   - 设立独立的监控定时任务（如 Prometheus SQL Exporter 或监控脚本），检查 `SELECT COUNT(*) FROM outbox_events WHERE status = 'FAILED'`。
   - 只要出现 `FAILED` 状态的记录，说明重试次数耗尽，需要立刻向企业微信/飞书/Slack 告警人工介入。

### 问：

```
@router.post(
    "/threads/{thread_id}/run",
    response_model=RunCreateResponse
)
async def create_chat_run(
        thread_id: uuid.UUID,
        req: RunCreateRequest,
        db: AsyncSession = Depends(get_session),
        redis: Redis = Depends(get_redis),
        current_user: User = Depends(get_current_user)
):
    # 1 进行鉴权
    # 鉴权放在分布式锁前面是因为分布式锁锁的是thread_id
    # 防止别的用户抢占属于当前用户的thread的锁
    existing_thread = await thread_crud.get_thread_by_id(db, thread_id)
    if existing_thread is None or existing_thread.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread不存在"
        )
    # 2获取 Thread 级分布式锁
    # 锁的粒度选择是Thread
    # 若选择 run， 则粒度太细， 无法阻止同一个 Thread 两个 Run 并发
    # 若选择 user, 则粒度太粗， 导致同一用户无法在两个或多个Thread中一起跑
    lock = RedisDistributedLock(
        redis,
        key=f"lock:thread:{thread_id}",
        ttl=30
    )
    # 两个几乎同时的并发请求过来枪锁，它们可能是同一请求，带有同一幂等键，也可能不同
    acquired = await lock.acquire()
    # 没有抢到锁的一方
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="当前会话正在处理其它请求，请稍后重试"
        )
    try:
        # 抢到锁的一方，先进行权限校验
        # 首先是Thread级别的校验
        existing_thread = await thread_crud.get_thread_by_id(db, thread_id)
        # 之前通过鉴权的时候还存在，但极小概率现在又被删除了
        if existing_thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread不存在"
            )
        # 当前Thread的状态，如果是活跃状态，则通过，反之如果是归档、删除状态，则报错
        if existing_thread.status != ThreadStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Thread 当前不可执行"
            )
        # Thread校验完毕后，开始对run进行校验
        # 首先是check then act 校验，这层校验无法兜底
        existing_run = await run_crud.get_run_by_user_idempotency_key(
            db,
            current_user.id,
            req.idempotency_key
        )
        # 当前已经有run存在了，说明已经有相同请求创建了run（因为幂等键相同）
        if existing_run is not None:
            # 这个run属于当前用户，并且和当前请求是统一幂等键
            # 但还有变数，这个run的thread_id相同和不同
            # 不同的情况说明当前用户的相同请求也发给了另一个会话窗口
            # 客户端误用 key：同一 key 指到了不同 Thread。
            # 必须明确拒绝，否则会把 A thread 的 run 返回给 B thread。
            if existing_run.thread_id != thread_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="当前请求已被其它会话Thread使用，请为新的请求生成新的幂等键"
                )
            # 运行到这里，说明当前已经有相同请求在该thread创建了run
            # 那么可以直接返回结果
            return _build_run_response(existing_run)
        # 运行到这里，说明当前幂等键对应的run还不存在
        # 我们的目的创建run
        # 但是在创建run之前，还得保证当前thread没有其它幂等键对应的活跃的run
        active_run = await run_crud.get_active_run_by_thread_id(db, thread_id)
        # 当前thread已经有正在运行的run了
        if active_run is not None:
            # 这个活跃的run可能是同一幂等键请求创建的，也可能是不同幂等键请求创建的
            # 需要进行区分
            if active_run.idempotency_key == req.idempotency_key:
                return _build_run_response(active_run)
            # 不是同一幂等键，那么就是同一用户的不同请求了
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "THREAD_HAS_ACTIVE_RUN",
                    "message": "当前 Thread 已存在正在执行的 Run",
                    "run_id": str(active_run.id),
                    "run_status": active_run.status,
                },
            )
        # 运行到这里，说明当前thread还不存在正在运行的run
        # 可以进行创建
        # 但是众所周知，以上操作都是check then act操作，并不靠谱
        # 因此还需要数据库进行兜底
        run_dict = {
            "thread_id": thread_id,
            "user_id": current_user.id,
            "idempotency_key": req.idempotency_key,
            "scope": req.scope,
            "scope_id": req.scope_id,
            "status": RunStatus.QUEUED.value,
            "trigger_type": "user_prompt"
        }
        new_run = await run_crud.create_run(db, run_dict)
        message_dict = {
            "thread_id":thread_id,
            "run_id": new_run.id,
            "role": MessageRole.USER.value,
            "content": req.prompt,
            "status": MessageStatus.SUCCESS.value
        }
        new_message = await message_crud.create_message(db, message_dict)
        existing_thread.updated_at = utc_now()
        try:
            await db.commit()
        except Exception as e:
            # rollback 必须立刻执行，否则 session 不可用。
            await db.rollback()

            # ---------- (A) 同 key 并发 → 幂等返回 ----------
            existing_run = await run_crud.get_run_by_user_idempotency_key(
                db,
                current_user.id,
                req.idempotency_key
            )
            if existing_run is not None:
                if existing_run.thread_id != thread_id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "code": "IDEMPOTENCY_KEY_MISMATCH",
                            "message": "idempotency_key 已属于其他 Thread",
                        },
                    )
                return _build_run_response(existing_run)

            # ---------- (B) 同 Thread 已有活跃 Run ----------
            active_run = await run_crud.get_active_run_by_thread_id(db, thread_id)
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
            #   客户端在收到 503 后用同一个 idempotency_key 重试时，
            #   会命中幂等分支，拿到这个 failed 的 Run。
            #   建议客户端在 503 时换新 key 重试，
            #   或改用 Outbox 模式异步投递（见文末说明）。
            #
        try:
            task = await execute_agent_run.kiq(
                run_id=str(new_run.id),
                thread_id=str(thread_id),
                resume=False,
                resolution=None,
            )

            # new_run.task_id = task.task_id
            # await db.commit()

        except Exception as exc:

            # 用一个新的短事务标记失败，避免污染已 commit 的状态。
            new_run.status = RunStatus.FAILED.value
            new_run.error_code = "TASK_ENQUEUE_FAILED"
            new_run.error_message = str(exc)
            new_run.finished_at = utc_now()
            await db.commit()

            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "TASK_ENQUEUE_FAILED",
                    "message": "Agent 任务提交失败，请稍后重试",
                },
            )
        return _build_run_response(new_run)

    finally:
        # 不管是执行异常还是代码正常执行，锁都要正常释放
        await lock.release()


# 任务恢复，返回值应该是什么？
# 要有 thread_id，run_id, status
@router.post(
    "/threads/{thread_id}/runs/{run_id}/resume",
    response_model=ResumeResponse
)
async def resume_run(
        thread_id: uuid.UUID,
        run_id: uuid.UUID,
        req: ResumeRequest,
        redis: Redis = Depends(get_redis),
        current_user: User = Depends(get_current_user)
):
    # 先进行权限鉴定
    # Thread是否属于当前用户，是否是活跃状态
    async with AsyncSessionLocal() as db:
        thread = await thread_crud.get_thread_by_id(db, thread_id)
        # 递进关系
        # 首先thread存在
        if thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread 不存在",
            )
        # 其次thread属于当前用户
        if thread.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权操作此资源",
            )
        # 最后thread得是活跃的
        if thread.status != ThreadStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Thread 当前处于非活跃状态",
            )

    # 会话级分布式锁挡住第一波
    lock = RedisDistributedLock(
        redis,
        key=f"lock:thread:{thread_id}",
        ttl=30
    )
    acquired = await lock.acquire()
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前会话正在执行别的操作"
        )
    # 运行到这里说明请到了分布式锁，有了执行权，但还是需要数据库来兜底
    try:
        async with AsyncSessionLocal() as db:
            async with db.begin():
                run = await run_crud.get_run_by_id(db, run_id)
                if run is None or run.thread_id != thread_id:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Run不存在"
                    )
                if run.status != RunStatus.REQUIRES_ACTION.value:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"当前Run状态为{run.status}, 不可执行resume操作"
                    )
                # CAS原子更新
                result = await run_crud.update_run_status(
                    db,
                    run_id,
                    src_status=RunStatus.REQUIRES_ACTION.value,
                    dest_status=RunStatus.QUEUED.value
                )
                if result.rowcount == 0:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Run 状态已被并发修改",
                    )
                # cas更新成功，抢到执行权
                # 进行下一步操作，修改Interrupt的状态
                # 先获取Interrupt
                interrupt = await interrupt_crud.get_pending_interrupt(
                    db,
                    thread_id,
                    run_id
                )
                if not interrupt:
                    # 本来是要回滚的，现在是在事务中，不需要回滚了
                    # 这里进行rollback是因为前面已经有了状态更新操作，出错之前必须回滚
                    # await db.rollback()
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="未找到挂起状态的中断操作（可能已被处理）",
                    )
                interrupt.status = InterruptStatus.RESOLVED.value
                interrupt.resolution = req.resolution
                interrupt.resolved_by = current_user.id
                interrupt.resolved_at = utc_now()
        # 启用事务后，没必要显式提交和rollback
        # try:
        #     await db.commit()
        # except Exception as e:
        #     await db.rollback()
        #     raise HTTPException(
        #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        #         detail="任务状态更新出现异常",
        #     )

        # 后续该用outbox模式进行任务投递
        # 这样一来不仅通过数据库使得任务投递具有一致性
        # 且业务代码还会变得更加简单
        try:
            await execute_agent_run.kiq(
                run_id=str(run_id),
                thread_id=str(thread_id),
                resume=True,
                resolution=req.resolution,
            )
            # commit不能放到队列下面，否则会出现队列中任务已经在运行，而路由函数却没修改的异常情况
            # await db.commit()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="恢复任务提交失败",
            )
        await redis.xadd(
            f"agent:stream:{run_id}",
            {
                "event": "human_action",
                "data": json.dumps(
                    {
                        "interrupt_id": str(interrupt.id),
                        "resolution": req.resolution
                    },
                    ensure_ascii=False
                )
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

### 问：很好，最后就是我上述的两段函数如何统一改成outbox事务投递模式？需要改哪些地方

将这两段代码改造成 Outbox 模式，核心逻辑非常纯粹：**将直接调用 `execute_agent_run.kiq` 的代码全部剔除，替换为在同一个数据库本地事务中插入一条 `outbox_events` 记录。**



改造后不仅保证了跨系统的绝对一致性，还顺带根除了两处原本非常棘手的“状态卡死”Bug。



### 一、通用基础：定义 Outbox 辅助工具

统一一个事件写入方法，把任务投递参数打包成标准的 JSON Payload：



Python

```
# app/crud/outbox_crud.py
from app.models.outbox import OutboxEvent

async def create_agent_run_event(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    thread_id: uuid.UUID,
    resume: bool = False,
    resolution: dict | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        aggregate_type="agent_run",
        aggregate_id=str(run_id),
        event_type="agent.run.dispatch",
        payload={
            "run_id": str(run_id),
            "thread_id": str(thread_id),
            "resume": resume,
            "resolution": resolution,
        },
        status="PENDING",
    )
    db.add(event)
    return event
```

### 二、改造函数 1：`create_chat_run`

#### 关键改动点：

1. **删除 `execute_agent_run.kiq(...)`** 及其后续的 `try...except` 标记 `FAILED` 逻辑。
2. 在 `commit` 之前，把 `OutboxEvent` 与 `new_run`、`new_message` 一起提交。
3. 彻底解决了之前 **“队列投递失败导致该 key 对应的 Run 永久定格在 FAILED，客户端无法安全重试”** 的隐患。

Python

```
@router.post(
    "/threads/{thread_id}/run",
    response_model=RunCreateResponse
)
async def create_chat_run(
    thread_id: uuid.UUID,
    req: RunCreateRequest,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user)
):
    # 1. 鉴权
    existing_thread = await thread_crud.get_thread_by_id(db, thread_id)
    if existing_thread is None or existing_thread.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thread不存在"
        )

    # 2. 获取分布式锁（防瞬态并发冲突）
    lock = RedisDistributedLock(
        redis,
        key=f"lock:thread:{thread_id}",
        ttl=30
    )
    acquired = await lock.acquire()
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="当前会话正在处理其它请求，请稍后重试"
        )

    try:
        # 二次检查 Thread 状态
        existing_thread = await thread_crud.get_thread_by_id(db, thread_id)
        if existing_thread is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thread不存在"
            )
        if existing_thread.status != ThreadStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Thread 当前不可执行"
            )

        # 3. 幂等性检查（Check-then-Act 前置过滤）
        existing_run = await run_crud.get_run_by_user_idempotency_key(
            db,
            current_user.id,
            req.idempotency_key
        )
        if existing_run is not None:
            if existing_run.thread_id != thread_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="当前请求已被其它会话Thread使用，请为新的请求生成新的幂等键"
                )
            return _build_run_response(existing_run)

        # 检查当前 Thread 是否有活跃 Run
        active_run = await run_crud.get_active_run_by_thread_id(db, thread_id)
        if active_run is not None:
            if active_run.idempotency_key == req.idempotency_key:
                return _build_run_response(active_run)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "THREAD_HAS_ACTIVE_RUN",
                    "message": "当前 Thread 已存在正在执行的 Run",
                    "run_id": str(active_run.id),
                    "run_status": active_run.status,
                },
            )

        # 4. 本地事务组装：Run + Message + OutboxEvent
        run_dict = {
            "thread_id": thread_id,
            "user_id": current_user.id,
            "idempotency_key": req.idempotency_key,
            "scope": req.scope,
            "scope_id": req.scope_id,
            "status": RunStatus.QUEUED.value,
            "trigger_type": "user_prompt"
        }
        new_run = await run_crud.create_run(db, run_dict)
        
        message_dict = {
            "thread_id": thread_id,
            "run_id": new_run.id,
            "role": MessageRole.USER.value,
            "content": req.prompt,
            "status": MessageStatus.SUCCESS.value
        }
        await message_crud.create_message(db, message_dict)
        existing_thread.updated_at = utc_now()

        # 【核心改造 1】：同时写入发件箱
        await outbox_crud.create_agent_run_event(
            db,
            run_id=new_run.id,
            thread_id=thread_id,
            resume=False,
            resolution=None
        )

        try:
            await db.commit()
        except IntegrityError:
            # 唯一索引冲突兜底（并发穿透时）
            await db.rollback()
            existing_run = await run_crud.get_run_by_user_idempotency_key(
                db, current_user.id, req.idempotency_key
            )
            if existing_run and existing_run.thread_id == thread_id:
                return _build_run_response(existing_run)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前会话已存在并发任务冲突，请稍后重试"
            )

        # 【核心改造 2】：不再调用 execute_agent_run.kiq，事务提交成功即代表投递成功
        return _build_run_response(new_run)

    finally:
        await lock.release()
```

### 三、改造函数 2：`resume_run`

#### 关键改动点：

1. **解决死锁 Bug**：原逻辑在 `async with db.begin():` 提交后才调用 `execute_agent_run.kiq`。一旦 TaskIQ 故障，DB 状态已经变成 `QUEUED`、中断已变成 `RESOLVED`，但任务压根没进入队列。用户再次点击 resume 会报 409，任务永久假死。
2. **纳入同一事务**：将状态更新、中断解除、`OutboxEvent` 写入合并在同一个 `db.begin()` 内，实现真正的 All-or-Nothing。
3. **Session 治理**：统一使用 FastAPI 依赖注入的 `db: AsyncSession = Depends(get_session)`，避免反复手动开闭 `AsyncSessionLocal()`。

Python

```
@router.post(
    "/threads/{thread_id}/runs/{run_id}/resume",
    response_model=ResumeResponse
)
async def resume_run(
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    req: ResumeRequest,
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user)
):
    # 1. 鉴权
    thread = await thread_crud.get_thread_by_id(db, thread_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread 不存在")
    if thread.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权操作此资源")
    if thread.status != ThreadStatus.ACTIVE.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Thread 当前处于非活跃状态")

    # 2. 会话级分布式锁
    lock = RedisDistributedLock(
        redis,
        key=f"lock:thread:{thread_id}",
        ttl=30
    )
    acquired = await lock.acquire()
    if not acquired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前会话正在执行别的操作")

    try:
        # 3. 单事务完成：状态跃迁 + Interrupt 处理 + Outbox 事件落库
        async with db.begin():
            run = await run_crud.get_run_by_id(db, run_id)
            if run is None or run.thread_id != thread_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run不存在")
            
            if run.status != RunStatus.REQUIRES_ACTION.value:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"当前Run状态为{run.status}, 不可执行resume操作"
                )

            # CAS 更新 Run 状态为 QUEUED
            result = await run_crud.update_run_status(
                db,
                run_id,
                src_status=RunStatus.REQUIRES_ACTION.value,
                dest_status=RunStatus.QUEUED.value
            )
            if result.rowcount == 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Run 状态已被并发修改",
                )

            # 更新 Interrupt 状态
            interrupt = await interrupt_crud.get_pending_interrupt(db, thread_id, run_id)
            if not interrupt:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="未找到挂起状态的中断操作（可能已被处理）",
                )
            interrupt.status = InterruptStatus.RESOLVED.value
            interrupt.resolution = req.resolution
            interrupt.resolved_by = current_user.id
            interrupt.resolved_at = utc_now()

            # 【核心改造 1】：在同一事务中写入恢复事件
            await outbox_crud.create_agent_run_event(
                db,
                run_id=run_id,
                thread_id=thread_id,
                resume=True,
                resolution=req.resolution
            )

        # 【核心改造 2】：删除了原本的 execute_agent_run.kiq 及其 503 异常处理
        # 走出 async with db.begin() 块后，事务已经原子性提交

        # 4. 前端实时流通知（轻量通知，即使网络抖动丢弃也不破坏核心调度正确性）
        try:
            await redis.xadd(
                f"agent:stream:{run_id}",
                {
                    "event": "human_action",
                    "data": json.dumps(
                        {"interrupt_id": str(interrupt.id), "resolution": req.resolution},
                        ensure_ascii=False
                    )
                },
            )
        except Exception:
            pass  # 实时推送失败不影响后端执行流

        return ResumeResponse(
            thread_id=thread_id,
            run_id=run_id,
            status=RunStatus.QUEUED.value,
        )

    finally:
        await lock.release()
```

### 四、改造前后的架构对比

| **考量维度**       | **改造前（直接调用 TaskIQ）**                                | **改造后（Transactional Outbox）**                           |
| ------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **一致性保障**     | 靠“后置捕获异常打补丁”，属于脆弱的最终一致性。               | **ACID 强一致性**：业务数据和事件记录同一个事务落盘。        |
| **消息队列宕机时** | 接口直接报 503；`create` 会把 Run 改为 FAILED 导致重试锁死；`resume` 会把任务卡死在 QUEUED。 | 接口**仍然能秒级响应 200**；消息安全留在 Outbox 表中，等队列恢复后由 Relay Worker 自动补投。 |
| **错误处理代码量** | 充斥大量 `try ... except ... commit FAILED` 的防御性补偿代码。 | **大幅精简**：无需在路由层处理 MQ 连接失败、超时等各种网络异常。 |
| **调用链延迟**     | Web 线程必须等待与 MQ/Redis 的网络握手（增加接口 P99 耗时）。 | 只与本地数据库通信一次即可返回，降低接口响应延迟。           |

### 问：改造之后似乎逻辑还更加清晰了

是的，这种“变清爽”的感觉源于一个底层的架构跃迁：**你把原本试图在 HTTP 请求周期内解决的“跨网络分布式一致性问题”，降级成了一个最简单的“本地数据库 ACID 事务”。**



代码之所以大幅精简且更具可读性，本质上是因为去掉了以下 3 类认知负担：



### 1. 消除了脆弱的“补丁补偿逻辑”

改造前，接口在尝试扮演一个半成品的分布式协调者：



- DB 成功但 MQ 失败时，不得不写一段 `new_run.status = FAILED` 的**逆向补偿事务**。
- 这种补偿不仅写起来臃肿，而且漏洞百出（比如让同一个 `idempotency_key` 永久卡死在失败态）。
- 改用 Outbox 后，所有的写操作（Run、Message、Outbox）在一个 `commit` 搞定。**要么全成，要么全败**，系统从根本上失去了产生“半中间脏状态”的可能，自然也就不需要任何补偿代码了。

### 2. 剥离了多外部系统的容灾假设

在原逻辑中，Web 接口必须同时假设：



- 数据库可能连不上 / 约束冲突
- Redis 锁可能失效
- TaskIQ / Broker 可能超时或宕机

这迫使你在一个几十行的路由函数里，混杂处理 HTTP 状态码（404、409、500、503）。Outbox 把**业务状态持久化**与**消息可靠投递**物理切开后，Web 接口只与数据库对话，网络拓扑立刻简化为单线联系。



### 3. 职责回归纯粹：从“立即交付”到“记录意图”

- **改造前（过程式思维）**：“我创建了一个 Run，现在我**必须立刻通知 Worker 跑起来**，通知失败我就得擦屁股。”
- **改造后（事件驱动思维）**：“我验证了请求，把 Run 和‘需要运行该 Run 的意图（Event）’原子性地记入账本，我的工作已经圆满完成。”

好的架构往往不是做加法，而是把不属于该生命周期的责任优雅地推出去。Web 请求只负责**记录事实**，交付任务交给专业的后台 Relay Worker 去做，代码自然就顺畅通透了。

### 问：我还是有点不放心，首先是改成outbox投递模式会导致worker端的代码需要发生改变吗？还有outbox_events 还是你一开始定义的那个样子吗？

你的担心非常务实，这两个问题直击 Outbox 模式落地的核心细节。我们逐一梳理清楚：



### 一、Worker 端的代码需要改变吗？

这里必须先明确两个角色的职责分工，避免概念混淆：



1. **Outbox Relay Worker（新角色，消息中继搬运工）**：这是**新增**的轻量轮询进程，它从数据库读取事件，执行 `await execute_agent_run.kiq(...)`，投递成功后把事件标记为 `PUBLISHED`。
2. **Agent Execution Worker（原有角色，业务执行者）**：这是原本就在跑的 TaskIQ Worker，负责处理 `@broker.task async def execute_agent_run(...)` 的长任务（大模型推理、工具调用等）。

#### 结论：Agent Execution Worker 的入参和核心逻辑「完全不需要变」，但有一项前置防御检查必须确认。

#### 1. 入参和接口完全不变

因为 Outbox Relay 搬运时，传给 TaskIQ 的参数依然是：



Python

```
await execute_agent_run.kiq(
    run_id=payload["run_id"],
    thread_id=payload["thread_id"],
    resume=payload["resume"],
    resolution=payload.get("resolution"),
)
```

所以 `execute_agent_run` 的函数签名、入参格式、大模型调用流完全不需要改动。



#### 2. 唯一需要检查的点：执行端的状态跃迁幂等（At-least-once 防御）

由于 Outbox 是**至少投递一次（At-least-once）**，极端情况下（例如 Relay 把任务推进了 TaskIQ，但在回写 DB 为 `PUBLISHED` 的前一毫秒进程被机器重启中断），Relay 重启后会再次投递同一条任务。



因此，你的 `execute_agent_run` 启动时，**必须以原子更新（CAS）或状态检查作为第一步**，防止两个 Worker 重复跑同一个 Run：



Python

```
# app/tasks/agent_tasks.py

@broker.task
async def execute_agent_run(
    run_id: str,
    thread_id: str,
    resume: bool = False,
    resolution: dict | None = None,
):
    async with AsyncSessionLocal() as db:
        # 原子抢占：只有处于 QUEUED 状态的 run 才能被改成 IN_PROGRESS
        # 这一步能彻底杜绝重复投递导致跑两次大模型的问题
        stmt = (
            update(Run)
            .where(Run.id == uuid.UUID(run_id))
            .where(Run.status == RunStatus.QUEUED.value)
            .values(
                status=RunStatus.IN_PROGRESS.value,
                started_at=utc_now()
            )
        )
        result = await db.execute(stmt)
        await db.commit()

        # 如果受影响行数为 0，说明这个任务已经被别的 Worker 接走或者取消了，直接忽略退出
        if result.rowcount == 0:
            logger.info(f"Run {run_id} 已经被处理或状态不符合预期，跳过执行")
            return

    # 下方是原本的大模型循环调用逻辑，一行都不用改
    await run_agent_loop(...)
```

如果你原先代码里就已经有这种状态校验，那么执行端代码**零改动**。



### 二、`outbox_events` 表还是最初定义的样子吗？

最初给出的是通用的概念 SQL。结合你当前采用的 **SQLModel + PostgreSQL + TaskIQ** 技术栈，针对生产环境做适配和优化后的最简设计如下：



#### 1. 生产级 SQLModel 模型定义

Python

```
# app/models/outbox.py
from datetime import datetime
from typing import Any
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel, Index, text
from app.core.time import utc_now


class OutboxEvent(SQLModel, table=True):
    __tablename__ = "outbox_events"

    # 主键建议使用自增 BigInteger，而不是 UUID
    # 理由：顺序递增主键在 B-Tree 索引中插入开销极小，且按照 id 顺序 FIFO 消费性能远超 UUID
    id: int | None = Field(default=None, sa_column=sa.Column(sa.BigInteger, primary_key=True, autoincrement=True))
    
    # 业务聚合根类型及 ID
    aggregate_type: str = Field(index=True, max_length=64)  # 如 "agent_run"
    aggregate_id: str = Field(index=True, max_length=64)    # run_id (uuid 转字符串)
    
    # 事件类型
    event_type: str = Field(index=True, max_length=64)      # 如 "agent.run.dispatch"
    
    # 投递所需的数据载荷
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=sa.Column(JSONB, nullable=False))
    
    # 状态：PENDING (待投递) / PUBLISHED (已投递) / FAILED (重试耗尽死信)
    status: str = Field(default="PENDING", max_length=32)
    
    retry_count: int = Field(default=0)
    error_message: str | None = Field(default=None, sa_column=sa.Column(sa.Text, nullable=True))
    
    created_at: datetime = Field(default_factory=utc_now, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False))
    published_at: datetime | None = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True))

    __table_args__ = (
        # 核心局部索引（Partial Index）：只对 PENDING 状态建索引
        # 表里就算有上千万历史完成记录，扫描待处理事件的索引大小依然只有几 KB，极速查询
        Index(
            "idx_outbox_pending_events",
            "id",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )
```

#### 2. 与最初的定义相比做了哪些优化？

| **字段/设计**      | **最初通用版**   | **生产适配优化版**                      | **为什么做这个改动？**                                       |
| ------------------ | ---------------- | --------------------------------------- | ------------------------------------------------------------ |
| **主键类型**       | `BIGSERIAL`      | SQLModel `BigInteger` 自增              | 维持自增整数。不用 UUID，保证事件天然按物理顺序 FIFO 投递。  |
| **`locked_until`** | 预留了锁过期字段 | **移除**                                | 既然已选定 PostgreSQL 的 `FOR UPDATE SKIP LOCKED`，行锁由 PG 事务生命周期自动管理，不再需要应用层计算过期时间。 |
| **索引设计**       | 全表扫描普通索引 | **PostgreSQL 部分索引 (Partial Index)** | 只索引 `status = 'PENDING'` 的记录。随着已处理数据累积，索引体积不会膨胀，避免磁盘 IO 拖慢数据库。 |
| **`payload`**      | 字符串/JSON      | `JSONB`                                 | 原生 PostgreSQL JSONB 类型，序列化开销低，便于排查数据。     |

### 三、完整的 Outbox Relay 循环参考

搭配上述模型的标准 Relay 处理逻辑如下：



Python

```
# app/workers/outbox_relay.py
import asyncio
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import AsyncSessionLocal
from app.models.outbox import OutboxEvent
from app.tasks.agent_tasks import execute_agent_run
from app.core.time import utc_now
from loguru import logger

BATCH_SIZE = 50
MAX_RETRIES = 5

async def process_outbox_batch() -> int:
    async with AsyncSessionLocal() as db:
        # 1. 安全捞取并锁定一批待发送事件
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.status == "PENDING")
            .order_by(OutboxEvent.id.asc())
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        events = (await db.execute(stmt)).scalars().all()
        
        if not events:
            return 0

        for event in events:
            try:
                # 2. 根据 event_type 分发并投递到 TaskIQ
                if event.event_type == "agent.run.dispatch":
                    p = event.payload
                    await execute_agent_run.kiq(
                        run_id=p["run_id"],
                        thread_id=p["thread_id"],
                        resume=p.get("resume", False),
                        resolution=p.get("resolution"),
                    )
                else:
                    raise ValueError(f"未知的事件类型: {event.event_type}")

                # 3. 投递成功，标记状态
                event.status = "PUBLISHED"
                event.published_at = utc_now()

            except Exception as e:
                event.retry_count += 1
                event.error_message = str(e)
                if event.retry_count >= MAX_RETRIES:
                    event.status = "FAILED"
                    logger.error(f"Outbox 事件 {event.id} 重试耗尽，已转死信: {e}")
                else:
                    logger.warning(f"Outbox 事件 {event.id} 投递失败，待下次重试: {e}")

        # 4. 统一提交本批次状态更新
        await db.commit()
        return len(events)
```

这样配合，既不需要动你既有的 Agent 执行层逻辑，数据一致性也有了完整的底层保证。