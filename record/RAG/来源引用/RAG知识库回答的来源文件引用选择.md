### 问：做RAG知识库问答，用户可能每轮对话中都想看到当前问答中检索到的文本块及其来源文件，那么有个问题是，这个来源文件到底是原始文档还是该原始文档对应的markdown解析文件呢？毕竟原始文档解析得到的markdown解析文件的文本才是真正被分块并向量化和存入向量数据库的

在用户侧呈现的来源文件**必须是原始文档**（如 PDF、Word、Excel 等），而绝对不应该是中间解析出的 Markdown 文件。



Markdown 只是 RAG 数据处理管道（ETL）中的**中间产物（Intermediate Artifact）**。对最终用户而言，系统暴露中间文件不仅破坏产品体验，还会增加认知负担。



### 为什么必须指向原始文档？

- **符合用户认知模型**：用户清楚自己上传了《2024员工考勤管理办法.pdf》或《Q3财务汇报.docx》，但并不知道也不关心系统内部生成了什么 `doc_id_clean.md`。
- **保证权威性与核验能力**：用户查看来源的核心目的是**溯源与验真**。只有打开原始格式文件（查看原版排版、图表、印章签名），溯源才有法律和业务价值。
- **交互体验连贯性**：现代知识库的标准体验是“点击引用角标 $\rightarrow$ 弹出原文档预览 $\rightarrow$ 自动高亮跳转至对应页码/段落”。如果跳转到一个丢失排版样式的纯文本 Markdown，体验会大打折扣。

### 系统层级与元数据映射设计

虽然向量数据库里存的文本确实来自 Markdown 分块，但可以通过在分块时注入**元数据（Metadata）**，将分块与原始文档精准绑定：



| **维度**     | **用户看到的（展示层）** | **向量库实际存储的（数据层）**                 | **内部系统保留的（ETL层）**    |
| ------------ | ------------------------ | ---------------------------------------------- | ------------------------------ |
| **文件名**   | `2024员工手册.pdf`       | `file_name: "2024员工手册.pdf"`                | `raw_path: "s3://.../doc.pdf"` |
| **定位信息** | 第 14 页 / 第 3 节       | `page_number: 14`, `headers: ["福利", "年假"]` | `chunk_index: 82`              |
| **文本内容** | 高亮的原文档区域         | `text: "年假应提前5个工作日申请..."`           | `markdown_ast_node_id: 104`    |
| **操作行为** | 下载原件 / 预览 PDF 高亮 | 参与向量余弦相似度计算与召回                   | 用于断点重跑与解析调试         |

### 如何实现从 Markdown 分块反查原文档？

要在分块来源于 Markdown 的前提下实现精准原文档溯源，业内通常采用以下三种工程方案：



**1. 页面级切分与标记（Page Marker）**



- 解析 PDF/Word 为 Markdown 时，解析器（如 Marker、MinerU、PyMuPDF）在输出文本中插入分页标记（例如 `<!-- Page: 12 -->`）。
- 在按 Chunk 切分时，提取前后的页码标记并写入 Chunk 的 `metadata.page_number`。
- 前端展示时，直接调用 PDF 预览组件跳转至 `page_number`。

**2. 标题层级路径（Breadcrumbs / Header Tree）**



- 将 Markdown 的 `#`、`##` 标题解析为面包屑路径（例如 `薪酬管理 > 绩效奖金 > 发放时间`）。
- 将该路径存入 Chunk 的 `metadata.heading_hierarchy`。
- 前端不仅展示来源为《薪酬制度.docx》，还能清晰提示来源路径位于哪个章节。

**3. 坐标级绑定（Bounding Box - 最精准方案）**



- 高级文档解析引擎（如 LayoutLM、Document AI、OCR Pipeline）在输出 Markdown 或 JSON 时，会保留每个文本块在 PDF 页面中的二维坐标 `bbox: [x0, y0, x1, y1]`。
- 将坐标随 Chunk 存入向量库，前端渲染 PDF 时可以直接绘制黄色高亮框圈出检索到的文本。

向量库里切片（Chunk）的内容是 Markdown 文本，但它的身份证（Metadata）必须写明原始文档的信息。中间 Markdown 仅用于向量检索计算和开发者日志调试，对业务用户保持透明。

### 问：向量数据库中存储什么属性使得能够定位到原始文件以及解析文件呢

为了在 RAG 系统中实现从检索到的 Chunk 同时**精准定位到原始文件**（供用户溯源、页面高亮）以及**中间解析文件**（供系统调试、滑动窗口拼接、重新切分），向量数据库（如 Milvus、Qdrant、PGVector 等）的元数据（`Metadata` / `Payload`）通常会划分为 **4 个层级** 的属性设计。



### 一、 核心元数据字段设计（JSON 示例）

在向量库中，每一条向量记录对应一个切片，其元数据结构通常如下：



JSON

```
{
  "id": "chunk_01J8F9X...",
  "vector": [0.024, -0.015, 0.089, ...],
  "payload": {
    "text": "年假应提前5个工作日向直属主管提交申请...",
    
    // 1. 原始文件层（面向用户界面与业务）
    "doc_id": "doc_9527",
    "file_name": "2024年员工福利与休假制度.pdf",
    "file_type": "pdf",
    "raw_file_url": "s3://knowledge-base/raw/doc_9527.pdf",
    "kb_id": "kb_hr_01",
    
    // 2. 中间解析文件层（面向数据管线与调试）
    "parsed_file_id": "parsed_9527_v1",
    "parsed_file_url": "s3://knowledge-base/parsed/doc_9527.md",
    "parsed_layout_url": "s3://knowledge-base/parsed/doc_9527_layout.json",
    "parser_name": "mineru_v1.2",
    "md_char_start": 4210,
    "md_char_end": 4580,
    
    // 3. 文档内精确定位层（面向原文档高亮与锚点）
    "page_numbers": [12],
    "bboxes": [
      {
        "page": 12,
        "coords": [72.5, 230.0, 520.0, 310.5] // [x0, y0, x1, y1] 坐标
      }
    ],
    "heading_hierarchy": ["第一章 考勤制度", "第二节 年假规定"],
    
    // 4. 切片拓扑层（面向上下文增强与邻近检索）
    "chunk_index": 35,
    "prev_chunk_id": "chunk_01J8F9W...",
    "next_chunk_id": "chunk_01J8F9Y...",
    "parent_chunk_id": "parent_01J8F9..."
  }
}
```

### 二、 属性分层详解

#### 1. 原始文件定位属性（User-Facing / Storage）

用于让前端直接展示文档名、下载原件或调用在线预览器（如 PDF.js / OnlyOffice）。



- **`doc_id`**：关系型数据库（MySQL/PostgreSQL）中的主文档 ID，用于权限校验与元数据关联。
- **`file_name`**：原始文件名（包含后缀，如 `.pdf`, `.docx`），直接展示给用户。
- **`raw_file_url` / `raw_storage_key`**：原始文件在对象存储（S3/MinIO/OSS）中的路径，用于生成有时效的下载/预览预签名链接。
- **`file_type`**：文件格式（`pdf`, `docx`, `xlsx`, `pptx` 等），前端根据类型决定调用哪种预览组件。

