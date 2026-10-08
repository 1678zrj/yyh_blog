```python
@broker.task(task_name="merge_file_task")
async def merge_file(
    upload_id: str
):
    """当前文档还是以绝对路径存储在数据库中，未来需要修改"""
    storage = LocalStorage()
    redis = RedisManager.get_client()
    session_manager = SessionManager(redis_client=redis)
    lock_key = f"lock:upload:merge:{upload_id}"
    # 使用分布式锁保证并发安全,后续引入看门狗机制,防止合并期间锁过期
    async with redis.lock(lock_key, timeout=600):
        # 合并任务改造
        session = await session_manager.get_session(upload_id)
        # 幂等校验,通常另一个重复请求拿到锁后,就已经是完成或者失败了
        if session.status in (UploadStatus.FAILED, UploadStatus.COMPLETED):
            return
        total_chunks = session.total_chunks
        source_paths = [build_tmp_path(upload_id, i) for i in range(total_chunks)]
        chunks_dir = source_paths[0].parent
        true_target_path = build_final_path(upload_id, session.file_name)
        fake_target_path = Path(f"{true_target_path}.{uuid.uuid4().hex}.tmp")
        # 数据库永远保证兜底校验
        # 1 合并前秒传检测: 若已有记录,直接复用并清理分片
        async with AsyncSessionLocal() as db:
            existing_file_record = await upload_crud.get_file_record_by_hash(db, session.file_hash)
            # 说明已经合并完成了,worker可能重复执行
            if existing_file_record is not None:
                await session_manager.set_completed(upload_id, existing_file_record.id)
                # 此时只有分片,对分片进行清理
                await cleanup_resources(dirs=[chunks_dir])
                return
        # 2 状态标记(用于精准的失败资源回滚)
        # 临时合并文件是否存在
        temp_merged = False
        # 最终文件是否存在且属于本次任务
        target_merged = False
        try:
            # 将文件进行合并
            actual_hash = await storage.merge_chunks(source_paths, fake_target_path)
            # 临时合并文件此时存在
            temp_merged = True
            # 合并得到的文件的hash值与客户端传来的不符
            if actual_hash.lower() != session.file_hash.lower():
                raise FileHashMismatchError(f"Hash mismatch. Expected: {session.file_hash}, Actual: {actual_hash}")
            # 进行到这里说明合并成功且hash值一致,可以进行原子落盘,
            await aiofiles.os.replace(fake_target_path, true_target_path)
            # 临时合并文件此时不存在,最终文件存在
            temp_merged = False
            target_merged = True
            # 以上操作完成后,可以进行数据库持久化保存了
            async with AsyncSessionLocal() as db:
                try:
                    # 合并成功后将数据插入到数据库
                    file_record = await upload_crud.create_upload(
                        db=db,
                        storage_type=settings.storage_type,
                        storage_key=str(true_target_path),
                        total_size=session.total_size,
                        file_hash=session.file_hash,
                        file_ext=session.file_ext,
                        mime_type=session.mime_type
                    )
                    await db.commit()
                    await db.refresh(file_record)
                    # 运行到这里说明成功落盘了
                    target_merged = False
                    # 先后有讲究,因为运行到这里已经实际落库落盘了,不能进行清理
                    # 所以要防止redis保存影响状态更新
                    await session_manager.set_completed(upload_id, file_record.id)
                # 该异常说明已经有另一个用户传了相同文件并落库了
                except IntegrityError:
                    await db.rollback()
                    # 文件记录已存在的话可以设置状态为completed
                    existing_file_record = await upload_crud.get_file_record_by_hash(db, actual_hash)
                    if existing_file_record is not None:
                        await session_manager.set_completed(upload_id, existing_file_record.id)
                        if existing_file_record.storage_key != str(true_target_path):
                            target_merged = False
                    else:
                        raise
            # 成功完成,清理分片目录
            await cleanup_resources(dirs=[chunks_dir])
        except Exception as e:
            # 统一异常处理: 设置失败 收集残留文件并清理
            await session_manager.set_failed(upload_id, error_msg=str(e))
            clean_files = []
            if temp_merged:
                clean_files.append(fake_target_path)
            if target_merged:
                clean_files.append(true_target_path)
            await cleanup_resources(files=clean_files, dirs=[chunks_dir])
            raise
```

### 问：这段代码怎么样？

这段代码整体设计**非常成熟，具备工业级健壮性**。你已经考虑了分布式锁、幂等性、并发秒传冲突（`IntegrityError`）、文件内容 Hash 校验以及原子落盘（`replace`）。

