"""把 377 篇笔记按面试专题分桶并切块，产出 interview/_plan.json。

后续每个"块"交给一个子代理去精读并写出对应的小节，最后合并成专题文档。
"""

from __future__ import annotations

import json
import pathlib

MAX_CHARS_PER_CHUNK = 90_000   # 单个子代理最多精读的字符数
MAX_FILES_PER_CHUNK = 12       # 单个子代理最多精读的文件数

# 专题定义：(专题标题, 输出文件名, [匹配规则], 处理方式)
# 规则用前缀匹配 notes 根目录下的相对路径
PLAN = [
    # ---------- Agent 方向（55%） ----------
    ("LLM 基础与 Prompt 工程", "agent/01-LLM基础与Prompt工程",
     ["typeora/large language models", "typeora/ollama", "typeora/模型下载",
      "record/常识/请求发送后再终止", "typeora/Coding Agent"], "full"),
    ("LangChain 核心", "agent/02-LangChain核心",
     ["typeora/langchain", "record/langchain"], "full"),
    ("LangGraph 与 Agent 编排", "agent/03-LangGraph与Agent编排",
     ["record/langgraph"], "full"),
    ("MCP 协议", "agent/04-MCP协议",
     ["record/MCP"], "full"),
    ("RAG 与向量检索", "agent/05-RAG与向量检索",
     ["record/RAG"], "full"),
    ("Agent 工程实践", "agent/06-Agent工程实践",
     ["record/AI", "typeora/Coding Agent",
      "typeora/python/大语言模型API流式输出", "record/常识/请求发送后再终止",
      "record/指数退避算法"], "full"),

    # ---------- Python 与后端（45%） ----------
    ("Python 语言与进阶", "backend/01-Python语言与进阶",
     ["typeora/python", "record/Python", "record/常识/为什么有了缓存区"], "full"),
    ("并发、异步与锁", "backend/02-并发异步与锁",
     ["record/异步", "record/并发安全（锁）", "record/瓶颈"], "full"),
    ("数据库与 ORM", "backend/03-数据库与ORM",
     ["record/数据库", "record/SQLModel", "record/Pydantic",
      "typeora/数据库", "typeora/前后端/后端/python后端/SQLModel"], "full"),
    ("Redis", "backend/04-Redis",
     ["record/Redis", "typeora/Redis"], "full"),
    ("计算机网络与操作系统", "backend/05-计算机网络与操作系统",
     ["typeora/计算机网络体系", "record/TCP", "typeora/windows",
      "typeora/服务器知识", "record/nginx"], "full"),
    ("消息队列与分布式事务", "backend/06-消息队列与分布式事务",
     ["record/消息队列", "record/TaskIQ", "record/分布式事务"], "full"),
    ("FastAPI 与 Web 工程", "backend/07-FastAPI与Web工程",
     ["record/FastAPI", "typeora/fastapi", "record/架构设计",
      "record/异常处理", "record/响应", "record/鉴权",
      "typeora/前后端/后端", "typeora/前后端/前后端数据保存职责划分"], "full"),
    ("部署与运维", "backend/08-部署与运维",
     ["record/部署", "record/docker", "typeora/docker"], "full"),

    # ---------- 项目深挖 ----------
    ("项目深挖：Agent 问答链路", "project/01-Agent问答链路深挖",
     ["record/业务上的疑问（经验类）/langgraph Agent开发",
      "record/业务上的疑问（经验类）/Agent问答模块代码解析"], "full"),
    ("项目深挖：文件上传与下载", "project/02-文件上传与下载深挖",
     ["record/业务上的疑问（经验类）/文件上传",
      "record/业务上的疑问（经验类）/文件下载",
      "record/业务上的疑问（经验类）/Httpx embedder"], "full"),
    ("项目深挖：业务模块与架构升级", "project/03-业务模块与架构升级深挖",
     ["record/业务上的疑问（经验类）/课程", "record/业务上的疑问（经验类）/选课记录",
      "record/业务上的疑问（经验类）/用户创建", "record/业务上的疑问（经验类）/会话管理",
      "record/业务上的疑问（经验类）/后续升级", "record/业务上的疑问（经验类）/幂等和并发安全分析",
      "record/业务上的疑问（经验类）/Redis", "record/业务上的疑问（经验类）/TaskIQ"], "full"),

    # ---------- 速查 ----------
    ("深度学习与 Transformer 速查", "cheatsheet/深度学习与Transformer",
     ["typeora/pytorch", "typeora/经典神经网络", "typeora/transformers",
      "typeora/nlp", "typeora/深度学习", "typeora/GPU"], "skim"),
    ("前端与工程杂项速查", "cheatsheet/前端与工程杂项",
     ["typeora/前后端/前端", "typeora/git", "record/Git",
      "typeora/jupyter notebook", "typeora/刷题"], "skim"),
]


def collect() -> dict:
    files: list[pathlib.Path] = []
    for root_name in ("record", "typeora"):
        root = pathlib.Path(root_name)
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".md", ".txt"}:
                continue
            if not path.read_text(encoding="utf-8", errors="replace").strip():
                continue
            files.append(path)

    assigned: set[str] = set()
    sections: list[dict] = []

    for title, out_name, prefixes, mode in PLAN:
        matched = []
        for path in files:
            key = path.as_posix()
            if key in assigned:
                continue
            if any(key.startswith(prefix) for prefix in prefixes):
                matched.append(path)
                assigned.add(key)

        # 按字符数切块，保证单个子代理的阅读量可控
        chunks: list[list[pathlib.Path]] = [[]]
        size = 0
        for path in matched:
            length = len(path.read_text(encoding="utf-8", errors="replace"))
            if chunks[-1] and (size + length > MAX_CHARS_PER_CHUNK
                               or len(chunks[-1]) >= MAX_FILES_PER_CHUNK):
                chunks.append([])
                size = 0
            chunks[-1].append(path)
            size += length

        for index, chunk in enumerate(chunks, start=1):
            if not chunk:
                continue
            total = sum(len(p.read_text(encoding="utf-8", errors="replace")) for p in chunk)
            sections.append({
                "id": f"{out_name.split('/')[-1]}-{index}",
                "chapter": title,
                "output": out_name,
                "part": index,
                "parts": len(chunks),
                "mode": mode,
                "chars": total,
                "files": [p.as_posix() for p in chunk],
            })

    return {"sections": sections, "unassigned": sorted(
        p.as_posix() for p in files if p.as_posix() not in assigned
    )}


if __name__ == "__main__":
    plan = collect()
    out = pathlib.Path("interview/_plan.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'专题':<26}{'块':>4}{'文件':>5}{'字数(万)':>10}")
    print("-" * 52)
    chapters: dict[str, list[dict]] = {}
    for section in plan["sections"]:
        chapters.setdefault(section["chapter"], []).append(section)
    for chapter, items in chapters.items():
        files = sum(len(i["files"]) for i in items)
        chars = sum(i["chars"] for i in items)
        print(f"{chapter:<26}{len(items):>4}{files:>5}{chars / 10000:>10.1f}")

    total_files = sum(len(i["files"]) for i in plan["sections"])
    total_chars = sum(i["chars"] for i in plan["sections"])
    print("-" * 52)
    print(f"{'合计':<26}{len(plan['sections']):>4}{total_files:>5}{total_chars / 10000:>10.1f}")
    if plan["unassigned"]:
        print(f"\n未分配 {len(plan['unassigned'])} 篇：")
        for name in plan["unassigned"]:
            print("   ", name)
