# -*- coding: utf-8 -*-
"""把新增小节里的 ASCII 直引号 "..." 统一成中文引号 「...」，与原文风格一致。

只在反引号代码片段之外做替换，避免破坏 `response="我不知道"` 这类代码。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DRAFT_DIR = Path(r"D:\mycode\python\my_blog\interview\.drafts")

PAIR = re.compile(r'"([^"\n]{1,120})"')


def fix_segment(seg: str) -> str:
    def repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        if inner.startswith(" ") or inner.endswith(" "):
            return m.group(0)
        return "「" + inner + "」"

    prev = None
    while prev != seg:
        prev = seg
        seg = PAIR.sub(repl, seg)
    return seg


def fix_line(line: str) -> str:
    # 按反引号切分，奇数段落是代码，跳过
    parts = line.split("`")
    for i in range(0, len(parts), 2):
        parts[i] = fix_segment(parts[i])
    return "`".join(parts)


def main() -> int:
    total = 0
    for path in sorted(DRAFT_DIR.glob("Q*.md")):
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        out = []
        changed = 0
        for ln in lines:
            new = fix_line(ln)
            if new != ln:
                changed += 1
            out.append(new)
        if changed:
            path.write_text("\n".join(out), encoding="utf-8", newline="\n")
            print(f"{path.name}: {changed} 行")
            total += changed
    print(f"合计 {total} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
