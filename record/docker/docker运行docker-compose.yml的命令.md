### 问：docker有了docker-compose.yml文件后，应该如何运行呢

在终端中进入 `docker-compose.yml` 所在的目录，执行以下命令即可在后台启动并运行所有服务：



Bash

```
docker compose up -d
```

*(注：如果使用的是较旧的 Docker Compose V1 版本，请使用带连字符的命令 `docker-compose up -d`)*



**核心操作流程与常用命令**



**1. 进入目录并启动**



Bash

```
# 1. 切换到 docker-compose.yml 存放路径
cd /path/to/your-project

# 2. 拉取镜像并在后台启动容器
docker compose up -d
```

*验证方式*：终端会显示 `Network`、`Volume` 以及各个容器的创建进度，最后输出类似 `Started` 或 `Running` 即表示创建完成。



**2. 检查运行状态**



Bash

```
docker compose ps
```

*验证方式*：查看输出列表中的 `STATUS` 列，确认所有容器状态均为 `Up` 或 `running`。如果出现 `Exited (1)` 等字样，说明容器启动后异常退出。



**3. 查看实时日志（排查问题常用）**



Bash

```
# 查看所有容器的实时合并日志
docker compose logs -f

# 仅查看指定服务的日志（如 web 服务）
docker compose logs -f <service_name>
```

**4. 停止与管理**



- **停止并删除容器、网络**（不删除挂载的数据卷）：

  Bash

  ```
  docker compose down
  ```

- **停止并删除容器、网络及关联的数据卷**（会清空卷内数据，谨慎使用）：

  Bash

  ```
  docker compose down -v
  ```

- **重启服务**：

  Bash

  ```
  docker compose restart
  ```

- **修改了代码或 Dockerfile 后重新构建并启动**：

  Bash

  ```
  docker compose up -d --build
  ```