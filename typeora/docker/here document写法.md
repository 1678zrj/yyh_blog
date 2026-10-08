# **here document**（也叫 **heredoc**）写法

```
<<-'EOF'
{
    "registry-mirrors": [
        "https://mirror.ccs.tencentyun.com",
        "https://docker.m.daocloud.io"
    ]
}
```

这段代码是一个 **here document**（也叫 **heredoc**）的写法，常用于 shell 脚本或命令行中，用来将多行文本写入文件或传递给命令。具体来说，它是通过 `<<-'EOF'` 来标识输入的开始，`EOF` 用来标识输入的结束。

### **Heredoc 写法解析**

```bash
<<-'EOF'
{
    "registry-mirrors": [
        "https://mirror.ccs.tencentyun.com",
        "https://docker.m.daocloud.io"
    ]
}
EOF
```

#### 1. **`<<-'EOF'`**

- **`<<`**：表示开始输入一个 **here document**，它告诉 shell 将后续的多行文本传输给命令。

- `'EOF'`

  ：

  ```
  EOF
  ```

   是一个标记符号，表示这个文本的结束位置。这个标记符号可以是任何字符串（常用的有 

  ```
  EOF
  ```

  、

  ```
  END
  ```

  、

  ```
  EOF_FILE
  ```

   等），它没有特定的意义，重要的是标记的起始和结束要一致。

  - **`-`（连字符）**：这个连字符表示“去除行首的空格”。如果有 `-`，shell 会忽略每行前面的空白字符。这对于格式化文本非常有用，避免了文本缩进对输出的影响。

#### 2. **多行文本内容**

文本内容（如 JSON 配置）被包裹在 `<<-'EOF'` 和 `EOF` 之间，它可以是任意内容。在这个例子中，是一个 JSON 格式的 Docker 配置，用来指定 Docker 镜像加速器。

#### 3. **`EOF`**

- **`EOF`**：这是标记符号，用来标识输入文本的结束位置。它必须单独一行，且不能有额外的空格或字符。文本会被直接写入文件或传递给命令。

### **完整解释：这段代码的作用**

- **目的**：将 JSON 配置写入 `/etc/docker/daemon.json` 文件。

- 命令解析

  ：

  ```bash
  sudo tee /etc/docker/daemon.json <<-'EOF'
  {
      "registry-mirrors": [
          "https://mirror.ccs.tencentyun.com",
          "https://docker.m.daocloud.io"
      ]
  }
  EOF
  ```

  - `sudo tee /etc/docker/daemon.json`：用 `tee` 命令将输入写入文件 `/etc/docker/daemon.json`。由于 `sudo`，它会以管理员权限写入文件。
  - `<<-'EOF'`：表示接下来的文本会被传递给 `tee`，并写入文件。文本内容会直接插入到文件中。
  - `EOF`：标识文本的结束，shell 会知道从此行开始到 `EOF` 结束之间的内容需要写入文件。

### **为什么使用 Heredoc？**

- **多行文本输入**：Heredoc 使得向命令传递多行内容变得简便。
- **可读性**：比使用多个 `echo` 命令逐行写入文本更简洁。
- **支持格式化**：可以保留文本的格式，特别适用于配置文件（如 JSON、YAML）或脚本中的大块文本。

### **常见用法**

1. **向文件写入内容**：

   ```bash
   cat <<EOF > file.txt
   This is a multi-line text.
   It will be written to file.txt.
   EOF
   ```

2. **传递多行文本给命令**：

   ```bash
   mysql -u root -p <<EOF
   CREATE DATABASE test;
   USE test;
   EOF
   ```

------

### **总结**

- **Heredoc** 是一种简洁的多行文本输入方式，常用于 shell 脚本中。
- 在 `<<-'EOF'` 中，`<<` 指示多行文本的开始，`EOF` 是标记符号，表示文本的结束。
- `-` 可以去除文本行首的空格，确保文本格式化良好。

这种写法在配置文件、脚本生成、或多行文本传递时非常常见。