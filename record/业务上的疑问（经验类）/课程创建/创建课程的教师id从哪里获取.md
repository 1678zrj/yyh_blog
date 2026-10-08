### 问：我在开发FastAPI程序，现在有个疑惑，场景是我已经实现了access token机制和依赖token的鉴权，然后我有课程表，课程表teacher_id属性也绑定了外键教师id，只有教师能够新建课程，那么问题是新增课程的请求体模型需要有教师id这个属性吗？还是从token中获取呢？

结论非常明确：**强烈建议从 Token 中获取 `teacher_id`，而不是放在请求体（`CourseCreate`）中。**

在“普通教师创建自己的课程”这个业务场景下，让前端在 Body 里传 `teacher_id` 属于典型的**冗余设计**，并且存在严重的**安全隐患**。

## 为什么一定要从 Token 中获取？

### 1. 越权风险（Horizontal Privilege Escalation）

如果把 `teacher_id` 放在 Request Body 中：

- 假设教师 A 的 ID 为 `1`，但他可以通过 Postman 或修改前端请求，把 Body 中的 `teacher_id` 改成 `2`（教师 B）。
- 如果后端没有额外校验 `Token 中的用户 ID == Body 中的 teacher_id`，教师 A 就能以教师 B 的名义创建课程。
- 直接使用 Token 注入的当前用户 ID，从根本上杜绝了这类越权伪造。

### 2. 提升前端体验（DX）与数据一致性

- 前端不需要在每次发请求前手动拼接当前用户的 ID，减少了前端的逻辑负担。
- Token 是已通过认证的唯一信任源（Single Source of Truth），直接取用性能更好且不易出错。

### 3. 减少冗余数据库查询

在你现有的逻辑里，`CourseService` 专门去查了一次 `user_crud.get_by_id` 来确认教师是否存在。而通常 `get_teacher` 依赖（Dependency）在解析 Token 时就已经校验过教师身份并查出用户对象了，这里重复查询是没有必要的。

## 最佳实践代码重构

以下是针对你的代码做出的修改建议：

### 1. 修改 Schema (`app/schemas/course_schema.py`)

将 `teacher_id` 从 `CourseCreate` 中移除：

Python

```
from pydantic import BaseModel
from datetime import datetime

class CourseCreate(BaseModel):
    # 不需要 teacher_id，后端会自动补全
    name: str
    course_code: str | None = None
    overview: str = "暂无简介"


class CourseResponse(BaseModel):
    id: int
    name: str
    course_code: str | None
    teacher_id: int
    overview: str
    created_at: datetime
```

### 2. 修改 Service (`app/services/course_service.py`)

把 `teacher_id` 作为独立参数传入：

Python

```
from sqlmodel.ext.asyncio.session import AsyncSession
from fastapi import Depends
from app.db.session import get_session
from app.models.table import Course
from app.schemas.course_schema import CourseCreate
from app.crud.course_crud import course_crud

class CourseService:
    def __init__(
            self,
            db: AsyncSession = Depends(get_session)
    ):
        self.db = db

    async def register_course(self, course_in: CourseCreate, teacher_id: int) -> Course:
        # 将结构体转为 dict，并主动补充 teacher_id 属性
        course_data = course_in.model_dump(exclude_unset=True)
        course_data["teacher_id"] = teacher_id
        
        new_course = await course_crud.create_course(self.db, course_data)
        await self.db.commit()
        return new_course
```

### 3. 完成 Router (`app/api/course_router.py`)

在路由处理函数中通过依赖注入获取 `current_teacher`，提取其 ID 传给 Service：

Python

```
from fastapi import APIRouter, Depends, status
from app.core.dependencies import get_teacher
from app.schemas.course_schema import CourseCreate, CourseResponse
from app.services.course_service import CourseService
from app.models.table import User  # 假设你的 User / Teacher 模型路径

router = APIRouter()

@router.post("/register", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def register_course(
    course_in: CourseCreate,
    current_teacher: User = Depends(get_teacher),  # 依赖项自动完成Token鉴权并返回当前教师对象
    service: CourseService = Depends()
):
    return await service.register_course(course_in, teacher_id=current_teacher.id)
```

