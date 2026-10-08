```
curl --location 'https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings' \--header "Authorization: Bearer $DASHSCOPE_API_KEY" \--header 'Content-Type: application/json' \--data '{
    "model": "qwen3.7-text-embedding",
    "input": "衣服的质量杠杠的"
}'
```

### 问：上面这段curl代码对应httpx发送请求的json还是data？

对应 `httpx` 中的 **`json`** 参数。

在 `httpx` 中，传参推荐使用 `json`，原因如下：

- **自动序列化与 Header**：直接传入 Python 字典给 `json` 参数，`httpx` 会自动将其转换为 JSON 字符串，并自动添加 `'Content-Type': 'application/json'` 请求头。
- **`data` 的区别**：如果直接将字典传给 `data` 参数，`httpx` 默认会将其作为表单编码（`application/x-www-form-urlencoded`），这会导致服务端解析失败报错；若非要用 `data`，则必须手动调用 `json.dumps()` 且显式指定 `Content-Type` 请求头。

### 对应的 `httpx` 代码示例

```
import os
import httpx

url = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings"

headers = {
    "Authorization": f"Bearer {os.environ.get('DASHSCOPE_API_KEY')}"
}

payload = {
    "model": "qwen3.7-text-embedding",
    "input": "衣服的质量杠杠的"
}

# 使用 json 参数传递字典
response = httpx.post(url, headers=headers, json=payload)

print(response.json())
```

### 问：那为什么curl中又是data呢

`curl` 的 `--data` 本质上是**直接向 HTTP 请求体（Request Body）填充原始数据**，它本身并不关心数据的具体格式；而 `httpx` 等高级语言库为了提升开发体验，将“数据类型”与“序列化逻辑”进行了分工。



具体原因可以从以下三个维度理解：



- **`curl` 的设计更偏向底层（Raw Body）**

  `curl -d` 或 `--data` 的原始含义是：“将这段文本/字节作为 Body 发送，并将请求方法置为 POST”。

  `curl` 并不知道你传的是 JSON、XML 还是纯文本，因此在 curl 命令中必须显式加上 `--header 'Content-Type: application/json'`，告诉服务端如何解析这串原始字符。

- **`curl` 新版本其实已经支持 `--json`**

  从 `curl 7.82.0`（2022年起）开始，curl 引入了 `--json` 参数。使用 `--json` 时无需手动写 Header：

  Bash

  ```
  # 现代 curl 写法（等价于原先的 --data + Header）
  curl --location 'https://...' \
    --header "Authorization: Bearer $DASHSCOPE_API_KEY" \
    --json '{
      "model": "qwen3.7-text-embedding",
      "input": "衣服的质量杠杠的"
    }'
  ```

- **`httpx` / `requests` 在应用层做了语义拆分**

  Python 库面对的是原生数据结构（字典、列表），为了避免开发者每次手动序列化，设计了两个参数：

  - **`json=...`（高层封装）**：接受 Python 字典，自动执行 `json.dumps()` 并自动加上 `application/json` Header。
  - **`data=...`（表单 / 原始数据）**：如果传字典，会默认转为表单编码（`x-www-form-urlencoded`）；如果传已经序列化好的字符串或二进制流（`str` / `bytes`），则行为与 `curl --data` 完全一致。

### 问：为什么这些协议的实现这么混乱呢

这种“混乱”并不是 HTTP 协议本身混乱，而是**协议规范、历史演进包袱**与**工具抽象层级**三者交织产生的结果。



### 1. HTTP 协议本身其实极其简单且纯粹

在 HTTP 规范（RFC）底层，根本没有所谓的 `json`、`data` 或 `form` 参数区分。一个 HTTP 请求的本质只有两部分：



- **Header（元数据）**：包含 `Content-Type`、`Authorization` 等键值对。
- **Body（纯字节流）**：一串无格式偏好的原始数据（Raw Bytes）。

协议本身只管搬运字节，至于字节流是 JSON、表单文本、XML 还是二进制图片，完全取决于双方约定的 `Content-Type`。



### 2. 30年的历史演进与“向下兼容”包袱

当前开发中使用的各种工具诞生于不同时代：



- **1990 年代（`curl` 诞生时期）**：当时主流是 HTML 网页表单提交（`x-www-form-urlencoded`），JSON 甚至还没被发明。`curl` 的 `--data` 最初设计就是为了模拟浏览器提交表单或填充一段原始数据。
- **2010 年代（RESTful API 爆发）**：JSON 成为 API 事实上的数据交换标准。
- **不能破坏既有生态**：`curl` 服务于全球数以亿计的遗留 Shell 脚本，它绝不可能直接把 `--data` 的默认行为改成 JSON，因此只能在 2022 年（v7.82.0）新增一个独立的 `--json` 参数。

### 3. 高层封装为了“开发体验（DX）”引入了语义分化

像 Python 的 `requests`、`httpx` 或 JS 的 `axios`，目标是让开发者直接操作语言原生的对象（字典、列表），而不是手动拼接字节流。为了提升体验，它们按使用场景拆分了参数：



| **客户端参数** | **协议底层实际做的事**                               | **适用场景**         |
| -------------- | ---------------------------------------------------- | -------------------- |
| **`params`**   | 拼接到 URL 末尾（Query String）                      | GET 请求过滤、分页   |
| **`json`**     | 自动 `json.dumps()` + 补齐 `application/json`        | 现代 RESTful API     |
| **`data`**     | 字典转为 `key1=val1&key2=val2` 或发送原始字符串/字节 | 传统表单提交、原始流 |
| **`files`**    | 编码为 `multipart/form-data`                         | 文件上传             |

### 建立统一的心智模型

不同工具的 API 名称各异，但只要穿透抽象层看底层 HTTP，逻辑就会变得清晰：



> **统一规则**：发送端负责两件事——**1. 把数据序列化成字节流塞进 Body**；**2. 在 Header 里放上对应的 `Content-Type`**。
>
> - `curl --data` 是让你**手动**完成这两步；
> - `curl --json` 和 `httpx(json=...)` 是帮你**全自动**打包完成。