但在边界条件、资源回收和并发竞态下，仍有几个**隐藏 Bug 和优化点**需要处理：

---

### 关键问题与缺陷

* **秒传冲突导致的文件孤儿（磁盘空间泄漏）：**
当触发 `IntegrityError` 并查到已有记录时：
```python
if existing_file_record.storage_key != str(true_target_path):
    target_merged = False

```


此时 `true_target_path` 已经落盘，但既然复用了旧记录，本次生成的 `true_target_path` 就成了**未被数据库引用的孤儿文件**。此时不仅要把 `target_merged` 置为 `False`，还必须**显式删除当前的 `true_target_path**`，否则该文件将永久滞留磁盘。
* **空分片导致的 `IndexError`：**
`chunks_dir = source_paths[0].parent`，如果 `session.total_chunks <= 0`，此处会直接抛出 `IndexError`。建议直接通过函数获取目录，例如 `build_tmp_dir(upload_id)`。
* **计算密集型与大文件 I/O 阻塞 Event Loop：**
大文件（如几个 GB）在合并与计算 Hash 时是重度 CPU/磁盘 I/O 操作。如果 `storage.merge_chunks` 内部没有妥善处理 `asyncio.to_thread`，会直接**阻塞整个 AsyncIO 事件循环**，导致心跳丢失、Redis 锁无法续期或任务超时。
* **静态 600s 锁超时风险：**
GB 级文件在磁盘读写繁忙时很容易超过 10 分钟。如果锁超时释放，另一个 Worker 会并发进入，导致文件写入竞争损坏。

---

### 优化重构后的代码

```python
import uuid
import asyncio
from pathlib import Path
from sqlalchemy.exc import IntegrityError

@broker.task(task_name="merge_file_task")
async def merge_file(upload_id: str):
    storage = LocalStorage()
    redis = RedisManager.get_client()
    session_manager = SessionManager(redis_client=redis)
    lock_key = f"lock:upload:merge:{upload_id}"

    # 1. 分布式锁保护合并过程
    async with redis.lock(lock_key, timeout=600):
        session = await session_manager.get_session(upload_id)
        if not session or session.status in (UploadStatus.FAILED, UploadStatus.COMPLETED):
            return

        if session.total_chunks <= 0:
            await session_manager.set_failed(upload_id, error_msg="Invalid total_chunks count")
            return

        source_paths = [build_tmp_path(upload_id, i) for i in range(session.total_chunks)]
        chunks_dir = source_paths[0].parent
        
        true_target_path = build_final_path(upload_id, session.file_name)
        fake_target_path = Path(f"{true_target_path}.{uuid.uuid4().hex}.tmp")

        # 2. 合并前秒传拦截
        async with AsyncSessionLocal() as db:
            existing_record = await upload_crud.get_file_record_by_hash(db, session.file_hash)
            if existing_record is not None:
                await session_manager.set_completed(upload_id, existing_record.id)
                await cleanup_resources(dirs=[chunks_dir])
                return

        # 资源追踪状态
        temp_merged = False
        target_merged = False

        try:
            # 3. 合并与哈希校验
            actual_hash = await storage.merge_chunks(source_paths, fake_target_path)
            temp_merged = True

            if actual_hash.lower() != session.file_hash.lower():
                raise FileHashMismatchError(
                    f"Hash mismatch. Expected: {session.file_hash}, Actual: {actual_hash}"
                )

            # 原子落盘
            await aiofiles.os.replace(fake_target_path, true_target_path)
            temp_merged = False
            target_merged = True

            # 4. 数据库持久化及并发落库冲突处理
            async with AsyncSessionLocal() as db:
                try:
                    file_record = await upload_crud.create_upload(
                        db=db,
                        storage_type=settings.storage_type,
                        storage_key=str(true_target_path),
                        total_size=session.total_size,
                        file_hash=session.file_hash,
                        file_ext=session.file_ext,
                        mime_type=session.mime_type
                    )
                    await db.commit()
                    await db.refresh(file_record)
                    target_merged = False  # 正常入库，所有权转移给数据库，后续不可清理
                    await session_manager.set_completed(upload_id, file_record.id)

                except IntegrityError:
                    await db.rollback()
                    # 并发秒传命中：其他 Worker 抢先一步入库了同 Hash 文件
                    existing_record = await upload_crud.get_file_record_by_hash(db, actual_hash)
                    if existing_record is not None:
                        await session_manager.set_completed(upload_id, existing_record.id)
                        
                        # 【核心修复】：如果复用了已存在的文件路径，删除本次落盘的冗余文件，防磁盘泄漏
                        if existing_record.storage_key != str(true_target_path):
                            await cleanup_resources(files=[true_target_path])
                            target_merged = False
                        else:
                            target_merged = False
                    else:
                        raise

            # 成功后清理分片
            await cleanup_resources(dirs=[chunks_dir])

        except Exception as e:
            # 统一异常回滚与清理
            await session_manager.set_failed(upload_id, error_msg=str(e))
            clean_files = []
            if temp_merged:
                clean_files.append(fake_target_path)
            if target_merged:
                clean_files.append(true_target_path)
            await cleanup_resources(files=clean_files, dirs=[chunks_dir])
            raise

```