## 唯一的例外场景

只有一种场景你需要在 Body 里传递 `teacher_id`：

- **管理员功能（Admin System）**：如果是系统管理员为某个教师**指派**或**代建**课程，由于创建人和课程所属教师不是同一个人，此时接口应该接受 `teacher_id`。
- **建议做法**：为管理员单独设计一个接口（如 `POST /admin/courses`）以及独立的 `AdminCourseCreate` 模型，不要和普通教师的自建课程接口混用。

### 问：感觉非常有道理。我是缺少什么开发上的经验常识或思维僵化导致我的疑惑呢

这绝对**不是因为你思维僵化**，而是每一个后端开发者在从“**写 CRUD / 学基础语法**”跨越到“**设计生产级 API & 安全架构**”时，必然会经历的一个认知阶段。

你会产生这种疑惑，非常自然，根源在于你之前踩在“数据库视角”看问题。把这种疑惑解开后，你会建立起以下 4 个非常核心的后端工程思维：

## 1. 数据库模型思维 vs. API 契约思维

- **你原有的直觉（数据库视角）**：

  > “数据库 `courses` 表里有 `teacher_id` 这个非空列，所以我定义 Pydantic 的 `CourseCreate` 时，也应该把 `teacher_id` 声明为必需字段，这样才能对应上。”

- **进阶的后端思维（API 契约视角）**：

  > **数据表 Schema $\neq$ API 请求 Schema。**
  >
  > API 的 Request Body 表达的是**“客户端需要主动提交的业务数据”**，而不是数据库表结构的直接映射。后端完全可以在 API 接收到纯粹的业务参数后，在 Service 层自动拼装补全系统上下文（如当前操作人 ID、IP 地址、租户 ID 等），再写入数据库。

## 2. 忽略了 HTTP 请求中的“隐式上下文”

在设计 Restful API 时，请求参数通常分为两类：

1. **显式数据（Payload）**：用户在表单里填写的文本、上传的文件（如课程名、简介）。
2. **上下文数据（Context）**：请求发起者的身份、权限、客户端信息（如当前 User ID、IP、Device ID）。

- **思维误区**：把“上下文数据”当成了“显式数据”，要求客户端在 Body 里重复声明。
- **架构原则**：**身份由 Auth Token 守护，上下文从 Token/Session 中提取。** 凡是能从加密/鉴权成功的 Token 中安全提取的信息，绝不让前端在 Body 或 Query 中显式传第二次。这既减少了冗余，也保证了单一步骤信任源（Single Source of Truth）。

## 3. 零信任与安全边界意识（Zero Trust Input）

在后端开发中有一个铁律：**“永远不要信任前端传来的任何数据（Never Trust Client Input）”**。

- **如果从 Body 取 `teacher_id`**：你把“确定课程归属者”的控制权交给了**客户端 HTTP 请求体**，这在安全领域属于把信任边界推到了不可控的端侧。
- **如果从 Token 取 `teacher_id`**： Token 是由你的后端私钥签名的（如 JWT），或者在服务端 Session 中存储的，客户端无法伪造。你将信任边界锁在了**服务端鉴权中间件**内。

## 4. CRUD 过程思维 vs. 领域业务意图思维

- **面向 CRUD 编程**：我的接口目标是“往 `courses` 表插一条记录”，所以我需要提供插入该表所需的所有字段。
- **面向业务领域编程**：我的接口目标是“**当前登录的教师**发布了一门新课程”。既然“当前登录的教师”是动作的主体，那么“谁发布的”就是动作自带的属性，不需要再被当作输入参数。

## 💡 总结：未来设计接口时的决策小卡片

以后在设计 API 字段时，可以问自己这个简单的判定流程：

