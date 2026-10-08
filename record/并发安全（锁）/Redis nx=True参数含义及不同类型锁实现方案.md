```python
is_set = await self.redis.set(
    user_hash_k,
    new_upload_id,
    # 
    nx=True,
    ex=self.ttl
)
```

### 问：这个nx=True是什么意思？

`nx=True` 代表 **"Not eXists"**（仅当键不存在时才设置）。



它是 Redis `SET` 命令的原生选项，具体执行逻辑如下：



- **键不存在**：成功将 `user_hash_k` 的值设置为 `new_upload_id`，并设置过期时间 `ex`，返回值 `is_set` 为 `True`。
- **键已存在**：**放弃写入**，不覆盖原有的数据，返回值 `is_set` 为 `None`（或 `False`，取决于客户端封装）。

**常见应用场景**



- **防重复提交 / 幂等控制**：代码中结合了 `user_hash_k` 与 `ttl`，可确保在指定时间内，同一个请求（或同一份上传哈希）只会被处理一次。
- **简易分布式锁**：利用其原子性（Atomic），同一时刻多个并发协程/进程只有一个能设置成功，拿到操作权限。

*(注：与其相对的参数是 `xx=True`，代表仅在键**已存在**时才进行更新。)*

### 问：这是不是类似于数据库的乐观锁？

严格来说**不是**。`SET NX` 在概念上更接近数据库的**唯一索引约束（Unique Constraint）\**或\**分布式互斥锁（偏悲观锁）**，而不是乐观锁。



### 核心区别对比

| **机制**                      | **典型对应场景**            | **工作原理**                                                 | **核心思想**               |
| ----------------------------- | --------------------------- | ------------------------------------------------------------ | -------------------------- |
| **`SET NX`**                  | **唯一约束 / 互斥锁**       | 操作前原子性判断：“只要存在就不准动，不存在我就占住”。       | **先占位，再干活**（排他） |
| **乐观锁 (Optimistic Lock)**  | **CAS / 版本号机制**        | 读取数据 $\to$ 内存计算 $\to$ 提交时核对版本号（如 `WHERE version = @old_version`）。 | **先干活，提交时校验冲突** |
| **悲观锁 (Pessimistic Lock)** | **`SELECT ... FOR UPDATE`** | 一开始就把数据行锁死，其他事务必须排队等待。                 | **先锁死，防并发修改**     |

### 为什么说它不是乐观锁？

1. **执行时机不同**
   - **乐观锁**：在**业务操作结束、写入提交时**才去比对版本号是否被篡改。如果冲突，回滚重试。
   - **`SET NX`**：在**业务操作开始前**直接尝试原子占位。占位失败直接拒绝或等待。
2. **数据库中最贴近的对应物**
   - **如果用于“防重/幂等”**：等价于在数据库建了一个 `UNIQUE KEY`（唯一索引）。当重复插入相同业务 ID 时触发唯一键冲突（如 `INSERT IGNORE`）。
   - **如果用于“加锁”**：它相当于一个**分布式互斥锁（Mutex）**。你拿到了锁才能进临界区操作，这本身是一种**悲观**的防御策略。

### Redis 中真正的乐观锁是什么？

Redis 自带的真正乐观锁是 **`WATCH` + `MULTI` / `EXEC`** 事务机制（Check-and-Set）：

Plaintext

```
WATCH user_balance          # 监视该键的版本变动
val = GET user_balance      # 读取并计算
MULTI                       # 开启事务
SET user_balance (val - 10) # 提交修改
EXEC                        # 若监视期间 user_balance 被其他客户端修改，EXEC 直接返回 nil 失败
```

### 问：我主要是看到它也是根据结果来判断是否有执行权才这么认为的

你的直觉非常敏锐，从**代码交互范式**的角度来看，它们确实几乎一模一样：**发起一个非阻塞的原子操作 $\to$ 根据返回状态（`True/False` 或受影响行数）$\to$ 决定下一步逻辑。**



