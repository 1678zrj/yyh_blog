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
    "notes_dirs": ["record", "typeora"],  # 原始笔记目录（相对仓库根目录），可以有多个
    "notes_extensions": [".md", ".txt"],  # 参与发布的笔记后缀（.txt 按 Markdown 处理）
    "dates_file": "note-dates.json",  # 记录「文件 -> 日期」的清单，需要提交到 git
    "cache_dir": ".build-cache",    # 构建缓存（代码语言识别结果），已在 .gitignore 中
    "exclude": [],                  # 要跳过的路径 glob，例如 ["record/私密/*"]
    "dedupe": True,                 # 内容完全相同的笔记只保留一篇
    "guess_code_language": True,    # 为没有标注语言的代码块自动识别语言
    "localize_images": True,        # 把本地绝对路径引用的图片收进仓库并改写链接
    "images_dir": "assets/images/notes",
    "image_max_width": 1500,        # 截图超过这个宽度会等比缩小
    "image_min_bytes": 400_000,     # 小于这个体积的图片不做处理
    "image_max_error": 0.02,        # 压缩后允许的最大画质误差，超了就保持原图
    "heading_base_level": 2,        # 正文最高级标题统一成几级（页面标题已经占了一级）
    "max_description": 150,         # 列表页摘要最大字数
    "excerpt_paragraphs": 2,        # 列表页最多展示正文的前几个段落
    "chars_per_minute": 400,        # 估算阅读速度（字符/分钟）
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


EXCERPT_MARKER = "<!-- more -->"


def insert_excerpt(blocks, paragraphs: int) -> None:
    """在正文前几个段落之后插入 ``<!-- more -->``，让列表页只展示摘要。

    没有这个标记时 mkdocs-material 会把整篇文章渲染进列表页，
    列表会又长又慢。
    """
    for index, (is_code, lang, lines) in enumerate(blocks):
        if is_code:
            continue

        ends: list[int] = []
        inside = False
        for position, line in enumerate(lines):
            if line.strip():
                inside = True
            elif inside:
                ends.append(position)
                inside = False
        if inside:
            ends.append(len(lines))
        if not ends:
            continue

        at = ends[min(paragraphs, len(ends)) - 1]
        if at < len(lines) and not lines[at].strip():
            new_lines = lines[:at] + ["", EXCERPT_MARKER] + lines[at:]
        else:
            new_lines = lines[:at] + ["", EXCERPT_MARKER, ""] + lines[at:]
        blocks[index] = (is_code, lang, new_lines)
        return


def estimate_readtime(body: str, chars_per_minute: int) -> int:
    """估算阅读时长（分钟），代码块不计入。"""
    text = re.sub(r"^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$", "", body, flags=re.S | re.M)
    chars = len(re.sub(r"\s+", "", text))
    return max(1, round(chars / max(chars_per_minute, 1)))


IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\s*\))")


def _image_error(left, right) -> float:
    """两张图之间的归一化均方根误差，用来判断压缩后画质掉得厉不厉害。"""
    from PIL import ImageChops, ImageStat

    diff = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
    return ImageStat.Stat(diff).rms[0] / 255.0


def optimize_image(path: Path, max_width: int, min_bytes: int, max_error: float) -> bool:
    """压缩过大的截图：等比缩小 + 量化调色板，画质误差超限就放弃。

    只在图片刚被复制进仓库时调用一次；已经处理过的图片体积会降下来，
    再跑也不会重复处理（配合 ``--optimize-images`` 可以批量补做）。
    """
    if path.suffix.lower() in {".gif", ".svg", ".webp"}:
        return False
    try:
        if path.stat().st_size < min_bytes:
            return False
    except OSError:
        return False

    try:
        import io

        from PIL import Image

        with Image.open(path) as opened:
            opened.load()
            image = opened.copy()

        if image.width > max_width:
            ratio = max_width / image.width
            image = image.resize((max_width, max(1, round(image.height * ratio))), Image.LANCZOS)

        best = image
        if image.mode in {"RGB", "RGBA"}:
            opaque = image.mode == "RGB" or image.getchannel("A").getextrema()[0] == 255
            if opaque:
                rgb = image.convert("RGB")
                # 注意：这里要保持 P 模式（调色板 PNG），转回 RGB 会把体积优势丢掉
                quantized = rgb.quantize(colors=256, method=Image.MEDIANCUT)
                if _image_error(rgb, quantized) <= max_error:
                    best = quantized

        buffer = io.BytesIO()
        best.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
        if len(data) >= path.stat().st_size:
            return False
        path.write_bytes(data)
    except Exception:
        return False
    return True

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
                optimize_image(
                    dest,
                    settings["image_max_width"],
                    settings["image_min_bytes"],
                    settings["image_max_error"],
                )

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


