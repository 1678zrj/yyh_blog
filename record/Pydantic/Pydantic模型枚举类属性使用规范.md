### 问：我想了解一下pydantic模型对象的model_dump方法是否可以将枚举类型的属性转化成它们的真实数值？

**可以，但默认情况下不会转换，需要显式传入 `mode='json'` 参数。**



在 Pydantic V2 中，`model_dump()` 默认使用的导出模式是 `mode='python'`，此时字典中保留的是原始的 Python 对象（即 Enum 实例）。只有切换为 `mode='json'` 时，Pydantic 才会将枚举、日期、UUID 等非原生 JSON 类型转换为其底层真实值或基础类型。



### 代码对比示例

Python

```
from enum import Enum
from pydantic import BaseModel

class Status(Enum):
    PENDING = 0
    ACTIVE = 1

class User(BaseModel):
    id: int
    name: str
    status: Status

user = User(id=101, name="Alice", status=Status.ACTIVE)

# 1. 默认导出 (mode='python')
dump_python = user.model_dump()
print(dump_python)
# 输出: {'id': 101, 'name': 'Alice', 'status': <Status.ACTIVE: 1>}
print(type(dump_python['status']))
# 输出: <enum 'Status'>

# 2. 导出为底层真实值 (mode='json')
dump_json = user.model_dump(mode='json')
print(dump_json)
# 输出: {'id': 101, 'name': 'Alice', 'status': 1}
print(type(dump_json['status']))
# 输出: <class 'int'>
```

### 其他进阶场景处理

#### 1. 仅转换特定枚举字段（使用 `@field_serializer`）

如果希望在默认的 `model_dump()` 中也直接输出真实值，或者只想针对某一个枚举字段单独序列化，可以使用字段序列化器：



Python

```
from pydantic import BaseModel, field_serializer

class User(BaseModel):
    id: int
    status: Status

    # when_used='always' 表示无论 mode='python' 还是 mode='json' 都会执行
    @field_serializer('status', when_used='always')
    def serialize_status(self, status: Status) -> int:
        return status.value

user = User(id=101, status=Status.ACTIVE)
print(user.model_dump())
# 输出: {'id': 101, 'status': 1}
```

#### 2. 使用 `IntEnum` / `StrEnum`

如果枚举继承自 `int, Enum`（或 Python 3.11+ 的 `IntEnum`），即便在 `mode='python'` 下它仍然是枚举类型实例，但因其本身也是 `int` 的子类，可以直接无缝参与整数运算、数据库操作或索引取值：



Python

```
from enum import IntEnum

class Status(IntEnum):
    PENDING = 0
    ACTIVE = 1

user = User(id=101, status=Status.ACTIVE)
# user.model_dump()['status'] == 1 结果直接为 True
```

#### 3. 关于 Pydantic V1 的 `use_enum_values`

如果你之前习惯在模型配置中使用 `use_enum_values = True`（Pydantic V1 写法），在 V2 中通常推荐统一通过 `model.model_dump(mode='json')` 处理，无需再额外配置模型类。

### 问：还有一个问题就是我创建User对象时

### user = User(id=101, name="Alice", status=Status.ACTIVE.value) 

### 即传入的是Status.ACTIVE.value而不是Status.ACTIVE，可以吗？

**完全可以，而且这是 Pydantic 非常核心且常用的特性之一。**



Pydantic 在实例化和数据校验时，会自动识别字段的类型注解。当它看到字段类型是 `Status` 枚举、而传入的是原始数值（如 `1` 或 `Status.ACTIVE.value`）时，会自动进行**类型转换（Coercion）**，将其安全地构造成对应的枚举实例。



### 1. 运行表现

实例化完成后，Pydantic 内部存储的依然是**枚举对象**，而不是原始数值：



Python

```
from enum import Enum
from pydantic import BaseModel

class Status(Enum):
    PENDING = 0
    ACTIVE = 1

class User(BaseModel):
    id: int
    name: str
    status: Status

# 直接传入枚举值（1）
user = User(id=101, name="Alice", status=Status.ACTIVE.value)

print(user.status)
# 输出: Status.ACTIVE

print(type(user.status))
# 输出: <enum 'Status'>

print(user.status == Status.ACTIVE)
# 输出: True
```