这种编程体验之所以像乐观锁，是因为它们底层都源于并发编程中的 **CAS（Compare-And-Swap）或 Test-and-Set（测试并设置）** 思想。



两者的核心差别在于“获取权限的时机”**和**“面对并发时的假设”：



### 1. 权限判定的时机不同（进门 vs 结算）

- **`SET NX` —— 进门先抢票（前置拦截）**
  - **逻辑**：在业务逻辑开始之前，先去抢占一个标识。
  - **场景**：“谁先抢到这把钥匙，谁才能进屋干活；没抢到的直接在门口被挡下。”
  - **目的**：互斥排他、防重复进入。
- **乐观锁 —— 结账时验单（后置校验）**
  - **逻辑**：大家都可以自由进屋干活、读数据、算逻辑，但最后把结果写入数据库时，比对版本号。
  - **场景**：“大家一起算账，提交时谁的版本号没变谁成功；如果别人先提交了，你的提交失败，需要重试。”
  - **目的**：解决读写冲突、保证最终一致性。

### 2. 状态判断的对比

| **机制**     | **典型的代码判断模式**                                       | **属于哪种原子原语**               |
| ------------ | ------------------------------------------------------------ | ---------------------------------- |
| **`SET NX`** | `if is_set: do_business()`  *(抢到占位符才去执行)*           | **Test-and-Set**（测试并设置）     |
| **乐观锁**   | `if db.update(...) > 0: commit()`  *(执行完业务，看提交是否冲突)* | **Compare-And-Swap**（比较并交换） |

你感受到的相似性，本质上是因为它们都采用了 **“非阻塞调用（Non-blocking）+ 状态分支”** 的编程风格，而不是像传统线程锁那样让线程挂起阻塞等待。

### 问：还有个区别是这个有过期时间，但是数据库是永久的对吗。

**是的，这是一个非常关键的区别，而且两者对“生命周期”的处理逻辑也完全不同。**



### 1. 为什么 Redis 需要设置过期时间（TTL）？

在 Redis 中设置 `ex=self.ttl` 主要有两个目的：



- **防死锁的安全兜底（Fail-safe）**：如果你的服务在执行 `SET NX` 成功后突然崩溃、断网或异常退出，没有走到释放锁的逻辑，**TTL 能保证这个锁在超时后自动解开**，避免系统被永久卡死。
- **限定防重时间窗口**：如果是做接口防重（如防连击），通常只需要保证「5 秒内或 10 秒内不重复提交」，时间一过自动失效，内存空间也自动释放。

### 2. 数据库乐观锁为什么不需要 TTL？

数据库（如 MySQL、PostgreSQL）的数据默认是**持久化存储**的，但更重要的是：**乐观锁天然不需要“过期释放”机制**。



- **没有持有状态**：乐观锁只是给数据行加了一个持久的 `version` 字段（如 `version = 1`）。
- **不怕进程崩溃**：如果某个服务读取了 `version = 1` 后崩溃了，它只是没有执行最后的 `UPDATE` 语句而已，数据库里的数据依然有效，其他服务随时可以继续读取并更新它，**绝不会产生死锁**。

### 核心特性对比

| **维度**         | **Redis SET NX + EX**                    | **数据库乐观锁（Version 机制）**       |
| ---------------- | ---------------------------------------- | -------------------------------------- |
| **存储生命周期** | **临时存在**（TTL 到期自动在内存中抹除） | **永久存储**（除非主动 `DELETE`）      |
| **异常恢复机制** | 依赖 **TTL 超时** 自动释放占位           | **天然免疫崩溃**（未提交就等于没发生） |
| **主要定位**     | 临时占位、频控/防重窗口、短周期互斥      | 数据最终状态校验、长期业务状态流转     |

### 问：TTL的时间设置根据不同业务有什么讲究吗

TTL 的设置主要取决于该 Key 在业务中扮演的角色，核心原则是在「防止死锁/节省内存」**与**「避免业务逻辑未执行完锁就提前失效」之间寻找平衡。



