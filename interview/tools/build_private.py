"""把 interview/ 下的八股文加密成可以公开发布的密文，供网站上的「私密笔记」专区解密阅读。

设计：
- 明文只留在本地（interview/ 已被 .gitignore 忽略），仓库里只有密文。
- 每个页面单独一个密文文件，按需加载，手机上也不会一次性下载几 MB。
- 目录结构与题目索引也各自加密成一个文件：不输入口令，连标题都看不到。
- 密钥：PBKDF2-HMAC-SHA256（60 万次）从口令派生 → AES-256-GCM 加密，每个文件独立随机 IV。
- 浏览器端用 WebCrypto 做同样的运算（见 docs/assets/private.js），
  跨实现校验见 interview/tools/verify_private.mjs。

用法（在仓库根目录）：
    .venv\\Scripts\\python.exe interview\\tools\\build_private.py            # 用已有口令构建
    .venv\\Scripts\\python.exe interview\\tools\\build_private.py --new-passphrase   # 重新生成口令
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
OUT = ROOT / "docs" / "assets" / "private"
PAGES = OUT / "pages"
PASSPHRASE_FILE = INTERVIEW / ".passphrase"
SALT_FILE = INTERVIEW / ".kdf-salt"   # 固定下来，这样内容更新后本机已记住的密钥依然有效
KDF_ITERATIONS = 600_000
MAX_PAGE_CHARS = 28_000        # 单页超过这个字符数就按题目边界切开
QUESTION_RE = re.compile(r"^###\s+(Q\d+\s*[.．、:：].*)$", re.M)
HEADING_RE = re.compile(r"^(#{2,4})\s+(.+?)\s*$", re.M)

# 侧边栏分组：(显示名, 源文件所在子目录，空字符串表示 interview/ 根目录)
GROUPS = [
    ("开始这里", ""),
    # —— 主线：你自己的笔记，永远排前面 ——
    ("项目经历", "project"),
    ("Agent 与大模型", "agent"),
    ("Python 与后端", "backend"),
    ("速查表", "cheatsheet"),
    # —— 补充：机器整理，用来查漏补缺 ——
    ("冲刺 · 面试加分答案", "scoring"),
    ("补充 · Agent 八股", "agent-baguwen"),
    ("补充 · 大厂真题", "agent-real"),
    ("补充 · 经典加强", "deep"),
    ("补充 · 市场题校准", "market"),
    ("补充 · 通用八股", "gap"),
]

LINK_RE = re.compile(r"\]\(([^)\s]+?)\.md(#[^)]*)?\)")


# --------------------------------------------------------------------------- #
# Markdown → HTML（与站点同一套扩展，保证样式一致）
# --------------------------------------------------------------------------- #

def render_markdown(text: str) -> str:
    import markdown
    from material.extensions.emoji import to_svg, twemoji

    # 列表前必须有空行，否则 Python-Markdown 会把列表并进上一段，
    # 页面上就是一大段带 "- " 的密排文字（这是阅读体验差的主因之一）
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from md_fix import fix_markdown

    md = markdown.Markdown(
        extensions=[
            "extra", "toc", "tables", "attr_list", "admonition", "def_list",
            "footnotes", "md_in_html",
            "pymdownx.betterem", "pymdownx.caret", "pymdownx.mark", "pymdownx.tilde",
            "pymdownx.keys", "pymdownx.details", "pymdownx.inlinehilite",
            "pymdownx.superfences", "pymdownx.tasklist",
            "pymdownx.tabbed",
            "pymdownx.highlight",
            "pymdownx.emoji",
        ],
        extension_configs={
            "pymdownx.highlight": {"anchor_linenums": True, "line_spans": "__span"},
            "pymdownx.tabbed": {"alternate_style": True},
            "pymdownx.tasklist": {"custom_checkbox": True},
            "pymdownx.emoji": {"emoji_index": twemoji, "emoji_generator": to_svg},
            "toc": {"permalink": False},
        },
        output_format="html5",
    )
    return md.convert(fix_markdown(text))


# --------------------------------------------------------------------------- #
# 切页
# --------------------------------------------------------------------------- #

def split_pages(text: str) -> list[str]:
    """按题目边界把过长的文档切成多页。"""
    if len(text) <= MAX_PAGE_CHARS:
        return [text]

    # 找出所有题目的起始位置，按累计长度分组
    starts = [match.start() for match in QUESTION_RE.finditer(text)]
    if len(starts) < 2:
        return [text]

    chunks: list[list[int]] = [[]]
    size = 0
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        length = end - start
        if chunks[-1] and size + length > MAX_PAGE_CHARS:
            chunks.append([])
            size = 0
        chunks[-1].append(index)
        size += length

    head = text[: starts[0]]
    pages: list[str] = []
    for position, group in enumerate(chunks):
        first, last = group[0], group[-1]
        begin = starts[first]
        finish = starts[last + 1] if last + 1 < len(starts) else len(text)
        body = text[begin:finish].rstrip()
        prefix = head if position == 0 else ""
        pages.append((prefix + body).strip())
    return pages


def page_title(chapter: str, index: int, total: int, questions: list[str]) -> str:
    if total == 1:
        return chapter
    hint = re.sub(r"^Q\d+\s*[.．、:：]\s*", "", questions[0])[:18] if questions else ""
    return f"{chapter}（{index}/{total}）{hint}…"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def load_passphrase(force_new: bool) -> str:
    if not force_new and PASSPHRASE_FILE.is_file():
        value = PASSPHRASE_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    # 生成一个好记又够强的口令：4 个词 + 4 位数字
    words = ["harbor", "copper", "lantern", "falcon", "meadow", "quartz",
             "saddle", "thunder", "velvet", "walnut", "zephyr", "ember"]
    phrase = "-".join(secrets.SystemRandom().sample(words, 4)) + f"-{secrets.randbelow(9000) + 1000}"
    PASSPHRASE_FILE.write_text(phrase + "\n", encoding="utf-8")
    return phrase


def load_salt(force_new: bool) -> bytes:
    """KDF 的 salt。

    **必须固定下来**：密钥 = PBKDF2(口令, salt)，如果每次构建都换 salt，
    那用户浏览器里"记住"的密钥在每次内容更新后都会失效，只能重新输口令。
    只有换口令（--new-passphrase）时才顺便换 salt。
    """
    if not force_new and SALT_FILE.is_file():
        try:
            data = base64.b64decode(SALT_FILE.read_text(encoding="utf-8").strip())
            if len(data) == 16:
                return data
        except Exception:
            pass
    salt = secrets.token_bytes(16)
    SALT_FILE.write_text(base64.b64encode(salt).decode("ascii") + "\n", encoding="utf-8")
    return salt


def derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    return PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITERATIONS
    ).derive(passphrase.encode("utf-8"))


def encrypt(key: bytes, plaintext: str, *, seed: str = "") -> bytes:
    """加密。给了 seed 就用「seed + 内容」派生出确定的 IV。

    这样做是为了让**内容没变时密文也不变**：否则每次重建都会生成一批全新的随机密文，
    git 里就会出现几 MB 毫无意义的二进制变更。
    安全性不受影响：IV 仍然由 (页面标识, 明文) 唯一决定，不会出现同一密钥下 IV 复用。
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    data = plaintext.encode("utf-8")
    if seed:
        iv = hashlib.sha256(seed.encode("utf-8") + b"\x00" + data).digest()[:12]
    else:
        iv = os.urandom(12)
    return iv + AESGCM(key).encrypt(iv, data, None)