这也是从前端接收 JSON 数据（如 `{"status": 1}`）反序列化为模型时，Pydantic 能够无缝将其转为 Python 枚举的原因。



### 2. 传入不存在的枚举值会怎样？

如果传入的值不在枚举的定义范围内，Pydantic 会直接抛出 `ValidationError`，并明确指出允许的取值：



Python

```
# 传入未定义的数值
User(id=102, name="Bob", status=99)
```

**报错提示：**



Plaintext

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for User
status
  Input should be 0 or 1 [type=enum, input_value=99, input_type=int]
```

### 3. 需要注意的边界情况：严格模式（`strict=True`）

只有在**显式启用了严格模式**时，这种写法才会报错：



Python

```
from pydantic import BaseModel, ConfigDict

class StrictUser(BaseModel):
    model_config = ConfigDict(strict=True)

    id: int
    name: str
    status: Status

# 在 strict=True 下传入 int 会报错，必须传入 Status.ACTIVE
strict_user = StrictUser(id=101, name="Alice", status=Status.ACTIVE.value)
# 报错: Input should be an instance of Status [type=is_instance_of]
```

在默认的非严格模式下，传入 `Status.ACTIVE.value` 或直接传 `1` 都是完全有效且标准的做法。

### 问：嗯嗯，我确实就是被这种转换规则困扰着，特别是在fastapi后端开发中，我都不知道到底应该用User,status.value还是User,status了。还有进行比较的时候也是

在 FastAPI 和 Pydantic 开发中，这种困扰非常普遍。根本原因在于**数据在“外层协议（JSON/数据库）”与“内部逻辑（Python代码）”之间切换时形态不一致**。



记住一条核心原则：**对内（业务逻辑与比较）一律用枚举对象，对外（存入非枚举列数据库、三方接口请求）才转 `.value`。**



### 一、 条件比较：怎么比才规范？

#### 1. 推荐：永远与枚举常量比，禁止比底层数值

Python

```
# ✅ 正确且优雅：享受 IDE 补全、重构支持、类型检查
if user.status == Status.ACTIVE:
    ...

# ✅ 同样合法（枚举是单例，可以用 is）
if user.status is Status.ACTIVE:
    ...

# ❌ 反模式（Anti-Pattern）：破坏了使用枚举的初衷
if user.status.value == 1:
    ...
```

> **为什么不要比 `.value`？**
>
> 枚举的最大价值在于**消灭代码里的“魔法数字（Magic Number）”**。一旦写出 `user.status.value == 1`，如果未来状态码调整（比如 `1` 改为 `10`），IDE 的引用查找和重构工具将无法帮你追踪到这些散落的硬编码。

#### 2. 特别注意：普通 `Enum` 与数值直接比较永远为 `False`

Python

```
class Status(Enum):
    ACTIVE = 1

# ❌ 普通 Enum 哪怕底层值是 1，它也不等于 1！
Status.ACTIVE == 1  # 结果是 False！
```

### 二、 FastAPI 全生命周期流转图

FastAPI 会在边界层自动为你做转换，你几乎**不需要手动写 `.value`**：



Plaintext

```
[ 前端请求 ] 
    │  {"status": 1} (JSON 数字)
    ▼
[ Pydantic 模型解析 ]
    │  Pydantic 自动转换为 Status.ACTIVE
    ▼
[ 你的业务代码 (Service / Handler) ]
    │  全程使用 user.status 和 Status.ACTIVE 比较
    │  例: if user.status == Status.ACTIVE: ...
    ▼
[ 数据库保存 (ORM) ]
    │  SQLAlchemy Enum 类型可以直接收枚举；
    │  如果是普通 Integer 字段，才需要 user.status.value
    ▼
[ FastAPI 响应前端 ]
    │  FastAPI 序列化 response_model 时自动提取 .value 输出给前端
    ▼
[ 前端接收 ] 
       {"status": 1}
```

- **入参**：前端传 `1`，Pydantic 自动构建为 `Status.ACTIVE`。
- **出参**：接口返回定义了 `response_model=UserResponse`，FastAPI 内部会自动调用类似 `model_dump(mode='json')` 的机制，返回给前端的直接就是 `1`，不需要在返回前手动转成 `.value`。

### 三、 治本良方：使用 `IntEnum` 或 `StrEnum`

如果觉得普通 `Enum` 容错率低，最实用的技巧是改用**派生枚举**。



#### 1. 数值枚举：改用 `IntEnum`

如果状态值是数字，继承 `IntEnum`：



Python

```
from enum import IntEnum

