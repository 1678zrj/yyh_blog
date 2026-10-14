"""爬取高质量八股资料，落到 interview/raw/（本地参考，永不进公开仓库）。

设计（v2，按真实仓库结构重写）：
  1. **部分克隆**：`git clone --depth 1 --filter=blob:none --no-checkout`，先不下载任何文件内容。
  2. **在 Python 里列全量路径**（`git ls-tree -r -z`）并按扩展名 + 包含/排除规则筛选——
     不依赖 git 稀疏规则（上一版就是被它坑了：规则写错会静默抓到 0 个文件）。
  3. **批量检出**筛选出的路径；对 **Windows 非法文件名**（尾部空格/点、冒号等）单独走
     `git show HEAD:<path>` 通道，避免整仓库因一个坏文件名检出失败。
  4. 版权：原文只作本地学习参考；许可证为 Apache-2.0 / CC-BY 的可派生，无许可证的只参考、
     写进八股文库时一律原创综合 + 标注来源。

用法：
    .venv\\Scripts\\python.exe interview\\tools\\crawl_sources.py --list
    .venv\\Scripts\\python.exe interview\\tools\\crawl_sources.py [--only id ...] [--refresh]
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "interview" / "raw"
CACHE = ROOT / ".build-cache" / "crawl"

MD_SUFFIXES = (".md", ".markdown")

SOURCES = [
    {
        "id": "javaguide",
        "repo": "Snailclimb/JavaGuide",
        "license": "Apache-2.0",
        "note": "计算机基础 / 数据库 / 分布式 / 系统设计 / AI 工程（语言无关部分质量很高）",
        "include": ["docs/cs-basics/", "docs/database/", "docs/distributed-system/",
                    "docs/system-design/", "docs/high-availability/",
                    "docs/high-performance/", "docs/ai/", "docs/ai-coding/",
                    "docs/interview-preparation/"],
    },
    {
        "id": "llm-action",
        "repo": "liguodongiot/llm-action",
        "license": "Apache-2.0",
        "note": "大模型工程化实战：微调 / 量化 / 推理部署 / AI Infra",
    },
    {
        "id": "llm-everybody",
        "repo": "luhengshiwo/LLMForEverybody",
        "license": "Apache-2.0",
        "note": "大模型面试速成，章节化组织",
        "exclude": ["docs/en/", "docs/ru/", "draft/"],      # 英俄语翻译版不要
    },
    {
        "id": "llm-note",
        "repo": "wdndev/llm_interview_note",
        "license": "",
        "note": "大模型应用/算法岗面试笔记，与你目标岗位最对口（仅本地参考）",
    },
    {
        "id": "cs-notes",
        "repo": "CyC2018/CS-Notes",
        "license": "",
        "note": "计算机基础经典笔记：算法 / 操作系统 / 网络 / 数据库（仅本地参考）",
        "include": ["notes/", "README.md"],       # 正文在 notes/，不是 docs/
    },
    {
        "id": "system-design",
        "repo": "donnemartin/system-design-primer",
        "license": "CC-BY-4.0",
        "note": "系统设计入门与范例（英文）",
        "include": ["solutions/", "README-zh-Hans.md"],
    },
    {
        "id": "leetcode-top",
        "repo": "afatcoder/LeetcodeTop",
        "license": "",
        "note": "各大公司高频 LeetCode 题统计（补手撕代码的空白）",
    },
    {
        "id": "python-interview",
        "repo": "taizilongxu/interview_python",
        "license": "",
        "note": "Python 面试题（仅本地参考）",
    },
]

# 专门指向 Agent 八股的免费权威源：付费课多是以这些为底，
# 所以先把底料爬全，再谈要不要买课。
AGENT_SOURCES = [
    {
        "id": "agent-anthropic-cookbook",
        "repo": "anthropics/anthropic-cookbook",
        "license": "MIT",
        "note": "Anthropic 官方 cookbook：工具调用、Agent 模式、子代理、评测",
        "exclude": ["images/"],
    },
    {
        "id": "agent-anthropic-quickstarts",
        "repo": "anthropics/anthropic-quickstarts",
        "license": "MIT",
        "note": "Anthropic 官方 Agent 项目示例（含 computer-use、客服 Agent）",
    },
    {
        "id": "agent-openai-cookbook",
        "repo": "openai/openai-cookbook",
        "license": "MIT",
        "note": "OpenAI 官方 cookbook：function calling、结构化输出、评测、RAG",
        "exclude": ["images/", "examples/vector_databases/"],
    },
    {
        "id": "agent-openai-agents",
        "repo": "openai/openai-agents-python",
        "license": "MIT",
        "note": "OpenAI Agents SDK 文档与示例（handoff / guardrail / tracing）",
    },
    {
        "id": "agent-microsoft-course",
        "repo": "microsoft/ai-agents-for-beginners",
        "license": "MIT",
        "note": "微软官方 18 节课免费 Agent 课程",
        "exclude": ["translations/"],
    },
    {
        "id": "agent-12factor",
        "repo": "humanlayer/12-factor-agents",
        "license": "",
        "note": "Agent 工程十二要素（把 Agent 当软件工程做，面试很吃这套）",
    },
    {
        "id": "agent-hf-course",
        "repo": "huggingface/agents-course",
        "license": "Apache-2.0",
        "note": "Hugging Face 官方 Agent 课程（含评测与实践）",
        "exclude": ["notebooks/"],
    },
    {
        "id": "agent-mcp-spec",
        "repo": "modelcontextprotocol/modelcontextprotocol",
        "license": "",
        "note": "MCP 协议规范与文档（工具/资源/提示、传输、安全）",
    },
    {
        "id": "agent-langgraph-docs",
        "repo": "langchain-ai/langgraph",
        "license": "MIT",
        "note": "LangGraph 文档（状态机、checkpointer、中断恢复、持久化）",
        "include": ["docs/", "README.md"],
    },
    {
        "id": "agent-google-adk",
        "repo": "google/adk-python",
        "license": "Apache-2.0",
        "note": "Google Agent Development Kit 文档",
        "include": ["docs/", "contributing/", "README.md"],
    },
    {
        "id": "agent-autogen",
        "repo": "microsoft/autogen",
        "license": "CC-BY-4.0",
        "note": "AutoGen 多智能体框架文档（会话模式、群聊、工具）",
        "include": ["docs/", "README.md"],
    },
    {
        "id": "agent-crewai",
        "repo": "crewAIInc/crewAI",
        "license": "MIT",
        "note": "CrewAI 多智能体编排文档",
        "include": ["docs/", "README.md"],
    },
    {
        "id": "agent-deepeval",
        "repo": "confident-ai/deepeval",
        "license": "Apache-2.0",
        "note": "LLM/Agent 评测框架文档（指标、合成数据集、护栏）",
        "include": ["docs/", "README.md"],
    },
    {
        "id": "agent-genai-tutorials",
        "repo": "NirDiamant/GenAI_Agents",
        "license": "",
        "note": "50+ Agent 实战教程（从 ReAct 到多智能体、记忆、评测）",
        "exclude": ["images/"],
    },
    {
        "id": "agent-handy-multi-agent",
        "repo": "datawhalechina/handy-multi-agent",
        "license": "",
        "note": "中文多智能体教程（仅本地参考）",
    },
    {
        "id": "agent-llm-universe",
        "repo": "datawhalechina/llm-universe",
        "license": "",
        "note": "中文大模型应用开发教程（含 RAG 与 Agent 章节，仅本地参考）",
    },
    # —— 直接面向「大厂 Agent 岗求职」的仓库（八股 + 面经），最对口 ——
    {
        "id": "agent-zero2agent",
        "repo": "ranxi2001/zero2Agent",
        "license": "MIT",
        "note": "面向大厂 Agent 研发岗：技术路线 + learn-agent-interview 七大模块 + 大厂面经",
    },
    {
        "id": "agent-llm-master",
        "repo": "youngyangyang04/llm-master",
        "license": "MIT",
        "note": "代码随想录作者的 LLM/Agent 全栈路线与面经（含 harness 可观测、框架对比、幻觉控制）",
    },
    {
        "id": "agent-hello-agents",
        "repo": "datawhalechina/hello-agents",
        "license": "",
        "note": "《从零开始构建智能体》教程，含「面试问题总结」专章（仅本地参考）",
        "exclude": ["images/", "docs/"],
    },
    # —— Python 专项：补「Python 并发 / 解释器原理」这块的语料空白 ——
    {
        "id": "python-cpython-internals",
        "repo": "zpoint/CPython-Internals",
        "license": "",
        "note": "CPython 内部原理图解：对象模型、内存管理、GIL、生成器与协程（仅本地参考）",
    },
    {
        "id": "python-whydo",
        "repo": "chinesehuazhou/python-whydo",
        "license": "",
        "note": "《Python 为什么》系列：把语言设计的动机讲透（仅本地参考）",
    },
    {
        "id": "python-guide-cn",
        "repo": "Prodesire/Python-Guide-CN",
        "license": "",
        "note": "Python 最佳实践指南中文版（仅本地参考）",
    },
]

ALL_SOURCES = SOURCES + AGENT_SOURCES

INVALID_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}


def run(args: list[str], cwd: Path | None = None, binary: bool = False):
    return subprocess.run(args, cwd=cwd, capture_output=True,
                          text=not binary, encoding=None if binary else "utf-8",
                          errors=None if binary else "replace")


def windows_safe(path: str) -> bool:
    for part in path.split("/"):
        if part.endswith((" ", ".")) or INVALID_CHARS.search(part):
            return False
        if part.split(".")[0].upper() in RESERVED:
            return False
    return True


def list_paths(repo: Path) -> list[str]:
    result = run(["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"], cwd=repo, binary=True)
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def prepare_clone(source: dict, refresh: bool) -> Path | None:
    if "--no-git" in sys.argv:
        return None            # 直接走 API + raw 通道（git 传输层故障时用，省掉等待超时）
    target = CACHE / source["id"]
    url = f"https://github.com/{source['repo']}.git"
    if (target / ".git").is_dir():
        if refresh:
            run(["git", "fetch", "--depth", "1", "origin"], cwd=target)
            run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=target)
        return target

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = run(["git", "clone", "--depth", "1", "--filter=blob:none",
                  "--no-checkout", url, str(target)])
    if result.returncode != 0:
        print(f"    ✗ 克隆失败：{(result.stderr or '').strip()[:150]}")
        return None
    return target


def wanted(source: dict, paths: list[str]) -> list[str]:
    include = source.get("include") or []
    exclude = source.get("exclude") or []
    picked = []
    for path in paths:
        if not path.lower().endswith(MD_SUFFIXES):
            continue
        if include and not any(path.startswith(prefix) for prefix in include):
            continue
        if any(path.startswith(prefix) for prefix in exclude):
            continue
        picked.append(path)
    return picked


def github_api(path: str, timeout: int = 25):
    """调 GitHub API（带重试）。"""
    import urllib.request

    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"User-Agent": "personal-study-crawler/1.2 (local, single user)",
                 "Accept": "application/vnd.github+json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            time.sleep(2.0 * (attempt + 1))
    return None


def list_paths_via_api(source: dict) -> tuple[list[str], str]:
    """不依赖 git：用 GitHub API 列出仓库全部路径，返回 (路径, 树 SHA)。

    为什么需要：git 的 HTTPS 传输层有时连不上（`Failed to connect to github.com port 443`），
    而 Python 的 HTTPS 一切正常。此时只能走 API + raw 这条路。
    结果缓存到 .build-cache，避免反复消耗 API 配额（未认证只有 60 次/小时）。
    """
    cache_file = CACHE / f"{source['id']}.paths.json"
    if cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached["paths"], cached["sha"]
        except (json.JSONDecodeError, KeyError):
            pass

    info = github_api(f"/repos/{source['repo']}")
    if not info:
        return [], ""
    branch = info.get("default_branch", "main")
    tree = github_api(f"/repos/{source['repo']}/git/trees/{branch}?recursive=1")
    if not tree:
        return [], ""
    paths = [node["path"] for node in tree.get("tree", []) if node.get("type") == "blob"]
    sha = tree.get("sha", "")
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"paths": paths, "sha": sha}, ensure_ascii=False),
                          encoding="utf-8")
    return paths, sha


def fetch_via_raw(source: dict, commit: str, out_dir: Path, missing: list[str],
                  workers: int = 4) -> tuple[int, int]:
    """用 raw.githubusercontent.com 取文件（配合上面的 API 通道可完全不用 git）。

    好处：不受 git 传输层故障影响；仓库里有没有 Windows 非法文件名都无所谓；
    只下 markdown，不下配图。并发 4，带退避重试，对 CDN 保持礼貌。
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor

    if not commit:
        return 0, 0
    base = f"https://raw.githubusercontent.com/{source['repo']}/{commit}/"
    headers = {"User-Agent": "personal-study-crawler/1.2 (single user, local notes)"}

    def fetch(path: str) -> tuple[str, str] | None:
        url = base + urllib.parse.quote(path)
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=30) as response:
                    return path, response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 404):        # 不存在或不给访问，重试无意义
                    return None
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
        return None

    files = chars = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(fetch, missing):
            if not result:
                continue
            path, text = result
            target = out_dir / sanitize(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            files += 1
            chars += len(text)
    return files, chars


def sanitize(path: str) -> str:
    """把仓库路径变成 Windows 安全、且不会互相覆盖的本地文件名。"""
    parts = []
    for part in path.split("/"):
        cleaned = re.sub(r'[<>:"|?*\x00-\x1f]', "_", part).rstrip(" .")
        if not cleaned:
            cleaned = "_"
        if cleaned.split(".")[0].upper() in RESERVED:
            cleaned = "_" + cleaned
        parts.append(cleaned)
    return "/".join(parts)


def fetch_via_tarball(source: dict, out_dir: Path) -> tuple[int, int, str]:
    """从 codeload 拉仓库压缩包，流式提取其中的 markdown。

    第三条通道，价值在于**既不依赖 git 传输层、也不消耗 GitHub API 配额**：
      - git 通道：传输层偶发连不上（Failed to connect to github.com port 443）
      - API 通道：未认证只有 60 次/小时，多爬几个仓库就会被限流
      - tarball 通道：走 codeload.github.com（另一个服务，限制宽松），用 tarfile 流式读，
        只把 markdown 写盘，图片直接跳过，所以磁盘占用和 raw 通道一样小。
    代价是要把压缩包整个下载下来（按仓库体积 1MB~300MB 不等）。
    """
    import tarfile
    import urllib.request

    include = source.get("include") or []
    exclude = source.get("exclude") or []
    headers = {"User-Agent": "personal-study-crawler/1.2 (single user, local notes)"}

    for branch in ("main", "master"):
        url = f"https://codeload.github.com/{source['repo']}/tar.gz/refs/heads/{branch}"
        try:
            response = urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=120)
        except Exception:
            continue

        files = chars = 0
        with tarfile.open(fileobj=response, mode="r|gz") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                # tarball 里的路径形如 <repo>-<branch>/<真实路径>
                parts = member.name.split("/", 1)
                if len(parts) < 2:
                    continue
                path = parts[1]
                if not path.lower().endswith(MD_SUFFIXES):
                    continue
                if include and not any(path.startswith(prefix) for prefix in include):
                    continue
                if any(path.startswith(prefix) for prefix in exclude):
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", "replace")
                if len(text.strip()) < 200:
                    continue
                target = out_dir / sanitize(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="\n")
                files += 1
                chars += len(text)
        return files, chars, branch
    return 0, 0, ""