def collect_chapters() -> list[dict]:
    chapters: list[dict] = []
    for group_name, folder in GROUPS:
        directory = INTERVIEW / folder if folder else INTERVIEW
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name.startswith("_"):      # _inventory / _owned_questions 之类的中间文件不发布
                continue
            text = path.read_text(encoding="utf-8")
            chapters.append({
                "group": group_name,
                "folder": folder,
                "name": path.stem,
                "rel": path.relative_to(INTERVIEW).as_posix(),
                "text": text,
            })
    return chapters


def rewrite_links(text: str, page_map: dict[str, str]) -> str:
    """把文档之间的 .md 交叉链接改成站内跳转（JS 会拦截 #p=xxx）。"""

    def replace(match: re.Match) -> str:
        target = match.group(1).lstrip("./")
        page_id = page_map.get(target) or page_map.get(target.split("/")[-1])
        if not page_id:
            return match.group(0)
        return f"](#p={page_id})"

    return LINK_RE.sub(replace, text)


def main() -> int:
    force_new = "--new-passphrase" in sys.argv
    passphrase = load_passphrase(force_new)
    salt = load_salt(force_new)
    key = derive_key(passphrase, salt)

    if PAGES.is_dir():
        for stale in PAGES.glob("*.bin"):
            stale.unlink()
    PAGES.mkdir(parents=True, exist_ok=True)

    # 第一遍：切页 + 分配 id，建立「源文件 → 页面 id」映射，供交叉链接改写用
    chapters = collect_chapters()
    for chapter in chapters:
        chapter["pages"] = split_pages(chapter["text"])
        # 页面文件名按「源路径 + 序号」稳定生成：内容没变时文件名不变，
        # git 里就只会有真正改动的那几页，不会每次更新都重写全部密文（省掉好几 MB 的仓库膨胀）
        chapter["ids"] = [
            hashlib.md5(f"{chapter['rel']}#{index}".encode("utf-8")).hexdigest()[:16]
            for index in range(1, len(chapter["pages"]) + 1)
        ]
    page_map: dict[str, str] = {}
    for chapter in chapters:
        page_map[chapter["rel"]] = chapter["ids"][0]
        page_map[chapter["name"]] = chapter["ids"][0]
        page_map[f"{chapter['rel']}.md"] = chapter["ids"][0]

    structure: list[dict] = []
    search: list[dict] = []
    total_pages = total_chars = 0

    for chapter in chapters:
        entries = []
        for index, (page_text, page_id) in enumerate(zip(chapter["pages"], chapter["ids"]), start=1):
            page_text = rewrite_links(page_text, page_map)
            questions = [re.sub(r"\s+", " ", q).strip()
                         for q in QUESTION_RE.findall(page_text)]
            headings = [h[1] for h in HEADING_RE.findall(page_text)]
            html = render_markdown(page_text)
            (PAGES / f"{page_id}.bin").write_bytes(encrypt(key, html, seed=page_id))

            title = page_title(chapter["name"], index, len(chapter["pages"]), questions)
            entries.append({"id": page_id, "title": title, "chars": len(page_text),
                            "questions": questions})
            search.append({
                "id": page_id,
                "g": chapter["group"],
                "c": chapter["name"],
                "t": title,
                "q": questions,
                "h": headings[:40],
            })
            total_pages += 1
            total_chars += len(page_text)

        structure.append({
            "group": chapter["group"],
            "chapter": chapter["name"],
            "pages": entries,
        })

    index_blob = encrypt(key, json.dumps(structure, ensure_ascii=False), seed="index")
    search_blob = encrypt(key, json.dumps(search, ensure_ascii=False), seed="search")
    (OUT / "index.bin").write_bytes(index_blob)
    (OUT / "search.bin").write_bytes(search_blob)

    # 构建号同样由内容派生：内容没变就保持一致（重建不产生 diff），
    # 内容一变就变，正好用来让浏览器丢掉旧缓存
    digest = hashlib.sha256()
    digest.update(salt)
    for path in sorted(PAGES.glob("*.bin")):
        digest.update(path.read_bytes())
    digest.update(index_blob)
    digest.update(search_blob)
    build_id = digest.hexdigest()[:8]
    (OUT / "config.json").write_text(
        json.dumps({
            "kdf": "PBKDF2-SHA256",
            "iterations": KDF_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "pages": total_pages,
            "build": build_id,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"口令：{passphrase}")
    print(f"（存在 {PASSPHRASE_FILE.relative_to(ROOT)}，该文件不进 git；想换口令用 --new-passphrase）")
    print(f"加密完成：{total_pages} 页 / 明文 {total_chars / 10000:.1f} 万字 / 密文 {size / 1048576:.1f} MB")
    print(f"输出目录：{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
