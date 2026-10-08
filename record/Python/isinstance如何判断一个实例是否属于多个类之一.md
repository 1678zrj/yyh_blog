### 问：Python的isinstance函数如何一次性判断实例是否是多个类中的一个？

将多个类以**元组（tuple）\**的形式作为第二个参数传入，或者在 Python 3.10+ 中直接使用\**管道符 `|`（联合类型）**。



### 1. 使用元组（全版本通用）

将目标类放入一个元组 `(ClassA, ClassB, ...)` 中：



Python

```
x = 3.14

# 判断 x 是否是 int 或 float 类型
if isinstance(x, (int, float)):
    print("x 是数值类型")  # 输出
```

> **注意：** 第二个参数必须是**元组**。如果传入列表（`[int, float]`）或集合（`{int, float}`），会直接抛出 `TypeError`。

### 2. 使用联合类型 `|`（Python 3.10+）

Python 3.10 引入了 PEP 604 语法，支持直接用 `|` 连接多个类型：



Python

```
x = "hello"

# 等同于 isinstance(x, (int, str, list))
if isinstance(x, int | str | list):
    print("匹配成功")  # 输出
```

### 额外特性说明

- **支持继承关系：** 只要实例属于元组中**任意一个类或其子类**，即返回 `True`。例如，`bool` 是 `int` 的子类，因此 `isinstance(True, (int, float))` 同样会返回 `True`。
- **嵌套元组：** 元组内部允许嵌套，如 `isinstance(x, (int, (float, complex)))` 同样有效。