```
这个字段是客户端用户填写的业务属性吗？
  ├── 是（如课程名称、价格） ──> 放入 Request Body (Schema)
  └── 否
       ├── 是关于“操作者是谁/什么权限”？ ──> 从 Auth Token / 鉴权依赖中提取
       ├── 是系统生成的时序/状态？ ──> 由数据库（默认值）或 Backend Service 生成
       └── 是路由层级的关联资源？ ──> 考虑作为 URL Path 参数（如 /teachers/{teacher_id}/courses）
```

掌握了这个思维切换后，你在设计如“发帖子（`author_id`）”、“下订单（`user_id`）”、“发表评论（`commenter_id`）”等绝大多数业务接口时，都会自然而然地选择从 Token 提取用户身份。

### 问：如何在 FastAPI 中优雅地设计多角色（如学生/教师/管理员）的权限控制依赖项（Dependencies）？

在 FastAPI 中，设计多角色（RBAC）权限控制最优雅、最 FastAPI Native 的方式是利用 **可调用类（Callable Class）** 或 **依赖项工厂（Dependency Factory）**。

下面为你展示一套高度复用、易于扩展且类型安全的架构方案。

## 核心设计模式：可调用类依赖项（`RoleChecker`）

利用 Python 的 `__call__` 特性，我们可以创建一个可以接收“允许角色列表”的类，同时这个类本身又能作为 FastAPI 的 `Depends` 依赖项。

### 1. 定义角色 Enum (`app/core/roles.py`)

使用 `Enum` 避免在代码里写死字符串，保证 IDE 的自动补全与类型安全：

Python

```
from enum import Enum

class UserRole(str, Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"
```

### 2. 实现通用角色校验器 (`app/core/dependencies.py`)

Python

```
from typing import List
from fastapi import Depends, HTTPException, status
from app.core.roles import UserRole
from app.models.user import User  # 假设你的 User ORM / Pydantic 模型
from app.core.security import get_current_active_user  # 基础 Token 解析依赖

class RoleChecker:
    """
    通用角色权限检查依赖项
    """
    def __init__(self, allowed_roles: List[UserRole]):
        self.allowed_roles = allowed_roles

    async def __call__(self, current_user: User = Depends(get_current_active_user)) -> User:
        # 1. 管理员通常拥有所有权限（超管逻辑）
        if current_user.role == UserRole.ADMIN:
            return current_user

        # 2. 检查当前用户角色是否在允许列表中
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="权限不足，无法执行此操作"
            )
        
        return current_user
```

### 3. (可选) 封装快捷别名，进一步简化 Router

为了避免在 Router 里重复写 `Depends(RoleChecker([...]))`，你可以预定义常用的权限依赖：

Python

```
# app/core/dependencies.py 底部

# 仅教师和管理员可访问
require_teacher = RoleChecker([UserRole.TEACHER])

# 仅学生可访问
require_student = RoleChecker([UserRole.STUDENT])

# 仅管理员可访问
require_admin = RoleChecker([UserRole.ADMIN])
```

### 4. 在 Router 中优雅注入

有了上面的设计，在路由层使用起来极其简洁且语义清晰：

Python

```
from fastapi import APIRouter, Depends, status
from app.schemas.course_schema import CourseCreate, CourseResponse
from app.services.course_service import CourseService
from app.models.user import User
from app.core.roles import UserRole
from app.core.dependencies import RoleChecker, require_teacher

router = APIRouter()

# 方式 A：使用预定义的快捷依赖项
@router.post("/register", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def register_course(
    course_in: CourseCreate,
    current_teacher: User = Depends(require_teacher), # 校验教师/管理员身份并注入用户对象
    service: CourseService = Depends()
):
    return await service.register_course(course_in, teacher_id=current_teacher.id)


# 方式 B：临时组合多个特定角色（灵活应变）
@router.get("/audit-logs")
async def get_audit_logs(
    current_user: User = Depends(RoleChecker([UserRole.TEACHER, UserRole.ADMIN]))
):
    return {"message": "只有教师和管理员能看到审计日志"}
```

## 为什么这种设计很“优雅”？

