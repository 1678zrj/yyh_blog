"""检查加密专区里到底发布了些什麼（解密目录，不打印正文）。

用途：发布后确认「哪些章节进了线上、多少页、多少题」，
避免只凭构建日志的页数就以为内容都上去了。

用法：
    .venv\\Scripts\\python.exe interview\\tools\\vault_status.py
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
OUT = ROOT / "docs" / "assets" / "private"


def main() -> int:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    config = json.loads((OUT / "config.json").read_text(encoding="utf-8"))
    passphrase = (INTERVIEW / ".passphrase").read_text(encoding="utf-8").strip()
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32,
        salt=base64.b64decode(config["salt"]), iterations=config["iterations"],
    ).derive(passphrase.encode("utf-8"))

    blob = (OUT / "index.bin").read_bytes()
    plain = AESGCM(key).decrypt(blob[:12], blob[12:], None)
    structure = json.loads(plain.decode("utf-8"))

    search_blob = (OUT / "search.bin").read_bytes()
    search = json.loads(
        AESGCM(key).decrypt(search_blob[:12], search_blob[12:], None).decode("utf-8")
    )

    total_pages = sum(len(chapter["pages"]) for chapter in structure)
    total_questions = sum(len(page.get("q", [])) for page in search)

    print(f"构建号 {config.get('build')} · 目录 {len(structure)} 个专题 · "
          f"{total_pages} 页 · {total_questions} 个问题")

    current_group = None
    for chapter in structure:
        if chapter["group"] != current_group:
            current_group = chapter["group"]
            print(f"\n[{current_group}]")
        questions = sum(len(page["questions"]) for page in chapter["pages"])
        print(f"  {chapter['chapter']:<46}{len(chapter['pages']):>3} 页  {questions:>4} 题")

    largest = sorted(search, key=lambda item: -len(item.get("h", []) + item.get("q", [])))[:3]
    print("\n问题最多的三页：")
    for page in largest:
        print(f"  {page['t'][:52]}  （{len(page['q'])} 题）")

    print(f"\n密文体积 {sum(f.stat().st_size for f in OUT.rglob('*') if f.is_file()) / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
