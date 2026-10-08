```python
import json
import time
from typing import Optional, Set, Tuple
from pydantic import BaseModel, Field
import redis.asyncio as aioredis
from app.schemas.upload_schema import UploadStatus
from app.redis.redis_client import RedisManager


class UploadSession(BaseModel):
    user_id: int
    upload_id: str
    file_name: str
    file_hash: str
    file_ext: Optional[str] = None
    mime_type: Optional[str] = None
    total_size: int
    chunk_size: int
    total_chunks: int
    status: UploadStatus = UploadStatus.UPLOADING
    uploaded_chunks: Set[int] = Field(default_factory=set)
    file_record_id: Optional[int] = None
    error_msg: Optional[str] = None
    updated_at: float = 0.0


class SessionManager:
    # Lua 脚本 1: 原子判定任务，活跃态或已完成态均滑动续期并直接返回
    INIT_SESSION_LUA = """
    local user_hash_key = KEYS[1]
    local new_upload_id = ARGV[1]
    local meta_prefix   = ARGV[2]
    local chunks_prefix = ARGV[3]
    local meta_json     = ARGV[4]
    local ttl           = tonumber(ARGV[5])

    local existing_upload_id = redis.call('GET', user_hash_key)

    if existing_upload_id then
        local existing_meta_key = meta_prefix .. existing_upload_id
        local raw_meta = redis.call('GET', existing_meta_key)

        if raw_meta then
            local data = cjson.decode(raw_meta)
            -- 活跃态或已完成态：统一续期并返回已有会话
            if data['status'] == 'uploading' or data['status'] == 'merging' or data['status'] == 'completed' then
                redis.call('EXPIRE', user_hash_key, ttl)
                redis.call('EXPIRE', existing_meta_key, ttl)
                redis.call('EXPIRE', chunks_prefix .. existing_upload_id, ttl)
                return {0, existing_upload_id, raw_meta}
            end

            -- 仅在失败态清理历史残留
            redis.call('DEL', chunks_prefix .. existing_upload_id)
            redis.call('DEL', existing_meta_key)
        end
    end

    -- 任务不存在/过期/失败：原子创建新任务并初始化 TTL
    local new_meta_key = meta_prefix .. new_upload_id
    redis.call('SET', user_hash_key, new_upload_id, 'EX', ttl)
    redis.call('SET', new_meta_key, meta_json, 'EX', ttl)

    return {1, new_upload_id, meta_json}
    """

    # Lua 脚本 2: 原子 CAS 状态转换
    CAS_STATUS_LUA = """
    local meta_key = KEYS[1]
    local expected_status = ARGV[1]
    local new_status = ARGV[2]
    local now_ts = tonumber(ARGV[3])

    local raw_meta = redis.call('GET', meta_key)
    if not raw_meta then
        return -1
    end

    local data = cjson.decode(raw_meta)

    if data['status'] == expected_status then
        data['status'] = new_status
        data['updated_at'] = now_ts
        redis.call('SET', meta_key, cjson.encode(data), 'KEEPTTL')
        return 1
    end

    return 0
    """

    # Lua 脚本 3: 原子写入终态（COMPLETED / FAILED）
    SET_FINAL_STATUS_LUA = """
    local meta_key = KEYS[1]
    local new_status = ARGV[1]
    local file_record_id = ARGV[2]
    local error_msg = ARGV[3]
    local now_ts = tonumber(ARGV[4])
    local ttl = tonumber(ARGV[5])

    local raw_meta = redis.call('GET', meta_key)
    if not raw_meta then
        return 0
    end

    local data = cjson.decode(raw_meta)

    -- 若已处于 completed 终态，保持幂等直接返回成功
    if data['status'] == 'completed' then
        return 1
    end

    data['status'] = new_status
    data['updated_at'] = now_ts
    if file_record_id ~= "" then
        data['file_record_id'] = tonumber(file_record_id)
    end
    if error_msg ~= "" then
        data['error_msg'] = error_msg
    end

    redis.call('SET', meta_key, cjson.encode(data), 'EX', ttl)
    return 1
    """

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.ttl = 86400 * 2  # 48 小时

    def _meta_key(self, upload_id: str) -> str:
        return f"upload:meta:{upload_id}"

    def _chunks_key(self, upload_id: str) -> str:
        return f"upload:chunks:{upload_id}"

    def _user_hash_key(self, user_id: int, file_hash: str) -> str:
        return f"upload:user_hash:{user_id}:{file_hash}"

    async def get_or_create_session(
        self,
        user_id: int,
        upload_id_generator,
        file_data: dict
    ) -> Tuple[UploadSession, bool]:
        user_hash_k = self._user_hash_key(user_id, file_data["file_hash"])
        new_upload_id = upload_id_generator()

        meta_payload = {
            "user_id": user_id,
            "upload_id": new_upload_id,
            "status": UploadStatus.UPLOADING.value,
            "updated_at": time.time(),
            **file_data
        }

        result = await self.redis.eval(
            self.INIT_SESSION_LUA,
            1,
            user_hash_k,
            new_upload_id,
            "upload:meta:",
            "upload:chunks:",
            json.dumps(meta_payload),
            self.ttl
        )

        is_new_created = (result[0] == 1)
        actual_upload_id = result[1]
        raw_meta = result[2]
        meta_dict = json.loads(raw_meta)

        uploaded_chunks = set()
        if not is_new_created:
            chunks = await self.redis.smembers(self._chunks_key(actual_upload_id))
            uploaded_chunks = {int(c) for c in chunks}

        return UploadSession(**meta_dict, uploaded_chunks=uploaded_chunks), is_new_created

    async def add_uploaded_chunk(self, upload_id: str, chunk_index: int, user_id: int, file_hash: str):
        meta_k = self._meta_key(upload_id)
        chunks_k = self._chunks_key(upload_id)
        user_hash_k = self._user_hash_key(user_id, file_hash)

        if not await self.redis.exists(meta_k):
            raise ValueError(f"Session not found: {upload_id}")

        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.sadd(chunks_k, chunk_index)
            pipe.expire(chunks_k, self.ttl)
            pipe.expire(meta_k, self.ttl)
            pipe.expire(user_hash_k, self.ttl)
            await pipe.execute()

    async def get_session(self, upload_id: str) -> UploadSession:
        meta_k = self._meta_key(upload_id)
        chunks_k = self._chunks_key(upload_id)

        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.get(meta_k)
            pipe.smembers(chunks_k)
            raw_meta, chunks = await pipe.execute()

        if not raw_meta:
            raise ValueError(f"Unknown upload session: {upload_id}")

        meta = json.loads(raw_meta)
        return UploadSession(**meta, uploaded_chunks={int(c) for c in chunks})

    async def compare_and_set_status(
        self,
        upload_id: str,
        expected_status: UploadStatus,
        new_status: UploadStatus
    ) -> bool:
        res = await self.redis.eval(
            self.CAS_STATUS_LUA,
            1,
            self._meta_key(upload_id),
            expected_status.value,
            new_status.value,
            time.time()
        )
        if res == -1:
            raise ValueError(f"Session not found: {upload_id}")
        return res == 1

    async def set_completed(self, upload_id: str, file_record_id: int):
        await self.redis.eval(
            self.SET_FINAL_STATUS_LUA,
            1,
            self._meta_key(upload_id),
            UploadStatus.COMPLETED.value,
            str(file_record_id),
            "",
            time.time(),
            self.ttl
        )

    async def set_failed(self, upload_id: str, error_msg: str):
        await self.redis.eval(
            self.SET_FINAL_STATUS_LUA,
            1,
            self._meta_key(upload_id),
            UploadStatus.FAILED.value,
            "",
            error_msg,
            time.time(),
            self.ttl
        )


def get_session_manager() -> SessionManager:
    return SessionManager(redis_client=RedisManager.get_client())
```

