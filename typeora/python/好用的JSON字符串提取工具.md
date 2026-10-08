```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""Utility functions for the OpenAI API."""

import json
import logging
import re
import ast

from json_repair import repair_json

log = logging.getLogger(__name__)


def try_parse_ast_to_json(function_string: str) -> tuple[str, dict]:
    """
     # 示例函数字符串
    function_string = "tool_call(first_int={'title': 'First Int', 'type': 'integer'}, second_int={'title': 'Second Int', 'type': 'integer'})"
    :return:
    """

    tree = ast.parse(str(function_string).strip())
    ast_info = ""
    json_result = {}
    # 查找函数调用节点并提取信息
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function_name = node.func.id
            args = {kw.arg: kw.value for kw in node.keywords}
            ast_info += f"Function Name: {function_name}\r\n"
            for arg, value in args.items():
                ast_info += f"Argument Name: {arg}\n"
                ast_info += f"Argument Value: {ast.dump(value)}\n"
                json_result[arg] = ast.literal_eval(value)

    return ast_info, json_result


def try_parse_json_object(input: str) -> tuple[str, dict]:
    """JSON cleaning and formatting utilities."""
    # Sometimes, the LLM returns a json string with some extra description, this function will clean it up.

    result = None
    try:
        # Try parse first
        result = json.loads(input)
    except json.JSONDecodeError:
        log.info("Warning: Error decoding faulty json, attempting repair")

    if result:
        return input, result

    _pattern = r"\{(.*)\}"
    _match = re.search(_pattern, input)
    input = "{" + _match.group(1) + "}" if _match else input

    # Clean up json string.
    input = (
        input.replace("{{", "{")
        .replace("}}", "}")
        .replace('"[{', "[{")
        .replace('}]"', "}]")
        .replace("\\", " ")
        .replace("\\n", " ")
        .replace("\n", " ")
        .replace("\r", "")
        .strip()
    )

    # Remove JSON Markdown Frame
    if input.startswith("```"):
        input = input[len("```"):]
    if input.startswith("```json"):
        input = input[len("```json"):]
    if input.endswith("```"):
        input = input[: len(input) - len("```")]

    try:
        result = json.loads(input)
    except json.JSONDecodeError:
        # Fixup potentially malformed json string using json_repair.
        json_info = str(repair_json(json_str=input, return_objects=False))

        # Generate JSON-string output using best-attempt prompting & parsing techniques.
        try:

            if len(json_info) < len(input):
                json_info, result = try_parse_ast_to_json(input)
            else:
                result = json.loads(json_info)

        except json.JSONDecodeError:
            log.exception("error loading json, json=%s", input)
            return json_info, {}
        else:
            if not isinstance(result, dict):
                log.exception("not expected dict type. type=%s:", type(result))
                return json_info, {}
            return json_info, result
    else:
        return input, result

