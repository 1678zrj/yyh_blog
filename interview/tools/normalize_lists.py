"""修 markdown 的「列表前缺空行」问题（这是正文读起来挤成一坨的真正原因）。

问题：Python-Markdown（mkdocs 用的那个）要求列表前必须有空行，否则

    **展开**：
    - 要点一。……
    - 要点二。……

会被渲染成**一个段落**，`- ` 原样留在文字里：

    <p><strong>展开</strong>：
    - 要点一。……
    - 要点二。……</p>

所以就出现"明明写了列表，页面上却是一大段密排文字"。

本脚本在「列表项紧跟在非空、且不是列表项的行之后」时补一个空行。

安全措施：
  - 代码块（``` 与 ~~~）内一律不动
  - 引用块（> 开头，或前一行是引用）不动，避免破坏 blockquote 里的列表
  - 表格行（| 开头）、前后都是列表项的情况不动
  - 默认预览，--apply 才真正写盘

用法：
    .venv\\Scripts\\python.exe interview\\tools\\normalize_lists.py            # 预览
    .venv\\Scripts\\python.exe interview\\tools\\normalize_lists.py --apply    # 写入
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
TARGET_DIRS = ["deep", "agent-baguwen", "agent-real", "market", "gap"]

LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,2}[.)])\s+\S")
FENCE = re.compile(r"^\s*(```|~~~)")


def needs_blank_before(previous: str | None, current: str) -> bool:
    if not LIST_ITEM.match(current):
        return False
    if previous is None or previous.strip() == "":
        return False
    if LIST_ITEM.match(previous):
        return False
    if previous.lstrip().startswith(">"):
        return False
    if current.lstrip().startswith(">"):
        return False
    return True


def process(path: Path, apply: bool) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_fence = False
    inserted = 0

    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and needs_blank_before(out[-1] if out else None, line):
            out.append("")
            inserted += 1
        out.append(line)

    if inserted and apply:
        path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return inserted


def main() -> int:
    apply = "--apply" in sys.argv
    total = files = 0
    print(f"{'文件':<52}{'补空行':>8}")
    print("-" * 62)
    for name in TARGET_DIRS:
        directory = INTERVIEW / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            inserted = process(path, apply)
            if inserted:
                print(f"{str(path.relative_to(INTERVIEW)):<52}{inserted:>8}")
                total += inserted
                files += 1
    print("-" * 62)
    print(f"{'已写入' if apply else '（预览，未写入）'}：{files} 个文件 / 补了 {total} 个空行")
    if not apply and total:
        print("确认后加 --apply 写入；改完要重跑 build_private.py 与 build_interview.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