---

### 后续架构演进建议

* **存储解耦：** 尽快将 `storage_key` 由绝对路径（如 `/data/uploads/...`）改为**相对存储桶/目录的逻辑路径**（如 `2026/09/hash.ext`），便于未来无缝切到 MinIO/S3。
* **分片存在性预检：** 如果客户端在网络中断时发起了合并请求，可能存在部分分片尚未落盘的情况。可以在合并前进行一次 `all(p.exists() for p in source_paths)` 校验。

### 问：请帮我为这个 Redis 分布式锁实现一个基于 asyncio 的后台自动续期看门狗机制。

实现看门狗（Watchdog）的核心在于：**获取锁时赋予较短的初始过期时间（如 30s），并通过后台 `asyncio.Task` 周期性执行 Lua 脚本续期（如每 10s 一次）；当任务退出或异常时，主动停止续期并安全释放锁。**



以下是完整的生产级异步看门狗锁实现及接入示例：



Python

```
import asyncio
import logging
import uuid
from typing import Optional
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Lua 续期脚本：仅当锁的 value 与持有者 token 一致时才刷新过期时间
LUA_RENEW_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
else
    return 0
end
"""

# Lua 释放脚本：仅当锁的 value 与持有者 token 一致时才删除
LUA_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class LockAcquireError(Exception):
    """获取锁超时或失败"""
    pass


class AsyncWatchdogLock:
    """具备后台自动续期的 Redis 异步分布式锁"""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        name: str,
        lease_time: int = 30,
        acquire_timeout: float = 10.0,
        retry_interval: float = 0.2,
    ):
        self.redis = redis_client
        self.key = name
        self.token = uuid.uuid4().hex
        self.lease_time = lease_time  # 锁有效租期（秒）
        self.acquire_timeout = acquire_timeout
        self.retry_interval = retry_interval

        self._watchdog_task: Optional[asyncio.Task] = None
        self._renew_script = self.redis.register_script(LUA_RENEW_SCRIPT)
        self._release_script = self.redis.register_script(LUA_RELEASE_SCRIPT)

    async def __aenter__(self):
        acquired = await self.acquire()
        if not acquired:
            raise LockAcquireError(f"Failed to acquire lock for key: {self.key}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()

    async def acquire(self) -> bool:
        """自旋尝试获取分布式锁"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.acquire_timeout

        while loop.time() < deadline:
            # SET key token NX EX lease_time
            ok = await self.redis.set(
                self.key, self.token, nx=True, ex=self.lease_time
            )
            if ok:
                self._start_watchdog()
                return True
            await asyncio.sleep(self.retry_interval)

        return False

    def _start_watchdog(self):
        """启动后台看门狗协程"""
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _watchdog_loop(self):
        """周期性延长锁的 TTL（默认为 lease_time 的 1/3 间隔）"""
        interval = max(self.lease_time / 3, 1.0)
        lease_ms = int(self.lease_time * 1000)

        try:
            while True:
                await asyncio.sleep(interval)
                # 执行续期
                renewed = await self._renew_script(
                    keys=[self.key],
                    args=[self.token, lease_ms]
                )
                if not renewed:
                    logger.warning(
                        f"Watchdog failed to renew lock: {self.key}. Lock was lost or expired."
                    )
                    break
        except asyncio.CancelledError:
            # 正常退出时的协程取消信号
            pass
        except Exception as e:
            logger.error(f"Unexpected error in lock watchdog: {e}")

    async def release(self):
        """停止看门狗并安全释放锁"""
        # 1. 停止看门狗续期协程
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            finally:
                self._watchdog_task = None

        # 2. Lua 原子删除锁
        try:
            await self._release_script(keys=[self.key], args=[self.token])
        except Exception as e:
            logger.error(f"Error releasing lock {self.key}: {e}")
```