#### 2. 解析文件定位属性（Pipeline & Debugging）

用于将 Chunk 映射回解析出的 Markdown/JSON 文件，主要服务于后台排障、断点续跑和动态窗口扩展。



- **`parsed_file_url`**：解析后生成的 Markdown 文件存储路径。
- **`parsed_layout_url`**：若解析器（如 LayoutLM/PaddleOCR/MinerU）生成了包含结构树的 `layout.json`，需记录其路径。
- **`md_char_start` / `md_char_end`**：当前 Chunk 在整个 Markdown 文件中的起始与结束字符偏移量（Character Offset）。
  - *作用*：如果需要做**动态滑动窗口**或**父子上下文扩充**，无需重新检索向量，直接按 offset 在 Markdown 中向前后扩展提取即可。
- **`parser_name`**：解析器版本标记（如 `docling_v2`），当解析算法升级时便于批量筛选重跑。

#### 3. 原文档精确定位与高亮属性（Anchoring & Visual Grounding）

这组属性是让用户点击引用后**跳转到原文档并圈出对应区域**的核心。



- **`page_numbers`（数组）**：当前切片所在的 PDF 页码（如 `[12]` 或跨页的 `[12, 13]`）。
- **`bboxes`（边界框坐标）**：文本块在 PDF 页面上的矩形框坐标 `[x0, y0, x1, y1]`。
  - *最佳实践*：存储**归一化坐标**（0~1 之间的小数，即相对于页面宽高比例），这样前端在不同屏幕分辨率或缩放比例下都能准确绘制高亮框。
- **`heading_hierarchy`（章节路径）**：当前切片所属的层级标题（如 `["员工手册", "福利制度", "年休假"]`），即使是非分页文档（Word/HTML/Markdown），也能通过章节面包屑提示用户所在位置。

#### 4. 切片拓扑属性（Topology & Chunk Context）

- **`chunk_index`**：文档内的切片序号（从 0 开始自增）。
- **`prev_chunk_id` / `next_chunk_id`**：双向链表指针，方便命中当前块后，迅速拉取前后的邻居块给大模型补充上下文（Neighbor Chunk Retrieval）。
- **`parent_chunk_id`**：父切片 ID（用于 Parent-Document 检索架构）。

### 三、 端到端的调用流转示例

```
[用户提问]
   │
   ▼
[向量召回 Top-K Chunk] ──► 获得 Chunk Payload
   │
   ├─► [组装 Prompt] ──► 提取 text 传给 LLM 生成回答，标注角标 [1]
   │
   └─► [返回前端 Metadata]
          │
          ├─ 面向用户侧：
          │    - 来源标题：file_name
          │    - 预览原件：raw_file_url
          │    - 视觉定位：page_numbers + bboxes (PDF.js 在第12页画黄色高亮框)
          │    - 章节面包屑：heading_hierarchy
          │
          └─ 面向管理/调试侧（可选）：
               - 查看解析结果：parsed_file_url
               - 查看切片位置：md_char_start ~ md_char_end
```

### 四、 工程建议

1. **避免在向量库存储超大字段**：向量库的标量过滤（Filter）和读取开销随着 Payload 增大而上升。不要将完整的中间解析文件内容直接塞入向量库；只存 `URL/Key` 和 `Offset/Index` 等指针属性，需要完整内容时再去对象存储或缓存拉取。
2. **坐标归一化**：如果切片来源是 PDF，务必在写入前将 OCR/解析器输出的像素坐标转换为归一化坐标（`x / page_width`, `y / page_height`），以避免前端渲染缩放时的错位问题。

### 问：你说的这些都不需要经过SQL数据库获取吗

在**工业级和中大型 RAG 架构**中，这些元数据通常**绝大部分都保存在 SQL 数据库（或结合缓存）中**，而不是全部硬编码塞进向量数据库。



把所有属性直接塞进向量库的 Payload 称为“胖切片模式（Fat Chunk）”**，适合小型 Demo；而将向量库与 SQL 数据库分离的**“瘦切片模式（Thin Chunk）”才是生产环境的标准范式。



### 生产级标准架构：双库分工

```
[ 用户提问 ]
     │
     ▼
[ 1. 向量数据库 (Milvus / Qdrant) ]
     │  检索只负责找相关的 ID
     ▼  返回: [chunk_id_1, chunk_id_2, ...] (耗时 ~10ms)
     │
     ▼
[ 2. 关系型数据库 (PostgreSQL / MySQL) / Redis ]
     │  通过 ID 批量回表: SELECT * FROM chunks WHERE id IN (...)
     ▼  拿到完整的 text, bboxes, page_numbers, raw_file_url
     │
     ├─► 组装 Prompt 发送给 LLM
     └─► 拼装引用源元数据返回给前端渲染
```

| **存储组件**                       | **存储内容**                                          | **核心职责**                                                 |
| ---------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------ |
| **向量数据库**  (Vector DB)        | `chunk_id`, `vector`, `kb_id`, `doc_id`               | **相似度计算与粗筛**。  内存昂贵，只保留向量索引和最少量的过滤标量。 |
| **SQL 数据库**  (PostgreSQL/MySQL) | 文档元数据表、切片详情表（`bboxes`, `text`, `pages`） | **业务逻辑、权限控制与详实数据回表**。  提供事务一致性（ACID）、级联删除与复杂关联。 |
| **对象存储**  (S3 / MinIO)         | `doc.pdf`, `doc.md`, `layout.json`                    | **大文件持久化**。  存放原始文档、中间解析文件和图片。       |

### 为什么不能把所有属性全塞在向量库？

**1. 消除数据冗余与内存膨胀**



- 一篇 50 页的 PDF 会切出 100~200 个 Chunk。如果每个 Chunk 的 Payload 都存一遍 `file_name`、`raw_file_url`、`parsed_file_url`，同一份元数据被重复存了数百次。
- 向量数据库为了支持毫秒级检索，索引（如 HNSW）和 Payload 往往需要常驻内存或频繁缓存，冗余的大量文本和 JSON 坐标会导致 RAM 成本急剧上升。

**2. 解决元数据更新难题（ACID 痛点）**



- **重命名场景**：用户将《2024规章.pdf》更名为《2024规章(终版).pdf》。
  - *全在向量库*：后端必须扫描并更新该文档下的 200 个向量点，开销极大且容易出现分布式不一致。
  - *使用 SQL*：只需执行一条 `UPDATE documents SET file_name = '...' WHERE id = '...'`，毫秒级生效。
- **删除与权限变更**：文档下线、删除或更改部门可见权限时，SQL 只需修改单行状态或关联表即可拦截，无需对数十万向量执行昂贵的批量写操作。

**3. 坐标与排版数据的尺寸过大**



- 解析 PDF 产生的 `bboxes` 包含密集的段落坐标点数组，若合并存入向量库 Payload，单条记录大小可能突破数 KB，会拖慢向量库的检索与传输吞吐量。

### 生产级 SQL 表结构设计参考

通常在 PostgreSQL / MySQL 中建立两张核心表：



**1. 文档主表 (`t_document`)**