```

## 问：解释这段代码的功能

## Deepseek:

这段代码提供了两个实用函数，用于处理从OpenAI API返回的JSON数据，特别是修复和解析格式异常的JSON字符串。以下是详细解释：

### 1. `try_parse_ast_to_json(function_string: str)`
**功能**：  
解析类似函数调用的字符串，提取参数并转换为JSON对象。适用于非标准JSON格式（如Python字典样式的字符串）。

**输入示例**：
```python
"tool_call(first_int={'title': 'First Int', 'type': 'integer'}, second_int={'title': 'Second Int', 'type': 'integer'})"
```

**处理流程**：
1. **AST解析**：使用Python的`ast`模块解析字符串为抽象语法树。
2. **遍历语法树**：
   - 查找`ast.Call`节点（函数调用）
   - 提取函数名称（如`tool_call`）
   - 提取关键字参数（如`first_int`和`second_int`）
3. **值转换**：
   - 通过`ast.literal_eval()`将参数字典值（如`{'title': ...}`）转换为Python字典
4. **返回结果**：
   - `ast_info`：调试信息（函数名+参数详情）
   - `json_result`：解析后的字典（如`{"first_int": {...}, "second_int": {...}}`）

**典型应用场景**：  
解析模型返回的非标准JSON（如函数调用参数）。

---

### 2. `try_parse_json_object(input: str)`
**功能**：  
健壮地解析和修复可能损坏的JSON字符串，支持多种异常格式。

**处理流程**：
#### 阶段1：基础清理
```python
input = (
    input.replace("{{", "{")
    .replace("}}", "}")
    .replace('"[{', "[{")
    .replace('}]"', "}]")
    .replace("\\", " ")
    .replace("\\n", " ")
    .replace("\n", " ")
    .replace("\r", "")
    .strip()
)
```
- 修复常见错误：多余的花括号、转义字符、换行符等
- 示例：`"{{key: value}}"` → `"{key: value}"`

#### 阶段2：移除Markdown标记
```python
if input.startswith("```"): ...
if input.startswith("```json"): ...
if input.endswith("```"): ...
```
- 清除JSON代码块标记（常见于LLM返回结果）
- 示例：`"```json\n{\"key\":\"value\"}\n```"` → `"{\"key\":\"value\"}"`

#### 阶段3：JSON解析策略
1. **优先尝试标准解析**  
   ```python
   try: return json.loads(input)
   ```
2. **失败后尝试正则提取**  
   ```python
   _pattern = r"\{(.*)\}"  # 提取最外层{}间的内容
   ```
3. **使用json_repair修复**  
   ```python
   repair_json(json_str=input, ...)
   ```
4. **最终回退到AST解析**  
   ```python
   if len(json_info) < len(input):
       json_info, result = try_parse_ast_to_json(input)
   ```

#### 返回结果
- 成功时：返回(清理后的字符串, 解析的字典)
- 失败时：返回(错误信息, 空字典)

---

### 关键错误处理
1. **多层异常捕获**：
   - 标准JSON解析 → 正则处理 → 第三方修复库 → AST解析
2. **类型安全校验**：
   ```python
   if not isinstance(result, dict): ...  # 确保返回字典类型
   ```
3. **日志记录**：
   ```python
   log.exception("error loading json, json=%s", input)
   ```

---

### 典型使用场景
1. **解析OpenAI函数调用**：
   ```python
   # 模型返回: "add(a=5, b=3)"
   _, params = try_parse_json_object(response_str)
   print(params)  # {'a':5, 'b':3}
   ```
2. **修复损坏JSON**：
   ```python
   # 输入: "```json\n{name: \\"John\\",\n age: 30```"
   cleaned, data = try_parse_json_object(bad_json)
   print(data)  # {'name':'John','age':30}
   ```

### 设计亮点
- **渐进式修复策略**：从简单清理到复杂解析层层递进
- **多引擎支持**：结合标准库/第三方库/AST解析
- **防御性编程**：严格校验结果类型和异常边界
- **诊断信息**：返回清理过程信息便于调试

这段代码特别适合处理LLM返回的不稳定JSON格式，是构建鲁棒AI应用的关键工具。

## GPT:

这段代码是微软在 2024 年编写的一个用于处理 **OpenAI API 返回数据的工具模块**，它的主要功能是：

- **解析 LLM 返回的字符串中包含的 JSON 或函数调用格式的数据**；
- **自动修复损坏或不标准的 JSON 字符串**；
- **返回清洗后的 JSON 字典以及处理信息（字符串）**。

下面是对代码的逐行详细解释：

------

## 文件头

```python
# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License
"""Utility functions for the OpenAI API."""
```

这是版权声明和模块说明，表示这个模块是微软提供的，用于与 OpenAI API 交互的工具函数。

------

## 模块导入

```python
import json
import logging
import re
import ast

from json_repair import repair_json
```

- `json`: 用于处理 JSON 序列化/反序列化。
- `logging`: 日志记录模块，用于调试或异常打印。
- `re`: 正则表达式模块，用于提取 JSON 片段。
- `ast`: 抽象语法树模块，用于将字符串解析为 Python 表达式/函数。
- `json_repair`: 第三方库，用于修复损坏的 JSON。

------

## 日志定义

```python
log = logging.getLogger(__name__)
```

设置日志记录器，用于打印日志。

------

## 函数 1：`try_parse_ast_to_json`

```python
def try_parse_ast_to_json(function_string: str) -> tuple[str, dict]:
```

### 用途

尝试从一个看起来像函数调用的字符串中提取出函数名和参数，并将参数转为字典格式。

### 示例输入

```python
"tool_call(first_int={'title': 'First Int', 'type': 'integer'}, second_int={'title': 'Second Int', 'type': 'integer'})"
```

### 实现逻辑

```python
    tree = ast.parse(str(function_string).strip())
