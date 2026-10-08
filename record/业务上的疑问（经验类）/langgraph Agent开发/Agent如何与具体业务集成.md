```python
from typing import Annotated, Optional

from sqlmodel import Field, SQLModel
from datetime import datetime, timezone
from sqlalchemy import UniqueConstraint, Column, Enum, BigInteger, DateTime
import enum



# 枚举类型
class UserRole(int, enum.Enum):
    STUDENT = 0
    TEACHER = 1
    ADMIN = 2

def utc_now() -> datetime:
    """生成带有时区信息的当前UTC时间"""
    return datetime.now(timezone.utc)

AwareCreatedAt = Annotated[
    datetime,
    Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        description="创建时间（UTC）"
    )
]
AwareUpdatedAt = Annotated[
    datetime,
    Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        # 核心区别：声明当记录发生 UPDATE 且未显式提供该字段值时，自动调用 utc_now 刷新
        sa_column_kwargs={"onupdate": utc_now},
        description="更新时间 (UTC)",
    ),
]
AwareNullableDateTime = Annotated[
    Optional[datetime],
    Field(
        default=None,
        sa_type=DateTime(timezone=True),
        description="可选时间戳（UTC）"
    )
]

# 用户表
class User(SQLModel, table=True):
    __tablename__ = "user"
    id: int | None = Field(default= None, primary_key= True)
    role: UserRole = Field(default= UserRole.STUDENT, sa_column= Column(Enum(UserRole)))
    username: str = Field(index= True, unique= True)
    password_hash: str
    real_name: str
    email: str | None = Field(default= None)
    avatar: str | None = Field(default= None)
    created_at: AwareCreatedAt

# 课程表
class Course(SQLModel, table= True):
    __tablename__ = "course"
    id: int | None = Field(default= None, primary_key= True)
    name: str
    course_code: str | None = Field(default=None, unique=True, index=True,description="课程代码")
    teacher_id: int = Field(foreign_key="user.id",description="创建这门课程的教师id")
    overview: str = Field(default="暂无简介", description="课程简介")
    created_at: AwareCreatedAt


# 选课表
class Enrollment(SQLModel, table= True):
    __tablename__ = "enrollment"
    # 在 Python 中，带有括号但没有逗号的单元素会被解析为该元素本身，而不是元组（Tuple）。
    # 注意要加,（逗号），否则在Python中会被认成单个元素而非元组
    # 由于student_id写在前面，因此只给它加了索引
    __table_args__ = (UniqueConstraint("student_id", "course_id", name= "uq_student_course"),)
    id: int | None = Field(default=None, primary_key=True)
    student_id: int = Field(foreign_key="user.id", description="选修该门课程的学生")
    # 为了优化相关查询的速度（如统计选课学生人数），索引
    course_id: int = Field(foreign_key="course.id", index=True, description="该门课程的id")
    score: float | None = Field(default= None, description="学生该门课程的分数")
    created_at: AwareCreatedAt

# 课程资源表（一位老师可以上传多个课程资源，一个课程资源只对应一位老师）
# （一门课程有多个课程资源，一个课程资源只对应一门课程）
#  (一个课程资源对应一个文件)
class CourseResource(SQLModel, table= True):
    __tablename__ = "course_resource"
    id: int | None = Field(default=None, primary_key=True)
    course_id: int = Field(foreign_key="course.id",description="该资源对应的课程id")
    file_record_id: int = Field(foreign_key="file_record.id",description="该资源对应的文件id")
    uploaded_by: int = Field(foreign_key="user.id",description="上传该资源的老师id")
    file_name: str = Field(description="该文件的原始文件名")
    title: str
    created_at: AwareCreatedAt

# 作业表(一门课程有多个作业，一个作业只对应一门课程)
class Assignment(SQLModel, table= True):
    __tablename__ = "assignment"
    id: int | None = Field(default= None, primary_key= True)
    course_id: int = Field(foreign_key= "course.id",description="该作业对应的课程")
    title: str
    description: str
    # 截止时间通常由外部传入，不设默认生成，但必须带时区
    deadline: datetime = Field(
        sa_type=DateTime(timezone=True), description="该作业的截止日期 (UTC)"
    )
    created_at: AwareCreatedAt

# 作业附件表
# 一个作业可以有多个附件，一个附件只对应一个作业
# 一个附件对应一个文件
class AssignmentFile(SQLModel, table= True):
    __tablename__ = "assignment_file"
    id: int | None = Field(default= None, primary_key= True)
    assignment_id: int = Field(foreign_key="assignment.id", description="附件所属的作业id")
    file_record_id: int = Field(foreign_key="file_record.id", description="附件对应的文件id")
    file_name: str = Field(description="该文件的原始文件名")
    uploaded_by: int = Field(foreign_key="user.id", description="该文件的上传者")
    created_at: AwareCreatedAt

# 作业提交记录表
# （一个学生可以有多个提交记录，一个提交记录只对应一个学生）
# （一个作业可以有多个提交记录，一个提交记录只对应一门作业）
# （一个提交对应多个文件，一个文件对应一个提交）
class Submission(SQLModel, table= True):
    __tablename__ = "submission"
    id: int | None = Field(default= None, primary_key= True)
    student_id: int = Field(foreign_key= "user.id", description="提交的学生id")
    assignment_id: int = Field(foreign_key= "assignment.id", description="本次提交对应的作业id")
    score: float | None = Field(default= None,description="本次作业的分数")
    submitted_at: datetime = Field(
        default_factory=utc_now,
        sa_type=DateTime(timezone=True),
        description="作业提交时间 (UTC)",
    )
    feedback: str | None = Field(default= None, description="批语")
    attempt: int = Field(default= 1, description="表示第几次提交")





# 作业提交对应文件表
class SubmissionFile(SQLModel, table= True):
    __tablename__ = "submission_file"
    id: int | None = Field(default=None, primary_key=True)
    submission_id: int = Field(foreign_key="submission.id", description="本次提交的id")
    file_record_id: int = Field(foreign_key="file_record.id", description="本次提交对应的文件之一的id")
    file_name: str = Field(description="该文件的原始文件名")
    uploaded_by: int = Field(foreign_key="user.id", description="该文件的上传者")
    created_at: AwareCreatedAt

# 文件记录表
class FileRecord(SQLModel, table= True):
    __tablename__ = "file_record"
    id: int | None = Field(default= None, primary_key= True)
    file_ext: str | None = Field(default= None, description="该文件的类型")
    mime_type: str | None = Field(default= None,description="该文件的MIME类型")
    storage_type: str = Field(description="该文件的存储方式：本地 / minio")
    storage_key: str = Field(description="该文件的真实路径")
    total_size: int = Field(sa_type=BigInteger ,description="该文件的大小，单位字节")
    file_hash: str = Field(description="该文件的hash值", unique=True)
    created_at: AwareCreatedAt

```