SQL

```
CREATE TABLE t_document (
    doc_id VARCHAR(64) PRIMARY KEY,
    kb_id VARCHAR(64) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_type VARCHAR(32) NOT NULL,           -- pdf, docx, etc.
    raw_storage_path VARCHAR(512) NOT NULL,   -- s3://.../doc.pdf
    parsed_md_path VARCHAR(512),              -- s3://.../doc.md
    parsed_layout_path VARCHAR(512),          -- s3://.../layout.json
    parser_version VARCHAR(64),
    status VARCHAR(32) DEFAULT 'PARSED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**2. 切片详情表 (`t_chunk`)**



SQL

```
CREATE TABLE t_chunk (
    chunk_id VARCHAR(64) PRIMARY KEY,
    doc_id VARCHAR(64) REFERENCES t_document(doc_id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content_text TEXT NOT NULL,               -- 切片实际文本内容
    page_numbers JSONB,                       -- 存储 [12] 或 [12, 13]
    bboxes JSONB,                             -- 存储归一化坐标 [{"page":12, "coords":[...]}]
    heading_hierarchy JSONB,                  -- 存储章节层级 ["第一章", "第三节"]
    md_char_start INT,                        -- 在 Markdown 中的起始偏移
    md_char_end INT                           -- 在 Markdown 中的结束偏移
);
```

### 架构选型建议

- **小型原型 / POC 阶段**：直接将所有字段写入向量库（如 Milvus Dynamic Schema / Qdrant Payload），不需要建 SQL 表，单组件快速跑通闭环。
- **上线生产环境**：向量库仅存放 `vector`、`chunk_id`、`kb_id`（用于按知识库隔离过滤），检索出 Top-K 的 `chunk_id` 列表后，通过 SQL 批量 `IN` 查询或从 Redis 缓存中获取完整的高亮坐标、原文档路径与渲染上下文。

### 问：还有一个问题，原始文档对应的解析后的markdown文档是否也要持久化存储，且和原始文档一样存在文件记录表中？下面是我当前的数据库表设计和向量库schema设计，还应该做哪些修改和优化？

```
from sqlmodel import Field, SQLModel
from datetime import datetime, timezone
from sqlalchemy import UniqueConstraint, Column, Enum
import enum


# 枚举类型
class UserRole(int, enum.Enum):
    STUDENT = 0
    TEACHER = 1
    ADMIN = 2

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

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
    created_at: datetime = Field(default_factory= utc_now)

# 课程表
class Course(SQLModel, table= True):
    __tablename__ = "course"
    id: int | None = Field(default= None, primary_key= True)
    name: str
    course_code: str | None = Field(default=None, unique=True, index=True,description="课程代码")
    teacher_id: int = Field(foreign_key="user.id",description="创建这门课程的教师id")
    overview: str = Field(default="暂无简介", description="课程简介")
    created_at: datetime = Field(default_factory= utc_now)


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
    created_at: datetime = Field(default_factory=utc_now)

# 作业表(一门课程有多个作业，一个作业只对应一门课程)
class Assignment(SQLModel, table= True):
    __tablename__ = "assignment"
    id: int | None = Field(default= None, primary_key= True)
    course_id: int = Field(foreign_key= "course.id",description="该作业对应的课程")
    title: str
    description: str
    deadline: datetime = Field(description="该作业的截止日期")
    created_at: datetime = Field(default_factory=utc_now, description="该作业的创建日期")

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
    submitted_at: datetime = Field(default_factory=utc_now, description="作业提交时间")
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

# 文件记录表
class FileRecord(SQLModel, table= True):
    __tablename__ = "file_record"
    id: int | None = Field(default= None, primary_key= True)
    file_ext: str | None = Field(default= None, description="该文件的类型")
    mime_type: str | None = Field(default= None,description="该文件的MIME类型")
    storage_type: str = Field(description="该文件的存储方式：本地 / minio")
    storage_key: str = Field(description="该文件的真实路径")
    total_size: int = Field(description="该文件的大小，单位字节")
    file_hash: str = Field(description="该文件的hash值")
    created_at: datetime = Field(default_factory=utc_now)
    
# 一个知识库文件记录只对应一个物理文件实体（file_record）
# 但是一个物理文件实体可以对应多个知识库文件记录
# 因为不同的课程/用户的知识库中可能存同一份文件
class KnowledgeDocument(SQLModel, table=True):
    __tablename__ = "knowledge_document"
    __table_args__ = (UniqueConstraint("file_hash", "scope_id", name="uq_file_scope"),)
    id: int = Field(default=None, primary_key=True)
    file_record_id: int = Field(foreign_key="file_record.id")
    file_hash: str = Field(description="该文件的hash值,sha256")
    scope: str
    scope_id: str
    chunk_count: int
    created_at: datetime = Field(default_factory=utc_now)

```

### 然后FileRecord表后续file_hash要加上唯一索引以去重和实现秒传 

**解析后的 Markdown 文档必须持久化存储**，但**不建议作为普通用户文件混在 `FileRecord` 表中**。



解析后的 Markdown 和结构化数据（如 `layout.json`、提取的切片配图）属于 **ETL 派生产物（Derived Artifacts）**，它是由系统自动生成、服务于检索与溯源的中间状态。



### 一、 为什么必须持久化？为什么不宜混入 `FileRecord`？

- **必须持久化的原因**：
  1. **避免昂贵的重复解析**：PDF/Word 的 OCR 与版面解析（如使用 MinerU、Docling）耗费大量 CPU/GPU 与时间。如果后续需要调整切片大小（Chunk Size）、更换分块算法、或在重置向量索引时，直接读取已解析的 Markdown 重新切片即可，无需重新跑一遍文档解析。
  2. **动态滑动窗口与上下文扩展**：检索命中某个 Chunk 时，如果上下文不足，可以直接根据字符偏移量（`char_offset`）在持久化的 Markdown 中前后扩展抓取相邻段落。
  3. **排障与准确率评估**：当问答效果不好时，工程师可以直接对比“原文档 $\leftrightarrow$ Markdown $\leftrightarrow$ 切片”来定位是解析丢了内容还是向量检索失真。
- **存储方案选型**：
  - **推荐方案**：在 `KnowledgeDocument` 表中直接增加 `parsed_storage_key`（以及可选的 `layout_storage_key`）字段，指向 MinIO 中的 `knowledge/parsed/{file_hash}.md`。
  - **不建议直接放入 `FileRecord`**：`FileRecord` 是用户上传业务文件的物理抽象。若把临时派生文件存进去，会污染文件引用计数，并在处理“用户文件秒传/去重/网盘列表”等业务逻辑时产生额外的过滤条件负担。

### 二、 数据库与向量库现存问题与优化点

#### 1. SQL 数据库层面的缺陷与优化

1. **缺失 RAG 任务状态机（生命周期管理）**：
   - 文档上传、解析、分块、向量化是异步流水线。目前 `KnowledgeDocument` 只有 `chunk_count`，缺乏 `status`（如 `PENDING`, `PARSING`, `INDEXING`, `COMPLETED`, `FAILED`）和 `error_msg`，前端无法展示解析进度条，后端失败时也无法重试或告警。
2. **缺失 Chunk 详情表（或溯源定位信息丢失）**：
   - 目前系统没有存储每个 Chunk 的页码（`page_numbers`）、坐标框（`bboxes`）、章节面包屑（`heading_hierarchy`）。如果后续前端需要实现“点击引用跳转高亮 PDF”，当前结构无法支持。
3. **`FileRecord` 物理删除的级联风险**：
   - `file_hash` 加唯一索引后，一份物理文件被多个课程/作业引用。删除某门课程的资源时，必须通过外键/引用计数校验，绝不能直接物理删除 MinIO 里的文件。

#### 2. Milvus 向量库 Schema 的缺陷与优化

1. **数据类型不一致**：
   - SQL 中 `file_record_id` 是 `int`（主键），而 Milvus 中定义为 `VARCHAR(256)`。建议统一或在 Milvus 中使用业务主键 `knowledge_doc_id`。
2. **缺失切片序号（`chunk_index`）**：
   - Milvus 中只有文本和文档 ID，没有 `chunk_index`。无法在向量库内根据 `chunk_index - 1` 或 `chunk_index + 1` 召回命中块的前后邻居切片。
3. **文档与知识库定位粒度**：
   - 检索过滤通常基于 `knowledge_doc_id` 或 `scope_id`。建议在 Milvus 中添加 `knowledge_doc_id`（`INT64`），便于与 SQL 的 `KnowledgeDocument` 表精准联查。

### 三、 优化后的完整代码设计

#### 1. 优化后的 SQLModel 模型 (`models.py`)

Python

```
import enum
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel, Column
from sqlalchemy import UniqueConstraint, Enum, Text


class UserRole(int, enum.Enum):
    STUDENT = 0
    TEACHER = 1
    ADMIN = 2


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"       # 排队中
    PARSING = "PARSING"       # 正在解析为 Markdown
    INDEXING = "INDEXING"     # 正在切分与向量化
    COMPLETED = "COMPLETED"   # 全部完成
    FAILED = "FAILED"         # 失败


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ==================== 基础文件表 ====================

class FileRecord(SQLModel, table=True):
    """物理文件实体表（严格去重，支持秒传）"""
    __tablename__ = "file_record"
    id: int | None = Field(default=None, primary_key=True)
    file_hash: str = Field(unique=True, index=True, description="文件 SHA-256 哈希值")
    file_ext: str | None = Field(default=None, description="文件扩展名，如 .pdf")
    mime_type: str | None = Field(default=None, description="MIME类型")
    storage_type: str = Field(default="minio", description="存储类型：local / minio")
    storage_key: str = Field(description="原文件在对象存储中的 Key/路径")
    total_size: int = Field(description="文件大小（字节）")
    created_at: datetime = Field(default_factory=utc_now)


# ==================== 知识库与 RAG 表 ====================

class KnowledgeDocument(SQLModel, table=True):
    """知识库文档索引表（将物理文件挂载到具体的 Scope 中）"""
    __tablename__ = "knowledge_document"
    __table_args__ = (UniqueConstraint("file_hash", "scope_id", name="uq_file_scope"),)

    id: int | None = Field(default=None, primary_key=True)
    file_record_id: int = Field(foreign_key="file_record.id", index=True)
    file_hash: str = Field(index=True)
    
    # 隔离范围
    scope: str = Field(description="作用域：course / user / system", index=True)
    scope_id: str = Field(description="作用域 ID，例如 course_id", index=True)
    
    # 派生解析文件与状态跟踪
    parsed_storage_key: str | None = Field(default=None, description="解析后生成的 Markdown 在 MinIO 中的路径")
    layout_storage_key: str | None = Field(default=None, description="结构化坐标 layout.json 在 MinIO 中的路径")
    status: TaskStatus = Field(default=TaskStatus.PENDING, sa_column=Column(Enum(TaskStatus)), description="处理状态")
    error_msg: str | None = Field(default=None, description="失败原因描述")
    
    chunk_count: int = Field(default=0, description="切片总数")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeChunk(SQLModel, table=True):
    """
    切片详情回表（瘦切片架构），用于高亮定位与上下文展示
    如果数据量极大，可根据需要直接读 Redis 或 MinIO JSON
    """
    __tablename__ = "knowledge_chunk"
    id: str = Field(primary_key=True, description="Chunk 全局唯一 ID，如 {doc_id}_{chunk_index}")
    doc_id: int = Field(foreign_key="knowledge_document.id", index=True)
    chunk_index: int = Field(description="切片在文档内的序号")
    chunk_text: str = Field(sa_column=Column(Text), description="切片文本内容")
    
    # 溯源高亮与层级属性（存 JSON 字符串）
    page_numbers: str | None = Field(default=None, description="JSON 数组，如 [1, 2]")
    bboxes: str | None = Field(default=None, description="JSON 归一化坐标 [{'page':1,'coords':[x0,y0,x1,y1]}]")
    heading_hierarchy: str | None = Field(default=None, description="JSON 数组，如 ['第一章', '第二节']")
    created_at: datetime = Field(default_factory=utc_now)
```

#### 2. 优化后的 Milvus Collection 初始化代码

Python

```
from pymilvus import DataType, Function, FunctionType

async def create_collection(self):
    client = self.client
    
    # 如果存在则直接返回
    if await client.has_collection(self.collection_name):
        return

    schema = client.create_schema(
        auto_id=False,
        enable_dynamic_field=True
    )

    # 1. 唯一主键：推荐使用 "{knowledge_doc_id}_{chunk_index}" 字符串
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=128)
    
    # 2. 向量字段
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=1024)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

    # 3. 文本与分词（用于 BM25 稀疏向量生成与全文检索）
    analyzer_params = {
        "tokenizer": "standard",
        "filter": ["lowercase"]
    }
    schema.add_field(
        "chunk_text", 
        DataType.VARCHAR, 
        max_length=8192, 
        enable_analyzer=True, 
        analyzer_params=analyzer_params, 
        enable_match=True
    )

    # 4. 关键标量过滤字段
    schema.add_field("knowledge_doc_id", DataType.INT64) # 关联 SQL 中的 KnowledgeDocument.id
    schema.add_field("file_record_id", DataType.INT64)   # 关联物理文件 ID
    schema.add_field("file_hash", DataType.VARCHAR, max_length=64)
    schema.add_field("scope", DataType.VARCHAR, max_length=64)
    schema.add_field("scope_id", DataType.VARCHAR, max_length=64)
    schema.add_field("chunk_index", DataType.INT64)       # 切片序号，用于拉取相邻上下文

    # 5. BM25 稀疏向量函数
    bm25_function = Function(
        name="text_bm25_emb",
        input_field_names=["chunk_text"],
        output_field_names=["sparse_vector"],
        function_type=FunctionType.BM25,
    )
    schema.add_function(bm25_function)

    # 6. 配置索引
    index_params = client.prepare_index_params()
    
    # 向量索引
    index_params.add_index(
        field_name="dense_vector", 
        index_type="AUTOINDEX", 
        metric_type="IP"
    )
    index_params.add_index(
        field_name="sparse_vector", 
        index_type="SPARSE_INVERTED_INDEX", 
        metric_type="BM25",
        params={"inverted_index_algo": "DAAT_MAXSCORE"}
    )
    index_params.add_index(field_name="chunk_text", index_type="AUTOINDEX")

    # 标量倒排索引（加速 Scope 过滤与按文档级联删除）
    index_params.add_index(field_name="scope_id", index_name="idx_scope_id", index_type="INVERTED")
    index_params.add_index(field_name="scope", index_name="idx_scope", index_type="INVERTED")
    index_params.add_index(field_name="knowledge_doc_id", index_name="idx_kdoc_id", index_type="INVERTED")
    index_params.add_index(field_name="file_hash", index_name="idx_file_hash", index_type="INVERTED")

    await client.create_collection(
        collection_name=self.collection_name,
        schema=schema,
        index_params=index_params
    )
