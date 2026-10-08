## 问：python typing是干嘛的

`typing` 是 Python 的一个内置模块（从 Python 3.5 开始引入），**用于支持静态类型提示（type hints）**。

虽然 Python 是一门动态类型语言，但使用类型提示可以让代码更易读、更容易维护，并能帮助编辑器、IDE（如 VS Code、PyCharm）或工具（如 `mypy`）进行类型检查，提前发现错误。

------

### 一、为什么要用 `typing`？

传统写法（无类型提示）：

```python
def add(a, b):
    return a + b
```

这个函数的参数类型、返回值类型一目不清，别人只能猜。

加入 `typing` 类型提示：

```python
def add(a: int, b: int) -> int:
    return a + b
```

这表示：`a` 和 `b` 是 `int` 类型，返回值也是 `int`。更清晰！

------

### 二、`typing` 常用类型

| 类型       | 说明                      | 示例                                      |
| ---------- | ------------------------- | ----------------------------------------- |
| `List`     | 列表类型                  | `List[int]` 表示整数列表                  |
| `Dict`     | 字典类型                  | `Dict[str, int]` 表示键是字符串，值是整数 |
| `Tuple`    | 元组类型                  | `Tuple[int, str]`                         |
| `Optional` | 可为某类型或 `None`       | `Optional[int]` == `Union[int, None]`     |
| `Union`    | 可以是多种类型之一        | `Union[int, str]`                         |
| `Any`      | 任意类型                  | `Any`                                     |
| `Callable` | 可调用对象（函数）        | `Callable[[int, str], bool]`              |
| `Literal`  | 具体字面值（Python 3.8+） | `Literal["on", "off"]`                    |
| `TypeVar`  | 泛型类型变量              | `T = TypeVar('T')`                        |

------

### 三、示例代码

```python
from typing import List, Dict, Optional, Union

def greet(name: str) -> str:
    return f"Hello, {name}"

def get_scores() -> List[int]:
    return [80, 90, 100]

def find_user(id: int) -> Optional[Dict[str, Union[int, str]]]:
    if id == 1:
        return {"id": 1, "name": "Alice"}
    return None
```

------

### 四、工具支持（可选）

- **IDE 提示增强**：类型提示可以让 IDE 提前报错、自动补全。
- **mypy 工具**：可用 `mypy your_code.py` 静态检查类型是否匹配。
- **更好的文档**：让别人更容易理解你的函数/类/模块。

------

### 五、Python 3.9+ 新语法（更简洁）

从 Python 3.9 起，许多类型提示不再需要 `typing.List`、`typing.Dict`，可以直接使用内置泛型：

```python
def foo(data: list[int]) -> dict[str, int]:
    ...
```

等价于旧写法：

```python
from typing import List, Dict

def foo(data: List[int]) -> Dict[str, int]:
    ...
```

