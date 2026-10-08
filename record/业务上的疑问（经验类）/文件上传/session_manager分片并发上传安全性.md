```
async def upload_file_chunk(
        self,
        upload_id: str,
        user_id: int,
        chunk_index: int,
        chunk_file: AsyncGenerator[bytes, None]
    ):
        session = await self.get_status(upload_id=upload_id, user_id=user_id)
        if session.status != UploadStatus.UPLOADING:
            raise HTTPException(status_code=400, detail=f"Cannot upload chunks in {session.status.value} status")

        # 校验分片索引边界，防止越界上传
        if chunk_index >= session.total_chunks:
            raise HTTPException(
                status_code=400,
                detail=f"Chunk index {chunk_index} out of bounds (total_chunks: {session.total_chunks})"
            )

        tmp_path = build_tmp_path(upload_id=upload_id, chunk_index=chunk_index)
        part_tmp_path = tmp_path.with_name(f"{chunk_index}.{uuid.uuid4().hex}.part_tmp")

        await self.storage.upload_chunk(chunk_path=part_tmp_path, file=chunk_file)
        await asyncio.to_thread(os.replace, part_tmp_path, tmp_path)

        await self.session_manager.add_uploaded_chunk(
            upload_id=upload_id,
            chunk_index=chunk_index,
            user_id=session.user_id,
            file_hash=session.file_hash
        )

```

### 问：这段代码是否能够实现同一文本块重复写入的并发安全问题？

**在文件物理写入层面是并发安全的，但在业务状态管理和资源控制层面存在潜在的并发与健壮性隐患。**



### 一、 安全的方面（文件存储层）

- **写入隔离：** 每次上传均生成独立的 UUID 临时文件（`part_tmp_path`），多个相同分片的并发请求不会在写入阶段产生写入冲突或数据踩踏。
- **替换原子性：** `os.replace` 在 POSIX 系统及 Windows 上均具备原子覆盖（Atomic Rename/Replace）特性。即使两个并发请求同时完成写入，最终也只会有一个完整的临时文件成功覆盖目标 `tmp_path`，不会产生数据损坏或半写入（Partial Write）的文件。

### 二、 存在的并发风险与设计隐患

**1. 元数据状态非幂等风险（关键点）**



- `session_manager.add_uploaded_chunk` 的内部实现若采用计数器累加（如 `uploaded_count += 1`），重复并发上传相同分片会导致计数虚高，提前触发“上传完成”状态。
- **解决要求：** 底层必须基于具备去重特性的数据结构（如 Redis 的 `SADD`、位图 `SETBIT`，或数据库带有 `UNIQUE(upload_id, chunk_index)` 的记录表）。

**2. 触发合并的竞态条件（Race Condition）**



- 若 `add_uploaded_chunk` 内部包含“判断分片集已满则自动触发合并”的逻辑，两个并发的重复分片写入可能同时检测到满足合并条件，从而引发重复触发合并任务。

**3. 异常中断产生孤儿垃圾文件**



- 当前代码没有 `try...finally` 保护。若客户端在 `storage.upload_chunk` 流式传输过程中意外断开连接，抛出异常后未完成的 `part_tmp_path` 将永久残留在磁盘中。

**4. 缺乏前置去重检查（资源浪费）**



- 在校验状态后，未检查该分片是否已经处于已上传/完成状态。重复上传的大文件分片仍会完整接收数据并写入磁盘，徒增网络带宽与磁盘 I/O 开销。

### 三、 优化改进代码

Python