```

### 四、 优化后的核心运行逻辑

1. **上传与秒传**：
   - 上传文件 $\rightarrow$ 计算 Hash $\rightarrow$ 查 `FileRecord`：
     - 若已存在：跳过 MinIO 上传，直接复用 `file_record_id`。
     - 若不存在：写入 MinIO 并保存 `FileRecord`。
2. **知识库绑定与异步任务**：
   - 插入 `KnowledgeDocument`（状态置为 `PENDING`）。
   - 检查该 `file_hash` 是否已有解析好的 Markdown：
     - 若有：直接读取 `parsed_storage_key` 对应的 Markdown 文件跳过 OCR 解析，快速分块并写入 Milvus。
     - 若无：启动后台异步 Worker 执行 PDF $\rightarrow$ Markdown 解析，存储 `.md` 到 MinIO 并更新 `parsed_storage_key`，随后切片入库。
3. **问答与溯源高亮**：
   - 提问 $\rightarrow$ Milvus 过滤 `scope_id == "course_12"` 召回 Top-K Chunk ID $\rightarrow$ 回查 `KnowledgeChunk` / SQL 拿到 `bboxes` 和 `page_numbers` $\rightarrow$ 组装 Prompt 给 LLM，并将原始文件预览地址与坐标数组一并返回给前端渲染。

### 问：请给出一个使用 TaskIQ 配合 MinIO 实现 PDF 解析为 Markdown、分块并写入 Milvus 的完整异步任务代码示例。

这是一个基于 **TaskIQ + Redis + MinIO + PyMuPDF/pymupdf4llm + PyMilvus + SQLModel** 的完整异步解析与入库流水线实现方案。



架构采用“**下载原件 $\rightarrow$ 解析 Markdown $\rightarrow$ 持久化中间产物 $\rightarrow$ 分块与向量化 $\rightarrow$ 双写 Milvus & SQL $\rightarrow$ 更新状态机**”的标准 ETL 流程。



### 1. 依赖准备与 Broker 配置 (`tk_broker.py`)

Python

```
# tk_broker.py
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