class Status(IntEnum):
    PENDING = 0
    ACTIVE = 1
```

**带来的好处：**



- `Status.ACTIVE == 1` 结果为 `True`（即便有人误写了数字也能兼容）。
- 传给数据库驱动（如 PyMySQL、Asyncpg）时，驱动会自动把它当作普通 `int` 写入，彻底告别 `.value` 转换。

#### 2. 字符串枚举：改用 `StrEnum` (Python 3.11+)

如果状态值是字符串（现代 RESTful API 更推荐字符串状态码）：

Python

```
from enum import StrEnum  # Python 3.11+; 早期版本用 (str, Enum)

class Status(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"

# 直接等于字符串
Status.ACTIVE == "active"  # True
```

### 四、 避坑速查表

| **场景**                    | **正确做法**                                      | **错误 / 不推荐做法**                   |
| --------------------------- | ------------------------------------------------- | --------------------------------------- |
| **if 条件判断**             | `user.status == Status.ACTIVE`                    | `user.status.value == 1`                |
| **赋值/更新状态**           | `user.status = Status.ACTIVE`                     | `user.status = 1`                       |
| **FastAPI 接口返回**        | 直接返回 Pydantic 实例（定义好 `response_model`） | 手动写字典字典并 `.value`               |
| **原生 SQL/无映射 DB 插入** | `user.status.value`                               | 直接把普通 `Enum` 塞给不支持的 SQL 语句 |

### 问：那if user.status.value == Status.ACTIVE.value:呢

**功能上完全能跑通，结果也是正确的，但在工程实践中属于典型的“过度操作（冗余代码）”，强烈不推荐。**



写成 `if user.status.value == Status.ACTIVE.value:` 相当于把两个已经包装好的标准对象，各自拆出底层值再去比，不仅多余，还引入了额外的隐患。



### 为什么不推荐这么写？

#### 1. 纯粹的冗余（脱裤子放屁）

Python 的 `Enum` 本身已经重载了比较运算符 `__eq__`，并且每个枚举成员在内存中都是**单例（Singleton）**。



- 枚举之间的对比本来就是最快、最直接的：

  Python

  ```
  user.status == Status.ACTIVE  # 内部直接比对枚举身份/值，完全足够
  ```

- 两边都加上 `.value`，不仅代码变长、可读性变差，还失去了使用枚举的高级语义抽象。

#### 2. 隐藏的运行时崩溃风险（`AttributeError`）

在复杂业务或重构过程中，如果某个地方绕过了 Pydantic（比如某段历史代码传了个普通字典，或者某个函数返回值直接给的是整数 `1`）：



Python

```
# 假设某种异常情况下 status 变成了一个原生 int 1
status = 1

# ❌ 这样写直接崩掉抛出异常
if status.value == Status.ACTIVE.value:
    ...
# AttributeError: 'int' object has no attribute 'value'

# ✅ 直接与枚举比较，最多返回 False，绝不会报错崩溃
if status == Status.ACTIVE:
    ...
```

### 标准写法总结

| **写法**                                       | **是否推荐**   | **原因**                                           |
| ---------------------------------------------- | -------------- | -------------------------------------------------- |
| `if user.status == Status.ACTIVE:`             | **⭐ 最推荐**   | 语义清晰、代码地道、享受 IDE 自动补全              |
| `if user.status is Status.ACTIVE:`             | **✅ 推荐**     | 枚举是单例对象，用 `is` 进行身份比对完全合法且极快 |
| `if user.status.value == Status.ACTIVE.value:` | **❌ 不推荐**   | 逻辑有效，但极度冗余且有属性报错风险               |
| `if user.status.value == 1:`                   | **❌ 坚决反对** | 破坏枚举封装，引入硬编码魔法数字                   |

**一句话记忆：** 既然已经定义了 `Status.ACTIVE`，就把它当作一个完整的身份实体来用，**两边都直接用枚举本体比较**即可。