### 问：我在做一个迷你学习通系统，现在在引入Agent智能问答，我现在想的是能否把系统内部的API引入给Agent作为工具调用以提供更加智能化的服务，你觉得呢？这是当前的数据库表，你觉得可以怎么充分开发平台和Agent的能力呢？

把内部 API 封装为 Tool（Function Calling）给 Agent 调用，是学习通系统从“泛化聊天机器人”转变为“具身智能助教与学习管家”的最佳路径。



现有表结构设计规整（包含完备的外键约束、角色枚举、附件和提交多对多关联），完全可以围绕**学生端学习助手**与**教师端教学助手**落地高价值场景。



**核心场景与工具映射 (Tools Mapping)**



| **角色** | **Agent 落地能力**         | **核心 Tool API 逻辑**                                       | **依赖数据表**                                     |
| -------- | -------------------------- | ------------------------------------------------------------ | -------------------------------------------------- |
| **学生** | **DDL 催办与学业进度查询** | `get_pending_assignments()`: 过滤当前选课中截止时间大于当前时间且无提交记录的作业 | `Course`, `Enrollment`, `Assignment`, `Submission` |
| **学生** | **课程资料精准 RAG 问答**  | `search_course_knowledge(course_id, query)`: 结合文件解析与向量检索定位 PPT/课件内容 | `CourseResource`, `FileRecord`                     |
| **学生** | **成绩与反馈复盘**         | `get_submission_feedback(assignment_id)`: 调取历史批改记录，帮学生总结错因并生成强化练习 | `Submission`, `Assignment`                         |
| **教师** | **智能出题与作业发布**     | `create_assignment(course_id, title, desc, deadline)`: 根据教学目标或大纲自动生成作业草稿 | `Assignment`, `Course`                             |
| **教师** | **学情分析与未交催交**     | `get_assignment_submission_stats(assignment_id)`: 汇总交作业比例，一键圈出未提交学生名单 | `Enrollment`, `Submission`, `User`                 |
| **教师** | **预批改与反馈草拟**       | `generate_pre_grading(submission_id)`: 读取学生提交文件生成评分建议和批语草稿 | `SubmissionFile`, `FileRecord`, `Submission`       |

**关键系统架构与避坑要点**



- **身份上下文安全注入（防水平越权 IDOR）：**

  绝对不能把 `user_id` 作为 Tool 的入参交由 LLM 决定。Agent 调用内部 API 时，必须走请求上下文（如 FastAPI 的 `Depends(get_current_user)`），从鉴权 Token 中强制提取当前调用者的 `id` 和 `role`，防止学生通过自然语言诱骗 Agent 查询他人成绩或越权批改。

- **读写分离与 Human-in-the-Loop（人机协同）：**

  - **只读工具（查询成绩、查 DDL、课件检索）：** 允许 Agent 自动执行并返回结果。
  - **写入工具（布置作业、提交作业、写入成绩）：** Agent 必须先向用户输出确认卡片（如“即将为《计算机网络》发布作业‘TCP三次握手’，截止时间为下周一，确认提交吗？”），待用户点击或明确回复后再执行真实数据库写入。

- **非结构化文件解析管道（RAG 增强）：**

  目前的 `FileRecord` 仅保存了元数据。为了让 Agent 具备“根据老师上传的 PPT/PDF 回答学生问题”的能力，需在文件上传异步任务中增加一条 ETL 链路：

  `FileRecord (本地/MinIO) -> 文本抽取 (Unstructured/PyMuPDF) -> Chunking -> Vector DB`。Agent 通过向量相似度检索课件文本块，再生成带课件出处的精准回答。

### 问：我感觉工具之间也应该有定义，一些是全局工具，一些是有限制的工具，比如RAG我就限制在检索当前对应的课程知识库的范围内，而一些工具我觉得是可以在任意课程知识库使用的

这个认知非常精准。在复杂业务系统中，如果一股脑把几十个工具全部塞给大模型，不仅会消耗大量 Token、增加响应延迟，还会造成模型调用错误（Tool Misrouting）甚至越权问题。



