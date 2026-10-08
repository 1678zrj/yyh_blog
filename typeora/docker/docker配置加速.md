# docker配置加速

这是一段用于配置 Docker 加速器的命令集合，主要是为了提升从 Docker Registry 拉取镜像的速度，尤其是在国内网络环境下，这种配置非常常见。以下是命令的逐步解析：

------

### **1. 创建 Docker 配置目录**

```bash
sudo mkdir -p /etc/docker
```

- **作用**：
   创建 Docker 的配置目录 `/etc/docker`，如果目录已经存在则不会报错（`-p` 参数的作用）。
- **原因**：
   Docker 的配置文件通常存放在 `/etc/docker/daemon.json` 中，需要确保目录存在以便后续操作。

------

### **2. 创建或更新配置文件**

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

- 作用

  ：

  - 将以下内容写入到 

    ```
    /etc/docker/daemon.json
    ```

     文件中：

    ```json
    {
        "registry-mirrors": [
            "https://mirror.ccs.tencentyun.com",
            "https://docker.m.daocloud.io"
        ]
    }
    ```

  - 使用加速器镜像地址，替代默认的 Docker Hub 镜像源，加速镜像拉取速度。

- 关键点

  ：

  - `registry-mirrors` 是 Docker Daemon 的配置项，用于指定镜像仓库的加速地址。
  - `tee` 命令会覆盖原有文件内容，确保配置正确。

------

### **3. 重新加载 Docker 配置**

```bash
sudo systemctl daemon-reload
```

- **作用**： 通知 systemd 重新加载服务配置文件，以便使新的配置文件（如 `daemon.json`）生效。

------

### **4. 重启 Docker 服务**

```bash
sudo systemctl restart docker
```

- **作用**： 重新启动 Docker 服务，使更改后的配置立即生效。

------

### **加速器作用**

加速器提供了国内优化的 Docker 镜像拉取服务，能够显著提升镜像下载速度。上述配置的两个常见加速器：

1. **腾讯云镜像加速器**：
    地址：`https://mirror.ccs.tencentyun.com`
2. **DaoCloud 镜像加速器**：
    地址：`https://docker.m.daocloud.io`

------

### **验证加速器是否生效**

1. 查看 Docker 配置是否正确：

   ```bash
   cat /etc/docker/daemon.json
   ```

   输出应包含 `registry-mirrors`。

2. 拉取镜像测试速度：

   ```bash
   docker pull hello-world
   ```

   如果配置生效，镜像会通过加速器拉取。

3. 查看 Docker 服务日志：

   ```bash
   sudo journalctl -u docker
   ```

   确认服务启动时是否正确加载了 `daemon.json` 配置。

------

### **总结**

通过上述命令：

- 配置了 Docker 镜像加速器，提高了镜像拉取效率。
- 重新加载了 Docker 服务的配置并使其立即生效。 这种操作在国内网络环境下非常有用，有助于解决镜像拉取缓慢的问题。