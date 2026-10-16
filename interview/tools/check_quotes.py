# -*- coding: utf-8 -*-
"""校验新增小节里没有残留的 ASCII 直引号（反引号代码片段除外）。"""
from __future__ import annotations

from pathlib import Path

DRAFT_DIR = Path(r"D:\mycode\python\my_blog\interview\.drafts")

bad = 0
for path in sorted(DRAFT_DIR.glob("Q*.md")):
    for i, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        parts = line.split("`")
        for j in range(0, len(parts), 2):
            if '"' in parts[j]:
                print(f"{path.name}:{i}: {parts[j][:100]}")
                bad += 1
print("剩余直引号片段数:", bad)