# 使用 Redis 作为 TaskIQ 的任务队列和结果后端
redis_url = "redis://localhost:6379/0"
result_backend = RedisAsyncResultBackend(redis_url="redis://localhost:6379/1")

broker = ListQueueBroker(
    url=redis_url,
).with_result_backend(result_backend)
```

### 2. 外部客户端与数据库会话管理 (`infra.py`)

Python

```
# infra.py
import io
from minio import Minio
from pymilvus import AsyncMilvusClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# 1. MinIO 客户端 (S3 兼容存储)
minio_client = Minio(
    endpoint="localhost:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    secure=False
)
RAW_BUCKET = "raw-files"
PARSED_BUCKET = "parsed-files"

# 确保 Bucket 存在
for bucket in [RAW_BUCKET, PARSED_BUCKET]:
    if not minio_client.bucket_exists(bucket):
        minio_client.make_bucket(bucket)

# 2. Milvus 异步客户端
milvus_client = AsyncMilvusClient(uri="http://localhost:19530")
COLLECTION_NAME = "knowledge_base"

# 3. 异步 SQLModel 数据库引擎
DATABASE_URL = "mysql+aiomysql://root:password@localhost:3306/rag_db"
async_engine = create_async_engine(DATABASE_URL, echo=False)
async_session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
```

### 3. 核心异步任务实现 (`tasks.py`)

任务内部在各关键节点更新 `TaskStatus`，并在异常时捕获堆栈回写 `error_msg`。



Python

```
# tasks.py
import json
import asyncio
import io
import pymupdf4llm
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from sqlmodel import select
from sentence_transformers import SentenceTransformer

from tk_broker import broker
from infra import (
    minio_client, 
    milvus_client, 
    async_session_factory, 
    RAW_BUCKET, 
    PARSED_BUCKET, 
    COLLECTION_NAME
)
from models import KnowledgeDocument, KnowledgeChunk, FileRecord, TaskStatus, utc_now

# 本地加载向量模型（Worker 启动时常驻显存/内存）
embedding_model = SentenceTransformer("BAAI/bge-large-zh-v1.5")


# ==================== 辅助方法 ====================

def parse_pdf_to_markdown_pages(pdf_bytes: bytes) -> list[dict]:
    """
    使用 pymupdf4llm 解析 PDF，返回按页划分的 Markdown 文本列表
    每个元素形如: {"text": "# 第一章...", "page": 1}
    """
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    # page_chunks=True 会返回每一页的解析字典，保留页码元数据
    md_pages = pymupdf4llm.to_markdown(doc, page_chunks=True)
    return md_pages


def chunk_markdown_pages(md_pages: list[dict]) -> list[dict]:
    """
    基于 Markdown 标题层级和字符长度进行二次切分，并继承所属页码与章节路径
    """
    headers_to_split_on = [
        ("#", "Header_1"),
        ("##", "Header_2"),
        ("###", "Header_3"),
    ]
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on, 
        strip_headers=False
    )
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=512, 
        chunk_overlap=64
    )

    all_chunks = []
    chunk_index = 0

    for page_info in md_pages:
        page_num = page_info["metadata"]["page"] + 1  # 转为从 1 开始的真实页码
        page_text = page_info["text"]

        if not page_text.strip():
            continue

        # 先按标题结构切分
        header_splits = header_splitter.split_text(page_text)
        for doc in header_splits:
            # 提取当前块所属的所有层级标题
            hierarchy = [v for k, v in doc.metadata.items() if k.startswith("Header_")]
            
            # 再按长度细分，防止单个标题下文本超长
            sub_chunks = text_splitter.split_text(doc.page_content)
            for sub_text in sub_chunks:
                all_chunks.append({
                    "chunk_index": chunk_index,
                    "chunk_text": sub_text,
                    "page_numbers": [page_num],
                    "heading_hierarchy": hierarchy
                })
                chunk_index += 1

    return all_chunks


# ==================== TaskIQ 异步主任务 ====================

