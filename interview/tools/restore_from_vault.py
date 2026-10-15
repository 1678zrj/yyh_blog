"""从加密专区里把某一章的明文还原回 markdown。

用途（2026-xx 实际用到）：代理「边写边清空」目标文件，而 interview/ 没进 git、
没有版本可回滚，唯一的旧副本就是上一轮构建出的密文页面。本脚本解密那些页面、
把 HTML 转回 markdown，重新拼成一章。

用法：
    .venv\\Scripts\\python.exe interview\\tools\\restore_from_vault.py <章节名关键词> [--write]
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
OUT = ROOT / "docs" / "assets" / "private"


def load_key():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    config = json.loads((OUT / "config.json").read_text(encoding="utf-8"))
    passphrase = (INTERVIEW / ".passphrase").read_text(encoding="utf-8").strip()
    return PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32,
        salt=base64.b64decode(config["salt"]), iterations=config["iterations"],
    ).derive(passphrase.encode("utf-8"))


def decrypt(key: bytes, path: Path) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    blob = path.read_bytes()
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def html_to_markdown(html: str) -> str:
    import html2text
    import re

    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = True
    converter.ignore_emphasis = False
    text = converter.handle(html).strip()
    # html2text 会在 **加粗** 和后面的标点之间塞一个空格（`**一句话回答** ：`），去掉它
    text = re.sub(r"\*\*\s+([：:，。；、])", r"**\1", text)
    return text


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    needle = sys.argv[1]
    write = "--write" in sys.argv

    key = load_key()
    structure = json.loads(decrypt(key, OUT / "index.bin").decode("utf-8"))

    chapter = next((c for c in structure if needle in c["chapter"]), None)
    if not chapter:
        print(f"目录里找不到包含「{needle}」的章节")
        return 1

    print(f"命中章节：{chapter['group']} / {chapter['chapter']}（{len(chapter['pages'])} 页）")
    pieces: list[str] = []
    for page in chapter["pages"]:
        html = decrypt(key, OUT / "pages" / f"{page['id']}.bin").decode("utf-8")
        pieces.append(html_to_markdown(html))

    markdown = "\n\n---\n\n".join(pieces) + "\n"
    questions = markdown.count("\n### Q")
    print(f"还原出 {len(markdown)} 字符 / 约 {questions} 道题")

    target = INTERVIEW / chapter["output"] if "output" in chapter else None
    # 索引里存的是 pages 与 chapter 名，落盘路径按分组约定推断
    guess = {
        "补充 · Agent 八股": "agent-baguwen",
        "补充 · 经典加强": "deep",
        "补充 · 市场题校准": "market",
        "补充 · 通用八股": "gap",
    }.get(chapter["group"])
    if guess:
        target = INTERVIEW / guess / f"{chapter['chapter']}.md"

    if write and target:
        if target.is_file():
            backup = target.with_suffix(".md.before-restore")
            backup.write_text(target.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
            print(f"旧文件已备份到 {backup.relative_to(ROOT)}")
        target.write_text(markdown, encoding="utf-8", newline="\n")
        print(f"已写入 {target.relative_to(ROOT)}")
    else:
        print("（预览模式：加 --write 才会落盘）")
        print("前 600 字符预览：")
        print(markdown[:600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
