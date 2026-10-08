# 模拟在开发项目过程中如何使用 Git 管理代码，包括 **新建、修改、删除文件** 的完整流程。

下面是一个更 **具体** 的例子，模拟在开发项目过程中如何使用 Git 管理代码，包括 **新建、修改、删除文件** 的完整流程。

------

## **项目场景：开发一个简单的 Python 项目**

假设我们正在开发一个简单的 **计算器** 项目，逐步增加功能。我们会经历以下操作：

1. 新建项目文件夹，初始化 Git 仓库。
2. 新建文件（例如 `main.py`）。
3. 修改文件（添加功能）。
4. 新建其他模块文件（例如 `calculator.py`）。
5. 删除不需要的文件。

------

### 1. **新建项目并初始化 Git 仓库**

```bash
# 创建项目目录
mkdir calculator_project
cd calculator_project

# 初始化 Git 仓库
git init
```

------

### 2. **新建文件并提交**

创建一个 `main.py` 文件，作为程序入口。

```bash
# 创建 main.py 文件
echo "print('Welcome to the calculator!')" > main.py

# 查看文件状态
git status

# 将文件添加到暂存区
git add main.py

# 提交到本地仓库
git commit -m "Initial commit: Add main.py with welcome message"
```

------

### 3. **修改文件：添加功能**

修改 `main.py` 文件，添加简单的加法功能。

```bash
# 修改 main.py 文件
echo "a = 5; b = 3" >> main.py
echo "print('Sum:', a + b)" >> main.py

# 查看修改状态
git status

# 添加修改到暂存区
git add main.py

# 提交修改
git commit -m "Update main.py: Add simple addition feature"
```

------

### 4. **新建文件：分离功能到模块**

创建一个 `calculator.py` 文件，将计算功能单独放在这里。

```bash
# 创建 calculator.py 文件
echo "def add(a, b):" > calculator.py
echo "    return a + b" >> calculator.py

# 修改 main.py 导入模块
echo "from calculator import add" >> main.py
echo "print('Sum using calculator module:', add(5, 3))" >> main.py

# 添加新文件和修改的文件
git add calculator.py main.py

# 提交到仓库
git commit -m "Add calculator.py module and integrate it into main.py"
```

------

### 5. **删除不需要的文件**

假设我们不再需要 `calculator.py` 文件，决定删除它。

```bash
# 删除文件
rm calculator.py

# 查看文件状态
git status

# 将删除操作添加到暂存区
git add calculator.py

# 提交删除操作
git commit -m "Remove calculator.py as it is no longer needed"
```

------

### 6. **查看提交历史**

查看项目的提交历史，确认所有操作：

```bash
git log --oneline
```

输出示例：

```
3eac4c5 Remove calculator.py as it is no longer needed
1b0d2f8 Add calculator.py module and integrate it into main.py
7fcd4a1 Update main.py: Add simple addition feature
a2e4b8c Initial commit: Add main.py with welcome message
```

------

### 7. **回退操作（可选）**

如果删除 `calculator.py` 文件是个错误操作，可以恢复它。

```bash
# 恢复最近一次删除的文件
git checkout HEAD~1 calculator.py

# 添加文件回到暂存区
git add calculator.py

# 提交恢复操作
git commit -m "Restore calculator.py after accidental deletion"
```

------

### 8. **创建分支开发新功能**

假设要在新分支上开发减法功能。

```bash
# 创建并切换到新分支
git checkout -b feature/subtraction

# 修改 main.py 添加减法功能
echo "def subtract(a, b): return a - b" > calculator.py
echo "print('Difference:', subtract(5, 3))" >> main.py

# 添加并提交新功能
git add main.py calculator.py
git commit -m "Add subtraction feature in calculator module"
```

------

### 9. **合并分支到主分支**

开发完成后，将新功能合并回主分支：

```bash
# 切换到主分支
git checkout master

# 合并 feature/subtraction 分支
git merge feature/subtraction

# 查看合并结果
git log --oneline
```

------

### 10. **推送代码到远程仓库**

最后，将代码推送到 GitHub 等远程仓库。

```bash
# 添加远程仓库地址
git remote add origin https://github.com/yourusername/calculator_project.git

# 推送代码到远程仓库
git push -u origin master
```

------

## **总结：完整操作流程**

1. **初始化仓库**：`git init`
2. **新建文件**：`git add` → `git commit`
3. **修改文件**：`git add` → `git commit`
4. **删除文件**：`rm file` → `git add` → `git commit`
5. **分支管理**：`git branch` → `git checkout`
6. **合并分支**：`git merge`
7. **推送代码**：`git remote add` → `git push`

------

## **最终项目结构**

```plaintext
calculator_project/
│
├── .git/              # Git 版本库
├── main.py            # 主程序入口，包含加法和减法功能
└── calculator.py      # 计算器模块（包含加法和减法函数）
```

------

通过这个示例，你可以看到 **项目开发中新增、修改、删除文件** 的具体 Git 命令应用。这样可以高效地管理代码，并确保每次改动都有记录，方便追踪和回退。