将工具划分**全局作用域**与**上下文限制作用域**，本质上是 Agent 的**上下文隔离与动态挂载机制**。



**工具作用域的三层划分**



| **作用域类型**                 | **适用场景**                 | **典型工具示例**                                             | **上下文获取方式**                         |
| ------------------------------ | ---------------------------- | ------------------------------------------------------------ | ------------------------------------------ |
| **全局工具 (Global)**          | 跨课程、个人维度的学业统筹   | `get_my_courses()`  `get_cross_course_ddl()`  `search_public_faq()` | 仅依赖当前用户的 `student_id`              |
| **课程作用域 (Course Scoped)** | 特定课程内部的资料检索与事务 | `search_course_kb(query)`  `get_course_assignments()`  `ask_course_teacher()` | 必须绑定明确的 `course_id`                 |
| **角色专属 (Role Scoped)**     | 具有管理或写权限的高危操作   | `publish_assignment()`  `export_grade_stats()`               | 必须校验 `UserRole.TEACHER` 且是课程创建者 |

**实现限制工具的两种核心范式**



**1. 动态挂载（根据前端页面/路由过滤 Tool 集合）**

大模型不需要在任何时刻都能看到所有工具。用户身处不同前端页面时，后端的 Agent 运行时只下发对应的工具子集：



- **在系统首页/个人中心：** 只注册全局工具（查日程、查成绩单、所有待办）。
- **进入《计算机网络》课程主页：** 动态卸载无关工具，注册该课程绑定的 RAG 工具和本课作业工具。
- **教师进入管理后台：** 额外注册发布、批阅类工具。

**2. 参数隐式注入（参数柯里化 / Context Injected Tools）**

对于 RAG 这类特定课程工具，**不要把 `course_id` 交给大模型去猜或填**，而应在系统运行时通过闭包或中间件强制隐式绑定。



Python

```
from langchain_core.tools import tool

def create_course_kb_tool(course_id: int):
    """
    通过工厂函数为特定课程生成专属的 RAG Tool。
    LLM 只能看到 query 参数，无法篡改 course_id。
    """
    @tool
    def search_course_knowledge_base(query: str) -> str:
        """检索当前课程的课件、讲义与教材知识库内容。"""
        # 内部强制限定 course_id 检索向量库（如 Milvus / Chroma）的特定 Partition 或 Metadata
        chunks = vector_store.similarity_search(
            query=query,
            k=3,
            filter={"course_id": course_id}  # 强隔离：物理/逻辑过滤
        )
        return "\n\n".join([c.page_content for c in chunks])
    
    return search_course_knowledge_base
```

**设计时的关键准则**



- **原则：环境做决定，模型做填空：** 能由 Session/URL 确定的参数（如 `course_id`、`student_id`），坚决不要暴露给 LLM 作为 Tool 参数，否则用户输入“帮我搜下隔壁班《高等数学》的内容”时，LLM 可能会私自调换 `course_id` 造成越界访问。
- **跨课程模糊查询的降级策略：** 如果学生在全局首页提问“三次握手的流程是什么？”，全局 Agent 发现没有直接绑定的 RAG 工具时，可以设计一个两阶段链路：
  1. 调用全局工具 `search_my_enrolled_courses()` 找到与“三次握手”相关的课程 ID；
  2. 由 Agent 路由到该课程知识库，或先反问学生：“您是在询问《计算机网络》这门课的内容吗？”

### 问：而且根据用户的身份如管理员、教师、学生，它们能够使用的工具以及权限也是不同的，但是我感觉这个的实现是非常复杂的

它看似复杂，是因为我们容易陷入一个误区：**试图让 Agent 在推理时自己判断权限**（比如让模型判断“我是不是老师，我能不能调这个工具”）。



权限控制坚决不能交给模型做决定。最优雅、可靠的做法是：**“按需装配”原则**——把权限拦截放在后端，在把工具清单交给大模型之前，就完成过滤。大模型根本不需要知道其他角色的工具存在，它拿到的就是一个“为当前用户量身定制的只读/只用工具包”。



这样一来，权限控制的代码量实际上只有几十行。



**三层权限过滤模型**



整个过滤流程只需在一个工厂函数中串联三个维度：



Plaintext

```
[ 用户请求 (UserRole, CourseID) ]
               │
               ▼
   1. 角色白名单过滤 (Role Check) ──> 剔除未授权工具 (如学生剔除出题工具)
               │
               ▼
   2. 作用域过滤 (Scope Check)   ──> 剔除无上下文工具 (如不在课程页剔除本课RAG)
               │
               ▼
   3. 运行时参数注入 (Context Bind) ──> 闭包固化 user_id / course_id
               │
               ▼
       [ 干净安全的 Tool 列表 ] ──> 送给 LLM 运行
```

**轻量级实现方案：工具元数据注册表**



不需要引入复杂的权限框架，直接通过元数据标记工具的适用角色和作用域：



Python

