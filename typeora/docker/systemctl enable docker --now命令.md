# 1、systemctl enable docker --now命令作用

命令 **`systemctl enable docker --now`** 是用于在基于 systemd 的 Linux 系统中管理 Docker 服务的复合命令，它结合了两项操作：**启用服务** 和 **启动服务**。

------

### **分解命令含义**

#### 1. **`systemctl enable docker`**

- **功能**：
   将 Docker 服务（`docker.service`）配置为 **开机自启动**。
- **作用**：
   创建一个符号链接，将 Docker 服务的配置文件链接到系统的默认启动目录（如 `/etc/systemd/system/multi-user.target.wants/`）。
- **结果**：
   下次系统启动时，Docker 服务会自动运行。

------

#### 2. **`--now`**

- **功能**：
   在启用服务（`enable`）的同时，立即启动服务。

- 等效命令

  ：

  该选项相当于同时运行以下两条命令：

  ```bash
  systemctl enable docker
  systemctl start docker
  ```

- **作用**：
   避免分步骤运行命令，直接启用并启动服务。

------

### **完整命令作用**

- 启用 **Docker** 服务，使其随系统启动自动运行。
- 立即启动 **Docker** 服务，无需等到系统下次重启。

------

### **执行后的验证**

运行该命令后，可以通过以下方式验证 Docker 服务的状态：

1. 查看服务是否正在运行：

   ```bash
   systemctl status docker
   ```

   输出中显示 `Active: active (running)` 表示服务已成功启动。

2. 确认服务是否开机自启：

   ```bash
   systemctl is-enabled docker
   ```

   输出 `enabled` 表示服务已被配置为开机自启。

------

### **示例场景**

#### 场景 1：首次安装 Docker

安装 Docker 后，需要它立即运行并且每次系统重启时自动启动。

#### 场景 2：修复服务配置

如果 Docker 服务被意外禁用或未启动，运行该命令可一键修复。

------

### **总结**

`systemctl enable docker --now` 是一个便捷的命令，用于同时完成以下两项工作：

1. **启用** Docker 服务，确保它在每次系统启动时自动运行。
2. **立即启动** Docker 服务，使其在当前会话中立即生效。