### 问：这段代码使用Redis运行lua脚本，会导致Redis速度变慢吗

**在绝大多数业务场景下，这段代码不会导致 Redis 变慢。**

Lua 脚本中执行的均是 $O(1)$ 复杂度的基础读写（`GET`、`SET`、`DEL`、`EXPIRE`），且 `cjson` 序列化的是仅包含十几个字段的小对象。单个脚本在 Redis 单线程内的执行时间通常在 **0.05~0.1 毫秒以内**，不会造成 Redis 线程阻塞。

不过，代码中存在几处**网络 I/O 放大**与**潜在架构瓶颈**，在高并发场景下值得优化：

### 性能与架构隐患分析

- **使用 `EVAL` 而非 `EVALSHA`（网络带宽浪费）**
  - 代码中直接使用 `self.redis.eval(SCRIPT_STRING, ...)`，每次调用都会通过网络传输数百字节的 Lua 脚本源码。
  - **影响**：高并发下会徒增网络 I/O 开销与 Redis 解析负载。
- **分片上传接口的命令放大与额外网络往返（RTT）**
  - `add_uploaded_chunk` 先做了一次 `await exists()`，再执行包含 `SADD` + 3 次 `EXPIRE` 的 Pipeline。
  - **影响**：每次分片上传产生 2 次网络 RTT 和 5 条 Redis 指令。如果 1000 个用户同时上传大文件，Redis 的 QPS 会被大量无意义的 `EXPIRE` 迅速占满。
