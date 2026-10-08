### 问：SQLModel的update返回结果中影响的行数是row_count还是rowcount

是 **`rowcount`**（全部小写，中间没有下划线）。



SQLModel 底层基于 SQLAlchemy 开发，通过 `session.exec()` 执行 DML 语句（如 `update()` 或 `delete()`）时，返回的是 SQLAlchemy 的 `CursorResult` 对象。该对象遵循 Python DB-API 2.0 规范，表示受影响行数的属性固定为 **`rowcount`**。



**示例代码：**



Python

```
from sqlmodel import Session, update
from models import Hero

# 构造并执行批量更新语句
statement = update(Hero).where(Hero.name == "Spider-Boy").values(age=18)
result = session.exec(statement)
session.commit()

# 获取受影响行数
print(result.rowcount)
```

**注意事项：**



- **逐个对象更新方式无 `rowcount`**：如果使用的是查询对象后修改属性并 `session.add(hero)` 的 ORM 风格更新，不会生成 `CursorResult`，无法直接通过该属性读取行数。
- **数据库驱动行为差异**：部分数据库驱动（例如 MySQL 的 `pymysql`）默认返回的是**实际发生数据变更**的行数，而非 `WHERE` 条件匹配到的行数；如果需要匹配行数，通常需要在数据库连接 URL 中配置对应参数（如 MySQL 的 `client_flag` 包含 `CLIENT.FOUND_ROWS`）。