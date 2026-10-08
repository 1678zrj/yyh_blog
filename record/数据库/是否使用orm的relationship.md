### 问：是否有必要使用ORM的relationship

简短回答：**有必要，但不是必须。** 是否使用 ORM 的 `relationship`，取决于你的数据访问模式，而不是"所有模型都应该加 relationship"。

可以从几个方面来判断。

### 什么时候应该使用 relationship

如果两个表存在明确的关联，并且业务代码经常需要跨表访问，那么 `relationship` 会让代码更加自然。

例如：

```python
class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True)
    orders = relationship("Order", back_populates="user")


class Order(Base):
    __tablename__ = "order"

    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey("user.id"))

    user = relationship("User", back_populates="orders")
```

查询时：

```python
order = session.get(Order, 1)

print(order.user.name)
```

而不是：

```python
order = session.get(Order, 1)
user = session.get(User, order.user_id)
```

优点：

- 面向对象，代码可读性更高
- 可以配合 eager loading（`joinedload`、`selectinload`）避免 N+1 问题
- 删除、级联、双向引用等功能更方便
- SQLAlchemy 可以自动维护对象关系

------

### 什么时候没必要使用 relationship

如果：

- 只是保存一个外键
- 很少访问关联对象
- 查询基本都是自己写 SQL/Join
- 项目偏 CQRS、Repository 风格

那么完全可以只保留外键。

例如：

```python
class Order(Base):
    __tablename__ = "order"

    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey("user.id"))
```

查询：

```python
stmt = (
    select(Order, User)
    .join(User, Order.user_id == User.id)
)

result = session.execute(stmt)
```

很多大型项目就是这样做的。

------

### relationship 的代价

很多人觉得它"不好"，通常是因为几个问题。

#### 1. 隐式 SQL

例如：

```python
print(order.user.name)
```

如果 `user` 没加载：

```sql
SELECT * FROM user WHERE id = ...
```

代码里看不到 SQL，容易产生性能问题。

------

#### 2. N+1 查询

例如：

```python
orders = session.scalars(select(Order)).all()

for order in orders:
    print(order.user.name)
```

可能执行：

```
1 次查询 Order
100 次查询 User
```

而正确做法：

```python
stmt = (
    select(Order)
    .options(selectinload(Order.user))
)
```

或者

```python
.options(joinedload(Order.user))
```

------

#### 3. 循环引用

例如：

```
User
  ├── orders
Order
  └── user
```

序列化 JSON 时很容易无限递归。

很多 Web 框架都会遇到这个问题。

------

### 大型项目怎么做

很多成熟项目都会遵循一个原则：

> **数据库模型有 relationship，但业务代码不要滥用 lazy loading。**

例如：

```python
stmt = (
    select(Order)
    .options(selectinload(Order.user))
)
```

之后：

```python
order.user.name
```

不会再发 SQL。

也就是说：

- relationship 用来描述对象关系
- 查询由 Repository/DAO 控制
- 明确指定加载策略（`joinedload`、`selectinload`）
- 尽量避免默认 lazy loading

这样既保留了 ORM 的便利，又避免了隐式性能问题。

------

### 我的建议

对于绝大多数使用 SQLAlchemy、Django ORM、Entity Framework 等 ORM 的项目：

- **建议建立 `relationship`** 来表达实体之间的关系。
- **不要依赖默认的懒加载**，查询时显式决定是否使用 `selectinload`、`joinedload` 等加载策略。
- 如果某个关联几乎不会使用，或者你的项目主要通过手写 `JOIN`、DTO、Repository 来组织查询，那么只保留外键即可，不必强行定义 `relationship`。

一句话概括：

- **`ForeignKey`** 负责维护数据库层面的关系（数据完整性）。
- **`relationship`** 负责维护 ORM 层面的对象关系（开发便利性）。

前者通常是必需的；后者是为了提升开发体验，应根据实际访问模式选择是否使用，而不是默认全部添加或全部避免。

### 问：是否有必要使用relationship呢？我怕不熟练不熟悉它的机制导致性能损失，并且都在业务代码里写其实逻辑更清晰

你的担心其实很有代表性，而且很多有经验的 SQLAlchemy 用户都会经历这个阶段。

我的建议是：

