### 问：上面这段代码和下面的代码比哪个更好？

    @router.get("/stream/{run_id}")async def stream_agent_events(
        run_id: uuid.UUID,
        last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
        db: AsyncSession = Depends(get_async_session),):
        run = await db.get(Run, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run 不存在")
    async def event_generator():
        stream_key = f"agent:stream:{run_id}"
        # 游标支持：断网重连带上 Last-Event-ID 即可从断开处补发数据
        current_id = last_event_id if last_event_id else "0-0"
        terminal_status = {"completed", "failed", "cancelled", "requires_action"}
    
        while True:
            # 阻塞读取 Redis Stream
            response = await redis_client.xread({stream_key: current_id}, count=50, block=2000)
    
            if response:
                for _, messages in response:
                    for msg_id, fields in messages:
                        current_id = msg_id
                        raw_payload = fields.get("payload", "{}")
    
                        # 标准 SSE 格式，透传 id 供浏览器自动追踪 Last-Event-ID
                        yield f"id: {current_id}\ndata: {raw_payload}\n\n"
    
                        try:
                            payload_json = json.loads(raw_payload)
                            ev = payload_json.get("event")
                            status_val = payload_json.get("data", {}).get("status")
    
                            if ev == "run_status" and status_val in terminal_status:
                                return
                        except Exception:
                            pass
            else:
                # 心跳包维持长连接，防止中间网关/Nginx 超时断开
                yield ": ping\n\n"
    
                # 兜底：若推流期间 Worker 故障并在 DB 留存终态
                async with async_session_maker() as fallback_db:
                    active_run = await fallback_db.get(Run, run_id)
                    if active_run and active_run.status in terminal_status:
                        yield f"data: {json.dumps({'event': 'run_status', 'data': {'status': active_run.status}})}\n\n"
                        return
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
### 

    @router.get("/stream/{run_id}")
    async def stream_agent_events(
        request: Request,
        run_id: uuid.UUID,
        last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    ):
        # 仅做轻量校验，毫秒级归还连接
        async with async_session_maker() as db:
            run = await db.get(Run, run_id)
            if not run:
                raise HTTPException(status_code=404, detail="Run 不存在")
    async def event_generator():
        stream_key = f"agent:stream:{run_id}"
        current_id = last_event_id if last_event_id else "0-0"
        terminal_status = {"completed", "failed", "cancelled", "requires_action"}
        idle_ticks = 0
    
        while True:
            # 客户端若关闭网页，立即中断生成器，防止僵尸循环
            if await request.is_disconnected():
                break
    
            response = await redis_client.xread({stream_key: current_id}, count=50, block=2000)
    
            if response:
                idle_ticks = 0  # 有消息，清零空闲计数
                for _, messages in response:
                    for msg_id, fields in messages:
                        current_id = msg_id
                        raw_payload = fields.get("payload", "{}")
                        yield f"id: {current_id}\ndata: {raw_payload}\n\n"
    
                        try:
                            payload_json = json.loads(raw_payload)
                            ev = payload_json.get("event")
                            status_val = payload_json.get("data", {}).get("status")
                            if ev == "run_status" and status_val in terminal_status:
                                return
                        except Exception:
                            pass
            else:
                yield ": ping\n\n"
                idle_ticks += 1
    
                # 每 10 次超时（约 20 秒无新消息），再做一次数据库异常兜底
                if idle_ticks >= 10:
                    idle_ticks = 0
                    async with async_session_maker() as fallback_db:
                        active_run = await fallback_db.get(Run, run_id)
                        if active_run and active_run.status in terminal_status:
                            yield f"data: {json.dumps({'event': 'run_status', 'data': {'status': active_run.status}})}\n\n"
                            return
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

**第二段代码显著优于第一段**，达到了生产级可用的标准，而第一段代码在高并发或长连接场景下存在**致命的数据库连接池耗尽与资源泄露风险**。



两者的核心差异对比如下：



| **维度**             | **第一段代码 (Snippet 1)**          | **第二段代码 (Snippet 2)**                      | **影响与评估**                                               |
| -------------------- | ----------------------------------- | ----------------------------------------------- | ------------------------------------------------------------ |
| **初始 DB 连接管理** | `Depends(get_async_session)` 注入   | `async with async_session_maker()` 手动作用域   | **Snippet 1 致命**：FastAPI 依赖项会在整个 SSE 挂起期间（数分钟/数小时）持续占用连接池连接。Snippet 2 验证完即释放。 |
| **客户端断开感知**   | 无感知机制                          | `await request.is_disconnected()`               | **Snippet 2 胜出**：用户关闭标签页时能立即退出循环，避免后台常驻“僵尸协程”。 |
| **降级查库频次**     | 每 2 秒查一次 DB (`xread` 超时即查) | 计数器节流，每 20 秒查一次 (`idle_ticks >= 10`) | **Snippet 2 胜出**：大幅减轻数据库压力，避免连接数和 QPS 激增打垮 DB。 |

### 为什么第一段代码在生产环境会出严重事故

**1. 数据库连接池瞬间枯竭 (Connection Pool Starvation)**



- **第一段**：通过 `Depends(get_async_session)` 注入的 `db` 对象，其生命周期与整个 HTTP 请求绑定。SSE 是长连接，如果同时有 50 个用户在线监听流，50 个数据库连接就会被长达数十分钟死死占住，新请求将直接报错 `TimeoutError: QueuePool limit exceeded`。
- **第二段**：通过局部上下文管理器校验完数据合法性后，连接在**几毫秒内就归还给连接池**，完全不影响长连接推流。

**2. 僵尸协程与服务端内存泄露**



- **第一段**：没有监听客户端的连接状态。当用户关闭浏览器、刷新页面或移动端切换后台时，服务端不会立即得知，生成器可能继续在后台白白消耗 Redis 连接、CPU 和内存。
- **第二段**：显式注入 `Request` 并在每次循环开始前执行 `await request.is_disconnected()`，一旦客户端断开立即 `break` 退出协程。

**3. 隐形打崩数据库 (Self-inflicted DB DDoS)**



- **第一段**：`xread(..., block=2000)` 在无消息时每 2 秒超时一次，随后**立即**进入 `async with async_session_maker()` 查询 DB。若有 500 个并发长连接处于等待状态，数据库每秒将被动承受 250 次无意义的 `SELECT` 穿透查询。
- **第二段**：引入 `idle_ticks`，将兜底查询频率降为 20 秒一次，降低了 90% 的无效 DB 开销。

### 第二段代码可进一步优化的细节

若要做到极致，第二段代码还可以补齐以下两点：



1. **入口处提前拦截终态**：如果任务在客户端发起连接前就已经处于 `terminal_status`，无需进入 Redis 轮询，直接推一条终态事件并关闭即可。
2. **异常捕获与退出**：外层包裹 `try...finally`，确保在异常断开或退出时可执行日志记录或上下文清理。

Python

```
# 建议的补充逻辑（紧接在 run 校验之后）：
if run.status in {"completed", "failed", "cancelled", "requires_action"}:
    async def completed_generator():
        yield f"data: {json.dumps({'event': 'run_status', 'data': {'status': run.status}})}\n\n"
    return StreamingResponse(completed_generator(), media_type="text/event-stream")
```