def collect_notes(notes_dirs: list[Path], root: Path, docs_dir: Path, settings: dict, cache: dict):
    """遍历所有笔记目录，产出文章列表。

    多个笔记目录会合并到同一套分类里：分类名取笔记目录下的第一层目录名，
    大小写不同视为同一个分类（例如 FastAPI 与 fastapi 合并）。
    """
    dates_path = root / settings["dates_file"]
    dates: dict = load_json(dates_path, {})
    if not isinstance(dates, dict):
        dates = {}

    canonical_categories: dict[str, str] = {}
    seen_hashes: dict[str, str] = {}
    notes, skipped_empty, skipped_dup, skipped_exclude = [], [], [], []
    missing_images: list[str] = []
    fresh_dates: dict[str, str] = {}
    extensions = tuple(settings["notes_extensions"])

    def canonical(name: str) -> str:
        return canonical_categories.setdefault(name.casefold(), name)

    for notes_dir in notes_dirs:
        if not notes_dir.is_dir():
            print(f"  ! 跳过不存在的笔记目录：{notes_dir}")
            continue

        # 排序键同时忽略大小写并保留原串，保证在不同操作系统上结果一致
        candidates = sorted(
            (path for path in notes_dir.rglob("*") if path.suffix.lower() in extensions),
            key=lambda item: (item.as_posix().casefold(), item.as_posix()),
        )

        for path in candidates:
            rel = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, pattern) for pattern in settings["exclude"]):
                skipped_exclude.append(rel)
                continue

            raw = read_text(path)
            if not raw.strip():
                skipped_empty.append(rel)
                continue
            body = raw

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
            category = canonical(parts[0]) if len(parts) > 1 else "未分类"
            tags = list(dict.fromkeys(parts[1:-1]))  # 更深层的目录名作为标签

            blocks = iter_blocks(body)
            normalize_headings(blocks, settings["heading_base_level"])
            if settings["guess_code_language"]:
                fill_code_languages(blocks, cache)
            if settings["localize_images"]:
                missing_images.extend(
                    localize_images(blocks, path, docs_dir, f"blog/posts/{slugify(category)}", settings)
                )
            insert_excerpt(blocks, settings["excerpt_paragraphs"])

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
                    "readtime": estimate_readtime(body, settings["chars_per_minute"]),
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


