## 问：Python重写类的_str_函数有什么用？

在 Python 里，重写类的 `__str__` 方法主要是为了 **自定义对象转为字符串时的显示效果**。

------

### 默认情况

如果你不写 `__str__`，Python 会用默认实现，打印出来的结果通常是这样的：

```python
class Person:
    def __init__(self, name, age):
        self.name = name
        self.age = age

p = Person("Alice", 25)
print(p)
```

输出类似：

```
<__main__.Person object at 0x0000021C5FAE4F10>
```

这个信息对调试没啥帮助。

------

### 重写 `__str__`

通过重写 `__str__` 方法，可以让对象在 `print()` 或 `str()` 时返回更友好的描述：

```python
class Person:
    def __init__(self, name, age):
        self.name = name
        self.age = age
    
    def __str__(self):
        return f"Person(name={self.name}, age={self.age})"

p = Person("Alice", 25)
print(p)
```

输出：

```
Person(name=Alice, age=25)
```

这样一目了然。

------

### 主要用途

1. **更友好地展示对象**（日志、调试、打印时更直观）。
2. **输出给用户看的描述**（比如数据模型的自然语言表示）。
3. **和 `__repr__` 区别**：
   - `__str__` 主要给 **用户** 用，追求可读性。
   - `__repr__` 给 **开发者** 用，追求尽可能准确（甚至能用于重建对象）。
   - 如果只定义了 `__repr__`，`print(obj)` 也会用 `__repr__` 的结果。

------

✅ 小总结：
 重写 `__str__` 的作用就是让你的类在 **print** 或转成 **字符串** 时输出更直观、更人性化的信息，而不是默认的内存地址。

------

要不要我帮你写一个对比 `__str__` 和 `__repr__` 的示例？这样你能更直观地理解两者的差别。

## 问：那如果我重写了类的__str__，对类的实例进行str()强制类型转换会发生什么？

好问题 👍

如果你在类里重写了 `__str__`，那么当你对这个类的实例调用 **`str()`** 时，就会触发你写的 `__str__` 方法，返回你定义的字符串。

------

### 示例

```python
class Person:
    def __init__(self, name, age):
        self.name = name
        self.age = age

    def __str__(self):
        return f"Person: {self.name}, {self.age} years old"

p = Person("Alice", 25)

print(str(p))   # 强制转换成字符串
print(p)        # print 默认也会调用 str()
```

输出：

```
Person: Alice, 25 years old
Person: Alice, 25 years old
```

------

### 关键点

- `str(obj)` **等价于** 调用 `obj.__str__()`。
- 如果你 **没定义** `__str__`，Python 会退而调用 `__repr__`。
- 所以：
  - `print(obj)` 和 `str(obj)` 行为一致（优先走 `__str__`）。
  - `repr(obj)` 或交互式解释器里直接输入对象名，会优先走 `__repr__`。

------

✅ 小结：
 重写了 `__str__` 后，`str(instance)` 就会执行你写的逻辑，返回你设计的字符串，而不是默认的内存地址。

------

要不要我顺便帮你写一个 **同时定义了 `__str__` 和 `__repr__`** 的例子，直观展示 `print()`、`str()`、`repr()` 三者的不同效果？