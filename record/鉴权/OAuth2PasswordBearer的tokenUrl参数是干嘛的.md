```python
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt

app = FastAPI()

# 1. 定义 OAuth2 方案，告诉 FastAPI Token 从 Authorization: Bearer 提取
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

SECRET_KEY = "your-secret-key"
ALGORITHM = "HS256"

# 2. 定义验证依赖项函数（核心验证逻辑发生在这里）
async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token 无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # 解密与验证 JWT 的签名与过期时间 (exp)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    
    # 此处也可以从数据库查出完整的 user 对象
    return {"username": username}

# 3. 受保护的业务 API：直接依赖 get_current_user 接口
@app.get("/items/")
async def read_items(current_user: dict = Depends(get_current_user)):
    # 只要能走到这里，说明 Token 已经被校验成功了
    return [{"item_id": 1, "owner": current_user["username"]}]
```

### 问：我想了解一下oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")中的tokenUrl="token"是干嘛的？

**`tokenUrl="token"` 的主要作用是为 Swagger UI 交互式文档提供获取 Token 的接口地址，并不会影响代码运行时的 Token 校验逻辑。**

### 1. 核心作用：给 Swagger UI 指路

当启动 FastAPI 并在浏览器中打开 `/docs`（Swagger UI）时，页面右上角会多出一个 **"Authorize"** 按钮，或者每个受保护接口右侧会出现一个小锁图标。

当你点击 "Authorize" 时：

- Swagger UI 会弹出一个登录框，要求你输入用户名（username）和密码（password）。
- 当你点击提交时，Swagger UI 就会向 **`tokenUrl` 指定的路径（即 `/token`）** 发送一个 `POST` 请求来获取 JWT Token。
- 拿到 Token 后，Swagger UI 会自动将其存入浏览器，并在后续请求的 Header 中带上 `Authorization: Bearer <token>`。

### 2. 对代码运行的影响

- **校验环节无关：** 无论 `tokenUrl` 写成什么（比如 `tokenUrl="login"` 或 `tokenUrl="[https://example.com/api/token](https://example.com/api/token)"`），你的后端接口（如 `/items/`）在实际接收请求并调用 `get_current_user` 校验 Token 时，**完全不依赖** 这个属性。它只负责解析 HTTP Header 里的 `Authorization` 字段。

- **接口路径匹配：** `tokenUrl="token"` 使用的是相对路径。如果你在应用中写了一个处理登录并颁发 Token 的接口：

  Python

  ```
  @app.post("/token")
  async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
      ...
  ```

  那么 `tokenUrl="token"` 就能完美匹配这个登录接口，让 Swagger UI 里的直接登录测试功能正常工作。

### 总结

`tokenUrl` 本质上是一个 **OpenAPI (Swagger) 文档配置项**。它是告诉前端文档：“如果用户想在页面上直接登录测试这个受保护的 API，请向这个 URL 发送登录请求获取 Token。”

### 问：所以tokenUrl应该指向的是登录的URL是吗？

**是的，完全正确。** `tokenUrl` 应该指向你的应用中**专门用来登录并返回 Token 的那个 API 路径**。

### 举个完整的例子

如果你写了一个登录接口，路由设为 `/api/v1/login`：

Python

```
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

app = FastAPI()

# 1. tokenUrl 填入你实际的登录接口路径
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")

# 2. 专门用于登录、颁发 Token 的接口
@app.post("/api/v1/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # 验证用户名和密码（此处仅做示例）
    if form_data.username == "admin" and form_data.password == "123456":
        # 返回符合 OAuth2 规范的 Token JSON 结构
        return {"access_token": "your_jwt_token_here", "token_type": "bearer"}
    raise HTTPException(status_code=400, detail="用户名或密码错误")

# 3. 需要 Token 校验的受保护接口
@app.get("/users/me")
async def read_users_me(token: str = Depends(oauth2_scheme)):
    return {"token": token}
```

### 匹配的好处

当你把 `tokenUrl` 设置为真实的登录路径（如 `/api/v1/login`）后：

1. 打开 `/docs` 交互文档。
2. 点击右上角的 **Authorize** 按钮。
3. 输入账号密码并点击 **Authorize**。
4. Swagger UI 会直接发请求给 `/api/v1/login`，拿到 Token 后自动帮你填入 Header。
5. 之后你在文档页面上直接点击“Try it out”测试任何受保护的 API（如 `/users/me`），就都不用手动复制粘贴 Token 了。