@broker.task
async def process_pdf_rag_pipeline(knowledge_doc_id: int):
    """
    PDF 处理主流水线任务
    """
    async with async_session_factory() as session:
        # 1. 查询待处理记录
        result = await session.exec(select(KnowledgeDocument).where(KnowledgeDocument.id == knowledge_doc_id))
        kdoc = result.first()
        if not kdoc:
            return

        try:
            # 更新状态为解析中
            kdoc.status = TaskStatus.PARSING
            kdoc.updated_at = utc_now()
            await session.commit()

            # 2. 获取原始文件信息并从 MinIO 读取字节流
            file_rec = await session.get(FileRecord, kdoc.file_record_id)
            if not file_rec:
                raise ValueError(f"FileRecord not found: id={kdoc.file_record_id}")

            response = minio_client.get_object(RAW_BUCKET, file_rec.storage_key)
            pdf_bytes = response.read()
            response.close()
            response.release_conn()

            # 3. CPU 密集型解析：通过线程池执行 PDF -> Markdown
            loop = asyncio.get_running_loop()
            md_pages = await loop.run_in_executor(None, parse_pdf_to_markdown_pages, pdf_bytes)

            # 4. 将合并后的完整 Markdown 持久化存入 MinIO
            full_markdown_text = "\n\n".join([f"<!-- Page {p['metadata']['page']+1} -->\n" + p["text"] for p in md_pages])
            parsed_storage_key = f"parsed/{kdoc.file_hash}.md"
            md_bytes = full_markdown_text.encode("utf-8")
            
            minio_client.put_object(
                bucket_name=PARSED_BUCKET,
                object_name=parsed_storage_key,
                data=io.BytesIO(md_bytes),
                length=len(md_bytes),
                content_type="text/markdown; charset=utf-8"
            )

            # 5. 更新状态为索引与分块中
            kdoc.status = TaskStatus.INDEXING
            kdoc.parsed_storage_key = parsed_storage_key
            kdoc.updated_at = utc_now()
            await session.commit()

            # 6. 分块处理
            chunks = chunk_markdown_pages(md_pages)
            if not chunks:
                raise ValueError("文档解析后未生成任何有效切片")

            # 7. 批量计算向量 Embedding（在线程池中执行）
            texts_to_embed = [c["chunk_text"] for c in chunks]
            dense_vectors = await loop.run_in_executor(
                None, 
                lambda: embedding_model.encode(texts_to_embed, normalize_embeddings=True).tolist()
            )

            # 8. 组装数据并写入 Milvus
            milvus_entities = []
            sql_chunks = []

            for chunk_data, vector in zip(chunks, dense_vectors):
                chunk_id = f"{kdoc.id}_{chunk_data['chunk_index']}"

                # Milvus 记录
                milvus_entities.append({
                    "id": chunk_id,
                    "dense_vector": vector,
                    "chunk_text": chunk_data["chunk_text"],
                    "knowledge_doc_id": kdoc.id,
                    "file_record_id": kdoc.file_record_id,
                    "file_hash": kdoc.file_hash,
                    "scope": kdoc.scope,
                    "scope_id": kdoc.scope_id,
                    "chunk_index": chunk_data["chunk_index"],
                })

                # SQL 实体记录（用于溯源回表）
                sql_chunks.append(
                    KnowledgeChunk(
                        id=chunk_id,
                        doc_id=kdoc.id,
                        chunk_index=chunk_data["chunk_index"],
                        chunk_text=chunk_data["chunk_text"],
                        page_numbers=json.dumps(chunk_data["page_numbers"]),
                        heading_hierarchy=json.dumps(chunk_data["heading_hierarchy"], ensure_ascii=False),
                    )
                )

            # 批量插入 Milvus
            await milvus_client.insert(
                collection_name=COLLECTION_NAME,
                data=milvus_entities
            )

            # 批量写入 SQL 关系库
            session.add_all(sql_chunks)

            # 9. 任务完成，更新文档最终状态
            kdoc.status = TaskStatus.COMPLETED
            kdoc.chunk_count = len(chunks)
            kdoc.updated_at = utc_now()
            await session.commit()

        except Exception as e:
            # 异常捕获：回滚事务并记录错误信息
            await session.rollback()
            kdoc.status = TaskStatus.FAILED
            kdoc.error_msg = str(e)[:1000]
            kdoc.updated_at = utc_now()
            await session.commit()
            raise e
```

### 4. 业务层触发与 Worker 启动

#### (1) 在 FastAPI 路由中触发任务

Python

```
# main.py (FastAPI Endpoint 片段)
from fastapi import FastAPI, Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from models import KnowledgeDocument, TaskStatus
from tasks import process_pdf_rag_pipeline

app = FastAPI()

@app.post("/api/knowledge/upload")
async def bind_and_process_document(
    file_record_id: int, 
    file_hash: str, 
    scope: str, 
    scope_id: str,
    session: AsyncSession = Depends(get_session)
):
    # 1. 创建知识库文档记录
    kdoc = KnowledgeDocument(
        file_record_id=file_record_id,
        file_hash=file_hash,
        scope=scope,
        scope_id=scope_id,
        status=TaskStatus.PENDING
    )
    session.add(kdoc)
    await session.commit()
    await session.refresh(kdoc)

    # 2. 异步派发 TaskIQ 任务
    await process_pdf_rag_pipeline.kiq(knowledge_doc_id=kdoc.id)

    return {"code": 0, "message": "任务已提交", "data": {"doc_id": kdoc.id, "status": kdoc.status}}