```
from typing import Callable, List
from pydantic import BaseModel
from langchain_core.tools import BaseTool

class ToolMeta(BaseModel):
    tool_instance: BaseTool
    allowed_roles: set[UserRole]
    scope: str  # "global" 或 "course"

# 1. 注册中心：声明每个工具的权限准入
TOOL_REGISTRY: list[ToolMeta] = [
    # 全局学生工具
    ToolMeta(
        tool_instance=get_my_pending_ddls,
        allowed_roles={UserRole.STUDENT},
        scope="global"
    ),
    # 课程内只读工具（师生均可）
    ToolMeta(
        tool_instance=search_course_kb,
        allowed_roles={UserRole.STUDENT, UserRole.TEACHER, UserRole.ADMIN},
        scope="course"
    ),
    # 教师专属课程管理工具
    ToolMeta(
        tool_instance=publish_assignment_draft,
        allowed_roles={UserRole.TEACHER, UserRole.ADMIN},
        scope="course"
    ),
    # 管理员系统级工具
    ToolMeta(
        tool_instance=audit_system_logs,
        allowed_roles={UserRole.ADMIN},
        scope="global"
    ),
]

# 2. 解析器：一句话完成动态过滤与装配
def resolve_tools_for_user(user: User, current_course_id: int | None = None) -> list[BaseTool]:
    available_tools = []
    
    for item in TOOL_REGISTRY:
        # 1. 角色不匹配直接丢弃
        if user.role not in item.allowed_roles:
            continue
        
        # 2. 如果是课程专属工具，但当前没有传入 course_id，跳过
        if item.scope == "course" and not current_course_id:
            continue
            
        # 3. 校验通过，加入当前会话的工具箱
        available_tools.append(item.tool_instance)
        
    return available_tools
```

**角色与权限矩阵示例**



| **角色**           | **全局工具 (首页/个人中心)**                  | **课程工具 (进入某门课后)**                                  |
| ------------------ | --------------------------------------------- | ------------------------------------------------------------ |
| **学生 (STUDENT)** | • 查全课表与待办 DDL  • 查个人总学分/成绩汇总 | • 检索本课课件与教案 (RAG)  • 查看本课作业与提交记录  • 获取本课成绩反馈分析 |
| **教师 (TEACHER)** | • 查待批改作业总览  • 查看授课日程            | • 检索本课所有教案与参考书  • 智能起草作业与发布  • 学情统计与未交名单催办  • 作业批量预批改建议 |
| **管理员 (ADMIN)** | • 全校课程检索与管理  • 教师/学生账户状态查询 | • 拥有所有课程的所有工具权限 (SuperUser)                     |

**数据层保底（Defense in Depth）**



即便前端或解析器意外把工具发给了非授权用户，工具内部的**数据库查询**依然要做最后的安全拦截：



- **只查关联数据：** 查询作业时，SQL 语句必须自带 `where student_id = current_user.id`。

- **教师权限验证：** 执行 `publish_assignment` 时，第一行代码必须验证：

  Python

  ```
  course = session.get(Course, course_id)
  if course.teacher_id != current_user.id and current_user.role != UserRole.ADMIN:
      raise PermissionError("您不是该课程的授课教师，无权发布作业")
  ```

按照这种方式设计，业务系统与 Agent 完全解耦：权限逻辑依然是熟悉的后端 API 校验那一套，Agent 只负责在被允许的沙盒内专注做好意图识别与参数填充。



### 问：也就是说，先实现所有的工具，然后再根据用户身份动态绑定对应的工具，我是用langgraph定义的Agent，这是不是意味着我需要在langgraph的图节点中动态绑定工具了？还是说有更好的实现方式

在 LangGraph 中，你**完全不需要**为了不同的权限去动态创建或重新编译整个 Graph。



LangGraph 官方最推荐的最佳实践是：**“图结构是静态的，但在运行时（Runtime）通过 `RunnableConfig` 动态绑定工具与上下文”**。



这既保留了 LangGraph 单例编译、状态持久化（Checkpointer）的高性能优势，又能完美实现角色与课程的隔离。



### 两种架构选型对比

| **方案**                                      | **实现方式**                                                 | **适用场景**                                                 | **优缺点评估**                                         |
| --------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------ |
| **方案 A：单图 + 运行时动态绑定（强烈推荐）** | 图结构固定（`agent` ↔ `tools`），在 Agent 节点执行时读取请求上下文，动态 `model.bind_tools()` | 当前中小型平台，师生核心工作流类似（对话 -> 调工具 -> 答复） | **极高灵活性**，开发维护成本最低，无需重复构建多张图。 |
| **方案 B：按角色拆分独立子图 (Multi-Agent)**  | 学生图、教师图各自独立，网关层或 Supervisor 路由分发         | 师生后续流程严重分叉（如教师端需要多步骤审批流、自动批改流水线） | 业务隔离彻底，但当前阶段会有很多重复节点，架构过重。   |

### 最佳实践落地：单图 + `RunnableConfig` 动态装配

在 LangGraph 中，任何节点和工具都可以接收一个隐式参数 `config: RunnableConfig`。你可以把当前用户的 `user_id`、`role`、`course_id` 放在 `config["configurable"]` 中传入。



#### 1. 定义工具：利用 LangChain 隐式注入上下文

在编写工具时，只要把 `config: RunnableConfig` 声明在参数里，**LangChain 会自动将其从传给 LLM 的 Schema 中剔除**（大模型完全看不到这个参数，杜绝伪造），而在运行时自动注入真实请求上下文：



Python

```
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

@tool
def get_my_pending_assignments(config: RunnableConfig) -> str:
    """查询学生当前有哪些未交的作业和截止时间。"""
    # 从隐式上下文安全读取，不需要大模型传入
    ctx = config["configurable"]
    student_id = ctx["user_id"]
    course_id = ctx.get("course_id")
    
    # 执行具体的 SQL 查询 ...
    return f"学生 {student_id} 在课程 {course_id} 下待完成作业为：..."

@tool
def search_course_kb(query: str, config: RunnableConfig) -> str:
    """在当前课程的课件知识库中进行语义检索。"""
    ctx = config["configurable"]
    course_id = ctx.get("course_id")
    if not course_id:
        return "错误：当前不在任何课程页面，无法检索课程知识库。"
    
    # 限制检索范围 filter={"course_id": course_id}
    return f"从课程 {course_id} 检索到的内容：..."
```

