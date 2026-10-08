### 问：用Lua脚本原子化执行的话是不是类似于过期时间都完全冻结了

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