- **Redis Cluster（集群模式）不兼容风险**
  - `INIT_SESSION_LUA` 只传入了 `KEYS[1]`（`user_hash_key`），却在脚本内部动态拼接并操作了 `existing_meta_key` 和 `chunks_prefix`。
  - **影响**：若未来部署 **Redis Cluster**，跨 Slot 操作多个未带有相同 Hash Tag 的 Key 会直接抛出 `CROSSSLOT Keys in request don't hash to the same slot` 异常，阻碍横向扩容。
- **极多分片下的 `SMEMBERS` 开销**
  - 若单文件切片数达到数万片，`SMEMBERS` 需要一次性提取并反序列化整个 Set。
  - **建议**：常规分片（几十到数百片）无影响；超大文件上传场景可考虑改用 Redis **Bitmap**（`SETBIT` / `BITCOUNT`）替代 Set，内存占用更小且检测进度更快。

### 关键优化建议

**1. 预编译 Lua 脚本（启用 SHA 缓存）**

在初始化时注册脚本，`aioredis` 会自动使用 `EVALSHA`，只在首次或缓存失效时发送完整脚本：



Python

```
class SessionManager:
    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.ttl = 86400 * 2
        # 预注册脚本
        self._init_session_script = self.redis.register_script(self.INIT_SESSION_LUA)
        self._cas_status_script = self.redis.register_script(self.CAS_STATUS_LUA)
        self._set_final_status_script = self.redis.register_script(self.SET_FINAL_STATUS_LUA)

    async def get_or_create_session(...):
        result = await self._init_session_script(
            keys=[user_hash_k],
            args=[new_upload_id, "upload:meta:", "upload:chunks:", json.dumps(meta_payload), self.ttl]
        )
```

**2. 精简 `add_uploaded_chunk` 逻辑**

去除前置的 `exists` 查询，将分片写入与续期收敛到单次 Pipeline 中：



Python

```
async def add_uploaded_chunk(self, upload_id: str, chunk_index: int, user_id: int, file_hash: str):
    chunks_k = self._chunks_key(upload_id)
    meta_k = self._meta_key(upload_id)
    user_hash_k = self._user_hash_key(user_id, file_hash)

    async with self.redis.pipeline(transaction=False) as pipe:
        pipe.sadd(chunks_k, chunk_index)
        pipe.expire(chunks_k, self.ttl)
        pipe.expire(meta_k, self.ttl)
        pipe.expire(user_hash_k, self.ttl)
        # SADD 返回 0 或 1，如果 key 完全不存在也可以通过判断处理
        await pipe.execute()
```

**3. 规范 Redis Key 前缀（适配集群 Hash Tag）**

如需支持 Redis Cluster，Key 的格式建议统一使用 `{user_id:file_hash}` 作为 Hash Tag，确保同一会话的所有 Key 落在同一分片节点：



- `upload:{123:abcdef}:meta:uuid`
- `upload:{123:abcdef}:chunks:uuid`
- `upload:{123:abcdef}:user_hash`

### 问：好的，我后续考虑进行这些优化，暂时先按照原样实现核心功能再说。这个优化应该改动不大吧？

**改动非常小，且完全封装在 `SessionManager` 内部，对外部调用的业务层零侵入。**



这些优化不需要重构数据结构或业务流程，后续改造成本极低：



- **Lua 脚本预编译 (`register_script`)**：只需在 `__init__` 里加 3 行注册代码，并把调用处的 `self.redis.eval(...)` 换成脚本对象调用，纯语法层面的调整，5 分钟即可完成。
- **分片写入精简**：直接删掉 `add_uploaded_chunk` 开头的 `if not await self.redis.exists(...)`，净减 2 行代码，属于纯精简操作。
- **集群 Hash Tag 支持**：如果未来要上 Redis 集群，只需在 `_meta_key`、`_chunks_key`、`_user_hash_key` 这 3 个私有方法里把拼接格式加上 `{}`（如 `upload:{user_id}:{file_hash}:meta:...`），改完即生效。

当前代码在单机/主从 Redis 架构下跑通核心上传、断点续传流程没有任何问题，优先聚焦业务闭环是完全合理的做法。

### 