```

将输入字符串解析为抽象语法树。

```python
    ast_info = ""
    json_result = {}
```

定义返回值：

- `ast_info`: 收集函数名、参数名、参数值等信息；
- `json_result`: 用于输出提取后的 JSON 字典。

```python
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function_name = node.func.id
```

遍历 AST，找到调用（`Call`）类型的节点，并取出函数名。

```python
            args = {kw.arg: kw.value for kw in node.keywords}
```

提取所有关键字参数（如 `first_int=...`）为字典。

```python
            for arg, value in args.items():
                ...
                json_result[arg] = ast.literal_eval(value)
```

打印出参数信息，并使用 `ast.literal_eval` 安全地将 AST 节点转换为 Python 字典。

### 返回值

- `ast_info`: 调试字符串（包含函数名、参数名、AST 结构）
- `json_result`: 一个键值形式的参数字典（例如：`{"first_int": {"title": ..., "type": ...}}`）

------

## 函数 2：`try_parse_json_object`

```python
def try_parse_json_object(input: str) -> tuple[str, dict]:
```

### 用途

从一个 **看似 JSON 的字符串中提取出真正的 JSON 对象**，自动修复格式问题，并返回 JSON 及处理后的字符串。

------

### 步骤 1：初次尝试解析 JSON

```python
    try:
        result = json.loads(input)
```

- 如果成功解析，说明是合法 JSON，直接返回。
- 如果失败，则进入下一步尝试修复。

------

### 步骤 2：尝试从中提取 JSON 部分

```python
    _pattern = r"\{(.*)\}"
    _match = re.search(_pattern, input)
    input = "{" + _match.group(1) + "}" if _match else input
```

这段代码通过正则表达式提取最外层的大括号内容，即可能的 JSON 主体。

------

### 步骤 3：清洗 JSON 字符串

```python
    input = (
        input.replace("{{", "{")
        ...
        .replace("\r", "")
        .strip()
    )
```

- 处理双层大括号、`\n`、`\r` 等问题；
- 替换反斜杠、Markdown 标记等非法字符；
- 去除 ````json`、````` 这类 Markdown 包裹。

------

### 步骤 4：再次尝试解析

```python
    try:
        result = json.loads(input)
```

- 如果这时成功了，说明是修复成功，返回结果；
- 否则尝试使用 `json_repair` 修复：

```python
        json_info = str(repair_json(json_str=input, return_objects=False))
```

调用 `json_repair` 修复损坏的 JSON 字符串。

------

### 步骤 5：最终解析或回退 AST 模式

```python
        if len(json_info) < len(input):
            json_info, result = try_parse_ast_to_json(input)
```

- 如果 `repair_json` 结果反而更短，说明修复失败，回退用 AST 模式处理。
- 否则再次使用 `json.loads(json_info)` 尝试解析修复后的 JSON。

------

### 返回值

- 成功：返回处理后的字符串和 JSON 字典；
- 失败：返回修复失败的字符串和空字典。

------

## 总结

这个模块定义了两个核心函数：

| 函数名                  | 功能                                                   |
| ----------------------- | ------------------------------------------------------ |
| `try_parse_ast_to_json` | 从函数调用字符串中提取出函数名和参数，并转为 JSON 字典 |
| `try_parse_json_object` | 尝试修复和解析 LLM 返回的非标准 JSON 字符串            |

它适用于 **LLM 返回的数据中夹杂注释、格式错误、函数调用格式等不标准内容的情况**，非常适合实际工程中对 OpenAI 或其他模型返回结果的预处理场景。

------

如需测试或示例使用方式，我也可以补充。是否需要？