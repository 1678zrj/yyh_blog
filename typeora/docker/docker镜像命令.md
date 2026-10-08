在 Docker 中，镜像是用于创建容器的模板。Docker 提供了许多与镜像相关的命令，下面是一些常用的 Docker 镜像命令：

### **1. 查看镜像**

- `docker images`

  列出本地存储的所有 Docker 镜像。

  ```bash
  docker images
  ```

  或者：

  ```bash
  docker image ls
  ```

  输出说明

  ：

  - `REPOSITORY`：镜像的仓库名。
  - `TAG`：镜像的标签（通常是版本号，如 `latest`）。
  - `IMAGE ID`：镜像的 ID。
  - `CREATED`：镜像的创建时间。
  - `SIZE`：镜像的大小。

### **2. 拉取镜像**

- `docker pull`

  从 Docker Hub 或其他仓库中拉取镜像。

  ```bash
  docker pull <image_name>:<tag>
  ```

  示例：

  ```bash
  docker pull ubuntu:latest
  ```

### **3. 构建镜像**

- `docker build`

  根据 Dockerfile 构建镜像。

  ```bash
  docker build -t <image_name>:<tag> <path_to_dockerfile>
  ```

  示例：

  ```bash
  docker build -t myimage:v1 .
  ```

  - `-t` 用于为镜像指定名称和标签。
  - `.` 指定当前目录为 Dockerfile 所在位置。

### **4. 删除镜像**

- `docker rmi`

  删除一个或多个 Docker 镜像。

  ```bash
  docker rmi <image_name>
  ```

  示例：

  ```bash
  docker rmi ubuntu:latest
  ```

  - 如果镜像正在被容器使用，会删除失败。

  - 可以使用 

    ```
    -f
    ```

     强制删除：

    ```bash
    docker rmi -f <image_name>
    ```

### **5. 查看镜像详细信息**

- `docker inspect`

  查看镜像的详细信息。

  ```bash
  docker inspect <image_name_or_id>
  ```

### **6. 标记镜像**

- `docker tag`

  为现有镜像打上新的标签。

  ```bash
  docker tag <image_name_or_id> <new_image_name>:<tag>
  ```

  示例：

  ```bash
  docker tag ubuntu:latest myubuntu:1.0
  ```

### **7. 查询镜像的历史**

- `docker history`

  查看镜像的历史记录。

  ```bash
  docker history <image_name_or_id>
  ```

  输出内容包括：层的创建时间、镜像大小、命令等。

### **8. 导出镜像**

- `docker save`

  将镜像保存为一个 tar 文件，方便传输。

  ```bash
  docker save -o <output_file.tar> <image_name>:<tag>
  ```

  示例：

  ```bash
  docker save -o myimage.tar myimage:v1
  ```

### **9. 导入镜像**

- `docker load`

  从一个 tar 文件导入镜像。

  ```bash
  docker load -i <input_file.tar>
  ```

  示例：

  ```bash
  docker load -i myimage.tar
  ```

### **10. 镜像去重**

- `docker prune`

  清理未使用的镜像，释放磁盘空间。

  ```bash
  docker image prune
  ```

  使用 

  ```
  -a
  ```

   可以删除所有未使用的镜像（包括 dangling 镜像）：

  ```bash
  docker image prune -a
  ```

### **11. 查找镜像**

- `docker search`

  搜索 Docker Hub 上的镜像。

  ```bash
  docker search <image_name>
  ```

  示例：

  ```bash
  docker search nginx
  ```

### **12. 查看镜像标签**

- `docker tag`

  查看镜像的所有标签：

  ```bash
  docker pull <image_name>
  docker images <image_name>
  ```

------

### **常用 Docker 镜像命令总结**

| 命令                          | 描述                         |
| ----------------------------- | ---------------------------- |
| `docker images`               | 查看本地镜像列表             |
| `docker pull <image_name>`    | 从 Docker Hub 或仓库拉取镜像 |
| `docker build`                | 根据 Dockerfile 构建镜像     |
| `docker rmi <image_name>`     | 删除本地镜像                 |
| `docker inspect <image_name>` | 查看镜像的详细信息           |
| `docker history <image_name>` | 查看镜像的构建历史           |
| `docker save`                 | 将镜像保存为 tar 文件        |
| `docker load`                 | 从 tar 文件导入镜像          |
| `docker image prune`          | 清理未使用的镜像             |

这些命令可以帮助你管理 Docker 镜像的各种操作，从拉取镜像、构建镜像、删除镜像到清理无用的镜像等。