#### 2. 编写权限过滤分发器

根据当前上下文中的身份，动态返回允许的工具列表：



Python

```
def get_authorized_tools(role: int, course_id: int | None) -> list:
    allowed = []
    # 所有人通用的基础工具（如果存在）
    # ...
    
    # 学生专属
    if role == UserRole.STUDENT:
        allowed.append(get_my_pending_assignments)
        
    # 课程上下文存在时，挂载 RAG 工具
    if course_id is not None:
        allowed.append(search_course_kb)
        
    # 教师专属（如布置作业）
    if role == UserRole.TEACHER:
        # allowed.append(publish_assignment_draft)
        pass
        
    return allowed
```

#### 3. 在 LangGraph 节点中动态绑定与拦截

Python

```
from langgraph.graph import StateGraph, MessagesState, START, END
from langchain_core.messages import ToolMessage

# 1. 核心 Agent 节点：动态绑定当前权限的 Tools
def call_model(state: MessagesState, config: RunnableConfig):
    ctx = config.get("configurable", {})
    role = ctx.get("role")
    course_id = ctx.get("course_id")
    
    # 核心：根据当前请求动态计算允许的工具集合
    tools = get_authorized_tools(role, course_id)
    
    # 将过滤后的工具列表绑定到模型
    model_with_tools = base_model.bind_tools(tools)
    response = model_with_tools.invoke(state["messages"])
    return {"messages": [response]}

# 2. 安全的 Tool 执行节点（防 Prompt 注入与越权）
def call_tools(state: MessagesState, config: RunnableConfig):
    ctx = config.get("configurable", {})
    tools_map = {t.name: t for t in get_authorized_tools(ctx.get("role"), ctx.get("course_id"))}
    
    last_message = state["messages"][-1]
    tool_messages = []
    
    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        
        # 兜底校验：如果模型被诱导调用了未授权的工具，直接拦截
        if tool_name not in tools_map:
            tool_messages.append(
                ToolMessage(
                    content=f"权限拒绝：您无权调用工具 '{tool_name}'。",
                    tool_call_id=tool_call["id"]
                )
            )
            continue
            
        # 正常执行，把 config 自动向下透传
        tool = tools_map[tool_name]
        result = tool.invoke(tool_call["args"], config=config)
        tool_messages.append(
            ToolMessage(content=str(result), tool_call_id=tool_call["id"])
        )
        
    return {"messages": tool_messages}

# 3. 路由判断
def should_continue(state: MessagesState):
    messages = state["messages"]
    if messages[-1].tool_calls:
        return "tools"
    return END

# 4. 构建并编译图（只需在程序启动时编译一次）
workflow = StateGraph(MessagesState)
workflow.add_node("agent", call_model)
workflow.add_node("tools", call_tools)

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, ["tools", END])
workflow.add_edge("tools", "agent")

app = workflow.compile()
```

#### 4. 在 FastAPI 接口中触发

在 Web 接口层，直接通过 `config` 传入当前登录用户的 Token 解析结果：



Python

```
@router.post("/chat")
async def chat_endpoint(
    request: ChatRequest,
    current_user: User = Depends(get_current_user), # 鉴权依赖
):
    # 组装安全运行时上下文
    config = {
        "configurable": {
            "user_id": current_user.id,
            "role": current_user.role,
            "course_id": request.course_id,  # 前端传入的当前课程上下文，可为 None
            "thread_id": f"user_{current_user.id}_session", # 支持 LangGraph 对话记忆
        }
    }
    
    # 启动图运行
    result = await app.ainvoke(
        {"messages": [("user", request.message)]},
        config=config
    )
    return {"reply": result["messages"][-1].content}
```

### 这种模式的核心收益

1. **零冷启动延迟：** 图在后端启动时编译成单例，请求进来时只做内存中的轻量工具过滤，并发性能高。
2. **彻底杜绝水平越权：** 大模型不知道 `user_id`，它只能通过 `RunnableConfig` 静默读取登录凭证，即使攻击者尝试注入（如输入“*帮我查学生张三的作业*”），工具底层查的仍然是当前登录用户的 ID。
3. **状态和记忆天然隔离：** 利用 `config["configurable"]["thread_id"]`，可以无缝挂载 LangGraph 的 `MemorySaver`，实现同一用户在不同课程、不同角色的记忆隔离。

### 问：确实，这样就很好很灵活，而且和具体业务逻辑解耦。那感觉难做的其实是get_authorized_tools和工具权限、范围定义的实现

如果用一大堆 `if-else` 去堆砌 `get_authorized_tools`，每加一个工具都要去改分发函数，系统很快就会变成代码泥潭。



解决这个问题的关键在于两点：



1. **分清两道防线**：工具曝光权（粗粒度，模型看得见谁）与数据访问权（细粒度，能不能查这条数据）。
2. **声明式元数据（Declarative Metadata）**：像写 FastAPI 路由一样，用装饰器把权限和范围直接“贴”在工具函数上。

### 第一道坎：不要把数据级权限塞进工具分发

这是最容易踩的坑——在 `get_authorized_tools` 里去查数据库判断“这个学生有没有选这门课”。