```
import asyncio
import os
import uuid
from pathlib import Path
from fastapi import HTTPException

async def upload_file_chunk(
    self,
    upload_id: str,
    user_id: int,
    chunk_index: int,
    chunk_file: AsyncGenerator[bytes, None]
):
    session = await self.get_status(upload_id=upload_id, user_id=user_id)
    if session.status != UploadStatus.UPLOADING:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot upload chunks in {session.status.value} status"
        )

    if chunk_index >= session.total_chunks or chunk_index < 0:
        raise HTTPException(
            status_code=400,
            detail=f"Chunk index {chunk_index} out of bounds (total_chunks: {session.total_chunks})"
        )

    # 1. 前置幂等检查：若分片已记录完成，直接返回成功，避免重复 I/O
    if await self.session_manager.is_chunk_uploaded(upload_id, chunk_index):
        return

    tmp_path = build_tmp_path(upload_id=upload_id, chunk_index=chunk_index)
    part_tmp_path = tmp_path.with_name(f"{chunk_index}.{uuid.uuid4().hex}.part_tmp")

    try:
        # 2. 写入独立临时文件
        await self.storage.upload_chunk(chunk_path=part_tmp_path, file=chunk_file)
        
        # 3. 原子覆盖目标分片
        await asyncio.to_thread(os.replace, part_tmp_path, tmp_path)

        # 4. 幂等更新元数据状态（内部需使用 SET/唯一约束）
        await self.session_manager.add_uploaded_chunk(
            upload_id=upload_id,
            chunk_index=chunk_index,
            user_id=session.user_id,
            file_hash=session.file_hash
        )
    except Exception:
        # 5. 异常清理：防止网络中断或异常留下孤儿文件
        if await asyncio.to_thread(os.path.exists, part_tmp_path):
            await asyncio.to_thread(os.remove, part_tmp_path)
        raise
```



### 问：这是当前SessionManager的代码

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

这套 `SessionManager` 的设计架构非常出色，**完全解决了上一轮讨论中关于“元数据幂等”与“重复合并竞态”的核心风险**。



它通过 **Redis Set (`SADD`)** 实现了天然的分片去重，并通过 **Lua CAS** 保证了状态机流转的严格单向性与原子性。



### 一、 架构亮点评估

- **天然分片去重：** `add_uploaded_chunk` 采用 `SADD`，多次写入相同 `chunk_index` 不会产生重复数据或脏计数，从根本上保证了分片统计的幂等性。
- **防重复合并：** `compare_and_set_status` 脚本通过 Lua 原子完成 `UPLOADING -> MERGING` 的校验与修改，并发情况下只有一个请求能成功触发后续的合并任务。
- **完善的会话复用与续期：** `INIT_SESSION_LUA` 对活跃/完成会话支持滑动续期，对失败会话支持历史清理，闭环了断点续传与重试链路。

### 二、 存在的细节隐患与优化建议

**1. `add_uploaded_chunk` 中的 TOCTOU 竞态漏洞**



Python

```
if not await self.redis.exists(meta_k): # 检查
    raise ValueError(...)
# 若恰好在此刻 meta_k 过期淘汰
pipe.sadd(chunks_k, chunk_index)        # 会重新在 Redis 建立无 meta 关联的孤儿 Set
```

- **优化：** 将校验与 `SADD` 放入同一 Lua 脚本中执行，或者利用 `EXISTS` 原子保护写入。

**2. 缺少前置查询接口**



- 缺少 `is_chunk_uploaded`（基于 `SISMEMBER`）方法，无法在前置流程中直接跳过已存在的分片。

**3. 触发合并时存在多余网络开销**



- 可以在 `add_uploaded_chunk` 写入时直接返回 `(added: bool, total_uploaded: int)`，这样上层无需再次发起 `get_session` 即可判断是否所有分片已就绪。

**4. Redis Cluster 兼容性隐患（跨 Slot 风险）**



- Lua 脚本中同时操作了 `upload:user_hash:...`、`upload:meta:...` 和 `upload:chunks:...`。如果后续架构迁移到 Redis Cluster，由于 Key 没有加统一的 Hash Tag（如 `{upload_id}`），会直接抛出 `CROSSSLOT Keys in request don't hash to the same slot` 错误。

### 三、 优化后的完整代码实现

针对上述细节进行了补全与强化的版本：

Python