### 问：

```python
class UserLogin(SQLModel):
    username: str
    password: str
@router.post("/login", response_model=Token)
def login(
        response: Response, # 需要用到 Response 对象来设置 Cookie,
        user_in: UserLogin,
        session: Session = Depends(get_session),
        settings: Settings = Depends(get_settings)
):

```

### 登录的接口是这么定义的会有影响吗?

**会有影响，主要是会导致 Swagger UI（`/docs` 页面）的“交互式登录测试”功能无法正常工作。**

虽然在你自己写的真实前端应用（如 React、Vue 或 App）中这样用完全没有问题，但在 FastAPI 的 Swagger UI 自动化文档层面上，两者存在**请求格式的不兼容**。

### 产生影响的核心原因：数据提交格式不同

标准的 OAuth2 规范与你的代码在接收数据格式上存在差异：

| **维度**       | **你当前的定义 (UserLogin)**             | **Swagger UI 的 OAuth2 规范 (tokenUrl)**           |
| -------------- | ---------------------------------------- | -------------------------------------------------- |
| **数据格式**   | **JSON 格式** (`application/json`)       | **表单格式** (`application/x-www-form-urlencoded`) |
| **请求体结构** | `{"username": "...", "password": "..."}` | `username=...&password=...`                        |

当你在 `/docs` 页面点击右上角 **"Authorize"** 锁图标输入账号密码提交时，Swagger UI 会自动按照 OAuth2 规范发送 `application/x-www-form-urlencoded` 表单数据。但你的 `/login` 接口要求的是 `UserLogin`（JSON 格式），这会导致 FastAPI 抛出 **`422 Unprocessable Entity` (请求参数格式错误)**。

### 解决方案

针对这种情况，通常有两种解决思路：

#### 方案 A：改用 FastAPI 官方推荐的 `OAuth2PasswordRequestForm`（最推荐）

如果你希望 `/docs` 文档的登录弹窗能够完美直接使用，将请求参数改用 FastAPI 提供的 `OAuth2PasswordRequestForm`：

Python

```
from fastapi.security import OAuth2PasswordRequestForm

@router.post("/login", response_model=Token)
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(), # 自动解析表单中的 username 和 password
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings)
):
    # 使用 form_data.username 和 form_data.password 进行校验
    user = authenticate_user(session, form_data.username, form_data.password)
    ...
```

#### 方案 B：保持使用 JSON (`UserLogin`)，调整 OAuth2 定义

如果你和前端团队已经约定好**必须使用 JSON 体传输登录数据**，你可以继续保持你现有的 `/login` 代码，但需要注意：

1. **真实业务调用**：移动端、Web 前端直接发送 JSON `{"username": "...", "password": "..."}` 请求 `/login`，**完全不受影响，正常运行**。
2. **文档调试**：Swagger UI 的右上角 **Authorize** 自动登录功能无法使用。如果你还想在 Swagger UI 中测试受保护接口，只能在页面下方直接找到 `/login` 接口的调试界面，手动发送 JSON 拿到 Token，再复制 Token 到右上角的 Authorization 框中。

> **小技巧（兼容方案）：** 也有不少开发者会定义两个接口：一个 `/login` 供前端传递 JSON 使用；另一个 `/token` 使用 `OAuth2PasswordRequestForm` 专门供 Swagger UI 文档弹窗测试使用。

### 问：如何在 FastAPI 中让登录接口同时支持 JSON 请求体和 Form 表单提交？

在 FastAPI 中，由于原生依赖解析（`Depends`）处理 JSON（`body`）和表单（`form`）的机制不同，一个路由默认无法直接自动兼容两种 Content-Type。

要实现同一个登录接口**既能接收 JSON 请求体（供前端/APP使用），又能接收 Form 表单提交（供 Swagger UI 自动登录使用）**，最优雅的解决方案是**通过自定义依赖项解析 Request**。

### 最佳实践方案：自定义 Request 解析依赖项

我们可以写一个自定义的依赖项，直接检查 HTTP 请求的 `Content-Type` Header：

- 如果是 `application/json`，就按 JSON 方式解析数据；
- 如果是 `application/x-www-form-urlencoded` 或 `multipart/form-data`，就按表单解析数据。

#### 完整代码实现

Python

