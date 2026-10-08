"""把 record/ 目录下的原始问答笔记同步成博客文章。

设计要点
--------
1. ``record/`` 是唯一内容源，本脚本只读，绝不修改原始笔记。
2. 文章日期取自**文件修改时间**，首次同步后写入 ``note-dates.json`` 固定下来。
   这样即使换电脑、或在 GitHub Actions 里重新检出（检出会让修改时间变成当前时间），
   博客日期也不会漂移；同时以后修改老笔记也不会把发布日期改掉。
3. 生成结果写到 ``docs/blog/posts/<分类>/<标题>.md``，带 YAML front matter，
   交给 mkdocs-material 的 blog 插件渲染。

作为 mkdocs 的 hook 使用（``mkdocs.yml`` 里的 ``hooks:``），
也可以直接 ``python scripts/gen_blog.py`` 手动跑一次。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import posixpath
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path, PurePosixPath

# --------------------------------------------------------------------------- #
# 默认配置（可在 mkdocs.yml 的 extra.note_sync 里覆盖）
# --------------------------------------------------------------------------- #

DEFAULTS: dict = {
    "notes_dir": "record",          # 原始笔记目录（相对仓库根目录）
    "dates_file": "note-dates.json",  # 记录「文件 -> 日期」的清单，需要提交到 git
    "cache_dir": ".build-cache",    # 构建缓存（代码语言识别结果），已在 .gitignore 中
    "exclude": [],                  # 要跳过的路径 glob，例如 ["record/私密/*"]
    "dedupe": True,                 # 内容完全相同的笔记只保留一篇
    "guess_code_language": True,    # 为没有标注语言的代码块自动识别语言
    "localize_images": True,        # 把本地绝对路径引用的图片收进仓库并改写链接
    "images_dir": "assets/images/notes",
    "heading_base_level": 2,        # 正文最高级标题统一成几级（页面标题已经占了一级）
    "max_description": 150,         # 列表页摘要最大字数
    "home_latest": 6,               # 首页展示的最新文章数量
    "categories_name": "分类",
}

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([^\s`]*)")

# Pygments 识别出的语言名 -> mkdocs 代码块语言标识；None 表示不接受该识别结果
LEXER_WHITELIST = {
    "Python": "python",
    "Python 3": "python",
    "Python 2": "python",
    "Python Console": "python",
    "Python traceback": "python",
    "JSON": "json",
    "SQL": "sql",
    "SQLite": "sql",
    "MySQL": "sql",
    "PostgreSQL SQL dialect": "sql",
    "Bash": "bash",
    "Shell Session": "bash",
    "Bash Session": "bash",
    "Console": "bash",
    "YAML": "yaml",
    "TOML": "toml",
    "INI": "ini",
    "JavaScript": "javascript",
    "TypeScript": "typescript",
    "HTML": "html",
    "XML": "xml",
    "CSS": "css",
    "Docker": "docker",
    "Dockerfile": "docker",
    "HTTP": "http",
    "Diff": "diff",
    "Makefile": "makefile",
    "Markdown": None,
    "Text only": None,
    "reStructuredText": None,
    "Jinja2": "jinja",
    "Go": "go",
    "Java": "java",
    "Rust": "rust",
    "C": "c",
    "C++": "cpp",
    "Nginx configuration file": "nginx",
}


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #

def read_text(path: Path) -> str:
    """按 UTF-8 读取，去掉 BOM，统一成 LF 换行。"""
    text = path.read_bytes().decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
    return default


def save_json(path: Path, data) -> bool:
    """写 JSON（排序、带缩进），内容没变则不落盘。返回是否真的写了。"""
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def slugify(text: str) -> str:
    """生成 URL 片段：保留中文、字母、数字，其余压成连字符。"""
    slug = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-").lower()
    return slug or "untitled"


def material_slugify(text: str) -> str:
    """分类页链接由 mkdocs-material 自己生成，这里复用它的 slug 规则以免链接对不上。"""
    try:
        from pymdownx.slugs import slugify as pymdownx_slugify

        return pymdownx_slugify(case="lower")(text, "-")
    except ImportError:
        return slugify(text)


def clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def clean_inline(text: str) -> str:
    """去掉行内 markdown 标记，便于生成摘要（保留下划线，避免把 args_schema 这类标识符改坏）。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*`~]", "", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Markdown 结构处理（必须区分代码块内外）
# --------------------------------------------------------------------------- #

def iter_blocks(text: str):
    """把正文切成片段：``(是否代码块, 语言, 行列表)``。"""
    blocks: list[tuple[bool, str, list[str]]] = []
    buf: list[str] = []
    in_code = False
    fence = ""
    lang = ""

    for line in text.splitlines():
        if not in_code:
            match = FENCE_RE.match(line)
            if match:
                if buf:
                    blocks.append((False, "", buf))
                    buf = []
                in_code = True
                fence = match.group(1)[0] * 3
                lang = match.group(2)
            buf.append(line)
            continue

        buf.append(line)
        stripped = line.strip()
        if stripped.startswith(fence) and stripped.rstrip(fence[0]).strip() == "":
            blocks.append((True, lang, buf))
            buf = []
            in_code = False
            fence = ""
            lang = ""

    if buf:
        blocks.append((in_code, lang, buf))
    return blocks


def render_blocks(blocks) -> str:
    return "\n".join("\n".join(lines) for _, _, lines in blocks)


def normalize_headings(blocks, base_level: int):
    """把正文里最高一级标题统一成 ``base_level``，避免和页面标题抢层级。"""
    levels = []
    for is_code, _, lines in blocks:
        if is_code:
            continue
        for line in lines:
            match = re.match(r"^(#{1,6})\s", line)
            if match:
                levels.append(len(match.group(1)))
    if not levels:
        return
    delta = base_level - min(levels)
    if delta == 0:
        return

    for index, (is_code, _, lines) in enumerate(blocks):
        if is_code:
            continue
        new_lines = []
        for line in lines:
            match = re.match(r"^(#{1,6})(\s.*)$", line)
            if match:
                level = max(1, min(6, len(match.group(1)) + delta))
                line = "#" * level + match.group(2)
            new_lines.append(line)
        blocks[index] = (is_code, blocks[index][1], new_lines)


def fingerprint_language(code: str) -> str | None:
    """用简单特征先判断一次，比 Pygments 盲猜更稳。"""
    if re.search(r"^\s*(async def |def |class |import |from \w[\w.]* import |@\w+\()", code, re.M):
        return "python"
    if re.search(r"^\s*(SELECT|INSERT INTO|UPDATE|DELETE FROM|CREATE TABLE|ALTER TABLE)\b", code, re.I | re.M):
        return "sql"
    if re.search(r"^\s*(\$ |>>> |sudo |pip install|npm install|pnpm |docker |git |curl |uvicorn |python -m |export \w+=)", code, re.M):
        return "bash"
    head = code.lstrip()
    if head[:1] in "[{" and re.search(r'"[^"]*"\s*:', code):
        return "json"
    if re.search(r"^[^\s:][^:]*:\s*(\S*)$", code, re.M) and re.search(r"^\s*-\s+\S", code, re.M):
        return "yaml"
    if re.search(r"^<\w+[^>]*>", code, re.M) and re.search(r"</\w+>", code):
        return "html"
    return None


ART_CHARS = "│─┌┐└┘├┤┬┴┼━┃╭╮╯╰→←↑↓⇒⇐↔"


def looks_like_art(code: str) -> bool:
    """判断代码块其实是 ASCII 流程图 / 纯文本示意，这种块不要瞎标语言。"""
    if any(char in code for char in ART_CHARS):
        return True
    alnum = sum(char.isalnum() for char in code)
    return alnum / max(len(code), 1) < 0.45


def guess_language(code: str, cache: dict) -> str | None:
    """识别代码块语言，结果按内容哈希缓存，避免每次构建都重算。"""
    digest = hashlib.md5(code.encode("utf-8")).hexdigest()
    if digest in cache:
        return cache[digest] or None

    lang = fingerprint_language(code)
    if lang is None and not looks_like_art(code):
        try:
            from pygments.lexers import guess_lexer

            if len(code.strip()) >= 12:
                lang = LEXER_WHITELIST.get(guess_lexer(code).name)
        except Exception:  # Pygments 缺失或识别失败都不影响构建
            lang = None

    cache[digest] = lang or ""
    return lang


def fill_code_languages(blocks, cache: dict):
    """给没写语言的代码块补上语言标识，让高亮生效。"""
    for index, (is_code, lang, lines) in enumerate(blocks):
        if not is_code or lang or len(lines) < 2:
            continue
        code = "\n".join(lines[1:-1])
        if not code.strip():
            continue
        guessed = guess_language(code, cache)
        if guessed:
            lines = list(lines)
            lines[0] = FENCE_RE.match(lines[0]).group(1) + guessed
            blocks[index] = (is_code, guessed, lines)


IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\s*\))")


def localize_images(blocks, source: Path, docs_dir: Path, post_rel_dir: str, settings: dict) -> list[str]:
    """把笔记里用本地绝对路径引用的图片复制进仓库，并改成能发布的相对路径。

    返回仍然找不到的图片引用列表（这些在网页上会是坏图）。
    """
    images_dir = settings["images_dir"].strip("/")
    target_dir = docs_dir / images_dir
    missing: list[str] = []

    for index, (is_code, _, lines) in enumerate(blocks):
        if is_code:
            continue

        def replace(match: re.Match) -> str:
            url = match.group(2)
            if url.startswith(("http://", "https://", "data:", "//")):
                return match.group(0)

            raw = url.replace("\\", "/")
            # 文件名只由「原始引用字符串」决定，不掺入本机路径，
            # 这样在 Windows 本地和 Linux CI 上算出的名字完全一致
            digest = hashlib.md5(raw.casefold().encode("utf-8")).hexdigest()[:8]
            dest = target_dir / f"{digest}-{PurePosixPath(raw).name}"

            if not dest.exists():
                candidate = Path(raw)
                if not candidate.is_absolute():
                    candidate = (source.parent / raw).resolve()
                else:
                    candidate = candidate.resolve()

                if candidate == docs_dir or docs_dir in candidate.parents:
                    return match.group(0)      # 已经在 docs/ 里，mkdocs 自己会处理
                if not candidate.is_file():
                    missing.append(f"{source.as_posix()} -> {url}")
                    return match.group(0)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, dest)

            relative = posixpath.relpath(f"{images_dir}/{dest.name}", post_rel_dir)
            return f"{match.group(1)}{relative}{match.group(3)}"

        new_lines = [IMAGE_RE.sub(replace, line) for line in lines]
        blocks[index] = (is_code, blocks[index][1], new_lines)

    return missing


# --------------------------------------------------------------------------- #
# 文章元数据
# --------------------------------------------------------------------------- #

QUESTION_PREFIX = ("问：", "问:", "Q：", "Q:")


def make_description(text: str, limit: int) -> str:
    """优先用笔记里的「问：xxx」，否则取第一段正文，作为列表页摘要。"""
    for line in text.splitlines()[:80]:
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^#{1,6}\s*(.+)$", stripped)
        if match:
            question = match.group(1).strip()
            if question.startswith(QUESTION_PREFIX):
                return clip(clean_inline(question.split("：", 1)[-1] if "：" in question else question), limit)
            break
        break

    buf: list[str] = []
    in_code = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = not in_code
            if buf:
                break
            continue
        if in_code:
            continue
        if not stripped:
            if buf:
                break
            continue
        if stripped[0] in "#>|![" or stripped.startswith(("- ", "* ", "1. ")):
            if buf:
                break
            continue
        buf.append(stripped)
        if sum(len(item) for item in buf) >= limit * 2:
            break
    return clip(clean_inline(" ".join(buf)), limit)


def collect_notes(notes_dir: Path, root: Path, docs_dir: Path, settings: dict, cache: dict):
    """遍历笔记目录，产出文章列表。"""
    dates_path = root / settings["dates_file"]
    dates: dict = load_json(dates_path, {})
    if not isinstance(dates, dict):
        dates = {}

    seen_hashes: dict[str, str] = {}
    notes, skipped_empty, skipped_dup, skipped_exclude = [], [], [], []
    missing_images: list[str] = []
    fresh_dates: dict[str, str] = {}

    # 排序键同时忽略大小写并保留原串，保证在不同操作系统上结果一致
    for path in sorted(notes_dir.rglob("*.md"), key=lambda item: (item.as_posix().casefold(), item.as_posix())):
        rel = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, pattern) for pattern in settings["exclude"]):
            skipped_exclude.append(rel)
            continue

        raw = read_text(path)
        if not raw.strip():
            skipped_empty.append(rel)
            continue

        body = raw
        if body.lstrip().startswith("---"):
            print(f"  ! {rel} 自带 front matter，已保留在正文中")

        if settings["dedupe"]:
            digest = hashlib.md5(body.encode("utf-8")).hexdigest()
            if digest in seen_hashes:
                skipped_dup.append(f"{rel}  (与 {seen_hashes[digest]} 内容相同)")
                continue
            seen_hashes[digest] = rel

        # 日期：优先取清单里已固定的日期，否则用文件修改时间
        date = dates.get(rel)
        if not date:
            date = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        fresh_dates[rel] = date

        relative = path.relative_to(notes_dir)
        parts = list(relative.parts)
        category = parts[0] if len(parts) > 1 else "未分类"
        tags = [slug for slug in parts[1:-1]]

        blocks = iter_blocks(body)
        normalize_headings(blocks, settings["heading_base_level"])
        if settings["guess_code_language"]:
            fill_code_languages(blocks, cache)
        if settings["localize_images"]:
            missing_images.extend(
                localize_images(blocks, path, docs_dir, f"blog/posts/{slugify(category)}", settings)
            )

        notes.append(
            {
                "rel": rel,
                "date": date,
                "title": path.stem.strip(),
                "category": category,
                "category_slug": slugify(category),
                "tags": tags,
                "slug": slugify(path.stem),
                "description": make_description(body, settings["max_description"]),
                "body": render_blocks(blocks),
            }
        )

    # 固定日期清单：保留仍然存在的文件，剔除已删除的文件
    if save_json(dates_path, fresh_dates):
        print(f"  · 已更新 {settings['dates_file']}（{len(fresh_dates)} 条）")

    return notes, {
        "empty": skipped_empty,
        "duplicate": skipped_dup,
        "excluded": skipped_exclude,
        "missing_images": missing_images,
    }


# --------------------------------------------------------------------------- #
# 生成站点文件
# --------------------------------------------------------------------------- #

def yaml_front_matter(data: dict) -> str:
    import yaml

    dumped = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return "---\n" + dumped + "---\n"


def write_posts(docs_dir: Path, notes: list[dict], settings: dict) -> dict[str, str]:
    """写文章文件，返回 {笔记相对路径: 站点链接}。"""
    posts_root = docs_dir / "blog" / "posts"
    if posts_root.exists():
        shutil.rmtree(posts_root)
    posts_root.mkdir(parents=True, exist_ok=True)

    urls: dict[str, str] = {}
    used: set[str] = set()

    for note in sorted(notes, key=lambda item: (item["rel"].casefold(), item["rel"])):
        folder = note["category_slug"]
        name = note["slug"]
        key = f"{folder}/{name}"
        counter = 2
        while key in used:  # 极端情况下标题+分类重名，加序号兜底
            key = f"{folder}/{name}-{counter}"
            counter += 1
        used.add(key)

        target = posts_root / folder / f"{key.split('/')[-1]}.md"
        target.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "title": note["title"],
            # 用 date 对象而不是字符串：mkdocs-material 要求这里是真正的日期类型
            "date": date.fromisoformat(note["date"]),
            "slug": key,
            "categories": [note["category"]],
        }
        if note["tags"]:
            meta["tags"] = note["tags"]
        if note["description"]:
            meta["description"] = note["description"]

        target.write_text(
            yaml_front_matter(meta) + "\n" + note["body"].strip() + "\n",
            encoding="utf-8",
            newline="\n",
        )
        urls[note["rel"]] = f"blog/{key}/"
    return urls


def write_home(docs_dir: Path, notes: list[dict], urls: dict[str, str], settings: dict):
    """生成首页：一句自我介绍 + 数据 + 分类入口 + 最新文章。"""
    ordered = sorted(notes, key=lambda item: (item["date"], item["rel"]), reverse=True)
    latest = ordered[: settings["home_latest"]]

    counts: dict[str, int] = {}
    for note in notes:
        counts[note["category"]] = counts.get(note["category"], 0) + 1
    categories = sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    dates = [note["date"] for note in notes]
    parts = [
        "<!-- 此文件由 scripts/gen_blog.py 生成，请勿直接修改 -->",
        "",
        '<div class="home-hero" markdown>',
        "",
        "# 技术笔记",
        "",
        "后端与 AI 应用开发的工程笔记：把踩过的坑、读过的源码、做过的取舍，"
        "整理成可以直接复用的问答。",
        "",
        f'<p class="home-stats">共 <strong>{len(notes)}</strong> 篇笔记 · '
        f'<strong>{len(categories)}</strong> 个分类 · '
        f"更新于 {max(dates)}</p>",
        "",
        '[浏览全部笔记 :material-arrow-right:](blog/index.md){ .md-button .md-button--primary }',
        "[关于我](about.md){ .md-button }",
        "",
        "</div>",
        "",
        "## 最新笔记",
        "",
    ]

    for note in latest:
        url = urls[note["rel"]]
        parts += [
            f'<div class="post-card">',
            f'  <div class="post-card__meta">{note["date"]} · {note["category"]}</div>',
            f'  <a class="post-card__title" href="{url}">{note["title"]}</a>',
            f'  <div class="post-card__desc">{note["description"]}</div>',
            "</div>",
            "",
        ]

    parts += ["[:material-archive: 查看全部 %d 篇](blog/index.md)" % len(notes), "", "## 分类", ""]
    for name, count in categories:
        parts.append(f"- [{name}](blog/category/{material_slugify(name)}.md) · {count} 篇")
    parts.append("")

    (docs_dir / "index.md").write_text("\n".join(parts), encoding="utf-8", newline="\n")

    blog_dir = docs_dir / "blog"
    blog_dir.mkdir(parents=True, exist_ok=True)
    latest_year = max(dates)[:4]
    sample_category = material_slugify(categories[0][0])
    (blog_dir / "index.md").write_text(
        "---\n"
        "hide:\n"
        "  - navigation\n"
        "---\n"
        "\n"
        "# 全部笔记\n"
        "\n"
        f"共 {len(notes)} 篇，按时间倒序排列。也可以按 "
        f"[归档](archive/{latest_year}.md) 或 [分类](category/{sample_category}.md) 浏览，"
        "或直接用左上角的搜索框搜关键词。\n",
        encoding="utf-8",
        newline="\n",
    )


def write_static_pages(docs_dir: Path):
    """只在文件不存在时补齐 about / tags / 404，避免覆盖用户自己改的内容。"""
    about = docs_dir / "about.md"
    if not about.exists():
        about.write_text(
            "# 关于我\n"
            "\n"
            "<!-- 请把下面这些占位内容换成你自己的信息，改完提交即可 -->\n"
            "\n"
            "你好，我是 **yyh**，后端 / AI 应用开发工程师。\n"
            "\n"
            "这个站是我个人的技术笔记库：`record/` 目录里每写一篇笔记，站点就自动多一篇文章，"
            "文章日期即笔记文件的修改日期。\n"
            "\n"
            "## 关注方向\n"
            "\n"
            "- Python 后端：FastAPI、SQLModel、Pydantic、Redis\n"
            "- LLM 应用：LangChain、LangGraph、MCP、RAG\n"
            "- 工程实践：任务队列、并发与幂等、可观测性、部署\n"
            "\n"
            "## 联系我\n"
            "\n"
            "- GitHub：<https://github.com/1678zrj>\n"
            "- Email：<2826981905@qq.com>\n",
            encoding="utf-8",
            newline="\n",
        )

    tags = docs_dir / "tags.md"
    if not tags.exists():
        tags.write_text(
            "---\n"
            "hide:\n"
            "  - toc\n"
            "---\n"
            "\n"
            "# 标签\n"
            "\n"
            "下面是全部笔记的标签索引，点击标签即可筛选。\n"
            "\n"
            "<!-- material/tags -->\n",
            encoding="utf-8",
            newline="\n",
        )

    not_found = docs_dir / "404.md"
    if not not_found.exists():
        not_found.write_text(
            "---\n"
            "hide:\n"
            "  - navigation\n"
            "  - toc\n"
            "---\n"
            "\n"
            "# 页面不存在\n"
            "\n"
            "你访问的页面走丢了，[回到首页](index.md) 或 [浏览全部笔记](blog/index.md)。\n",
            encoding="utf-8",
            newline="\n",
        )


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def run_sync(root: Path, docs_dir: Path, settings: dict) -> dict:
    notes_dir = root / settings["notes_dir"]
    if not notes_dir.is_dir():
        raise SystemExit(f"找不到笔记目录：{notes_dir}")

    cache_path = root / settings["cache_dir"] / "code-langs.json"
    cache = load_json(cache_path, {})
    if not isinstance(cache, dict):
        cache = {}

    notes, skipped = collect_notes(notes_dir, root, docs_dir, settings, cache)
    save_json(cache_path, cache)

    docs_dir.mkdir(parents=True, exist_ok=True)
    urls = write_posts(docs_dir, notes, settings)
    write_home(docs_dir, notes, urls, settings)
    write_static_pages(docs_dir)

    print(
        f"  · 已同步 {len(notes)} 篇笔记"
        f"（跳过空文件 {len(skipped['empty'])}、"
        f"重复 {len(skipped['duplicate'])}、"
        f"排除 {len(skipped['excluded'])}）"
    )
    for rel in skipped["duplicate"]:
        print(f"      重复跳过：{rel}")
    if skipped.get("missing_images"):
        print(f"  ! 有 {len(skipped['missing_images'])} 处图片找不到，网页上会是坏图：")
        for item in skipped["missing_images"]:
            print(f"      {item}")

    return {"posts": len(notes), **skipped}


def resolve_settings(config) -> tuple[dict, Path, Path]:
    root = Path(config["config_file_path"]).resolve().parent
    docs_dir = Path(config["docs_dir"]).resolve()
    settings = dict(DEFAULTS)
    settings.update((config.get("extra") or {}).get("note_sync") or {})
    return settings, root, docs_dir


def on_config(config, **kwargs):
    """mkdocs hook：在读取 docs 目录之前先同步笔记。"""
    settings, root, docs_dir = resolve_settings(config)
    run_sync(root, docs_dir, settings)
    return config


def load_mkdocs_config(config_file: Path) -> dict:
    """读取 mkdocs.yml 里的 note_sync / docs_dir 配置。

    mkdocs.yml 用了 ``!!python/name:`` 这类标签，SafeLoader 认不出来；
    这里只关心普通字段，遇到不认识的标签直接忽略。
    """
    import yaml

    class TolerantLoader(yaml.SafeLoader):
        pass

    TolerantLoader.add_multi_constructor(
        "tag:yaml.org,2002:python/name:", lambda loader, suffix, node: None
    )
    return yaml.load(config_file.read_text(encoding="utf-8"), Loader=TolerantLoader) or {}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    config_file = root / "mkdocs.yml"
    if config_file.exists():
        data = load_mkdocs_config(config_file)
        settings = dict(DEFAULTS)
        settings.update((data.get("extra") or {}).get("note_sync") or {})
        docs_dir = root / str(data.get("docs_dir", "docs"))
    else:
        settings = dict(DEFAULTS)
        docs_dir = root / "docs"

    print(f"同步 {settings['notes_dir']} -> {docs_dir}")
    run_sync(root, docs_dir, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