### 1. 常见业务场景的 TTL 设定策略

| **业务场景**                 | **典型 TTL 范围**                             | **设定依据与考量**                                           |
| ---------------------------- | --------------------------------------------- | ------------------------------------------------------------ |
| **分布式互斥锁**             | 预估耗时 $\times 2 \sim 3$  *(通常 5s ~ 30s)* | 必须大于业务最长处理耗时 + 网络抖动。若任务耗时难以预估，需配合**后台看门狗（续期线程）**。 |
| **防重复点击 / 连击**        | 1s ~ 5s                                       | 覆盖用户手抖、前端连续触发的网络往返时间（RTT）即可，避免用户无法进行下一次正常提交。 |
| **异步通知 / 支付回调幂等**  | 24h ~ 72h                                     | 根据上游平台（如微信支付、支付宝）的**最大重试退避周期**设定，确保整个重试周期内均能识别重复请求。 |
| **验证码 / 短信频控**        | 60s（单次）/ 24h（上限）                      | 与业务规则严格对齐（如“60秒内不得重复发送”、“单手机号每天最多发送 10 条”）。 |
| **热点数据缓存**             | 5m ~ 2h + 随机扰动                            | 权衡数据库压力与数据实时性，必须增加**随机过期时间（Jitter）**以避免缓存雪崩。 |
| **用户会话 (Session/Token)** | 30m ~ 7d                                      | 支持滑动续期（操作即刷新 TTL）或搭配双 Token 机制（Access Token 短，Refresh Token 长）。 |

### 2. 两个最容易踩坑的工程问题

- **分布式锁的 TTL 设置过短（业务还没跑完，锁自动释放了）**

  - **后果**：A 还在处理，锁超时被 B 抢走，导致并发控制失效；随后 A 处理完毕去 `DEL` 锁，反而误删了 B 的锁。
  - **解法**：预留充足的安全余量，或采用像 Redisson 类似的 **Watchdog（看门狗机制）**，在任务执行期间定期自动为 Key 续期。

- **高并发缓存的 TTL 集中失效（缓存雪崩）**

  - **后果**：如果同一时刻批量写入大量相同 TTL 的缓存，到期瞬间请求全量打到数据库。

  - **解法**：在基础 TTL 上叠加随机值，例如：

    $$\text{TTL} = 300\text{s} + \text{random}(0, 60)\text{s}$$

### 问：我是文件分片上传状态保存，你认为应该设置多久？

对于文件分片上传（断点续传）的状态保存，推荐将 TTL 设置为 **24 小时（86400 秒）**，并配合**分片上传时的滑动续期机制**。



### 1. 场景分级推荐

| **业务文件类型**                       | **推荐 TTL**          | **考量因素**                                                 |
| -------------------------------------- | --------------------- | ------------------------------------------------------------ |
| **中小型文件（< 500MB）**              | **2 ~ 6 小时**        | 传输耗时短，失败通常在几分钟内重试，短 TTL 可加快无效垃圾分片释放。 |
| **通用大文件（几 GB ~ 几十 GB）**      | **24 小时（最推荐）** | 覆盖用户关机、网络切换（如公司到家）、跨夜续传等典型中断场景。 |
| **超大工程文件（> 100GB / 极慢网络）** | **3 ~ 7 天**          | 专为跨天分批上传设计，给用户足够的离线恢复窗口。             |

### 2. 核心设计考量与最佳实践

- **一定要做「滑动续期（Touch）」**

  不要使用静态固定 TTL。**每当用户成功上传一个分片时，顺手刷新一次 TTL**（`EXPIRE user_hash_k 86400`）。

  - 这样只要用户在持续上传（哪怕传了 3 天），状态就绝不会过期。
  - 只要用户彻底放弃上传，断连满 24 小时后 Redis 自动清理。