> **如果你现在对 `relationship` 的加载机制（lazy、joined、selectin 等）还没有完全掌握，那么宁可先不用，也不要因为"ORM 就应该面向对象"而到处加 `relationship`。**

这并不是坏实践。

------

## 为什么你的想法有道理

你说：

> 都在业务代码里写其实逻辑更清晰

这一点我很认同。

例如不用 `relationship`：

```python
user = await user_repo.get(user_id)
orders = await order_repo.list_by_user(user.id)
```

你一眼就知道发生了两次 SQL。

而如果用了 `relationship`：

```python
user = await user_repo.get(user_id)

orders = user.orders
```

真正执行 SQL 的地方其实是第二行。

代码阅读者不知道：

- 是不是已经 preload？
- 会不会发 SQL？
- 发一条还是一百条？

这种"隐藏副作用"确实降低了可预测性。

很多大型项目（尤其 Go、Rust、Java 的 DDD 项目）都尽量避免这种隐式行为。

------

## relationship 最大的问题其实不是性能

很多人说：

> relationship 性能不好。

其实不是。

真正的问题是：

> **relationship 让 SQL 的执行位置变得隐式。**

举个例子：

```python
for order in orders:
    print(order.user.name)
```

如果没有 preload：

```
SELECT orders ...
SELECT user ...
SELECT user ...
SELECT user ...
```

如果用了：

```python
.options(selectinload(Order.user))
```

就会变成：

```
SELECT orders ...
SELECT users WHERE id IN (...)
```

性能很好。

所以：

> relationship 本身并不会导致性能差。

真正导致性能差的是：

> **不知道什么时候会触发 lazy load。**

------

## Repository 风格很多公司就是不用 relationship

例如：

```python
class OrderService:

    async def get_order_detail(order_id):

        order = await order_repo.get(order_id)

        user = await user_repo.get(order.user_id)

        products = await product_repo.list(order.id)
```

虽然代码长一点，

但是：

- SQL 全部可见
- 没有隐藏查询
- 很容易调优
- 很容易统计 SQL 数量

很多金融、交易系统就是这么写。

------

## 我更推荐一种折中的方式

其实 SQLAlchemy 官方很多资深开发者也推荐这种风格。

### Model 保留 relationship

例如：

```python
class Order(Base):
    user_id = mapped_column(ForeignKey("user.id"))

    user = relationship(...)
```

但是：

### 业务代码禁止直接访问

统一在查询时：

```python
stmt = (
    select(Order)
    .options(selectinload(Order.user))
)
```

Repository 返回的对象：

```python
order.user
```

已经加载好了。

如果没有 preload，

就禁止访问。

甚至可以这样：

```python
relationship(lazy="raise")
```

这样：

```python
order.user
```

如果没有提前加载，

直接抛异常。

不会偷偷发 SQL。

很多大型项目都会这样配置。

------

## 如果是我带一个新项目

如果团队里大家都不是 SQLAlchemy 专家，

我甚至会规定：

> **第一阶段不要使用 relationship。**

先全部写：

```python
join(...)
select(...)
```

等大家：

- 熟悉 Session
- 熟悉 Identity Map
- 熟悉 eager loading
- 熟悉 Unit of Work

以后，

再慢慢把真正经常访问的关系改成 relationship。

因为：

> **relationship 是开发效率工具，不是数据库设计工具。**

不用它，

你的数据库设计不会变差；

只是 ORM 自动导航能力少一点而已。

------

## 我的建议

根据你的描述——**担心机制不熟悉导致隐式性能问题，而且更喜欢业务代码中明确表达查询逻辑**——我建议采用下面的策略：

- 数据库层面正常使用 `ForeignKey`。
- 业务查询优先使用显式 `JOIN`、`select()` 和 Repository 方法。
- 暂时不要为了"ORM 风格"而给所有关联都加 `relationship`。
- 等你真正理解了 `relationship` 的加载策略后，再有选择地为那些**经常导航访问**的关联添加它。
- 如果以后使用 `relationship`，优先考虑配合 `lazy="raise"` 或在查询中显式使用 `selectinload()`、`joinedload()`，避免隐式 SQL。

这样做并不是"没有充分利用 ORM"，而是优先保证**代码行为可预测**。等你对 SQLAlchemy 的对象状态管理、加载策略和 Session 生命周期足够熟悉之后，再引入 `relationship`，你会更清楚它是在帮你，还是在增加复杂度。