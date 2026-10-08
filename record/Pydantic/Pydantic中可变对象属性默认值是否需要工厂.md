```python
class InitUploadResponse(BaseModel):
    is_completed: bool
    upload_id: str | None = None
    file_record_id: int | None = None
    uploaded_chunks: set[int] = set()
```

### 问：这个Pydantic模型定义中uploaded_chunks是否会存在问题，还是说Pydantic已经解决了这个问题？

**Pydantic 已经解决了 Python 原生可变默认参数（Mutable Default）的共享引用问题。**

在原生 Python 的函数或普通类中，直接使用 `set()` 或 `[]` 作为默认值会导致所有实例共享同一个内存对象。但在 Pydantic 中，每次实例化模型时，Pydantic 会自动对可变默认值执行**深拷贝（deep copy）**，因此不同实例之间的 `uploaded_chunks` 是完全独立、互不影响的。

```python
r1 = InitUploadResponse(is_completed=False)
r1.uploaded_chunks.add(1)

r2 = InitUploadResponse(is_completed=False)
print(r2.uploaded_chunks)  # 输出 set()，不会被 r1 污染
```

### 仍需注意的工程细节与最佳实践

虽然 Pydantic 规避了共享引用的 Bug，但这种写法在实际项目中有以下两点需要留意：

**1. 推荐使用 `Field(default_factory=set)`**

- 静态代码分析工具（如 Ruff、Flake8 的 `B006` 规则、SonarQube 等）可能无法完全识别 Pydantic 的内部机制，仍然会报 `Mutable default argument` 警告。
- 使用 `Field(default_factory=...)` 是更标准、语义更清晰的写法：

```python
from pydantic import BaseModel, Field

class InitUploadResponse(BaseModel):
    is_completed: bool
    upload_id: str | None = None
    file_record_id: int | None = None
    uploaded_chunks: set[int] = Field(default_factory=set)
```

**2. JSON 序列化与有序性**

- **类型转换**：JSON 标准中没有 `set` 类型。FastAPI / Pydantic 在将响应序列化为 JSON 字符串时，会自动将 `set` 转换为 JSON Array（`list`）。
- **顺序不确定**：`set` 是无序集合。如果前端期望已上传的分片索引是有序的（例如 `[0, 1, 2, 3]`），直接输出 `set` 可能导致每次返回的数组顺序不固定。如果对顺序有要求，建议在业务层排序或直接定义为 `list[int] = Field(default_factory=list)`。