- **曝光权（粗粒度，由 `get_authorized_tools` 负责）**：纯内存匹配。仅根据用户的 `role` 和是否处于 `course_id` 上下文，决定把哪些工具的 Schema 暴露给 LLM。**毫秒级，无数据库 IO。**
- **访问权（细粒度，由 Tool 内部或依赖拦截）**：如果学生不在课程里，即使模型误调用了 `search_course_kb`，Tool 内部查 DB 发现未选课，直接抛出 `PermissionError` 或返回“您未加入该课程”。

### 优雅方案：自定义装饰器注册机制

只需实现一个统一的 `@register_tool` 装饰器，给每个工具挂载元数据，增加新工具时完全不需要修改任何中央逻辑。



Python

```
from enum import Enum
from typing import Callable, Set, Optional
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel

# 1. 作用域枚举
class Scope(str, Enum):
    GLOBAL = "global"    # 全局可用（首页、个人中心）
    COURSE = "course"    # 必须在具体课程上下文内才可用

# 2. 工具元数据容器与全局注册表
class ToolMetadata(BaseModel):
    tool: BaseTool
    roles: Set[UserRole]
    scope: Scope

TOOL_REGISTRY: list[ToolMetadata] = []

# 3. 声明式装饰器：一键定义工具 + 绑定权限
def register_tool(roles: Set[UserRole], scope: Scope = Scope.GLOBAL):
    """
    兼具 @tool 功能并自动注册权限元数据
    """
    def decorator(func: Callable):
        # 先用 LangChain 原生的 @tool 进行包装
        tool_instance = tool(func)
        # 记录元数据并入库
        TOOL_REGISTRY.append(ToolMetadata(
            tool=tool_instance,
            roles=roles,
            scope=scope
        ))
        return tool_instance
    return decorator
```

### 工具定义示例：业务代码与权限共存

定义工具时，权限与作用域一目了然：



Python

```
from langchain_core.runnables import RunnableConfig

# 场景 A：全局学生工具
@register_tool(roles={UserRole.STUDENT}, scope=Scope.GLOBAL)
def get_my_pending_assignments(config: RunnableConfig) -> str:
    """查询当前学生在所有课程中的未完成作业。"""
    student_id = config["configurable"]["user_id"]
    return f"学生 {student_id} 的全部待办 DDL..."

# 场景 B：课程内只读工具（师生、管理员均可用）
@register_tool(
    roles={UserRole.STUDENT, UserRole.TEACHER, UserRole.ADMIN}, 
    scope=Scope.COURSE
)
def search_course_kb(query: str, config: RunnableConfig) -> str:
    """在当前课程资料库中检索相关知识点。"""
    course_id = config["configurable"]["course_id"]
    return f"已在课程 {course_id} 检索到内容..."

# 场景 C：教师专属的课程管理工具
@register_tool(roles={UserRole.TEACHER, UserRole.ADMIN}, scope=Scope.COURSE)
def create_assignment_draft(title: str, description: str, config: RunnableConfig) -> str:
    """为当前课程起草一份作业。"""
    course_id = config["configurable"]["course_id"]
    return f"已为课程 {course_id} 生成作业草稿：{title}"
```

### 极简的 `get_authorized_tools` 实现

有了上面的元数据，分发函数就缩减成了一行纯内存的推导式：



Python

```
def get_authorized_tools(role: UserRole, course_id: Optional[int] = None) -> list[BaseTool]:
    """
    根据角色和上下文过滤合法工具列表，无 DB 查询消耗。
    """
    return [
        item.tool for item in TOOL_REGISTRY
        if role in item.roles and (
            item.scope == Scope.GLOBAL or (item.scope == Scope.COURSE and course_id is not None)
        )
    ]
```

### 架构收益

- **零耦合扩容**：未来增加第 50 个工具时，只需要新建一个 Python 文件，加上 `@register_tool`，系统自动识别，主图和分发逻辑完全不需要动。
- **权限一处看清**：任何人看工具代码的第一行，就能立刻知道“谁在什么场景下可以用”，杜绝了权限配置分散在配置表或长篇 `if-else` 里的情况。
- **天然防御模型幻觉**：如果前端页面没传 `course_id`（如在个人主页），任何 `Scope.COURSE` 的工具直接从 LLM 的提示词中抹去，模型连“尝试调用”的机会都没有。

### 问：就是不知道这和RBAC有没有关系，还是说没必要用RBAC实现

这不仅和 RBAC 有关，你刚才设计的本质就是一个**代码级的轻量 RBAC（基于角色的访问控制）+ ABAC（基于属性的访问控制）的混合体**。



但在当前阶段，**完全没必要引入复杂的企业级、数据库动态 RBAC**（即建一堆 `role`、`permission`、`user_role`、`role_permission` 关联表并在后台配置权限）。



**概念对齐：它和 RBAC、ABAC 的真实映射**



你在数据库里定义的 `UserRole (STUDENT, TEACHER, ADMIN)`，就是最标准的 RBAC 雏形：



| **机制**          | **解决的问题**                                               | **在你系统中的对应体现**                                     |
| ----------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **标准 RBAC**     | **“你是谁，你能干什么动作？”**  根据角色决定是否有操作权限。 | `roles={UserRole.TEACHER}`：决定了教师角色才能调用“布置作业”这一工具能力。 |
| **ABAC / 上下文** | **“在什么条件下，你能对哪个具体资源干这件事？”**  根据环境属性、资源归属做二次裁决。 | `scope=COURSE` + `course_id`：教师可以布置作业，但前提是必须处于该课程页面，且该课程的 `teacher_id == current_user.id`。 |

