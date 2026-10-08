Python 的 `logging` 模块是标准库中用于记录日志的工具，适用于调试、错误追踪、程序运行监控等场景。下面是 `logging` 的基础用法和常见进阶用法。

------

## ✅ 一、快速入门

```python
import logging

# 设置日志级别和格式
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# 写日志
logging.debug("这是调试信息")
logging.info("这是普通信息")
logging.warning("这是警告信息")
logging.error("这是错误信息")
logging.critical("这是严重错误信息")
```

输出示例：

```
2025-05-28 12:34:56,789 - INFO - 这是普通信息
2025-05-28 12:34:56,789 - WARNING - 这是警告信息
...
```

------

## ✅ 二、日志级别（从低到高）

| 级别     | 函数                 | 说明                       |
| -------- | -------------------- | -------------------------- |
| DEBUG    | `logging.debug()`    | 最详细的调试信息           |
| INFO     | `logging.info()`     | 一般的信息，如运行状态     |
| WARNING  | `logging.warning()`  | 警告，不影响程序运行       |
| ERROR    | `logging.error()`    | 错误，可能导致某些功能失败 |
| CRITICAL | `logging.critical()` | 严重错误，程序可能无法继续 |

设置的级别为 `INFO` 时，只会输出 `INFO` 及以上的信息（不含 `DEBUG`）。

------

## ✅ 三、写入日志文件

```python
logging.basicConfig(filename='app.log',
                    level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
```

------

## ✅ 四、自定义 Logger（推荐生产中使用）

```python
import logging

# 创建 logger 对象
logger = logging.getLogger('my_logger')
logger.setLevel(logging.DEBUG)

# 创建控制台处理器
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# 创建文件处理器
file_handler = logging.FileHandler('app.log')
file_handler.setLevel(logging.WARNING)

# 设置日志格式
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# 添加处理器
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# 使用 logger
logger.debug("调试信息")      # 控制台不显示
logger.info("信息")         # 控制台显示
logger.warning("警告")      # 控制台+文件
logger.error("错误")        # 控制台+文件
```

------

## ✅ 五、清除重复日志（防止多次添加 Handler）

```python
if logger.hasHandlers():
    logger.handlers.clear()
```