```
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
    # Lua 脚本 1: 原子初始化/续期任务
    INIT_SESSION_LUA = """
    local user_hash_key = KEYS[1]
    local new_upload_id = ARGV[1]
    local meta_prefix   = ARGV[2]
    local chunks_prefix = ARGV[3]
    local meta_json     = ARGV[4]
    local ttl           = tonumber(ARGV[5])

    local existing_upload_id = redis.call('GET', user_hash_key)

    if existing_upload_id then
        local existing_meta_key = meta_prefix .. existing_upload_id
        local raw_meta = redis.call('GET', existing_meta_key)

        if raw_meta then
            local data = cjson.decode(raw_meta)
            if data['status'] == 'uploading' or data['status'] == 'merging' or data['status'] == 'completed' then
                redis.call('EXPIRE', user_hash_key, ttl)
                redis.call('EXPIRE', existing_meta_key, ttl)
                redis.call('EXPIRE', chunks_prefix .. existing_upload_id, ttl)
                return {0, existing_upload_id, raw_meta}
            end

            redis.call('DEL', chunks_prefix .. existing_upload_id)
            redis.call('DEL', existing_meta_key)
        end
    end

    local new_meta_key = meta_prefix .. new_upload_id
    redis.call('SET', user_hash_key, new_upload_id, 'EX', ttl)
    redis.call('SET', new_meta_key, meta_json, 'EX', ttl)

    return {1, new_upload_id, meta_json}
    """

    # Lua 脚本 2: 原子记录分片，避免 TOCTOU 导致的孤儿 Key，并直接返回当前总完成数
    ADD_CHUNK_LUA = """
    local meta_key = KEYS[1]
    local chunks_key = KEYS[2]
    local user_hash_key = KEYS[3]
    local chunk_index = tonumber(ARGV[1])
    local ttl = tonumber(ARGV[2])

    if redis.call('EXISTS', meta_key) == 0 then
        return -1
    end

    local added = redis.call('SADD', chunks_key, chunk_index)
    local total_uploaded = redis.call('SCARD', chunks_key)

    redis.call('EXPIRE', meta_key, ttl)
    redis.call('EXPIRE', chunks_key, ttl)
    if user_hash_key ~= "" then
        redis.call('EXPIRE', user_hash_key, ttl)
    end

    return {added, total_uploaded}
    """

    # Lua 脚本 3: 原子 CAS 状态转换
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

    # Lua 脚本 4: 原子终态写入
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
        self.ttl = 86400 * 2  # 48 小时

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

    async def is_chunk_uploaded(self, upload_id: str, chunk_index: int) -> bool:
        """快速判断分片是否已上传完成"""
        return bool(await self.redis.sismember(self._chunks_key(upload_id), chunk_index))

    async def add_uploaded_chunk(
        self, 
        upload_id: str, 
        chunk_index: int, 
        user_id: Optional[int] = None, 
        file_hash: Optional[str] = None
    ) -> Tuple[bool, int]:
        """
        原子记录分片并续期。
        返回: (is_new_chunk: bool, total_uploaded_chunks: int)
        """
        meta_k = self._meta_key(upload_id)
        chunks_k = self._chunks_key(upload_id)
        user_hash_k = self._user_hash_key(user_id, file_hash) if user_id and file_hash else ""

        result = await self.redis.eval(
            self.ADD_CHUNK_LUA,
            3,
            meta_k,
            chunks_k,
            user_hash_k,
            chunk_index,
            self.ttl
        )

        if result == -1:
            raise ValueError(f"Session not found: {upload_id}")

        is_new = bool(result[0] == 1)
        total_uploaded = int(result[1])
        return is_new, total_uploaded

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

### 问：跟我说说你改了哪块？

主要对 `SessionManager` 做了 **4 处针对并发安全与性能的核心改动**：



### 1. 新增 `ADD_CHUNK_LUA` 脚本（修复 TOCTOU 竞态漏洞）

- **原有问题：** 原代码先在 Python 层用 `await self.redis.exists(meta_k)` 检查，随后再通过 Pipeline 执行 `sadd`。如果 Session 恰好在检查通过后瞬间过期被淘汰，Pipeline 依然会执行 `sadd`，在 Redis 中残留一个没有关联元数据的“孤儿 Key”。
- **改动实现：** 将“检查 Meta 是否存在 + `SADD` 写入 + `SCARD` 统计 + 所有相关 Key 的 `EXPIRE` 续期”合并进单个原子 Lua 脚本中执行。若 Meta 不存在直接返回 `-1`，杜绝孤儿 Key 的产生。

Lua

```
-- Lua 内部原子保证：Meta 存在才允许写入分片并统一刷新 TTL
if redis.call('EXISTS', meta_key) == 0 then
    return -1