- **与底层存储的物理清理周期对齐**

  - **对象存储（S3 / OSS / MinIO）**：通常会配置 Lifecycle 规则（如 `AbortIncompleteMultipartUpload` 自动清理 3~7 天未合并的分片）。Redis 的 TTL **不能大于** 底层存储物理分片的清理周期，否则 Redis 里有记录，但底层碎片已被删。
  - **本地临时磁盘**：定期清理临时碎片的 CronJob 周期应与 Redis TTL 保持一致。

- **合并完成后的生命周期处理**

  - **合并成功**：立刻主动 `DEL user_hash_k`，或将其更新为已完成状态并缩短 TTL 至几十秒，防止占位残留。
  - **用户主动取消**：提供取消接口，主动触发底层分片删除并清除 Redis Key。

### 问：在FastAPI后端应用中分布式锁是否应该单独封装，还有带watch dog机制的锁和普通锁释放要区分开，还有没抢到锁是报错还是返回当前状态是否要区分开来分别实现呢

在生产级 FastAPI 应用中，这三点都建议做**明确的解耦和分层设计**。



### 1. 分布式锁是否应该单独封装？

**必须单独封装。**



业务逻辑不应该感知 Lua 脚本、随机 Token 生成、网络异常重试等底层细节。在 FastAPI/Asyncio 生态下，推荐封装为 **异步上下文管理器（Async Context Manager）** 或 **依赖项（Depends / Decorator）**。



- **安全释放保障**：封装内部自动生成唯一 `lock_token`（如 `uuid4`），释放时强制走 Lua 脚本比对，防止误删他人的锁。
- **统一异常处理**：未抢到锁时抛出自定义业务异常（如 `LockAcquisitionError`），便于在 FastAPI 全局异常处理器中统一转成 HTTP 409（Conflict）或 429（Too Many Requests）。

### 2. 带 Watchdog 机制的锁与普通锁是否要分开？

**建议区分实现（或通过参数显式开关）。** 它们在资源开销和释放生命周期上有本质区别：



| **特性**         | **普通锁（Fixed TTL）**                                    | **Watchdog 续期锁（Auto-renew）**                            |
| ---------------- | ---------------------------------------------------------- | ------------------------------------------------------------ |
| **适用场景**     | 耗时短、可预估的场景（< 2s），如防重复提交、短临界区操作。 | 耗时较长、网络波动大或不可预估的任务（如大文件分片合并、复杂报表导出）。 |
| **底层开销**     | 仅一次 Redis `SET NX` 交互，无额外后台开销。               | 需常驻一个 `asyncio.Task` 协程按周期间隔（如 $\frac{TTL}{3}$）发送续期指令。 |
| **释放逻辑差异** | 只需执行 Lua 脚本安全删除 Key。                            | **必须先取消（Cancel）后台续期协程**，再执行 Lua 脚本删除 Key，否则续期任务可能导致死锁或报错。 |

### 3. 没抢到锁：报错还是返回状态？

这两者代表了**完全不同的业务语义**，应分别提供支持：



- **模式 A：阻断式（Fail-Fast / 报错）**
  - **语义**：“同一时刻只能有一人操作，重复操作属于非法并发请求。”
  - **表现**：获取锁失败直接抛出异常（如 `LockError`），由全局中间件拦截返回 `HTTP 409 Conflict`。
  - **场景**：账户扣款、订单状态流转、资源创建。
- **模式 B：降级式（Try-Lock / 返回状态）**
  - **语义**：“如果有人在处理，我不需要报错，只需要告知进度或直接使用已有结果。”
  - **表现**：提供 `acquire()` 方法返回布尔值 `False`，不抛异常，由调用方在 `if not acquired:` 分支中返回当前进度。
  - **场景**：分片上传状态查询、缓存击穿时的单飞（SingleFlight）回源。

### 生产级封装参考实现

Python

