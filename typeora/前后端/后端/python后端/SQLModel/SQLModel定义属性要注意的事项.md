```python
class User(SQLModel, table=True):
__tablename__ = "users"
# 推荐：使用 Annotated，并将 default=None 移到赋值号右侧
id: Annotated[int | None, Field(primary_key=True)] = None
# 推荐：用户名必填，且带索引
username: Annotated[str, Field(unique=True, index=True)]
password_hash: str
# 0=学生, 1=老师, 2=管理员
# 这里的 default=0 可以直接赋值
role: Annotated[int, Field(description="0=学生, 1=老师, 2=管理员")] = 0
```

### 问：这是user.py的代码，使用fastapi dev app/main.py运行报错ArgumentError: Mapper Mapper[User(users)] could not assemble any primary key columns for mapped table 'users'，如何解决？

这个错误的根本原因是：**SQLModel（以及底层的 SQLAlchemy）在解析模型时，没有正确识别出 `Annotated` 中定义的 `primary_key=True`。**

虽然 FastAPI 提倡在接口定义（Pydantic 部分）中使用 `Annotated`，但在 **SQLModel 定义数据库表结构**时，目前最稳健、不易出错的写法仍然是将 `Field()` 作为**默认值赋值**，而不是放在 `Annotated` 的类型注解中。

### 解决方法

请修改 `app/models/user.py`，将 `Field` 从类型注解中拿出来，改为赋值给变量。

#### 修改后的 `app/models/user.py`

```python
from sqlmodel import SQLModel, Field

class User(SQLModel, table=True):
    __tablename__ = "users"

    # 【关键修改】将 Field(primary_key=True) 作为默认值赋值
    # 这样 SQLAlchemy 才能 100% 识别这是主键
    id: int | None = Field(default=None, primary_key=True)

    # 推荐：其他涉及数据库约束（unique, index）的字段也建议改用这种写法
    username: str = Field(unique=True, index=True)

    password_hash: str

    # role 字段同样建议修改，以确保 description 和默认值被正确处理
    role: int = Field(default=0, description="0=学生, 1=老师, 2=管理员")
```

------

### 为什么会报错？

1. **SQLAlchemy Mapper 机制**：报错信息 `Mapper[...] could not assemble any primary key` 意味着 SQLAlchemy 试图将这个类映射到数据库表时，找不到任何一个字段被标记为主键。
2. **Annotated 的局限性**：在 SQLModel 中，`Annotated[int, Field(primary_key=True)]` 这种写法主要用于 Pydantic 的数据验证层。虽然较新的版本正在尝试支持这种写法映射到数据库，但在很多情况下（特别是与 SQLAlchemy 核心交互时），它无法将 `primary_key` 这一关键元数据正确传递给 SQLAlchemy 的 Table 定义。
3. **最佳实践**：
   - **API 参数 / Pydantic 模型**：使用 `Annotated` 是很好的实践。
   - **SQLModel 数据库模型 (`table=True`)**：请始终使用 `field_name: type = Field(...)` 的写法。

修改后再次运行 `fastapi dev app/main.py`，错误应该就会消失，数据库表也会正确创建。