```
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import SQLModel

app = FastAPI()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

# 1. 定义数据模型，同时支持 JSON 与 Form 的字段名称映射
class UserLogin(BaseModel):
    username: str
    password: str

    # 允许从外部对象的属性中解析数据
    model_config = ConfigDict(from_attributes=True)

# 2. 核心：自定义数据抽取依赖项
async def get_login_data(request: Request) -> UserLogin:
    content_type = request.headers.get("content-type", "")

    # 情况 A：JSON 请求 (通常是 前端/Vue/React/APP 提交)
    if "application/json" in content_type:
        try:
            body = await request.json()
            return UserLogin(**body)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="无效的 JSON 请求体"
            )

    # 情况 B：Form 表单提交 (通常是 Swagger UI / OAuth2 弹窗提交)
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        
        if not username or not password:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="缺少必要的 username 或 password 表单字段"
            )
        return UserLogin(username=str(username), password=str(password))

    # 其他不支持的 Content-Type
    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"不支持的 Content-Type: {content_type}"
        )

# 3. 登录接口：使用自定义依赖项
@app.post("/login")
async def login(
    response: Response,
    credentials: UserLogin = Depends(get_login_data)  # 自动兼容两种格式
):
    # 统一使用 credentials.username 和 credentials.password 处理逻辑
    if credentials.username == "admin" and credentials.password == "123456":
        return {
            "access_token": "fake-jwt-token",
            "token_type": "bearer"
        }
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="用户名或密码错误"
    )
```

### 方案效果

采用上述方法后，你的 `/login` 接口将同时具备以下能力：

1. **服务真实前端（JSON 请求）：**

   客户端发送 `Content-Type: application/json` 且 Body 为 `{"username": "admin", "password": "123456"}` 时，能够正常验证并登录。

2. **服务 Swagger UI（Form 表单）：**

   打开 `/docs` 页面，点击右上角的 **Authorize** 按钮，直接在弹窗中输入账号密码点击提交，Swagger UI 发送表单请求同样能够成功验证并保存 Token。

### 问：前端是喜欢form_data: OAuth2PasswordRequestForm = Depends()还是user_in: UserLogin？

**绝大多数前端开发者都更喜欢 `user_in: UserLogin`（即 JSON 格式）。**

在现代 Web 和 App 开发中，**JSON 已经成为了 API 数据交互的标准**。除了一些特殊场景（如文件上传），前端在处理绝大多数接口时都首选 JSON。

### 前端为什么更偏爱 JSON？

1. **统一的代码编写习惯**

   前端项目（React, Vue, Mini Programs, Flutter 等）通常会封装统一的网络请求库（如 `axios` 或 `fetch`）。

   - 发送 JSON 时，直接传 JavaScript 对象即可，axios 会自动序列化：

     JavaScript

     ```
     // 简单、直观
     axios.post('/api/login', { username, password })
     ```

   - 发送 Form Data 时，前端需要额外实例化对象并手动添加字段：

     JavaScript

     ```
     // 繁琐，容易遗漏
     const formData = new FormData()
     formData.append('username', username)
     formData.append('password', password)
     axios.post('/api/login', formData)
     ```

2. **请求头（Header）配置更省心**

   Axios 等常用请求库默认的 `Content-Type` 就是 `application/json`。如果不小心用 Form 表单接口接收数据，前端忘了修改 Header 为 `application/x-www-form-urlencoded`，请求就会直接报 `422` 错误，增加沟通和排查成本。

3. **对复杂数据结构的支持更好**

   如果未来登录接口需要扩展，引入更复杂的字段（比如包含用户偏好数组、嵌套对象等），JSON 格式天生支持复杂结构，而 Form 表单在处理嵌套对象时会变得非常痛苦。

### 为什么 FastAPI / OAuth2 默认要用 Form 表单？

你可能会纳闷，既然前端喜欢 JSON，为什么 FastAPI 官方文档里的 OAuth2 示例全都是 `OAuth2PasswordRequestForm`？

- **因为规范限制：** FastAPI 的 `OAuth2PasswordBearer` 遵循的是标准的 **OAuth 2.0 协议规范 (RFC 6749)**。该规范在 2012 年制定时，明确规定了 Token 申请接口必须使用 `application/x-www-form-urlencoded` 格式。
- **为了配合 Swagger UI：** Swagger UI 是完全按照 OAuth2 标准协议构建的，因此它的 "Authorize" 弹窗只懂得发送 Form 表单数据。