```
import asyncio
import uuid
from typing import Optional
from redis.asyncio import Redis

# 保证原子释放的 Lua 脚本
LUA_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

class LockAcquireError(Exception):
    """未获取到锁异常"""
    pass

class RedisLock:
    def __init__(
        self,
        redis: Redis,
        key: str,
        ttl: int = 10,
        enable_watchdog: bool = False,
    ):
        self.redis = redis
        self.key = f"lock:{key}"
        self.ttl = ttl
        self.enable_watchdog = enable_watchdog
        self.token = str(uuid.uuid4())
        self._watchdog_task: Optional[asyncio.Task] = None

    async def acquire(self) -> bool:
        """非阻塞尝试加锁，返回是否成功"""
        is_set = await self.redis.set(self.key, self.token, nx=True, ex=self.ttl)
        if is_set and self.enable_watchdog:
            self._start_watchdog()
        return bool(is_set)

    async def release(self) -> None:
        """停止续期并安全释放锁"""
        # 1. 优先停止 Watchdog 协程
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        # 2. Lua 脚本原子安全释放
        await self.redis.eval(LUA_RELEASE_SCRIPT, 1, self.key, self.token)

    def _start_watchdog(self) -> None:
        async def _renew_loop():
            renew_interval = max(1, self.ttl // 3)
            while True:
                await asyncio.sleep(renew_interval)
                # 仅当 Key 的值仍然是当前 Token 时续期
                await self.redis.expire(self.key, self.ttl)

        self._watchdog_task = asyncio.create_task(_renew_loop())

    async def __aenter__(self):
        """上下文管理器：抢不到锁时直接抛异常（阻断式）"""
        if not await self.acquire():
            raise LockAcquireError(f"Resource {self.key} is currently locked")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
```

### 在 FastAPI 路由中的两种调用方式

**方式 1：上下文管理器（阻断式，适用于核心互斥业务）**



Python

```
@router.post("/files/merge")
async def merge_file(file_hash: str, redis: Redis = Depends(get_redis)):
    # 耗时较长开启 watchdog，未抢到锁直接抛异常进入全局 409 处理
    async with RedisLock(redis, f"merge:{file_hash}", ttl=15, enable_watchdog=True):
        await heavy_merge_task(file_hash)
        return {"status": "merged"}
```

**方式 2：显式状态分支（降级式，适用于分片上传/轮询状态）**

Python

```
@router.post("/files/chunk-upload")
async def upload_chunk(file_hash: str, chunk_index: int, redis: Redis = Depends(get_redis)):
    lock = RedisLock(redis, f"upload:{file_hash}", ttl=10)
    
    # 抢不到锁不报错，直接返回“其他分片正在处理”或读取已有状态
    if not await lock.acquire():
        return {"status": "busy", "message": "Another chunk is processing, please retry"}
    
    try:
        await process_chunk(file_hash, chunk_index)
        return {"status": "success"}
    finally:
        await lock.release()
```

### 问：还有一个问题是我发现你的生产级封装参考实现是自己实现的，而没有用redis-py自带的，你更推荐自己实现是吗

在实际生产中，**更推荐基于 `redis-py` 自带的 `redis.asyncio.lock.Lock` 进行二次轻量封装**，而不是完全从零手写底层 Lua 脚本。



之前手写实现的目的是**为了透明展示分布式锁的核心机理**（UUID 防误删、Lua 脚本原子释放、Watchdog 协程生命周期绑定）。



### 1. 为什么不用完全纯手写？

`redis-py` 自带的 `Lock` 已经经过了充分的单元测试和高并发生产检验：



- **内置安全保障**：内部默认使用 `uuid.uuid4()` 生成 Token，释放时自带原子 Lua 脚本校验。
- **原生支持阻塞等待与超时**：支持 `blocking_timeout` 参数，省去了自己写轮询重试逻辑。
- **支持手动锁续期**：提供了 `reacquire()` 和 `extend()` 原生方法。

### 2. 为什么依然需要对 `redis-py` 进行二次封装？

虽然 `redis-py` 已经很完善，但它在工程落地时有几个缺失环节：