**为什么没必要做重型的数据库 RBAC？**



在传统中后台系统里，重型 RBAC 的核心价值是“让管理员在界面上动态给不同岗位勾选权限菜单”。但在高校教学场景和 Agent 工具调用中，这种复杂度是完全过剩的：



- **业务角色高度固化**：学校系统的角色几十年不变，就是学生、教师、管理员（最多后期加一个助教 TA），不会像企业 SaaS 那样频繁增删自定义角色。
- **工具绑定在代码层**：每个 Tool 背后都是一段具体的 Python 代码和提示词，不可能由非技术管理员在后台点点鼠标就凭空“配置”出一个新工具。
- **避免性能损耗**：如果每轮对话、每个请求都要做 4 张权限表的连表查询（JOIN）来计算工具列表，会平白增加几十毫秒的数据库 I/O 延迟。

**推荐的折中演进：代码级“权限标识（Permission）”解耦**



如果直接把 `UserRole` 绑死在 `@register_tool` 上，唯一的痛点在于**未来增加角色时的扩展性**（比如新增一个“助教 TA”角色：他既能查作业、又能改作业，但不能创建课程）。



为了保持代码优雅又不过度设计，只需在 Python 代码层引入一套**字符串权限码**，把“角色”和“工具”通过“权限”解耦，依然不需要建任何数据库表：



Python

```
from enum import Enum

# 1. 业务权限枚举（仅定义在代码中，不入库）
class Permission(str, Enum):
    COURSE_KB_READ = "course_kb:read"
    ASSIGNMENT_READ = "assignment:read"
    ASSIGNMENT_WRITE = "assignment:write"
    STUDENT_STATS_READ = "student_stats:read"

# 2. 角色到权限的静态映射字典（配置极其直观）
ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.STUDENT: {
        Permission.COURSE_KB_READ,
        Permission.ASSIGNMENT_READ,
    },
    UserRole.TEACHER: {
        Permission.COURSE_KB_READ,
        Permission.ASSIGNMENT_READ,
        Permission.ASSIGNMENT_WRITE,
        Permission.STUDENT_STATS_READ,
    },
    UserRole.ADMIN: set(Permission),  # 管理员拥有全量权限
}
```

改造后的工具装饰器直接面向**权限**声明，而不是面向角色：



Python

```
# 声明工具只需具备某种权限，不再关心具体是老师还是助教
@register_tool(permission=Permission.ASSIGNMENT_WRITE, scope=Scope.COURSE)
def create_assignment_draft(title: str, description: str, config: RunnableConfig):
    ...

# 过滤函数依然是一行纯内存计算
def get_authorized_tools(role: UserRole, course_id: int | None = None):
    user_perms = ROLE_PERMISSIONS.get(role, set())
    return [
        item.tool for item in TOOL_REGISTRY
        if item.permission in user_perms and (
            item.scope == Scope.GLOBAL or (item.scope == Scope.COURSE and course_id is not None)
        )
    ]
```

**架构落地的核心边界**



- **RBAC 负责“视野裁剪”**：决定 Agent 能不能看到这个工具。用 Python 代码中的 `ROLE_PERMISSIONS` 搞定，坚决不建数据库权限表。
- **ABAC 负责“数据保底”**：决定工具能不能动这条数据。在 Tool 内部读取 `config` 校验 `course.teacher_id == user_id` 或 `student_id == user_id`，防止越权。

这样既保留了 RBAC 清晰的权限隔离思想，又省去了数据库权限引擎的繁琐架构。



### 问：请给出一个结合 LangGraph interrupt/人机交互确认的方案，展示教师调用作业发布工具时，如何暂停等待前端用户确认后再真正写入数据库。

在 LangGraph 中，通过 **`langgraph.types.interrupt`** 和 **`Command(resume=...)`** 可以直接在 Tool 内部挂起执行流。



配合 Checkpointer（检查点持久化），Graph 会在执行到数据库写入前安全冻结，并将待确认内容返回前端；用户确认后，Graph 沿着冻结现场无缝恢复并落库。



### 1. 包含 `interrupt` 的安全作业发布工具

在工具内部，先做业务属性校验，再调用 `interrupt()` 挂起。此时工具不会继续向下执行，直到外部通过 `Command(resume=...)` 唤醒：



Python

```
from datetime import datetime
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from sqlmodel import Session, select

from models import Assignment, Course, UserRole  # 引用之前定义的模型
from database import engine

@tool
def publish_assignment(
    title: str,
    description: str,
    deadline_iso: str,
    config: RunnableConfig,
) -> str:
    """为当前课程正式发布一份作业（需要教师在界面上确认后才会落库）。"""
    ctx = config.get("configurable", {})
    user_id = ctx.get("user_id")
    course_id = ctx.get("course_id")

    # 1. ABAC 权限保底拦截：确认当前操作者是该课程的负责教师
    with Session(engine) as session:
        course = session.get(Course, course_id)
        if not course:
            return f"错误：未找到 ID 为 {course_id} 的课程。"
        if course.teacher_id != user_id and ctx.get("role") != UserRole.ADMIN:
            return "权限拒绝：您不是该课程的授课教师，无法发布作业。"

    # 2. 触发 LangGraph 中断：挂起当前执行流，将预览载荷抛给外部环境
    user_decision = interrupt({
        "action": "confirm_assignment_publish",
        "course_id": course_id,
        "course_name": course.name,
        "title": title,
        "description": description,
        "deadline": deadline_iso,
    })

    # 3. 恢复执行后的分支处理（user_decision 由外部 resume 传入）
    if not user_decision.get("approved", False):
        reason = user_decision.get("reason", "教师取消了发布")
        return f"作业发布已取消，原因：{reason}。"

    # 4. 确认通过，执行真正的数据库写入
    parsed_deadline = datetime.fromisoformat(deadline_iso)
    new_assignment = Assignment(
        course_id=course_id,
        title=title,
        description=description,
        deadline=parsed_deadline,
    )
    with Session(engine) as session:
        session.add(new_assignment)
        session.commit()
        session.refresh(new_assignment)

    return f"作业《{title}》已成功发布到课程《{course.name}》，截止日期：{parsed_deadline.strftime('%Y-%m-%d %H:%M')}。"
```