```

#### (2) 启动 TaskIQ Worker 进程

Bash

```
# 运行 worker，监听 tasks 模块中的任务
taskiq worker tk_broker:broker tasks --workers 2
```

### 5. 流水线关键设计要点

- **防阻塞异步事件循环**：PDF 解析（PyMuPDF）和深度学习模型推理（PyTorch/Transformers）属于 CPU/GPU 密集型操作。代码中使用 `loop.run_in_executor(None, ...)` 将其分发到工作线程池执行，避免阻塞 FastAPI / TaskIQ 的 `asyncio` 事件循环。
- **分块元数据血缘保持**：`chunk_markdown_pages` 阶段通过 `page_numbers` 和 `heading_hierarchy` 捕获切片的物理位置与逻辑章节，并作为 JSON 写入 `KnowledgeChunk`，保证了前端溯源高亮能力。
- **中间文件 MinIO 独立归档**：将解析出来的 Markdown 独立保存至 `parsed-files` Bucket，后续若需要调整分块策略或做 BM25/向量重切分，只需从 MinIO 读取 Markdown 重跑，无需重复进行 OCR 与排版分析。

### 问：很好，不过现在的问题在于是否需要将主任务中的拆成文档解析、分块、向量化三个任务？你推荐这样做吗

**强烈不建议拆成 3 个独立任务**（特别是将“分块”独立拆成一个 Task 属于典型的过度设计）；但在生产进阶架构中，**推荐拆为 2 个任务阶段：【文档解析】与【分块及向量入库】**。



### 为什么不建议拆成 3 个任务？

把“分块（Chunking）”单独作为一个分布式任务存在明显的工程弊端：



- **计算耗时远低于调度开销**：对解析好的 Markdown 进行正则或层级切分是纯内存 CPU 计算，处理几十页文本通常只需 **5~20 毫秒**。如果将其独立为一个 Task，TaskIQ/Redis 的消息序列化、网络传输、队列调度耗时反而远超分块本身的执行时间。
- **消息体积膨胀与网络传输负担**：如果“分块”单独成任务，意味着分块任务要把几百个切片对象的完整文本、页码、元数据通过 Redis 消息体完整传给“向量化”任务，白白增加 Redis 内存占用与带宽消耗。

### 单任务 vs 2 阶段任务 对比选型

| **方案**                     | **拆分方式**                                                 | **适用场景**                                  | **核心优势**                                                 | **潜在劣势**                                        |
| ---------------------------- | ------------------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------- |
| 单任务流水线  (Monolithic)   | 1 个任务处理完整流程                                         | 中小型项目、POC 原型、单机部署                | 逻辑简单，无跨任务状态同步，开发排障成本极低                 | 无法按算力异构扩缩容；向量化失败需重跑全量 PDF 解析 |
| 2 阶段任务  (Recommended)    | **Task 1: 文档解析**  $\downarrow$  **Task 2: 分块与向量化** | 生产环境、多机器/多卡集群、需频繁调优切片参数 | 算力解耦（CPU解析 vs GPU推理）；调优分块策略时免重跑昂贵 OCR | 需维护两段状态流转，系统复杂度略有提升              |
| 3 任务流水线  (Anti-Pattern) | 解析 $\rightarrow$ 分块 $\rightarrow$ 向量化                 | 不推荐                                        | 无显著收益                                                   | 调度开销大，Redis 传输大 Payload，过度设计          |

### 推荐拆为 2 阶段的核心价值

#### 1. 算力环境与 Worker 队列解耦（异构计算调度）

- **解析阶段（Task 1）**：高度依赖 CPU 和复杂依赖库（如 OCR、C++ 扩展、PaddleOCR、MinerU）。
- **向量化阶段（Task 2）**：依赖 GPU 显存（本地 Embedding 模型）或高并发网络 I/O（调用外部 API）。
- **收益**：可以通过 TaskIQ 为两类任务打上不同的队列标签（`queue_name`），将 CPU 密集型解析任务分配给通用 CPU Worker，将向量化任务集中调度给带 GPU 的 Worker。

#### 2. RAG 调优场景下“免重解析”重跑

- RAG 迭代过程中，最频繁调整的是 `chunk_size`、`overlap` 或分块规则，而不是 PDF 解析结果。
- 拆分后，只要 MinIO 中持久化了 `parsed/{file_hash}.md`，调优时**直接重发 Task 2** 即可在几秒内重新切片入库，节省数十秒乃至数分钟的 PDF 排版/OCR 计算。

#### 3. 细粒度容错与重试

- 若 Embedding 推理因显存 OOM 或远程接口限流报错，Task 2 可以单独重试，而不需要重新去 MinIO 下载原版 PDF 并重新解析一遍。

### 2 阶段架构代码改造示例

#### 阶段 1：解析任务 (`tasks_parse.py`)

负责将原始文档转化为标准化 Markdown 并存入 MinIO，完成后触发阶段 2。



Python

```
@broker.task(queue_name="cpu_parsing_queue")
async def task_parse_pdf_to_markdown(knowledge_doc_id: int):
    async with async_session_factory() as session:
        kdoc = await session.get(KnowledgeDocument, knowledge_doc_id)
        if not kdoc:
            return

        try:
            kdoc.status = TaskStatus.PARSING
            await session.commit()

            # 1. 检查是否存在同 Hash 已解析好的 Markdown（秒级复用）
            parsed_storage_key = f"parsed/{kdoc.file_hash}.md"
            
            # 2. 如果 MinIO 中不存在，则执行解析
            if not minio_client_has_object(PARSED_BUCKET, parsed_storage_key):
                file_rec = await session.get(FileRecord, kdoc.file_record_id)
                pdf_bytes = download_from_minio(RAW_BUCKET, file_rec.storage_key)
                
                # CPU 密集型解析
                md_pages = await run_in_thread(parse_pdf_to_markdown_pages, pdf_bytes)
                upload_markdown_to_minio(PARSED_BUCKET, parsed_storage_key, md_pages)

            kdoc.parsed_storage_key = parsed_storage_key
            await session.commit()

            # 3. 链式触发阶段 2：分块与向量化
            await task_chunk_and_index.kiq(knowledge_doc_id=kdoc.id)

        except Exception as e:
            kdoc.status = TaskStatus.FAILED
            kdoc.error_msg = f"解析阶段失败: {str(e)[:500]}"
            await session.commit()
            raise e
```

#### 阶段 2：分块与向量化任务 (`tasks_index.py`)

负责从 MinIO 读取 Markdown，完成分块、Embedding、写入 Milvus 与 SQL。



Python

```
@broker.task(queue_name="gpu_embedding_queue")
async def task_chunk_and_index(knowledge_doc_id: int):
    async with async_session_factory() as session:
        kdoc = await session.get(KnowledgeDocument, knowledge_doc_id)
        if not kdoc or not kdoc.parsed_storage_key:
            return

        try:
            kdoc.status = TaskStatus.INDEXING
            await session.commit()

            # 1. 从 MinIO 读取已持久化的 Markdown / 结构化数据
            md_content = download_from_minio(PARSED_BUCKET, kdoc.parsed_storage_key)

            # 2. 内存中极速切分 (耗时 ~10ms)
            chunks = chunk_markdown_text(md_content)

            # 3. 批量计算向量 (GPU 或 外部 API)
            dense_vectors = await run_in_thread(
                lambda: embedding_model.encode([c["text"] for c in chunks], normalize_embeddings=True).tolist()
            )

            # 4. 双写入库 (Milvus + SQL)
            await batch_insert_milvus_and_sql(session, kdoc, chunks, dense_vectors)

            # 5. 标记完成
            kdoc.status = TaskStatus.COMPLETED
            kdoc.chunk_count = len(chunks)
            await session.commit()

        except Exception as e:
            kdoc.status = TaskStatus.FAILED
            kdoc.error_msg = f"索引阶段失败: {str(e)[:500]}"
            await session.commit()
            raise e