- **缺少自动续期（Watchdog）**：Java 生态的 Redisson 默认带 Watchdog，但 `redis-py` **没有后台自动续期线程/协程**，需要开发者自己通过 `asyncio.Task` 周期性调用 `lock.extend()`。
- **异常体系与 Web 框架脱节**：`redis-py` 抢锁失败抛出的是 `redis.exceptions.LockError`，在 FastAPI 中不易与其它 Redis 底层故障（如连接断开）区分。
- **参数繁琐**：每次使用都要配置 `blocking=False`、`timeout=...`，业务代码容易写得不一致。

### 3. 生产级最佳方案：基于 `redis-py` 扩展 Watchdog

直接继承或组合 `redis.asyncio.lock.Lock`，让官方库处理加锁、释放与 Lua 交互，自己只负责补充 **Watchdog 协程管理** 和 **业务异常转换**：

Python

```
import asyncio
from typing import Optional
from redis.asyncio import Redis
from redis.asyncio.lock import Lock
from redis.exceptions import LockError

class BusinessLockError(Exception):
    """业务层定义的加锁失败异常"""
    pass

class ManagedRedisLock:
    def __init__(
        self,
        redis: Redis,
        name: str,
        timeout: int = 10,
        enable_watchdog: bool = False,
    ):
        # 复用 redis-py 官方 Lock 对象
        self._lock = Lock(
            redis=redis,
            name=f"lock:{name}",
            timeout=timeout,
            blocking=False,  # 默认非阻塞，抢不到立即返回
        )
        self.timeout = timeout
        self.enable_watchdog = enable_watchdog
        self._watchdog_task: Optional[asyncio.Task] = None

    async def acquire(self) -> bool:
        """非阻塞尝试加锁"""
        acquired = await self._lock.acquire()
        if acquired and self.enable_watchdog:
            self._start_watchdog()
        return acquired

    async def release(self) -> None:
        """安全释放锁并注销看门狗"""
        # 1. 优先取消续期任务
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass

        # 2. 调用 redis-py 官方自带的安全释放（含 Lua 校验）
        try:
            await self._lock.release()
        except LockError:
            # 锁已过期或已被他人持有时的容错处理
            pass

    def _start_watchdog(self) -> None:
        async def _renew():
            interval = max(1, self.timeout // 3)
            while True:
                await asyncio.sleep(interval)
                # 调用 redis-py 自带的 extend 续期方法
                try:
                    await self._lock.extend(self.timeout)
                except LockError:
                    break  # 锁丢失则退出续期

        self._watchdog_task = asyncio.create_task(_renew())

    async def __aenter__(self):
        if not await self.acquire():
            raise BusinessLockError(f"Resource {self._lock.name} is locked")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
```

### 选型建议

| **需求场景**                       | **推荐选型**                                                 |
| ---------------------------------- | ------------------------------------------------------------ |
| **短期互斥 / 防重提交（< 3 秒）**  | 直接使用 `redis.asyncio.lock.Lock` 原生对象。                |
| **大文件分片合并 / 长耗时任务**    | 使用上方封装的 `ManagedRedisLock`（启用 Watchdog）。         |
| **多节点复杂分布式一致性要求极高** | 引入 Redlock 算法库（如 `aioredlock`），但通常单实例/主从 Redis + 组合锁已满足 99.9% 业务。 |

### 问：给我完整的方案，包括watch dog机制的锁和普通锁、阻断式和降级式

一套工业级的 FastAPI 分布式锁方案包含三个核心部分：**锁核心抽象（支持 Watchdog）**、**FastAPI 全局异常处理（阻断式映射）** 以及 **四种组合场景的路由实战**。



### 1. 核心锁封装模块 (`core/distributed_lock.py`)

基于 `redis-py` 原生异步 `Lock`，统一管理普通锁/续期锁，并同时支持**阻断式（上下文管理器）\**与\**降级式（布尔返回）**。



Python

