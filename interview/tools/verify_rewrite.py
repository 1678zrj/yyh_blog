"""校验「改写稿」是否可以直接替换原文件。

起因：有一次代理直接改原文件，边写边清空，文件从 65 KB 掉到 12.9 KB 而没人立刻发现。
所以以后改写一律**先写暂存文件**，再用本脚本过一遍闸门，通过了才替换。

检查项：
  1. 题数与题号集合与原文件一致（漏题/多题都会报）
  2. 每道题都保留四个核心标签，且顺序正确
  3. 每题字数不低于原题的 60%（防止"改写"变成"删减"）
  4. 列表项前有空行（Python-Markdown 的硬要求）
  5. 没有明显残留的四字口号

用法：
    .venv\\Scripts\\python.exe interview\\tools\\verify_rewrite.py <原文件> <改写稿>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUESTION = re.compile(r"(?m)^###\s+(Q\d+)\s*[.．、:：]\s*(.*)$")
CORE_LABELS = ["一句话回答", "展开", "常见追问", "容易答错的边界"]
SLANG = ["动态识别", "分层承载", "三层防线", "双写一致", "高内聚低耦合", "多快好省"]


def parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    matches = list(QUESTION.finditer(text))
    questions = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        questions[match.group(1)] = {
            "title": match.group(2).strip(),
            "body": text[start:end],
        }
    return {"text": text, "questions": questions}


def check_lists(text: str) -> int:
    """列表项紧跟非空且非列表行的情况有多少处（每处都会渲染错）。"""
    bad = 0
    previous = None
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence and re.match(r"^\s*(?:[-*+]|\d{1,2}[.)])\s+\S", line):
            if previous is not None and previous.strip() and not re.match(
                    r"^\s*(?:[-*+]|\d{1,2}[.)])\s+\S", previous) and not previous.lstrip().startswith(">"):
                bad += 1
        previous = line
    return bad


def evaluate(original: Path, staged: Path) -> dict:
    """跑完全部检查，返回结构化结果（供 apply_staged.py 复用）。"""
    before, after = parse(original), parse(staged)
    problems: list[str] = []

    missing = sorted(set(before["questions"]) - set(after["questions"]))
    extra = sorted(set(after["questions"]) - set(before["questions"]))
    if missing:
        problems.append(f"漏题：{', '.join(missing)}")
    if extra:
        problems.append(f"多出题号：{', '.join(extra)}")

    for number, item in after["questions"].items():
        positions = []
        for label in CORE_LABELS:
            found = re.search(r"\*\*" + label + r"\*\*", item["body"])
            positions.append(found.start() if found else -1)
        if -1 in positions:
            absent = [CORE_LABELS[i] for i, pos in enumerate(positions) if pos == -1]
            problems.append(f"{number} 缺标签：{'、'.join(absent)}")
        elif positions != sorted(positions):
            problems.append(f"{number} 标签顺序不对")

    shrunk = []
    for number, item in after["questions"].items():
        if number in before["questions"]:
            old = len(before["questions"][number]["body"])
            new = len(item["body"])
            if old > 0 and new < old * 0.6:
                shrunk.append(f"{number}（{old}→{new} 字）")
    if shrunk:
        problems.append("字数明显缩水：" + "、".join(shrunk))

    bad_lists = check_lists(after["text"])
    if bad_lists:
        problems.append(f"{bad_lists} 处列表前缺空行（会渲染成一大段）")

    slogans = [word for word in SLANG
               if word in after["text"] and word not in before["text"]]

    return {
        "ok": not problems,
        "problems": problems,
        "slogans": slogans,
        "before_questions": len(before["questions"]),
        "after_questions": len(after["questions"]),
        "before_chars": len(before["text"]),
        "after_chars": len(after["text"]),
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    original = Path(sys.argv[1])
    staged = Path(sys.argv[2])
    for path in (original, staged):
        if not path.is_file():
            print(f"找不到文件：{path}")
            return 1

    result = evaluate(original, staged)
    print(f"原文件：{result['before_questions']} 题 / {result['before_chars']} 字符")
    print(f"改写稿：{result['after_questions']} 题 / {result['after_chars']} 字符"
          f"（{result['after_chars'] / max(result['before_chars'], 1):.0%}）")
    if result["slogans"]:
        print(f"⚠ 新增口号词：{'、'.join(result['slogans'])}（应改成机制展开）")
    if not result["ok"]:
        print("\n❌ 未通过：")
        for item in result["problems"]:
            print("  -", item)
        return 1
    print("\n✅ 通过：题数一致、标签齐全、字数未缩水、列表格式正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
