"""判断「字数缩水」是真丢内容，还是重组压缩。

做法：从原稿里自动抽取**有信息量的术语**（加粗小标签、行内代码、4~8 字的中文短语），
再看改写稿里还在不在。只丢了个别同义表述 → 属重组；丢得多 → 要补。

用法：
    .venv\\Scripts\\python.exe interview\\tools\\check_shrink.py <原文件> <改写稿> <题号>
    .venv\\Scripts\\python.exe interview\\tools\\check_shrink.py <原文件> <改写稿> <题号> --terms 连接池,写偏斜
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def body(path: Path, number: str) -> str:
    text = path.read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^###\s+Q?(\d+)\s*[.．、:：]?", text))
    for index, match in enumerate(matches):
        if match.group(1) == number:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            return text[match.end():end]
    return ""


def terms_of(text: str) -> list[str]:
    """抽取有信息量的术语：加粗标签、行内代码、英文术语、4~8 字中文短语。"""
    found: list[str] = []
    found += re.findall(r"\*\*([^*\n]{2,16}?)\*\*", text)                   # 加粗小标签
    found += re.findall(r"`([^`\n]{2,40})`", text)                           # 行内代码 / 标识符
    found += re.findall(r"\b([A-Z][A-Za-z]{2,}(?:[A-Z][a-z]+)?)\b", text)    # 英文术语
    found += re.findall(r"[\u4e00-\u9fa5]{4,8}", text)                        # 中文短语粗筛

    seen: dict[str, None] = {}
    for term in found:
        cleaned = term.strip()
        if len(cleaned) >= 2:
            seen.setdefault(cleaned, None)
    return list(seen)


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 1
    original, staged, number = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

    explicit: list[str] = []
    if "--terms" in sys.argv:
        explicit = [t for t in sys.argv[sys.argv.index("--terms") + 1].split(",") if t]

    before, after = body(original, number), body(staged, number)
    if not before or not after:
        print(f"找不到题号 {number} 的正文（原稿 {len(before)} 字 / 改写 {len(after)} 字）")
        return 1

    terms = [t for t in (explicit or terms_of(before)) if t in before]
    missing = [t for t in terms if t not in after]

    print(f"题号 Q{number}：原稿 {len(before)} 字 → 改写 {len(after)} 字"
          f"（{len(after) / max(len(before), 1):.0%}）")
    print(f"抽取术语 {len(terms)} 个，改写稿缺失 {len(missing)} 个"
          f"（{len(missing) / max(len(terms), 1):.0%}）")
    if missing:
        print("缺失的术语（最多列 30 个）：")
        for term in missing[:30]:
            print(f"   - {term}")

    ratio = len(missing) / max(len(terms), 1)
    if ratio <= 0.2:
        print("\n结论：属**重组压缩**（关键术语基本都在），可以接受")
        return 0
    if ratio <= 0.4:
        print("\n结论：**部分信息丢失**，建议抽查缺失术语对应的内容是否该保留")
        return 2
    print("\n结论：**明显丢内容**，应当补回")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