### 如何替换原有任务中的锁

在你的 `merge_file` 任务中，将原有的 `async with redis.lock(lock_key, timeout=600):` 替换为自定义的看门狗锁：



Python

```
@broker.task(task_name="merge_file_task")
async def merge_file(upload_id: str):
    storage = LocalStorage()
    redis = RedisManager.get_client()
    session_manager = SessionManager(redis_client=redis)
    lock_key = f"lock:upload:merge:{upload_id}"

    # 使用看门狗锁：锁默认 TTL 为 30s，后台每 10s 自动续期一次
    # 如果任务耗时 20 分钟，锁会一直被续期；如果 Worker 意外宕机，30s 后锁自动释放
    try:
        async with AsyncWatchdogLock(redis, lock_key, lease_time=30, acquire_timeout=15.0):
            session = await session_manager.get_session(upload_id)
            if not session or session.status in (UploadStatus.FAILED, UploadStatus.COMPLETED):
                return
            
            # ... 后续的文件合并、落盘及入库逻辑 ...
            
    except LockAcquireError:
        # 说明有其他 Worker 正在合并该 upload_id，直接跳过或等待下一次重试
        return
```

### 运行机制说明

- **宕机安全：** `lease_time` 设为 30s。若 Worker 在合并大文件时发生 OOM、掉电或进程崩溃，不再需要傻等 600s，Redis 键在 **30 秒后自动失效**，其他节点可以迅速恢复处理。
- **防误删与防误续：** 续期和释放均通过校验唯一的 `UUID token` 完成，确保不会刷新或误删其他客户端重新持有的锁。
- **优雅关闭：** 当任务正常执行完毕或抛出异常跳出上下文管理器时，`__aexit__` 会立即 `cancel()` 续期协程并执行 Lua 脚本删除 Key，不会留下残留。

### 问：有一块我感觉比较奇怪的是，不带看门狗机制的Redis分布式锁就直接用redis-py自带的lock，但是要加入看门狗机制，就用Redis的SET NX机制自己实现了。

你的直觉非常敏锐，**这个“造轮子”的操作确实没必要**。



很多人一提到“看门狗”，就下意识去模仿 Java Redisson 的底层实现，手写 `SET NX` 和 Lua 脚本。但实际上，**`redis-py` 的 `Lock` 原生就提供了 `reacquire()` 方法**——它的底层早就封装好了安全的 Lua 续期脚本。



### 为什么很多人会手写？

1. **`redis-py` 没有内置后台循环任务：**

   Java 的 Redisson 在获取锁时只要不传超时时间，就会自动在后台挂一个 Timer 续期；而 Python 生态有同步线程（`threading`）和异步协程（`asyncio`）之分，官方库只提供了原子续期的基本原语（`reacquire()` / `extend()`），把“如何起后台任务”交给了业务层。

2. **教程常见误区：**

   大量网文在介绍看门狗原理时习惯从头写 Lua 脚本，导致大家误以为 `redis-py` 不支持续期。

### 最优雅的解法：直接基于 `redis-py` 自带的 Lock 封装

不用手写任何 Lua 脚本，也不用手写 `SET NX`，直接基于 `redis.lock()` 启动一个协程定期调用 `lock.reacquire()` 即可：



Python

```
import asyncio
import logging
from contextlib import asynccontextmanager
from redis.asyncio import Redis
from redis.asyncio.lock import Lock

logger = logging.getLogger(__name__)

class LockAcquireError(Exception):
    pass

@asynccontextmanager
async def watchdog_lock(
    redis: Redis,
    name: str,
    timeout: int = 30,
    blocking_timeout: float = 10.0,
    renew_interval: float = 10.0,
):
    """基于 redis-py 原生 Lock 的异步看门狗上下文管理器"""
    lock: Lock = redis.lock(name, timeout=timeout, blocking_timeout=blocking_timeout)
    
    # 1. 获取锁（底层已处理好 token、NX、blocking 等逻辑）
    acquired = await lock.acquire()
    if not acquired:
        raise LockAcquireError(f"Failed to acquire lock: {name}")

    stop_event = asyncio.Event()

    # 2. 后台续期协程
    async def _renew_loop():
        while not stop_event.is_set():
            try:
                # 用 wait 替代 sleep，能在退出时立刻被唤醒，无需等满周期
                await asyncio.wait_for(stop_event.wait(), timeout=renew_interval)
                break
            except asyncio.TimeoutError:
                # 定时触发：调用 redis-py 自带的 reacquire() 进行 Lua 安全续期
                try:
                    renewed = await lock.reacquire()
                    if not renewed:
                        logger.warning(f"Lock {name} was lost or expired during renewal.")
                        break
                except Exception as e:
                    logger.error(f"Error renewing lock {name}: {e}")
                    break

    task = asyncio.create_task(_renew_loop())

    try:
        yield lock
    finally:
        # 3. 停止看门狗
        stop_event.set()
        await task
        # 4. 原生释放锁（底层自动校验 token 防误删）
        try:
            await lock.release()
        except Exception as e:
            logger.warning(f"Error releasing lock {name}: {e}")
```