1. **组合优于继承**：遵循 FastAPI 的依赖注入哲学，`RoleChecker` 自动套在了 `get_current_active_user` 之上，形成依赖链（Dependency Chain）。
2. **零重复代码**：Token 解析、用户提取、角色校验彻底解耦，每个层级只做一件事。
3. **开箱即用的文档支持**：FastAPI 会自动把这些依赖项映射到 Swagger UI / OpenAPI 文档中，对于未授权/无权限的请求能自动生成正确的 HTTP 状态码说明（`401` 或 `403`）。

## 进阶：角色权限 vs 资源所有权（ABAC）

需要注意的是，上面的 RBAC 解决了“**是不是教师**”的问题。但在真实业务中，你很快会遇到 **资源所有权（ABAC）** 的需求：

- **RBAC 检查**：“教师可以修改课程吗？” $\rightarrow$ 可以（只要是 Teacher 角色）。
- **ABAC 检查**：“教师 A 可以修改**教师 B 创建的课程**吗？” $\rightarrow$ **不可以**！

对于资源级别的控制，建议放置在 **Service 层**（或定义专门的资源校验依赖项），例如在修改/删除课程时：

Python

```
# Service 层校验资源归属
async def update_course(self, course_id: int, current_user: User, update_data: dict):
    course = await course_crud.get_by_id(self.db, course_id)
    
    # 非管理员 且 不是课程本人，拒绝修改
    if current_user.role != UserRole.ADMIN and course.teacher_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="无权修改非本人创建的课程"
        )
    # 执行更新...
```

### 问：在我测试API功能是重复发送同一份数据，因为数据中某个属性有唯一约束，故在数据库插入时发生异常。但我不知道企业级规范开发中会如何处理 

你在测试中遇到的两个报错非常典型：

1. **服务端报错**：SQLAlchemy 抛出 `IntegrityError`（数据库 `course_code` 唯一索引冲突，触发 MySQL 1062 报错）。由于后端没有捕获这个异常，FastAPI 直接抛出了未处理的 **500 Internal Server Error**。
2. **客户端报错**：客户端接收到了 500 错误页面（通常是空响应或 HTML），直接调用 `response.json()` 解析失败，抛出 `JSONDecodeError`。

在企业级规范开发中，核心原则是：**“永远不能让数据库的原始异常直接抛给前端，也绝不能让接口返回未格式化的 500 响应。”**

为了应对这种唯一性冲突（Duplicate Entry），生产环境通常采用“三道防线”的架构设计：

## 🛡️ 企业级三道防线设计

### 第一道防线：业务层前置校验（主动检查）

在执行 `INSERT` 之前，先去数据库查询该 `course_code` 是否已经存在。如果存在，直接抛出业务异常或 `HTTP 400/409`。

- **优点**：逻辑清晰，可以给前端精准的错误提示（例如：“课程代码 '1' 已存在，请更换”）。

Python

```
# app/services/course_service.py
from fastapi import HTTPException, status

class CourseService:
    async def register_course(self, course_in: CourseCreate, teacher_id: int) -> Course:
        # 1. 前置校验：检查 course_code 是否重复
        if course_in.course_code:
            existing = await course_crud.get_by_code(self.db, course_in.course_code)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"课程代码 '{course_in.course_code}' 已存在"
                )

        course_data = course_in.model_dump(exclude_unset=True)
        course_data["teacher_id"] = teacher_id
        
        new_course = await course_crud.create_course(self.db, course_data)
        await self.db.commit()
        return new_course
```

### 第二道防线：DAO/CRUD 层异常捕获（防御高并发击穿）

在极高并发场景下，可能存在两个请求同时通过了“前置校验”，然后同时写入，此时依然会触发数据库唯一索引报错。因此，在 CRUD/Service 执行 `flush()` 或 `commit()` 时，需要针对 `IntegrityError` 做兜底捕获。

Python