### 建议与实践策略

| **方案**                                          | **适用场景**                                            | **优缺点**                                                   |
| ------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------ |
| **首选：使用 JSON (`UserLogin`)**                 | 团队前后端分离开发，以前端体验和项目架构统一为主        | **前端最舒服**；但 Swagger UI 右上角授权弹窗无法自动使用（需手动在 `/login` 接口中测试获取 Token）。 |
| **折中：自定义兼容方案**                          | 既想让前端传 JSON，又想保留 Swagger UI 授权弹窗调试功能 | 双方都满意，但后端需要编写额外的依赖解析逻辑（即上一轮回复的代码）。 |
| **合规：使用 Form (`OAuth2PasswordRequestForm`)** | 严格遵循 OAuth2 标准，或提供给第三方开放平台对接        | 标准规范；但前端调接口时需要专门将数据转为 Form 格式。       |

**总结：** 如果项目是纯内部团队协同，直接采用前端喜欢的 JSON (`user_in: UserLogin`) 是最顺手的做法。

### 问：你的意思是使用OAuth2PasswordRequestForm才能够支持SSO吗？

**不是的，SSO（单点登录）完全可以使用 JSON (`UserLogin`)，并不依赖 `OAuth2PasswordRequestForm`。**

你选择用 JSON 还是 Form 表单，**只影响你的 API 接收参数的格式**，绝对不会限制你接入或实现 SSO（单点登录）。

### 为什么说 SSO 和 `OAuth2PasswordRequestForm` 无关？

我们要区分“认证协议/流程”**和**“数据传输格式”：

1. **SSO 的本质是“信任传递”与“身份重定向”：**

   标准的 SSO（如基于 OIDC / OAuth2 / SAML / CAS 等）的核心流程是：用户在中央认证中心（Auth Center）登录，拿到一个 Authorization Code 或 Token，然后重定向回你的子系统应用。

   - 在这个过程中，子系统（你的 FastAPI 服务）通常是通过 **URL 回调（Callback）**、**解析 Header 中的 JWT** 或 **后端向 SSO 服务端校验 Token** 来识别用户身份的。
   - **子系统的业务 API 根本不需要自己去处理“输入账号密码”这一步。**

2. **`OAuth2PasswordRequestForm` 只是 OAuth2 的一种特定模式：**

   `OAuth2PasswordRequestForm` 对应的是 OAuth 2.0 中的 **密码模式（Resource Owner Password Credentials Grant）**。

   - 这种模式要求用户把账号密码直接输入给你的 API。
   - **巧合的是：** 现代 SSO 架构（如 Auth0、Keycloak、Okta 等）**强烈建议禁用密码模式**，因为密码模式要求第三方应用接触用户密码，违背了 SSO 的安全初衷（SSO 的初衷是用户只在统一的中央登录页输入密码）。

### 常见 SSO 场景下 FastAPI 如何配合

#### 场景 1：使用标准 SSO 流程（如 OIDC / OAuth2 Authorization Code 模式）

- **流程：** 用户点击“SSO登录” -> 跳转到 SSO 统一登录页输入密码 -> 登录成功跳回 FastAPI -> FastAPI 拿到 Code 换取/解析 Token。
- **FastAPI 的角色：** FastAPI 只需要一个接口接收 `code` 或直接校验 Header 里的 `Bearer Token`，**完全不需要任何账号密码表单**。

#### 场景 2：你的 FastAPI 本身就是“SSO 认证中心”

如果你在自研 SSO 统一认证中心，前端（Vue/React/APP）调用你的 `/login` 接口获取全局 SSO Token：

- 前端发送 JSON 请求：`POST /login` `{"username": "...", "password": "..."}`
- FastAPI 校验后颁发 JWT Token。
- 这完全能够完美支持 SSO，前端拿到这个 Token 就可以去访问你旗下的所有子系统。

### 总结

- **`OAuth2PasswordRequestForm` 的唯一硬性绑定者：** 是 **Swagger UI (`/docs`) 页面右上角的 Authorize 弹窗**（因为它硬编码了 OAuth2 的密码模式表单格式）。
- **SSO (单点登录)：** 关注的是**跨系统的身份认证与授权机制**，无论你的登录接口是用 JSON、Form 还是 OAuth2 Authorization Code 流程，都能毫无障碍地实现和支持 SSO。