```

### 实施建议

- **当前处于开发/联调阶段**：保持**单任务（一流水线到底）**，减少系统协作环节与调试成本。
- **准备上线或已有独立 GPU 机器/多实例**：采用上述 **2 阶段方案**，将解析与向量化分流到不同 Worker 队列。

### 问：哦我明白了，两阶段方案主要是针对那种大型生产级应用有很多算力资源，包括强CPU服务器和强GPU服务器，分成两阶段的话吃GPU的worker如文档解析在任务高峰期就可以多开实例，同理吃CPU的worker也是如此

理解得非常准确，这套设计的核心价值正是“算力异构解耦”与“独立弹性伸缩（Auto-scaling）”。



不过在具体的硬件与任务画像对应上，稍微做一点精准归类：



### 两阶段的真实资源画像

| **阶段**                                         | **典型任务**                                                 | **算力画像**                                            | **扩缩容策略（如 K8s / Docker 实例）**                       |
| ------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------- | ------------------------------------------------------------ |
| **阶段 1：文档解析**  (Parsing & OCR)            | 下载 PDF、PyMuPDF 转文本、Docx 解析、常规 OCR 版面分析、生成并持久化 Markdown | **高 CPU、高内存、高磁盘/网络 I/O**  *(CPU 多进程并发)* | **堆廉价的 CPU 实例**  高峰期排队时，秒级拉起 10~20 个普通 CPU Worker 实例疯狂消化文件。 |
| **阶段 2：向量化与入库**  (Embedding & Indexing) | 从 MinIO 读取 Markdown、分块、BGE/BERT 深度学习批量推理、Milvus 写入 | **高 GPU、高显存（VRAM）**  *(矩阵乘法与显存占用)*      | **挂载到昂贵的 GPU 实例（或固定实例）**  Worker 常驻显存，以恒定高吞吐消化解析好的切片，避免显存 OOM。 |

### 为什么说这在生产上极具性价比？

**1. 避免昂贵的 GPU 算力被“杂活”空耗**



- 一台配置了高性能显卡的 GPU 服务器租赁成本极高。
- 如果不拆分，GPU Worker 在做“从 MinIO 下载 200MB 的 PDF”、“等待 PyMuPDF 解压文字”、“做正则清洗”这漫长的几十秒里，**昂贵的 GPU 核心几乎处于 0% 利用率的闲置状态**，极其浪费。
- 拆分后，脏活累活由廉价的 CPU 实例做完，GPU 实例只接纯文本并发做批量矩阵推理（Batch Inference），跑满 Tensor Core。

**2. 应对差异化的负载洪峰**



- **场景 A：开学/期末批量导入几千份课程材料**
  - 解析队列瞬间堆积数万任务。此时系统自动横向扩容几十个 CPU 节点去拉取解析，而 GPU 节点维持稳定 Batch 处理，整体流水线不会崩盘。
- **场景 B：优化 RAG 检索效果，微调切片大小（Chunk Size）**
  - 只需要全量重跑阶段 2。系统完全绕过阶段 1（不需要占用任何 CPU 去跑耗时的 PDF 解析），直接利用 GPU 几分钟内刷完全库的向量重构。

**3. API 向量化场景同样适用**



- 如果未来向量化不是本地部署模型，而是调用外部大模型厂商的 API（如 OpenAI / 阿里云百炼），阶段 2 的画像就会变为 **高并发网络 I/O 密集型**（需要大量 `asyncio` 协程并发发 HTTP 请求）。此时将阶段 2 跑在低配置的异步轻量节点上即可，完全不占用重型机器。

### 问：但在这块我不太明白的主要是就不能让每台服务器都有好CPU和GPU吗？还是我想的太简单了，你帮我分析一下

在单机开发或实验室服务器环境中，“给每台机器都配上顶级 CPU 和高性能 GPU”确实是最省心、最直观的方案。但在真实的**企业级生产环境与云原生架构**中，这种“全能大水管”模式会迅速撞上**成本浪费、伸缩死锁与运维瓶颈**。



### 为什么不能让每台服务器都配满好 CPU 和 GPU？

**1. 严重的资源空转与“木桶效应”（ROI 极低）**



- **解析阶段**：当 Worker 在对一个 500 页的扫描件 PDF 进行排版还原、文本正则清洗、提取表格时，CPU 核心会被跑满（100%），而此时价值数万元的高端 GPU（如 A10/H100/4090）的计算利用率是 **0%**。
- **向量化阶段**：当模型在 GPU 显存里做高并发矩阵乘法时，GPU 占用率 100%，而原本高配的数十核 CPU 此时只负责把文本搬运进显存，处于几近闲置状态。
- **结果**：无论机器在干什么，永远有另一半极其昂贵的硬件在“带薪摸鱼”。

**2. 弹性伸缩（Auto-Scaling）时的“成本绑架”**



- 假设期末周学生突然集中上传了 5,000 份课件，后台解析队列瞬间堆积。
- **分离架构**：Kubernetes / 弹性伸缩组只需秒级拉起 20 个极便宜的 **纯 CPU 容器/云服务器**（例如 4 核 8G，每小时几毛钱），几分钟消化完任务后自动销毁。
- **全能机架构**：为了增加 20 个解析并发，你必须拉起 20 台**带 GPU 的高配大机**。不仅成本直接暴涨 10~30 倍，云厂商在高峰期甚至根本没有足够的物理 GPU 实例供你瞬间弹性调用。

**3. 云厂商的机型矩阵与供应链限制**



- 主流云厂商（如阿里云、腾讯云、AWS）的实例规格本就是**解耦销售**的：
  - **计算型实例（Compute-Optimized）**：主频高、核心多、内存适中、价格低，专门消化 OCR 与 Web 解析。
  - **GPU 异构计算型实例（Accelerated Computing）**：不仅价格高昂，且往往有配额（Quota）和库存限制。
- 试图让每台机器都兼具顶级 CPU 和大显存 GPU，不仅采购预算会迅速见底，还失去了按需选型（Right-Sizing）的灵活性。

**4. 镜像体积、冷启动与故障隔离（DevOps 视角）**



- **冷启动耗时**：一个纯 CPU 处理的 Python/Go 镜像经过精简通常只有 **300MB ~ 800MB**，K8s 扩容拉取镜像只需 2~3 秒；而挂载了 CUDA/cuDNN/PyTorch 的深度学习镜像动辄 **10GB ~ 20GB**，拉取和启动慢数倍，无法应对突发流量。
- **故障爆炸半径**：PDF 解析经常会遇到损坏文件、超大图片或畸形排版，容易导致底层的 C/C++ 库发生内存泄漏或进程崩溃。如果所有逻辑跑在同一台物理机/进程中，解析崩溃会直接波及常驻显存的模型推理服务。

### 全能机架构 vs 异构分离架构 对比

| **维度**         | **全能单机模式 (All-in-One)**                | **算力异构分离模式 (Decoupled)**         |
| ---------------- | -------------------------------------------- | ---------------------------------------- |
| **硬件成本**     | 极高（为所有节点配备冗余 GPU）               | 极低（CPU 节点便宜，GPU 节点常驻保吞吐） |
| **硬件利用率**   | CPU 与 GPU 交替闲置，整体 ROI 低             | 各司其职，CPU 与 GPU 均维持在高水位线    |
| **突发流量应对** | 扩容昂贵、缓慢，极易受云厂商 GPU 库存限制    | CPU Worker 秒级水平扩展，成本可控        |
| **环境依赖**     | 每台机器都要装复杂的 NVIDIA Driver / CUDA    | CPU 节点仅需轻量环境，GPU 节点单独维护   |
| **适用阶段**     | 实验室 Demo、单机研发、日请求 < 100 的小工具 | 生产系统、多租户 SaaS、高校级/企业级平台 |

### 架构演进建议

- **当前阶段（开发联调 / 实验室服务器）**：实验室通常有一两台固定的高配机器（好 CPU + 好 GPU），此时**直接跑在一台机器上是最经济、开发效率最高的选择**，没必要为了拆分而拆分。
- **上线阶段（上云 / 面对真实并发）**：当系统开始为整个学院或大量用户提供稳定服务时，再将流水线通过 TaskIQ 队列分流到纯 CPU 节点和 GPU/API 节点即可。