```
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException, status

async def create_course(self, db: AsyncSession, course_data_dict: dict) -> Course:
    try:
        new_course = Course(**course_data_dict)
        db.add(new_course)
        await db.flush()
        return new_course
    except IntegrityError as e:
        await db.rollback()  # 必须回滚事务，防止 Connection 污染
        
        # 识别是否是唯一性约束冲突
        if "Duplicate entry" in str(e.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="课程编号或其他唯一标识已存在，请勿重复创建"
            )
        # 其他完整性约束错误（如外键校验失败）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="数据完整性校验失败"
        )
```

### 第三道防线：FastAPI 全局异常捕获（系统级安全网）

即使前两道防线漏掉了某个未预料到的数据库异常，也不能让 FastAPI 崩掉抛出 500 文本或 HTML。企业级项目会在全局注册 SQLAlchemy 异常处理器，将其统一转化为规范的 JSON 响应。

在应用入口文件（如 `main.py`）注册全局 handler：

Python

```
# app/main.py
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
import logging

logger = logging.getLogger(__name__)
app = FastAPI()

@app.exception_handler(IntegrityError)
async def sqlalchemy_integrity_error_handler(request: Request, exc: IntegrityError):
    # 记录详细日志供后端排查
    logger.error(f"数据库完整性约束冲突: {str(exc.orig)}")
    
    # 向客户端掩盖敏感数据库细节，返回结构化的 JSON
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "code": 40901,
            "message": "数据资源冲突，请检查唯一字段（如编号/名称）是否重复",
            "detail": None
        }
    )
```

## 客户端测试脚本规范

在编写自动化测试（如 `httpx` / `requests`）时，**绝不能直接 `response.json()`**，而是需要先判断 HTTP 状态码：

Python

```
# tests/test_course.py
response = await client.post("/v1/courses/register", json=payload)

if response.status_code == 201:
    print("创建成功：", response.json())
else:
    print(f"请求失败，状态码：{response.status_code}")
    print("错误详情：", response.text)  # 输出文本，避免 500 时解析 JSON 崩溃
```

## 📊 标准规范对比表

| **处理方式**                                 | **状态码** | **客户端响应格式**                     | **是否生产安全** | **评估**                     |
| -------------------------------------------- | ---------- | -------------------------------------- | ---------------- | ---------------------------- |
| **未处理（你当前的状态）**                   | `500`      | HTML / 空文本 (引发 `JSONDecodeError`) | ❌ 极危险         | 泄露堆栈信息，引发客户端崩溃 |
| **仅前置查询校验**                           | `409`      | JSON: `{"detail": "课程代码已存在"}`   | ⚠️ 基本安全       | 无法抵御并发击穿             |
| **前置校验 + 兜底 + 全局 Exception Handler** | `409`      | 统一 JSON 错误格式                     | ✅ 企业级规范     | 高可用、安全、对前端友好     |

### 问：嗯，我的疑惑主要在第二道防线，是在service中捕获相关异常好还是在crud层捕获合适呢

在企业级架构中，**最推荐在 Service 层（或由 Service 管理事务的地方）捕获并处理异常**，而不是在 CRUD 层。

这是一个非常深刻且关键的分层设计问题。下面为你拆解为什么 **Service 层是最佳位置**，以及 CRUD 层如果捕获会带来什么问题。

## 核心原因拆解

### 1. 职责单一原则（Layer Separation）

- **CRUD 层（数据访问层）**：它的职责非常纯粹——**只做 SQL / ORM 操作**。它应该对上层的业务逻辑和上游的 Web 框架（FastAPI）**完全无感知**。
  - 如果你在 CRUD 层捕获了 `IntegrityError` 并抛出 FastAPI 的 `HTTPException(status_code=409)`，这就意味着你的 **CRUD 层与 HTTP 协议和 FastAPI 框架强绑定了**。
  - 假设以后你要写一个 CLI 命令行工具、定时任务（Celery/APScheduler）或者脚本来调用 CRUD 批量导入课程，CRUD 抛出的 `HTTPException` 在非 HTTP 环境下就会非常尴尬。

### 2. 只有 Service 层拥有“业务上下文（Business Context）”