def materialize(repo: Path, picked: list[str], out_dir: Path) -> tuple[int, int]:
    """把选中的文件取出来落到 out_dir，返回 (文件数, 字符数)。

    做法：关掉稀疏过滤，然后**按路径分批显式检出**。
    比 sparse-checkout 的模式匹配可靠得多——非 cone 模式对「单文件模式」要求前导斜杠，
    而且仓库里只要有一个 Windows 非法文件名（例如尾部空格），整次 checkout 就会 exit 128。
    """
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    safe = [p for p in picked if windows_safe(p)]
    unsafe = [p for p in picked if not windows_safe(p)]

    run(["git", "sparse-checkout", "disable"], cwd=repo)     # 已初始化过就关掉，未初始化会报错，忽略
    for start in range(0, len(safe), 200):
        chunk = safe[start:start + 200]
        result = run(["git", "checkout", "HEAD", "--"] + chunk, cwd=repo)
        if result.returncode != 0:
            print(f"    ! 检出 {len(chunk)} 个路径失败：{(result.stderr or '').strip()[:120]}")

    files = chars = 0
    for path in safe:
        source_file = repo / path
        if not source_file.is_file():
            continue
        text = source_file.read_text(encoding="utf-8", errors="replace")
        target = out_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        files += 1
        chars += len(text)

    # Windows 非法文件名（例如尾部空格）单独取内容，改成安全文件名
    for path in unsafe:
        result = run(["git", "show", f"HEAD:{path}"], cwd=repo, binary=True)
        if result.returncode != 0 or not result.stdout:
            continue
        text = result.stdout.decode("utf-8", "replace")
        safe_name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", path).strip().replace("/", "__")
        target = out_dir / f"{safe_name}.md"
        target.write_text(text, encoding="utf-8", newline="\n")
        files += 1
        chars += len(text)

    return files, chars


