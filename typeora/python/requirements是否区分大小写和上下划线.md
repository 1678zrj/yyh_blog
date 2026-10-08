## 问：Python的pip install和requirements.txt下载一些包的时候是否区分大小写以及‘-’和‘_’

Python 中使用 `pip install` 或 `requirements.txt` 安装包时：

------

### ✅ **1. 是否区分大小写？**

**不区分大小写。**

- `pip install Numpy` 和 `pip install numpy` 是等价的。
- PyPI（Python Package Index）中的包名本质上是**大小写不敏感的**，虽然包的原始名字可能含有大写字母，但安装时你可以用任何大小写形式。

📌 示例：

```bash
pip install Requests
pip install requests
pip install ReQuEsTs
```

都安装的是同一个包：`requests`

------

### ✅ **2. 是否区分 `-` 和 `_`？**

**不区分，自动兼容。**

- PyPI 将 `-` 和 `_` 视为等价。

- 例如：

  - `pip install my-package`
  - `pip install my_package`

  都会匹配到注册在 PyPI 上的 `my-package` 或 `my_package`（取决于它的发布名称，但你不用记清哪个）。

📌 示例：

```bash
pip install backports.zoneinfo
pip install backports_zoneinfo
```

这两个命令的效果是一样的，都会安装 `backports.zoneinfo`。

------

### 🔍 背后原理（标准依据）：

PEP 503 明确规定：

> All comparisons of distribution names MUST be case insensitive, and MUST treat `-`, `_`, and `.` as equivalent.

也就是说：

```
foo-bar == Foo_Bar == foo.bar
```

------

### ✅ `requirements.txt` 也是一样的规则：

文件内容可以写成如下任何形式，效果相同：

```txt
requests==2.31.0
REQUESTS==2.31.0
re-quests==2.31.0
```

虽然推荐你**使用 PyPI 上注册的标准包名形式（一般是小写+连字符）**，但解析和安装时是**不区分大小写，也不区分 - 和 _**。

------

### 🔒 例外说明：

虽然安装不区分，但**以下情况可能区分**（与你写代码有关）：

- 在你的代码中导入模块时（即 `import`），使用的是实际发布包里的模块名，**这可能是区分大小写和下划线的**：

```python
import my_package  # ✅ 正确
import my-package  # ❌ 会报语法错
```

------

### ✅ 推荐习惯：

- 安装时或写 `requirements.txt` 时，使用 PyPI 上正式发布的**标准小写名字**（如 `requests`、`numpy`）。
- 避免混用大小写和 `-` / `_`，虽然可以安装成功，但容易引发歧义或代码导入混乱。