end
local added = redis.call('SADD', chunks_key, chunk_index)
local total_uploaded = redis.call('SCARD', chunks_key)
...
```

### 2. `add_uploaded_chunk` 返回 `(is_new, total_uploaded)`（减少网络 RTT）

- **原有问题：** 原方法无返回值。分片写入成功后，外层业务若想知道“当前是否已收齐全部切片以触发合并”，通常需要再调一次 `get_session` 读取全量数据，多产生一次 Redis 网络开销。
- **改动实现：** 利用 Lua 内的 `SCARD` 直接返回 `Tuple[bool, int]`：
  - `is_new`: 本次写入是否为全新分片（`False` 表示该分片之前已存在，属于重复上传）。
  - `total_uploaded`: 当前已收到的有效分片总数。
- **收益：** 上层可直接通过 `if total_uploaded == session.total_chunks:` 判断是否尝试 CAS 切换到 `MERGING` 状态，省去一次全局查询。

### 3. 新增 `is_chunk_uploaded` 前置轻量校验方法

- **改动实现：** 增加基于 `SISMEMBER` 的快速判断方法：

  Python

  ```
  async def is_chunk_uploaded(self, upload_id: str, chunk_index: int) -> bool:
      return bool(await self.redis.sismember(self._chunks_key(upload_id), chunk_index))
  ```

- **收益：** 配合在 `upload_file_chunk` 最前层调用，遇到重复发起的切片上传请求时，可在**写入临时磁盘文件之前**直接退出，节省网络流接收与磁盘 I/O 损耗。

### 4. `add_uploaded_chunk` 参数解耦与鲁棒性优化

- **改动实现：** 将 `user_id` 和 `file_hash` 参数设为 `Optional`，并在 Lua 脚本中做了空串判断（`if user_hash_key ~= "" then redis.call('EXPIRE', ...)`）。
- **收益：** 在部分只持有 `upload_id` 的调用上下文中，不必强行传递 `user_id` 与 `file_hash` 也能正常记录切片并给基础 Meta/Chunks 续期。

### 问:那我就改add_uploaded_chunk的Lua那块可以吗？其它的包括返回值都不该

**完全可以。** 这样既能彻底修复 TOCTOU 竞态并把 2 次网络 RTT 降为 1 次，又对现有上层调用代码**零侵入、100% 向后兼容**。



只需在类中添加对应的 Lua 脚本并替换 `add_uploaded_chunk` 方法即可：

Python

```
class SessionManager:
    # 新增：原子检查 Meta 存在性、记录分片并统一续期的 Lua 脚本
    ADD_CHUNK_LUA = """
    local meta_key = KEYS[1]
    local chunks_key = KEYS[2]
    local user_hash_key = KEYS[3]
    local chunk_index = tonumber(ARGV[1])
    local ttl = tonumber(ARGV[2])

    -- 检查会话是否存在
    if redis.call('EXISTS', meta_key) == 0 then
        return 0
    end

    -- 写入分片并统一续期
    redis.call('SADD', chunks_key, chunk_index)
    redis.call('EXPIRE', meta_key, ttl)
    redis.call('EXPIRE', chunks_key, ttl)
    redis.call('EXPIRE', user_hash_key, ttl)

    return 1
    """

    # ... 其余代码和方法保持不变 ...

    async def add_uploaded_chunk(
        self, 
        upload_id: str, 
        chunk_index: int, 
        user_id: int, 
        file_hash: str
    ):
        meta_k = self._meta_key(upload_id)
        chunks_k = self._chunks_key(upload_id)
        user_hash_k = self._user_hash_key(user_id, file_hash)

        # 原子执行：检查 + SADD + 三键续期
        res = await self.redis.eval(
            self.ADD_CHUNK_LUA,
            3,
            meta_k,
            chunks_k,
            user_hash_k,
            chunk_index,
            self.ttl
        )

        if res == 0:
            raise ValueError(f"Session not found: {upload_id}")