TOPIC_KEYWORDS = {
    "数据库 / MySQL": ["mysql", "innodb", "索引", "事务", "mvcc", "sql", "数据库"],
    "Redis": ["redis", "缓存", "分布式锁"],
    "计算机网络": ["tcp", "http", "网络", "https", "dns", "socket"],
    "操作系统": ["操作系统", "进程", "线程", "内存", "死锁", "io 多路", "epoll"],
    "算法 / 手撕": ["leetcode", "算法", "题解", "动态规划", "二叉树", "排序", "链表"],
    "Java / JVM": ["java", "jvm", "gc", "spring"],
    "Python": ["python", "gIL", "装饰器", "生成器", "asyncio"],
    "大模型 / LLM": ["大模型", "llm", "transformer", "注意力", "token", "推理"],
    "RAG / 检索": ["rag", "向量", "embedding", "检索", "召回"],
    "Agent / 工具调用": ["agent", "工具调用", "langchain", "langgraph", "mcp", "function call"],
    "微调 / 训练": ["微调", "lora", "sft", "rlhf", "dpo", "量化", "训练"],
    "部署 / 工程化": ["部署", "docker", "k8s", "推理加速", "vllm", "并发", "性能"],
    "系统设计": ["系统设计", "架构", "高可用", "分布式", "限流", "消息队列"],
}