数据库抛出的 `IntegrityError` 只是一个冰冷的底层报错（例如 `Duplicate entry '1' for key 'ix_course_course_code'`）。

- **CRUD 层**：只知道 `Course` 写入失败了，但它不知道这在业务上意味着什么。
- **Service 层**：知道当下的业务动作是“教师正在注册新课程”，它能将底层的数据库错误**翻译**成人类可读的业务语言，如：“`课程编号 '1' 已被占用，请更换`”。

### 3. 事务控制权（Unit of Work / Transaction Scope）

在规范的开发中，**事务的开启、提交（`commit`）和回滚（`rollback`）应该由 Service 层统一管理**。

- 一个 Service 方法可能包含多个 CRUD 操作（例如：1. 创建课程 -> 2. 创建默认章节 -> 3. 扣减教师额度）。
- 如果错误在 CRUD 层被吞掉或单独回滚，会导致整体事务状态混乱。只有 Service 层能决定“一旦发生唯一性冲突，整体回滚并中断流程”。

## 最佳代码实践（推荐方案）

### 1. CRUD 层：保持简洁纯粹

CRUD 层只负责将对象添加到 Session 中或执行查询，**不处理 HTTP 异常**，让异常自然向上抛出（或者捕获后抛出自定义的领域异常）：

Python

```
# app/crud/course_crud.py
class CourseCrud:
    async def create_course(self, db: AsyncSession, course_data_dict: dict) -> Course:
        new_course = Course(**course_data_dict)
        db.add(new_course)
        # 注意：这里甚至不需要写 flush/commit，交给 Service 统一控制
        return new_course

course_crud = CourseCrud()
```

### 2. Service 层：控制事务与异常翻译（核心）

Service 层负责 `commit()`、`rollback()` 以及将数据库异常转化为业务/HTTP 异常：

Python

```
# app/services/course_service.py
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException, status

class CourseService:
    async def register_course(self, course_in: CourseCreate, teacher_id: int) -> Course:
        course_data = course_in.model_dump(exclude_unset=True)
        course_data["teacher_id"] = teacher_id
        
        try:
            # 1. 调用 CRUD 构造数据对象
            new_course = await course_crud.create_course(self.db, course_data)
            
            # 2. 在 Service 层统一提交事务（此时才会真正发 SQL 给 MySQL 校验约束）
            await self.db.commit()
            await self.db.refresh(new_course)
            return new_course

        except IntegrityError as e:
            # 3. 触发异常时，必须明确回滚事务
            await self.db.rollback()
            
            # 4. 翻译底层错误为业务 HTTP 异常
            error_msg = str(e.orig)
            if "ix_course_course_code" in error_msg:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"课程代码 '{course_in.course_code}' 已存在，请使用其他代码"
                )
            
            # 其他未知的完整性约束错误
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="创建课程失败，数据不符合约束条件"
            )
```

## 进阶：大厂/复杂项目的“解耦”写法（Custom Exceptions）

如果你希望 Service 层也不要直接 `import HTTPException`（实现更高级别的架构解耦，如 Clean Architecture / 领域驱动设计），可以采用 **“自定义业务异常 + 全局映射”** 的方式：

1. **Service 层**：抛出自己定义的 Python 异常。

   Python

   ```
   # 抛出自定义业务异常，不依赖 FastAPI
   raise CourseCodeAlreadyExistsException(code=course_in.course_code)
   ```

2. **FastAPI 全局 Handler**：捕获该自定义异常并转为 HTTP 409。

   Python

   ```
   @app.exception_handler(CourseCodeAlreadyExistsException)
   async def course_code_exist_handler(request: Request, exc: CourseCodeAlreadyExistsException):
       return JSONResponse(
           status_code=409,
           content={"message": f"课程代码 {exc.code} 已存在"}
       )
   ```

## 总结决策建议