### 2. 带有持久化检查点的 LangGraph 构建

`interrupt` 的生效**必须配置 Checkpointer**（生产环境用 `PostgresSaver`，开发测试用 `MemorySaver`），否则执行流丢失无法恢复：



Python

```
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI

tools = [publish_assignment]  # 假设已通过角色权限过滤
model = ChatOpenAI(model="gpt-4o", temperature=0).bind_tools(tools)

def call_model(state: MessagesState):
    return {"messages": [model.invoke(state["messages"])]}

def should_continue(state: MessagesState):
    if state["messages"][-1].tool_calls:
        return "tools"
    return END

# 构建图
workflow = StateGraph(MessagesState)
workflow.add_node("agent", call_model)
workflow.add_node("tools", ToolNode(tools))

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", should_continue, ["tools", END])
workflow.add_edge("tools", "agent")

# 必须挂载 checkpointer，使 thread_id 具备状态恢复能力
checkpointer = MemorySaver()
app = workflow.compile(checkpointer=checkpointer)
```

### 3. FastAPI 交互接口设计

前端通过同一个 `thread_id` 完成“发起对话 -> 收到挂起卡片 -> 确认提交”的闭环。

Python

```
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from langgraph.types import Command

router = APIRouter(prefix="/agent")

class ChatRequest(BaseModel):
    message: str
    course_id: int | None = None
    thread_id: str  # 会话标识，用于多轮上下文与挂起恢复

class ResumeRequest(BaseModel):
    thread_id: str
    approved: bool
    reason: str | None = None

@router.post("/chat")
async def chat(req: ChatRequest, current_user: User = Depends(get_current_user)):
    config = {
        "configurable": {
            "thread_id": req.thread_id,
            "user_id": current_user.id,
            "role": current_user.role,
            "course_id": req.course_id,
        }
    }

    # 执行图
    app.invoke({"messages": [("user", req.message)]}, config=config)

    # 检查图是否在执行过程中被 interrupt 拦截
    snapshot = app.get_state(config)
    if snapshot.tasks and any(t.interrupts for t in snapshot.tasks):
        # 提取 interrupt 抛出的数据载荷
        interrupt_data = snapshot.tasks[0].interrupts[0].value
        return {
            "status": "pending_confirmation",
            "confirmation_data": interrupt_data,
            "message": "大模型已生成作业草稿，请在右侧卡片确认是否发布。"
        }

    # 未被拦截，正常返回模型最后一句回复
    return {
        "status": "completed",
        "reply": snapshot.values["messages"][-1].content
    }

@router.post("/resume")
async def resume_interrupted_task(req: ResumeRequest, current_user: User = Depends(get_current_user)):
    config = {"configurable": {"thread_id": req.thread_id}}

    snapshot = app.get_state(config)
    if not snapshot.tasks or not any(t.interrupts for t in snapshot.tasks):
        raise HTTPException(status_code=400, detail="该会话没有待确认的挂起任务。")

    # 通过 Command 注入用户的决定（对应 tool 内 interrupt 的返回值）
    resume_payload = {"approved": req.approved, "reason": req.reason}
    app.invoke(Command(resume=resume_payload), config=config)

    # 取出恢复执行后的最终回复
    final_snapshot = app.get_state(config)
    return {
        "status": "completed",
        "reply": final_snapshot.values["messages"][-1].content
    }
```

### 4. 前后交互时序

| **阶段**        | **前端行为**                                                 | **后端与 Agent 状态**                                        | **数据库状态**               |
| --------------- | ------------------------------------------------------------ | ------------------------------------------------------------ | ---------------------------- |
| **1. 意图触发** | 教师输入：“帮我布置一个关于TCP三次握手的作业，下周五截止”    | Agent 解析参数并调用 `publish_assignment`                    | 无变动                       |
| **2. 执行挂起** | 收到 `pending_confirmation` 响应，界面弹出作业详情预览弹窗（含确认/驳回按钮） | `interrupt()` 触发，Graph 保存当前内存快照至 Checkpointer，退出进程 | 无变动                       |
| **3. 人工决策** | 教师在界面核验标题、截止时间，点击【确认发布】并发起 `/resume` | 后端加载 `thread_id` 快照，`interrupt()` 返回 `{approved: True}`，继续向下执行 | **正式写入 `Assignment` 表** |
| **4. 完成反馈** | 前端对话流追加：“作业《TCP三次握手》已成功发布...”           | Tool 产出 ToolMessage，Agent 生成最终肯定回复，Graph 流程走向 `END` | 提交完成                     |