def rebuild_manifest() -> list[dict]:
    """从磁盘实况重建清单（而不是只记录本次跑过的来源），并并入站点爬取结果。"""
    entries = []
    for source in ALL_SOURCES:
        directory = RAW / source["id"]
        files = [p for p in directory.rglob("*") if p.is_file()] if directory.is_dir() else []
        chars = sum(len(p.read_text(encoding="utf-8", errors="replace")) for p in files)
        cache = CACHE / source["id"]
        commit = run(["git", "rev-parse", "HEAD"], cwd=cache).stdout.strip()[:12] if (cache / ".git").is_dir() else ""
        entries.append({
            "id": source["id"], "repo": source["repo"], "license": source["license"],
            "note": source["note"], "commit": commit, "files": len(files), "chars": chars,
            "fetched_at": datetime.now().isoformat(timespec="seconds"), "ok": len(files) > 0,
        })

    # 站点爬取的结果单独记在 _sites.json，这里并进总清单
    sites_file = RAW / "_sites.json"
    if sites_file.is_file():
        try:
            for item in json.loads(sites_file.read_text(encoding="utf-8")):
                entries.append({
                    "id": item["id"], "repo": item["repo"], "license": item.get("license", ""),
                    "note": item.get("note", ""), "commit": "", "files": item.get("files", 0),
                    "chars": item.get("chars", 0), "fetched_at": item.get("fetched_at", ""),
                    "ok": bool(item.get("files")),
                })
        except (json.JSONDecodeError, KeyError):
            pass

    (RAW / "_manifest.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entries


def build_inventory(entries: list[dict]) -> Path:
    """生成语料盘点报告：每个来源有什么、整体覆盖哪些主题。"""
    lines = [
        "# 爬取语料盘点",
        "",
        "> 这些是从开源仓库/站点抓下来的**原始材料**，只作本地参考（`interview/` 在 .gitignore 内）。",
        "> 写进八股文库时是原创综合 + 标注来源，不整段搬运。",
        "",
        "| 来源 | 篇数 | 字数(万) | 许可证 | 可派生 | 说明 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in sorted(entries, key=lambda i: -i["chars"]):
        derivable = "✅" if item["license"] in ("Apache-2.0", "MIT", "CC-BY-4.0") else "仅参考"
        lines.append(f"| {item['id']} | {item['files']} | {item['chars'] / 10000:.1f} | "
                     f"{item['license'] or '未声明'} | {derivable} | {item['note']} |")
    lines += [
        f"| **合计** | **{sum(i['files'] for i in entries)}** | "
        f"**{sum(i['chars'] for i in entries) / 10000:.1f}** | | | |",
        "",
        "## 各来源的目录概览",
        "",
    ]
    for item in sorted(entries, key=lambda i: -i["chars"]):
        directory = RAW / item["id"]
        if not directory.is_dir():
            continue
        counter: dict[str, int] = {}
        for path in directory.rglob("*.md"):
            key = path.relative_to(directory).parts[0] if len(path.relative_to(directory).parts) > 1 else "（根目录）"
            counter[key] = counter.get(key, 0) + 1
        top = "、".join(f"{k}({v})" for k, v in sorted(counter.items(), key=lambda kv: -kv[1])[:6])
        lines.append(f"- **{item['id']}**：{top}")

    lines += ["", "## 主题覆盖（按「来源内相对路径 + 文件开头」关键词统计篇数）", "",
              "> 只按来源内的相对路径和文件开头匹配，避免工作目录名（如 python\\my_blog）"
              "或仓库名把所有文件都算进来。", "",
              "| 主题 | 命中篇数 |", "| --- | --- |"]
    corpus: list[str] = []
    for path in RAW.rglob("*.md"):
        relative = path.relative_to(RAW)
        if len(relative.parts) < 2:          # 跳过 _inventory.md 这类根目录文件
            continue
        inner = "/".join(relative.parts[1:]).lower()
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:300].lower()
        except OSError:
            head = ""
        corpus.append(inner + "\n" + head)

    for topic, keywords in TOPIC_KEYWORDS.items():
        count = sum(1 for text in corpus if any(k.lower() in text for k in keywords))
        lines.append(f"| {topic} | {count} |")
    lines.append(f"| （语料总篇数） | {len(corpus)} |")

    out = RAW / "_inventory.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return out


def main() -> int:
    if "--inventory" in sys.argv or "--rebuild-manifest" in sys.argv:
        entries = rebuild_manifest()
        out = build_inventory(entries)
        print(f"{'来源':<18}{'篇数':>6}{'字数(万)':>10}  许可证")
        print("-" * 56)
        for item in sorted(entries, key=lambda i: -i["chars"]):
            print(f"{item['id']:<18}{item['files']:>6}{item['chars'] / 10000:>10.1f}  "
                  f"{item['license'] or '未声明'}")
        print("-" * 56)
        print(f"{'合计':<18}{sum(i['files'] for i in entries):>6}"
              f"{sum(i['chars'] for i in entries) / 10000:>10.1f}")
        print(f"已写出：{out.relative_to(ROOT)}")
        return 0

    if "--list" in sys.argv:
        print(f"{'id':<18}{'许可证':<14}说明")
        print("-" * 100)
        for source in ALL_SOURCES:
            print(f"{source['id']:<18}{(source['license'] or '无（仅参考）'):<14}{source['note']}")
        return 0

    only = []
    for index, arg in enumerate(sys.argv):
        if arg == "--only":
            only = sys.argv[index + 1:]
    refresh = "--refresh" in sys.argv
    selected = [s for s in ALL_SOURCES if not only or s["id"] in only]

    RAW.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    started = time.time()

    print(f"{'来源':<20}{'markdown':>9}{'字数(万)':>10}  许可证")
    print("-" * 64)
    for source in selected:
        repo = prepare_clone(source, refresh)
        commit = ""
        tree_sha = ""
        if repo is not None:
            paths = wanted(source, list_paths(repo))
            files, chars = materialize(repo, paths, RAW / source["id"])
            commit = run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
            if not commit:
                tree_sha = list_paths_via_api(source)[1]
        else:
            # git 连不上：走 API + raw 通道；API 被限流时再退到 tarball 通道
            all_paths, tree_sha = list_paths_via_api(source)
            if not all_paths:
                out_dir = RAW / source["id"]
                out_dir.mkdir(parents=True, exist_ok=True)
                files, chars, branch = fetch_via_tarball(source, out_dir)
                if files:
                    print(f"    · API 不可用（多半被限流），改用 tarball 通道（{branch}）：{files} 篇")
                    manifest.append({
                        "id": source["id"], "repo": source["repo"],
                        "license": source["license"], "note": source["note"],
                        "commit": branch, "files": files, "chars": chars,
                        "fetched_at": datetime.now().isoformat(timespec="seconds"), "ok": True,
                    })
                    print(f"{source['id']:<20}{files:>9}{chars / 10000:>10.1f}  "
                          f"{source['license'] or '未声明'}")
                    continue
                print(f"    ✗ 三条通道都失败，跳过")
                manifest.append({**source, "ok": False})
                continue
            paths = wanted(source, all_paths)
            files = chars = 0

        # 无论走哪条路，最后都核对一遍：缺的文件用 raw 补齐
        out_dir = RAW / source["id"]
        got = {p.relative_to(out_dir).as_posix()
               for p in out_dir.rglob("*") if p.is_file()} if out_dir.is_dir() else set()
        missing = sorted(p for p in paths if sanitize(p) not in got)
        if missing:
            extra_files, extra_chars = fetch_via_raw(
                source, commit or tree_sha, out_dir, missing)
            files += extra_files
            chars += extra_chars
            channel = "raw 补齐" if repo is not None else "API+raw 通道"
            print(f"    · {channel}：{len(missing)} 篇 → 成功 {extra_files}")

        commit = (commit or tree_sha)[:12]
        manifest.append({
            "id": source["id"], "repo": source["repo"], "license": source["license"],
            "note": source["note"], "commit": commit, "files": files, "chars": chars,
            "fetched_at": datetime.now().isoformat(timespec="seconds"), "ok": True,
        })
        print(f"{source['id']:<18}{files:>9}{chars / 10000:>10.1f}  {source['license'] or '无（仅参考）'}")

    (RAW / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ok = [item for item in manifest if item.get("ok")]
    print("-" * 62)
    print(f"完成 {len(ok)}/{len(selected)} 个来源，共 {sum(i['files'] for i in ok)} 篇 / "
          f"{sum(i['chars'] for i in ok) / 10000:.1f} 万字，耗时 {time.time() - started:.0f} 秒")
    print(f"原文落在 {RAW.relative_to(ROOT)}（在 .gitignore 内，不会进公开仓库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