- **小型/中型项目（FastAPI 常用）**：在 **Service 层** 捕获 `IntegrityError`，做 `db.rollback()`，然后抛出 `HTTPException`。
- **大型/干净架构项目**：在 **Service 层** 捕获 `IntegrityError`，做 `db.rollback()`，抛出**自定义业务异常**，由 **全局 Exception Handler** 转为 HTTP 响应。
- **无论什么项目**：**不要在 CRUD 层做 HTTP 级的异常拦截。**

### 问：如何在 FastAPI + SQLModel/SQLAlchemy 中优雅地设计异步事务与 Unit of Work（工作单元）模式？

在复杂业务场景下（如：**“创建课程 + 更新教师发课数量 + 扣除资源配额”**），如果手动在每个 Service 方法里写 `try...except...commit()...rollback()`，不仅会有大量重复代码，还极易出现**部分数据库操作成功、部分失败导致数据不一致**的 Bugs。

**Unit of Work（工作单元，简称 UoW）模式** 的核心目的，就是**管理数据库 Session 的生命周期与事务边界**。它保证了在一个业务操作中，所有 Repository/CRUD 共享**同一个 AsyncSession**，并且做到：

1. **无异常退出 `async with` 块时，自动 `commit`**。
2. **发生任何异常时，自动 `rollback`**。
3. **彻底将 `commit/rollback` 等底层数据库控制代码从 Service 业务层中抽离**。

## 🏛️ 完整架构图解

Plaintext

```
[ FastAPI Router ]
        │
        ▼ (注入 UoW 依赖)
[ CourseService ]
        │
        ├─► async with uow:
        │     ├─► uow.users.get_by_id()      ─┐
        │     ├─► uow.courses.create()        ├─► 共享同一个 AsyncSession
        │     └─► uow.logs.create()           ─┘
        │
        └─► (自动 commit 或 rollback 并释放 Session)
```

## 🛠️ 分步落地代码实现

### 1. 数据库 Session 工厂设置 (`app/db/session.py`)

首先定义一个 `async_session_maker` 工厂，用于给 UoW 动态创建独立的 Session：

Python

```
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

DATABASE_URL = "mysql+aiomysql://root:password@localhost:3306/mydb"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)

# 创建异步 Session 工厂
async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 提交后不让属性过期，方便返回给前端
    autoflush=False
)
```

### 2. 重构 Repository/CRUD 层 (`app/repositories/`)

把 CRUD 类改造为接收 `AsyncSession` 实例。这样它们就可以复用 UoW 提供的同一个 Session。

Python

```
# app/repositories/course_repository.py
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.table import Course

class CourseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_code(self, course_code: str) -> Course | None:
        statement = select(Course).where(Course.course_code == course_code)
        result = await self.session.exec(statement)
        return result.first()

    async def create(self, course_data: dict) -> Course:
        new_course = Course(**course_data)
        self.session.add(new_course)
        # 注意：这里不需要 flush 或 commit，只管往 Session 里面加
        return new_course
```

### 3. 实现 Async Unit of Work (`app/core/uow.py`)

利用 Python 的异步上下文管理器（`__aenter__` 和 `__aexit__`）来实现 UoW：

Python

```
from abc import ABC, abstractmethod
from sqlmodel.ext.asyncio.session import AsyncSession
from app.db.session import async_session_maker
from app.repositories.course_repository import CourseRepository
from app.repositories.user_repository import UserRepository  # 假设有 UserRepository

class AbstractUnitOfWork(ABC):
    courses: CourseRepository
    users: UserRepository

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()

    @abstractmethod
    async def commit(self):
        pass

    @abstractmethod
    async def rollback(self):
        pass


class SqlModelUnitOfWork(AbstractUnitOfWork):
    """
    基于 SQLModel/SQLAlchemy 的工作单元具体实现
    """
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def __aenter__(self):
        # 1. 开启独立的 AsyncSession
        self.session: AsyncSession = self.session_factory()
        
        # 2. 实例化 Repositories，把同一个 session 传递给它们
        self.courses = CourseRepository(self.session)
        self.users = UserRepository(self.session)
        
        return await super().__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            # 执行父类的 commit/rollback 判定逻辑
            await super().__aexit__(exc_type, exc_val, exc_tb)
        finally:
            # 3. 无论成功还是抛出异常，最后务必关闭 Session
            await self.session.close()

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()
```