```

### 问：这样写执行速度有保证吗

**执行速度不仅有保证，而且比原有写法更快。**



具体原因和性能损耗对比如下：



### 1. 为什么它会更快？

- **网络 RTT 减半（性能提升核心）：**
  - **原写法：** 1 次 `EXISTS` 查询 + 1 次 `Pipeline` 提交 = **2 次网络往返（RTT）**。
  - **Lua 写法：** 仅 **1 次网络往返**。在实际网络环境中（即便内网延迟只有 0.5ms~2ms），网络 I/O 也是最大的耗时点，减少 1 次 RTT 会直接将该方法的响应时间降低近 50%。
- **微秒级的纯 $O(1)$ 内存计算：**
  - 脚本内执行的 `EXISTS`、`SADD`、`EXPIRE` 全部是 $O(1)$ 复杂度的原子内存操作。
  - Redis 处理整个脚本的 CPU 耗时在 **微秒级（通常 5~20 μs）**，完全不会阻塞 Redis 事件循环。

### 2. 极致性能优化小建议：使用 `register_script` (EVALSHA)

当前写法每次调用会发送整段 Lua 文本。如果并发量极大，建议在 `__init__` 中注册脚本，改用 `EVALSHA`（只传输 40 字符的 SHA1 摘要，省去每次传输 Lua 脚本的带宽与解析）：

Python

```
class SessionManager:
    ADD_CHUNK_LUA = """..."""

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.ttl = 86400 * 2
        # 预注册脚本，底层自动优先使用 EVALSHA
        self._add_chunk_script = self.redis.register_script(self.ADD_CHUNK_LUA)

    async def add_uploaded_chunk(self, upload_id: str, chunk_index: int, user_id: int, file_hash: str):
        meta_k = self._meta_key(upload_id)
        chunks_k = self._chunks_key(upload_id)
        user_hash_k = self._user_hash_key(user_id, file_hash)

        # 直接调用注册后的脚本对象
        res = await self._add_chunk_script(
            keys=[meta_k, chunks_k, user_hash_k],
            args=[chunk_index, self.ttl]
        )

        if res == 0:
            raise ValueError(f"Session not found: {upload_id}")
```

### 问:用Lua脚本原子化执行的话是不是类似于过期时间都完全冻结了

**是的，在 Lua 脚本执行期间，对于 Redis 内部来说时间确实相当于“冻结”的，中间绝对不会发生 Key 中途过期或被并发淘汰的情况。**



### 一、 为什么在 Lua 脚本中时间相当于“冻结”？

Redis 底层通过以下两个机制保证了这一点：



- **主线程绝对独占（单线程串行）：**

  Lua 脚本在 Redis 中是单线程原子运行的。在脚本执行完成之前，Redis 不会处理任何其他客户端请求，也不会运行定期的后台清理任务（Active Expiration）。

- **时间戳确定性（Deterministic Time）：**

  Redis 在开始执行 Lua 脚本的瞬间会记录一个固定时间戳。在整个脚本执行过程中，所有关于时间的计算（包括读取 TTL、写入过期时间等）都基于这个**静态时间基准**，脚本内部执行的每行指令之间时间差为 0。

### 二、 对分片上传逻辑的具体意义

在之前的 `ADD_CHUNK_LUA` 脚本中：



Lua

```
-- 1. 检查是否存在
if redis.call('EXISTS', meta_key) == 0 then
    return 0
end

-- 2. 写入分片数据
redis.call('SADD', chunks_key, chunk_index)

-- 3. 统一重置 TTL
redis.call('EXPIRE', meta_key, ttl)
redis.call('EXPIRE', chunks_key, ttl)
redis.call('EXPIRE', user_hash_key, ttl)
```

1. **绝对不会中途过期：** 只要第一行 `EXISTS meta_key` 判定为存在（即使当前剩余 TTL 只剩 1 毫秒），在执行到第三步 `EXPIRE` 之前，`meta_key` 绝对不可能发生过期被删。
2. **彻底解决竞态条件：** 检查、写入、续期这三步变成了不可分割的单个原子动作，中间不会插入任何外界的 `DEL` 命令或内存淘汰逻辑。

### 三、 唯一需要注意的前提

“冻结”只发生在**脚本开始执行之后**：



- 如果 Key 在**脚本被 Redis 接收之前**就已经过期或被删除了，脚本在第一行 `EXISTS` 就会直接检测到 `0` 并安全退出。
- 一旦脚本开始执行，内部的所有状态判断和数据写入就获得了全局唯一的时空保护。