def write_text_if_changed(path: Path, text: str) -> bool:
    """只在内容真的变了才写文件，返回是否写了。

    这一点很关键：mkdocs serve 监听的是 docs/ 目录，
    如果每次构建都把同样的内容重写一遍，就会不断触发新的构建，陷入死循环。
    """
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except (OSError, UnicodeDecodeError):
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def write_posts(docs_dir: Path, notes: list[dict], settings: dict):
    """写文章文件，返回 ``(链接表, 新写入篇数, 清理篇数)``。"""
    posts_root = docs_dir / "blog" / "posts"
    posts_root.mkdir(parents=True, exist_ok=True)

    urls: dict[str, str] = {}
    expected: set[Path] = set()
    used: set[str] = set()
    written = 0

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
        expected.add(target)

        meta = {
            "title": note["title"],
            # 用 date 对象而不是字符串：mkdocs-material 要求这里是真正的日期类型
            "date": date.fromisoformat(note["date"]),
            "slug": key,
            "categories": [note["category"]],
            "readtime": note["readtime"],
        }
        if note["tags"]:
            meta["tags"] = note["tags"]
        if note["description"]:
            meta["description"] = note["description"]

        content = yaml_front_matter(meta) + "\n" + note["body"].strip() + "\n"
        if write_text_if_changed(target, content):
            written += 1
        urls[note["rel"]] = f"blog/{key}/"

    # 清掉已经不存在（被删除或改名）的旧文章
    removed = 0
    for stale in posts_root.rglob("*.md"):
        if stale not in expected:
            stale.unlink()
            removed += 1
    for directory in sorted(
        (item for item in posts_root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()

    return urls, written, removed


def write_home(docs_dir: Path, notes: list[dict], urls: dict[str, str], settings: dict):
    """生成首页：数据概览 + 最新笔记 + 分类胶囊 + 年度归档。"""
    ordered = sorted(notes, key=lambda item: (item["date"], item["rel"]), reverse=True)
    latest = ordered[: settings["home_latest"]]

    counts: dict[str, int] = {}
    for note in notes:
        counts[note["category"]] = counts.get(note["category"], 0) + 1
    categories = sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    dates = [note["date"] for note in notes]
    years: dict[str, int] = {}
    for value in dates:
        years[value[:4]] = years.get(value[:4], 0) + 1

    parts = [
        "<!-- 此文件由 scripts/gen_blog.py 生成，请勿直接修改 -->",
        "",
        '<div class="home-hero" markdown>',
        "",
        '<p class="home-hero__eyebrow">BACKEND / AI ENGINEERING NOTES</p>',
        "",
        "# 技术笔记",
        "",
        "后端与 AI 应用开发的工程笔记：把踩过的坑、读过的源码、做过的取舍，"
        "整理成可以直接复用的问答。",
        "",
        '<div class="home-stats">',
        f'  <div class="home-stat"><strong>{len(notes)}</strong><span>篇笔记</span></div>',
        f'  <div class="home-stat"><strong>{len(categories)}</strong><span>个分类</span></div>',
        f'  <div class="home-stat"><strong>{len(years)}</strong><span>个年度</span></div>',
        f'  <div class="home-stat"><strong>{min(dates)[:4]}</strong><span>年起持续更新</span></div>',
        "</div>",
        "",
        '<div class="home-actions">',
        '[浏览全部笔记 :material-arrow-right:](blog/index.md){ .md-button .md-button--primary }',
        "[关于我](about.md){ .md-button }",
        "</div>",
        "",
        "</div>",
        "",
        "## 最新笔记",
        "",
        '<div class="post-grid">',
    ]

    for note in latest:
        url = urls[note["rel"]]
        parts += [
            '<a class="post-card" href="%s">' % url,
            '  <div class="post-card__meta">',
            f'    <span class="chip chip--date">{note["date"]}</span>',
            f'    <span class="chip chip--cat">{note["category"]}</span>',
            f'    <span class="chip">{note["readtime"]} 分钟</span>',
            "  </div>",
            f'  <div class="post-card__title">{note["title"]}</div>',
            f'  <div class="post-card__desc">{note["description"]}</div>',
            "</a>",
            "",
        ]
    parts += ["</div>", "", "## 按分类浏览", "", '<div class="chip-grid">']
    for name, count in categories:
        # 这里用裸 HTML，mkdocs 不会重写 .md 链接，所以直接写成最终的目录地址
        parts.append(
            f'<a class="chip chip--category" href="blog/category/{material_slugify(name)}/">'
            f'{name}<span class="chip__count">{count}</span></a>'
        )
    parts += ["</div>", "", "## 按时间浏览", "", '<div class="chip-grid">']
    for year in sorted(years, reverse=True):
        parts.append(
            f'<a class="chip chip--category" href="blog/archive/{year}/">'
            f'{year} 年<span class="chip__count">{years[year]}</span></a>'
        )
    parts.append("</div>")
    parts.append("")

    (docs_dir / "index.md").parent.mkdir(parents=True, exist_ok=True)
    write_text_if_changed(docs_dir / "index.md", "\n".join(parts))

    blog_dir = docs_dir / "blog"
    blog_dir.mkdir(parents=True, exist_ok=True)
    latest_year = max(dates)[:4]
    sample_category = material_slugify(categories[0][0])
    write_text_if_changed(
        blog_dir / "index.md",
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
            "下面是全部笔记的标签索引，每篇都标出了日期、分类和预计阅读时长，点击标题即可打开。\n"
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
    names = settings.get("notes_dirs") or [settings.get("notes_dir", "record")]
    notes_dirs = [root / name for name in names]
    if not any(path.is_dir() for path in notes_dirs):
        raise SystemExit(f"找不到任何笔记目录：{[str(p) for p in notes_dirs]}")

    cache_path = root / settings["cache_dir"] / "code-langs.json"
    cache = load_json(cache_path, {})
    if not isinstance(cache, dict):
        cache = {}

    notes, skipped = collect_notes(notes_dirs, root, docs_dir, settings, cache)
    save_json(cache_path, cache)

    docs_dir.mkdir(parents=True, exist_ok=True)
    urls, written, removed = write_posts(docs_dir, notes, settings)
    write_home(docs_dir, notes, urls, settings)
    write_static_pages(docs_dir)

    summary = f"  · 已同步 {len(notes)} 篇笔记（空文件 {len(skipped['empty'])}、重复 {len(skipped['duplicate'])}、排除 {len(skipped['excluded'])}）"
    if written or removed:
        summary += f"，写入 {written} 篇、清理 {removed} 篇"
    else:
        summary += "，内容无变化"
    print(summary)
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


def optimize_existing_images(root: Path, docs_dir: Path, settings: dict) -> None:
    """批量压缩已经复制进仓库的图片（新增图片在复制时就会自动压缩）。"""
    images_dir = docs_dir / settings["images_dir"].strip("/")
    if not images_dir.is_dir():
        print(f"没有找到图片目录：{images_dir}")
        return

    targets = sorted(path for path in images_dir.rglob("*") if path.is_file())
    before = sum(path.stat().st_size for path in targets)
    changed = 0
    for path in targets:
        if optimize_image(
            path,
            settings["image_max_width"],
            settings["image_min_bytes"],
            settings["image_max_error"],
        ):
            changed += 1
    after = sum(path.stat().st_size for path in targets)
    print(
        f"图片压缩：处理 {changed}/{len(targets)} 张，"
        f"{before / 1048576:.1f}MB -> {after / 1048576:.1f}MB"
    )


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

    if "--optimize-images" in sys.argv:
        optimize_existing_images(root, docs_dir, settings)
        return 0

    names = settings.get("notes_dirs") or [settings.get("notes_dir", "record")]
    print(f"同步 {'、'.join(names)} -> {docs_dir}")
    run_sync(root, docs_dir, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
