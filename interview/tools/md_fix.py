"""正文格式修复：给「紧跟在文字后面的列表」补空行。

`normalize_lists.py` 是命令行工具（用来批量修源文件），这里是给构建脚本调用的库版本。
放在构建流程里是必要的——主线章节每次都由 `build_interview.py` 从 `_parts/` 重新合并，
手工修好的文件会被覆盖，所以必须在生成时和渲染前都过一遍。
"""

from __future__ import annotations

import re

LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,2}[.)])\s+\S")
FENCE = re.compile(r"^\s*(```|~~~)")


def needs_blank_before(previous: str | None, current: str) -> bool:
    if not LIST_ITEM.match(current):
        return False
    if previous is None or previous.strip() == "":
        return False
    if LIST_ITEM.match(previous):
        return False
    if previous.lstrip().startswith(">") or current.lstrip().startswith(">"):
        return False
    return True


def fix_markdown(text: str) -> str:
    """在需要的地方给列表补空行，其余内容原样保留。

    Python-Markdown 要求列表前有空行，否则「**展开**：\\n- 要点」会被当成一个段落，
    页面上就出现一大段带 "- " 的密排文字。
    """
    lines = text.splitlines()
    out: list[str] = []
    in_fence = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and needs_blank_before(out[-1] if out else None, line):
            out.append("")
        out.append(line)
    return "\n".join(out)