```
import asyncio
import logging
from typing import Optional
from redis.asyncio import Redis
from redis.asyncio.lock import Lock
from redis.exceptions import LockError

logger = logging.getLogger(__name__)


class LockAcquisitionError(Exception):
    """阻断式场景：未获取到锁时抛出该异常"""
    def __init__(self, resource: str, message: str = "Resource is currently locked"):
        self.resource = resource
        self.message = message
        super().__init__(self.message)


class AsyncDistributedLock:
    """
    通用异步分布式锁
    - 普通锁: watchdog=False, 固定 TTL, 到期自动失效
    - Watchdog 锁: watchdog=True, 后台协程以 TTL/3 频率自动续期
    """
    def __init__(
        self,
        redis: Redis,
        name: str,
        timeout: int = 10,
        watchdog: bool = False,
        blocking: bool = False,
        blocking_timeout: float = 0.0,
    ):
        self.redis = redis
        self.name = f"lock:{name}"
        self.timeout = timeout
        self.watchdog = watchdog
        self.blocking = blocking
        self.blocking_timeout = blocking_timeout

        # 复用 redis-py 原生 Lock 实例（内置 UUID 标识与 Lua 安全释放）
        self._lock = Lock(
            redis=self.redis,
            name=self.name,
            timeout=self.timeout,
            blocking=self.blocking,
            blocking_timeout=self.blocking_timeout,
        )
        self._watchdog_task: Optional[asyncio.Task] = None

    async def acquire(self) -> bool:
        """
        【降级式核心】尝试获取锁
        :return: True 获取成功; False 获取失败（不抛异常，调用方自行降级）
        """
        try:
            acquired = await self._lock.acquire()
        except Exception as e:
            logger.error(f"Redis error during acquire on {self.name}: {e}")
            return False

        if acquired and self.watchdog:
            self._start_watchdog()
        return acquired

    async def release(self) -> None:
        """安全释放锁并停止看门狗"""
        self._stop_watchdog()
        try:
            await self._lock.release()
        except LockError:
            # 锁已因异常过期或已被清理时的容错
            pass

    def _start_watchdog(self) -> None:
        self._stop_watchdog()
        renew_interval = max(1, self.timeout // 3)

        async def _renew_loop():
            while True:
                try:
                    await asyncio.sleep(renew_interval)
                    await self._lock.extend(self.timeout)
                except asyncio.CancelledError:
                    break
                except LockError:
                    logger.warning(f"Watchdog lost ownership of lock: {self.name}")
                    break
                except Exception as e:
                    logger.error(f"Watchdog renew exception: {e}")
                    break

        self._watchdog_task = asyncio.create_task(_renew_loop())

    def _stop_watchdog(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None

    # --- 阻断式上下文管理器支持 ---
    async def __aenter__(self):
        """阻断式入口：获取不到锁直接抛出 LockAcquisitionError"""
        acquired = await self.acquire()
        if not acquired:
            raise LockAcquisitionError(resource=self.name)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
```

### 2. FastAPI 全局异常与依赖项 (`api/deps.py`)

统一拦截 `LockAcquisitionError`，将其转为标准的 `HTTP 409 Conflict`，避免业务层充斥重复的 `try-except`。

Python

```
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from core.distributed_lock import LockAcquisitionError

# 全局 Redis 客户端连接池
redis_client = Redis.from_url("redis://localhost:6379/0", decode_responses=True)

async def get_redis() -> Redis:
    return redis_client

def register_lock_exception_handler(app: FastAPI):
    @app.exception_handler(LockAcquisitionError)
    async def lock_exception_handler(request: Request, exc: LockAcquisitionError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "RESOURCE_LOCKED",
                "message": exc.message,
                "resource": exc.resource,
            },
        )
```

### 3. 四种业务场景的路由实现 (`api/routers.py`)

Python