### 业务代码直接调用

代码量直接减半，而且可靠性与原生 `redis-py` 完全一致：

Python

```
@broker.task(task_name="merge_file_task")
async def merge_file(upload_id: str):
    storage = LocalStorage()
    redis = RedisManager.get_client()
    session_manager = SessionManager(redis_client=redis)
    lock_key = f"lock:upload:merge:{upload_id}"

    # 初始锁 30 秒，每 10 秒自动续期，任务结束或退出自动清理
    async with watchdog_lock(redis, lock_key, timeout=30, renew_interval=10):
        session = await session_manager.get_session(upload_id)
        if not session or session.status in (UploadStatus.FAILED, UploadStatus.COMPLETED):
            return
        
        # ... 后续合并逻辑不变 ...
```

### 问：好家伙，这个确实相当方便了，但是是不是自己手写和基于 redis-py 自带的 Lock 封装的写法都掌握比较好？

**是的，而且两者解决的是完全不同层面的问题：手写是“内功心法”，封装是“工程交付”。**



### 为什么手写实现必须吃透？

手写版本的价值不在于代码本身，而在于它逼你掌握分布式系统的几个核心边界：



- **原子性与竞态条件（Race Condition）：**

  为什么不能先 `GET` 检查 token 再 `DEL` 或 `EXPIRE`？因为在网络抖动、进程被系统挂起（GC / 缺页中断）时，这两个独立操作之间存在时间窗口。理解手写 Lua 脚本，才算真正理解 Redis 的单线程原子执行模型。

- **防止误删与误续期（ABA 问题）：**

  理解为什么必须注入唯一的客户端标识（`UUID / token`）。如果锁已超时被其他 Worker 获取，无脑续期或删除会导致“误把别人的锁当成自己的”。

- **跨语言迁移与定制能力：**

  如果切到 Go、Rust 或 Node.js，或者遇到某些轻量级客户端根本没有现成的 `reacquire` 方法，你不需要依赖第三方 Redisson 级别的重型库，10 分钟就能用 Lua 落地一套安全的分布式锁。

- **处理复杂业务锁：**

  当业务需要支持**可重入锁（Reentrant Lock）**、**读写锁（RWLock）\**或\**公平锁**时，`redis-py` 的原生 `Lock` 无法直接满足需求，必须靠手写更复杂的 Lua 字典结构来实现。

### 为什么生产环境首选基于官方库封装？

既然手写能学到原理，为什么生产环境更推荐封装 `redis-py`？



- **网络与脚本开销优化（EVALSHA）：**

  手写 Lua 如果每次都执行 `EVAL`，会把整段 Lua 代码通过网络发给 Redis。`redis-py` 内部封装了 `register_script`，首次加载后会自动通过 `EVALSHA` 传输 SHA1 摘要执行，在大并发下显著降低网络带宽。

- **集群与边缘边界处理：**

  官方库对 Redis Cluster、连接池重连（ConnectionPool）、Pipeline 兼容性做了大量边界覆盖，自己造轮子容易在 Redis 发生主从切换或网络波动时出现偶发诡异 bug。

- **团队心智成本低：**

  业务代码保持整洁，其他维护者一眼就能看出这是在标准 `redis.lock` 之上增加了自动续期逻辑，不需要逐行 Review 你的 Lua 脚本逻辑。

### 两者的使用定位

| **场景**                       | **推荐方式**               | **关注点**                                             |
| ------------------------------ | -------------------------- | ------------------------------------------------------ |
| **日常业务开发 / 生产落地**    | 基于 `redis-py` 封装看门狗 | 代码行数少、利用官方测试用例的稳健性、网络传输优化     |
| **技术面试 / 架构设计推演**    | 手写 `SET NX` + Lua 脚本   | 解释为什么需要原子性、为什么会有误删风险、锁失效的根因 |
| **高阶定制（重入/公平/计数）** | 手写底层 Lua 数据结构      | 突破标准库功能限制                                     |