### 4. 纯净的 Service 层业务逻辑 (`app/services/course_service.py`)

有了 UoW，Service 层的代码将变得极其优雅干净！**你不再需要写任何 `db.commit()`、`db.rollback()`，甚至连 `try...except` 都可以交给 UoW 和全局异常捕获。**

Python

```
from fastapi import HTTPException, status
from app.core.uow import AbstractUnitOfWork
from app.schemas.course_schema import CourseCreate
from app.models.table import Course

class CourseService:
    def __init__(self, uow: AbstractUnitOfWork):
        self.uow = uow

    async def register_course(self, course_in: CourseCreate, teacher_id: int) -> Course:
        # 使用 async with 开启事务上下文
        async with self.uow:
            # 1. 检查教师是否存在（使用 uow 下的 users repository）
            teacher = await self.uow.users.get_by_id(teacher_id)
            if not teacher:
                # 抛出异常会触发 __aexit__ 中的 rollback，并关闭 session
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, 
                    detail="教师账号不存在"
                )

            # 2. 检查课程编码是否冲突
            if course_in.course_code:
                existing = await self.uow.courses.get_by_code(course_in.course_code)
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"课程编码 '{course_in.course_code}' 已存在"
                    )

            # 3. 组装并创建课程
            course_data = course_in.model_dump(exclude_unset=True)
            course_data["teacher_id"] = teacher_id
            new_course = await self.uow.courses.create(course_data)
            
            # 4. (可选) 修改教师数据或其他关联表数据
            teacher.course_count += 1  # 跨表更新，全都在同一个事务里！

            return new_course
            # 退出 async with 时：
            # - 如果没有抛出异常：UoW 自动执行 await self.commit() 提交全局事务
            # - 如果抛出 HTTPException 或数据库异常：UoW 自动执行 await self.rollback()，原样抛出异常
```

### 5. 在 FastAPI Router 中进行依赖注入

定义一个创建 UoW 的依赖函数，并在 Service/Router 中注入：

Python

```
# app/api/dependencies.py
from app.core.uow import SqlModelUnitOfWork, AbstractUnitOfWork
from app.services.course_service import CourseService

def get_uow() -> AbstractUnitOfWork:
    return SqlModelUnitOfWork()

def get_course_service(uow: AbstractUnitOfWork = Depends(get_uow)) -> CourseService:
    return CourseService(uow)
```

Python

```
# app/api/v1/endpoints/course.py
from fastapi import APIRouter, Depends, status
from app.schemas.course_schema import CourseCreate, CourseResponse
from app.services.course_service import CourseService
from app.core.dependencies import require_teacher
from app.models.user import User

router = APIRouter()

@router.post("/register", response_model=CourseResponse, status_code=status.HTTP_201_CREATED)
async def register_course(
    course_in: CourseCreate,
    current_teacher: User = Depends(require_teacher),
    course_service: CourseService = Depends(get_course_service)
):
    return await course_service.register_course(course_in, teacher_id=current_teacher.id)
```

## 🌟 这种设计带给你的 4 大好处

1. **绝对原子性（ACID 保障）**：多表操作（如创建课程同时更新教师统计信息）天然运行在同一个事务中，要么全成功，要么全回滚。
2. **自动化事务清理**：即使在 `async with` 块内部发生 `HTTPException(409)` 或未捕获的 Python 异常，UoW 的 `__aexit__` 保证 `rollback()` 和 `session.close()` **100% 被执行**，彻底避免连接池泄漏。
3. **极佳的可测试性（单元测试 / Mock）**：在为 `CourseService` 编写单元测试时，你只需要写一个内存版的 `FakeUnitOfWork` 即可，完全脱离真实的 MySQL 数据库。
4. **符合 DDD（领域驱动设计）架构**：Service 层完全表达业务意图，再也不受 SQLAlchemy 的 `commit/rollback` 等持久化细节所困扰。