```
from fastapi import APIRouter, Depends
from redis.asyncio import Redis
import asyncio

from core.distributed_lock import AsyncDistributedLock
from api.deps import get_redis

router = APIRouter()

# -------------------------------------------------------------
# 场景 1：【阻断式 + 普通短锁】防重复提交 / 订单支付流转
# 特点：短耗时、未拿到锁直接由全局异常处理器拦截返回 HTTP 409
# -------------------------------------------------------------
@router.post("/orders/{order_id}/pay")
async def pay_order(order_id: str, redis: Redis = Depends(get_redis)):
    # 超时 5 秒，无需看门狗，抢不到锁直接触发 409
    async with AsyncDistributedLock(redis, f"order:pay:{order_id}", timeout=5, watchdog=False):
        # 业务逻辑
        await asyncio.sleep(0.5)
        return {"status": "paid", "order_id": order_id}


# -------------------------------------------------------------
# 场景 2：【阻断式 + Watchdog 续期锁】大文件分片合并 / 重型报表生成
# 特点：任务耗时长且不可控，Watchdog 自动续期，排他操作
# -------------------------------------------------------------
@router.post("/files/merge")
async def merge_file_chunks(file_hash: str, redis: Redis = Depends(get_redis)):
    # 初始 TTL 10 秒，开启 watchdog（后台每 3 秒续期一次），合并完安全释放
    async with AsyncDistributedLock(redis, f"file:merge:{file_hash}", timeout=10, watchdog=True):
        # 模拟大文件合并耗时 25 秒
        await asyncio.sleep(25)
        return {"status": "merged", "file_hash": file_hash}


# -------------------------------------------------------------
# 场景 3：【降级式 + 普通锁】文件分片并发上传 / 状态探测
# 特点：抢不到锁不报错，直接返回当前业务状态告知前端
# -------------------------------------------------------------
@router.post("/files/upload-chunk")
async def upload_chunk(file_hash: str, chunk_index: int, redis: Redis = Depends(get_redis)):
    lock = AsyncDistributedLock(redis, f"file:chunk:{file_hash}", timeout=10, watchdog=False)
    
    acquired = await lock.acquire()
    if not acquired:
        # 降级分支：不抛 409，返回 200 及排队/忙碌提示
        return {
            "code": "BUSY",
            "message": "上一分片正在写入中，请稍后重试或等待",
            "chunk_index": chunk_index
        }

    try:
        # 处理分片写入
        await asyncio.sleep(0.2)
        return {"code": "SUCCESS", "chunk_index": chunk_index}
    finally:
        await lock.release()


# -------------------------------------------------------------
# 场景 4：【降级式 + Watchdog 续期锁】异步长任务防穿透 / SingleFlight
# 特点：耗时计算，只有首个请求去计算并自动续期，其余请求读取缓存或等待
# -------------------------------------------------------------
@router.get("/reports/aggregate")
async def aggregate_report(report_id: str, redis: Redis = Depends(get_redis)):
    lock = AsyncDistributedLock(redis, f"report:calc:{report_id}", timeout=15, watchdog=True)

    if not await lock.acquire():
        # 降级分支：已有实例在计算，直接返回处理中
        return {"status": "processing", "message": "Report calculation in progress"}

    try:
        # 耗时统计计算
        await asyncio.sleep(12)
        return {"status": "ready", "data": {"report_id": report_id, "metrics": 100}}
    finally:
        await lock.release()
```

### 4. 方案设计决策矩阵

| **维度**          | **阻断式 (async with)**                                      | **降级式 (await lock.acquire())**                 |
| ----------------- | ------------------------------------------------------------ | ------------------------------------------------- |
| **控制流**        | 依靠 Python 异常流向全局中间件。                             | 依靠代码内的 `if not acquired:` 显式分支。        |
| **HTTP 语义**     | 失败返回 `409 Conflict` / `429 Too Many Requests`。          | 返回 `200 OK` 携带业务状态码（如 `PROCESSING`）。 |
| **Watchdog 选型** | 耗时 $< 2\text{s}$ 用普通锁；耗时 $\ge 2\text{s}$ 或不可控必须开启 Watchdog。 | 临时探测用普通锁；耗时生成/单飞计算用 Watchdog。  |