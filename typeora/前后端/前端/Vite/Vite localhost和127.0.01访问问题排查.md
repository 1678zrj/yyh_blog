### 问：我是用npm run dev运行Vue项目后，为什么http://localhost:5173/是可以访问的，但是http://127.0.0.1:5173/是不可以的呢？

这是一个非常经典的网络监听配置问题，在使用 Vite（Vue 默认构建工具）和较新版本的 Node.js 时经常遇到。

简单来说，原因在于**你的开发服务器默认只监听了 IPv6 的回环地址（::1），而没有监听 IPv4 的回环地址（127.0.0.1）。**

下面是详细的解释和解决方法：

### 1. 为什么会出现这种情况？

- **`localhost` 是个域名：** 它通常会被解析为 IPv4 的 `127.0.0.1` **或者** IPv6 的 `::1`。
- **Node.js 的策略变化：** 在 Node.js v17 及以上版本中，它会优先使用 IPv6。
- **Vite 的默认行为：** 当你运行 `npm run dev` 时，Vite 默认只绑定在 `localhost` 上。由于 Node.js 的偏好，它实际上只监听了 IPv6 的 `::1` 端口。
- **结果：**
  - 当你访问 `http://localhost:5173/`：浏览器解析 `localhost`，发现服务器在监听 IPv6，连接成功。
  - 当你访问 `http://127.0.0.1:5173/`：你强制浏览器去连接 IPv4 地址，但服务器并没有在 IPv4 上监听，所以连接被拒绝。

### 2. 如何解决？

如果你希望通过 IP (`127.0.0.1`) 甚至局域网 IP (如 `192.168.x.x`) 访问，你需要配置 Vite 让它监听所有 IP 地址。

#### 方法一：修改 `vite.config.js`（推荐）

这是最永久的办法。打开你的 `vite.config.js` (或 `vite.config.ts`) 文件，在 `server` 选项中添加 `host: true` 或 `host: '0.0.0.0'`。

JavaScript

```
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    // 添加这一行
    host: '0.0.0.0' 
  }
})
```

- **注意：** 设置为 `0.0.0.0` 后，你的项目不仅可以通过 `127.0.0.1` 访问，同一局域网下的其他设备（比如你的手机）也可以通过你的电脑 IP 访问该页面。

#### 方法二：通过命令行参数

如果你不想改配置文件，也可以在运行命令时临时指定。

在终端中运行：

Bash

```
npm run dev -- --host
```

*(注意中间的 `--` 是必须的，它告诉 npm 后面的参数是传给 vite 的)*

------

### 总结

| **地址**    | **状态** | **原因**                                                   |
| ----------- | -------- | ---------------------------------------------------------- |
| `localhost` | ✅ 通     | 系统自动解析到了 IPv6 (::1)，正好服务器在监听它。          |
| `127.0.0.1` | ❌ 不通   | 这是纯 IPv4 地址，默认配置下服务器没有监听 IPv4。          |
| `0.0.0.0`   | ✅ 通     | 修改配置后，服务器会监听所有可用 IP（包括 IPv4 和 IPv6）。 |

**需要我教你如何查看本机的局域网 IP，以便用手机测试你的 Vue 项目吗？**

### 问：好的，我还想知道如果到时候部署到生产环境上nginx服务器需要配置这些吗？

这是一个非常关键的概念转换问题。简短的回答是：**不需要，而且配置了也没用。**

在 `vite.config.js` 里的 `server: { host: '0.0.0.0' }` 配置 **仅对开发环境（`npm run dev`）有效**。

当你部署到生产环境（Production）时，运行原理完全变了。下面我为你详细拆解一下区别，以及 Nginx 到底需要配置什么。

### 1. 核心区别：开发模式 vs 生产模式

- **开发模式 (`npm run dev`)：**
  - 这时候是 **Vite** 启动了一个基于 Node.js 的微型服务器。
  - 这个服务器负责把 Vue 代码实时编译发给浏览器。
  - **你需要**配置 `host`，告诉 Vite 这个微型服务器监听谁的请求。
- **生产模式 (`npm run build`)：**
  - 你会先运行打包命令。Vite 会把你的 `.vue`, `.js`, `.css` 全部“压扁”成纯粹的静态文件（HTML/CSS/JS），通常放在一个 `dist` 文件夹里。
  - **Vite 的任务到此结束了。** 此时已经没有 Vite 服务器在运行了。
  - **Nginx 的角色：** Nginx 只是负责把 `dist` 文件夹里的静态文件“拿”给访问者看。

### 2. Nginx 需要配置什么？

在 Nginx 中，你不需要像 Vite 那样显式写 `host: '0.0.0.0'`，因为 Nginx 的 `listen` 指令默认就是监听所有网卡接口（也就是默认就是 0.0.0.0）。

你需要关注的是以下 **3 个核心配置**：

#### A. 端口监听 (listen)

这是用来替代 Vite 开发端口（5173）的。通常生产环境使用 80 (HTTP) 或 443 (HTTPS)。

#### B. 静态文件路径 (root)

告诉 Nginx 你打包好的 `dist` 文件夹放在服务器的哪个位置。

#### C. 路由重定向 (非常重要！)

这是 Vue 单页应用（SPA）部署最容易踩的坑。因为 Vue 使用的是前端路由，刷新页面时，Nginx 会以为你要找一个文件夹，结果找不到报错 404。必须配置 `try_files` 把请求指回 `index.html`。

### 3. 标准的 Nginx 配置模板 (Vue 专用)

假设你把 `npm run build` 生成的 `dist` 文件夹上传到了服务器的 `/usr/share/nginx/html` 目录：

Nginx

```
server {
    # 1. 监听端口
    # listen 80; 等同于监听 0.0.0.0:80，所有外网IP都能访问
    listen       80;
    server_name  your_domain.com; # 你的域名或公网IP

    # 2. 核心文件服务配置
    location / {
        # 你的 dist 文件夹所在的绝对路径
        root   /usr/share/nginx/html;
        # 默认入口文件
        index  index.html index.htm;
        
        # 3. 【关键配置】解决 Vue 路由 404 问题
        # 意思是：先找有没有对应的文件？没有就找对应的目录？还没有就退回到 index.html 让 Vue 自己处理
        try_files $uri $uri/ /index.html;
    }
}
```

### 总结一张表

| **特性**        | **开发环境 (Vite)**                    | **生产环境 (Nginx)**                     |
| --------------- | -------------------------------------- | ---------------------------------------- |
| **运行命令**    | `npm run dev`                          | Nginx 后台服务 (systemctl start nginx)   |
| **谁提供服务**  | Vite (Node.js)                         | Nginx (高性能 Web 服务器)                |
| **IP 监听配置** | 需在 `vite.config.js` 手动设 `0.0.0.0` | Nginx 默认监听所有 IP (`listen 80`)      |
| **跨域代理**    | `vite.config.js` 里的 `proxy`          | **失效**，需在 Nginx 里配置 `proxy_pass` |

特别提醒：

如果你在开发环境 (vite.config.js) 里配置了 server: { proxy: { ... } } 来解决后端接口跨域问题，这个配置在生产环境也会失效。

到时候你需要把这个代理规则“翻译”成 Nginx 的配置。**需要我给你展示一下怎么把 Vite 的 proxy 转换成 Nginx 的 proxy 吗？**