```python
from sqlmodel import Session, select
from app.models.lab import Category
from typing import List


def get_all(session: Session) -> List[Category]:
    # 按 sort_order 排序返回
    statement = select(Category).order_by(Category.sort_order)
    return session.exec(statement).all()
```

### 问：关于这段代码，如何指定排序的顺序？

在 SQLModel (以及其底层 SQLAlchemy) 中，你可以通过调用字段上的 `.desc()` 或 `.asc()` 方法来指定排序顺序。

以下是具体的写法：

### 1. 降序排序 (Descending)

如果你希望 `sort_order` 从大到小排列（例如：数字 10 在 1 之前），请使用 `.desc()`：

Python

```
def get_all(session: Session) -> List[Category]:
    # 使用 .desc() 进行降序
    statement = select(Category).order_by(Category.sort_order.desc())
    return session.exec(statement).all()
```

### 2. 升序排序 (Ascending)

这是默认行为。如果你希望从小到大排列（例如：数字 1 在 10 之前），可以直接使用字段本身，或者显式调用 `.asc()`：

Python

```
# 写法 A (默认):
statement = select(Category).order_by(Category.sort_order)

# 写法 B (显式，更清晰):
statement = select(Category).order_by(Category.sort_order.asc())
```

------

### 进阶：多重排序

如果你想先按 `sort_order` 排序，如果通过，再按 `id` 倒序排列，可以传递多个参数给 `order_by`：

Python

```
def get_all(session: Session) -> List[Category]:
    # 先按 sort_order 升序，若相同则按 id 降序
    statement = select(Category).order_by(
        Category.sort_order.asc(), 
        Category.id.desc()
    )
    return session.exec(statement).all()
```

下一步建议

既然你在写获取列表的接口，如果数据量未来可能很大，你是否需要我帮你加上分页 (Pagination) 的功能？