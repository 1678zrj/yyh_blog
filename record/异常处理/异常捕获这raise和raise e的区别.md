```python
@broker.task(task_name="merge_file_task")
async def merge_file(
    upload_id: str
):
    storage = await get_storage()
    session_manager = await get_session_manager()
    session = await session_manager.get_session(upload_id)
    total_chunks = session.total_chunks
    source_paths = [build_tmp_path(upload_id, i) for i in range(total_chunks)]
    target_path = build_final_path(upload_id, session.file_name)
    async with AsyncSessionLocal() as db:
        try:
            # 将文件进行合并
            await storage.merge_chunks(source_paths, target_path)
            # 合并成功后将数据插入到数据库
            file_record = await upload_crud.create_upload(
                db=db,
                storage_type=settings.storage_type,
                storage_key=str(target_path),
                total_size=session.total_size,
                file_hash=session.file_hash,
                file_ext=session.file_ext,
                mime_type=session.mime_type
            )
            await db.commit()
            await db.refresh(file_record)
            # 将Redis中的文件上传状态设置为completed
            if source_paths and source_paths[0].parent.exists():
                await asyncio.to_thread(shutil.rmtree, source_paths[0].parent, ignore_errors=True)
            await session_manager.set_status(upload_id, UploadStatus.COMPLETED, file_record_id=file_record.id)

        except Exception as exc:
            await db.rollback()
            await session_manager.set_status(upload_id, "failed", error_msg=str(exc))
            raise exc
@broker.task(task_name="merge_file_task")
async def merge_file(
    upload_id: str
):
    storage = await get_storage()
    session_manager = await get_session_manager()
    session = await session_manager.get_session(upload_id)
    total_chunks = session.total_chunks
    source_paths = [build_tmp_path(upload_id, i) for i in range(total_chunks)]
    target_path = build_final_path(upload_id, session.file_name)
    async with AsyncSessionLocal() as db:
        try:
            # 将文件进行合并
            await storage.merge_chunks(source_paths, target_path)
            # 合并成功后将数据插入到数据库
            file_record = await upload_crud.create_upload(
                db=db,
                storage_type=settings.storage_type,
                storage_key=str(target_path),
                total_size=session.total_size,
                file_hash=session.file_hash,
                file_ext=session.file_ext,
                mime_type=session.mime_type
            )
            await db.commit()
            await db.refresh(file_record)
            # 将Redis中的文件上传状态设置为completed
            if source_paths and source_paths[0].parent.exists():
                await asyncio.to_thread(shutil.rmtree, source_paths[0].parent, ignore_errors=True)
            await session_manager.set_status(upload_id, UploadStatus.COMPLETED, file_record_id=file_record.id)

        except Exception as exc:
            await db.rollback()
            await session_manager.set_status(upload_id, "failed", error_msg=str(exc))
            raise 

```

### 问：我想了解以下异常的抛出，上面两段代码等价吗?

微观上裸 `raise` 确实比 `raise exc` 少一次局部变量的名称查找（LOAD_FAST），但**性能从来不是推荐使用裸 `raise` 的核心原因**。



异常处理本身属于低频的“错误恢复路径”（Cold Path），纳秒级别的微小差异在实际工程中完全可以忽略。推荐裸 `raise` 的根本原因在于**语义规范**和**调用栈的准确性**。



### 为什么选择裸 `raise` 的真实原因

- **Traceback（堆栈信息）更清晰精准**
  - `raise exc` 会在调用栈中额外记录一行“重新抛出”的栈帧，可能干扰排查，让人误以为错误发生在这里；
  - `raise` 是真正的原样冒泡，完美保留异常初次被触发时的完整堆栈和行号。
- **避免变量覆写或作用域陷阱**
  - 如果 `try...except` 块较长，中间逻辑不小心修改或重新绑定了 `exc` 变量，`raise exc` 抛出的就可能不再是最初捕获的对象；裸 `raise` 始终由 Python 解释器内部的当前活动异常表维护，安全且可靠。
- **符合 PEP 8 与 Pythonic 惯用法**
  - Python 社区将“捕获仅为做副作用（回滚/写日志）后继续向外抛”的模式统一定义为裸 `raise`，代码意图更直观明确。

### 问：所以效果是等价的，但是直接raise性能更好

微观上裸 `raise` 确实比 `raise exc` 少一次局部变量的名称查找（LOAD_FAST），但**性能从来不是推荐使用裸 `raise` 的核心原因**。



异常处理本身属于低频的“错误恢复路径”（Cold Path），纳秒级别的微小差异在实际工程中完全可以忽略。推荐裸 `raise` 的根本原因在于**语义规范**和**调用栈的准确性**。



### 为什么选择裸 `raise` 的真实原因

- **Traceback（堆栈信息）更清晰精准**
  - `raise exc` 会在调用栈中额外记录一行“重新抛出”的栈帧，可能干扰排查，让人误以为错误发生在这里；
  - `raise` 是真正的原样冒泡，完美保留异常初次被触发时的完整堆栈和行号。
- **避免变量覆写或作用域陷阱**
  - 如果 `try...except` 块较长，中间逻辑不小心修改或重新绑定了 `exc` 变量，`raise exc` 抛出的就可能不再是最初捕获的对象；裸 `raise` 始终由 Python 解释器内部的当前活动异常表维护，安全且可靠。
- **符合 PEP 8 与 Pythonic 惯用法**
  - Python 社区将“捕获仅为做副作用（回滚/写日志）后继续向外抛”的模式统一定义为裸 `raise`，代码意图更直观明确。

### 问：其实我还想了解一下，在try代码块中raise异常的话，会被下面的except捕获吗？

**会，完全会被捕获。**

在 Python 中，无论是系统运行时自动抛出的异常（如 `ZeroDivisionError`、`KeyError`），还是你在 `try` 块中通过 `raise` 主动抛出的异常，底层的捕获机制**没有任何区别**。只要 `except` 声明的类型匹配（或声明的是其父类），就会进入对应的 `except` 块。



### 典型示例

Python

```
try:
    # 模拟业务校验失败，主动抛出异常
    if user_id is None:
        raise ValueError("用户 ID 不能为空")
    
    print("这一行不会被执行")

except ValueError as e:
    print(f"成功捕获: {e}")  # 输出: 成功捕获: 用户 ID 不能为空
```

### 需要注意的 3 个关键点

- **继承关系匹配**：

  如果抛出的是 `ValueError`，而写的是 `except Exception:`，依然会被正常捕获，因为 `Exception` 是 `ValueError` 的基类。

- **同层级 `except` 不会二次捕获**：

  如果你在 `except` 块内部再写 `raise`，这个异常**不会**被当前 `try...except` 的其他分支捕获，而是直接向外层（外部包裹的 `try` 或调用栈上游）冒泡。

- **`Exception` 捕获不到的特例**：

  如果主动 `raise KeyboardInterrupt()` 或 `raise SystemExit()`，普通的 `except Exception:` 无法捕获，因为它们直接继承自 `BaseException` 而非 `Exception`。若要全量捕获，需使用 `except BaseException:`（通常不推荐拦截此类系统中断）。