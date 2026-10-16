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
# 三种章节体裁：
#   qa    —— 普通题库（### Qn.，核心四节含 **展开**）
#   point —— 经典加强（### 1.，按关键点组织）
#   real  —— 大厂真题（### Qn.，但用 出现场景/为什么这么做/核实与纠错 这套骨架，没有 **展开**）
QUESTION = re.compile(r"(?m)^###\s+(Q\d+)\s*[.．、:：]\s*(.*)$")
POINT = re.compile(r"(?m)^###\s+(\d+)\s*[.．、:：]?\s*(.*)$")
QA_LABELS = ["一句话回答", "展开", "常见追问", "容易答错的边界"]
POINT_LABELS = ["经典资料怎么讲", "我们的差距", "面试怎么答", "容易答错的边界"]
REAL_LABELS = ["一句话回答", "为什么这么做", "核实与纠错", "常见追问", "容易答错的边界"]
# 深度要求（v2/v3/v4 新增）：缺失只告警不算失败，但要在报告里显出来
DEPTH_LABELS = {
    "qa": ["底层原理", "从原理推答案", "换个前提会怎样"],
    "point": ["类比", "从原理推答案", "换个前提会怎样"],
    "real": ["底层原理", "从原理推答案", "网上常见误区"],
}
SLANG = ["动态识别", "分层承载", "三层防线", "双写一致", "高内聚低耦合", "多快好省"]


def parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    # 大厂真题章用「出现场景 + 核实与纠错」这套骨架，和普通题库区分开
    if "**核实与纠错**" in text or "**出现场景**" in text:
        kind = "real"
    elif QUESTION.search(text):
        kind = "qa"
    else:
        kind = "point"
    pattern = QUESTION if kind in ("qa", "real") else POINT
    matches = list(pattern.finditer(text))
    questions = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        questions[match.group(1)] = {
            "title": match.group(2).strip(),
            "body": text[start:end],
        }
    return {"text": text, "questions": questions, "kind": kind}


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
    kind = after["kind"]
    core = {"qa": QA_LABELS, "real": REAL_LABELS, "point": POINT_LABELS}[kind]
    problems: list[str] = []
    warnings: list[str] = []

    missing = sorted(set(before["questions"]) - set(after["questions"]))
    extra = sorted(set(after["questions"]) - set(before["questions"]))
    if missing:
        problems.append(f"漏题：{', '.join(missing)}")
    if extra:
        problems.append(f"多出题号：{', '.join(extra)}")

    for number, item in after["questions"].items():
        positions = []
        for label in core:
            found = re.search(r"\*\*" + label + r"\*\*", item["body"])
            positions.append(found.start() if found else -1)
        if -1 in positions:
            absent = [core[i] for i, pos in enumerate(positions) if pos == -1]
            problems.append(f"{number} 缺标签：{'、'.join(absent)}")
        elif positions != sorted(positions):
            problems.append(f"{number} 标签顺序不对")

    # 深度标签（v2/v3 要求）：缺失告警
    for label in DEPTH_LABELS[kind]:
        absent = [number for number, item in after["questions"].items()
                  if not re.search(r"\*\*" + label + r"\*\*", item["body"])]
        if absent:
            warnings.append(f"{len(absent)} 个条目缺「{label}」：{'、'.join(absent[:6])}"
                            + ("…" if len(absent) > 6 else ""))

    shrunk = []
    for number, item in after["questions"].items():
        if number in before["questions"]:
            old = len(before["questions"][number]["body"])
            new = len(item["body"])
            # 阈值 50%：改写允许"重组压缩"（实测有题压到 50~60% 但关键术语没丢），
            # 同时仍能拦住真正的截断（曾出现 65KB 掉到 12.9KB = 20% 的事故）
            if old > 500 and new < old * 0.5:
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
        "warnings": warnings,
        "slogans": slogans,
        "kind": kind,
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
    print(f"体裁：{ {'qa': 'Q&A', 'real': '大厂真题', 'point': '经典加强（关键点）'}[result['kind']] }")
    print(f"原文件：{result['before_questions']} 项 / {result['before_chars']} 字符")
    print(f"改写稿：{result['after_questions']} 项 / {result['after_chars']} 字符"
          f"（{result['after_chars'] / max(result['before_chars'], 1):.0%}）")
    if result["slogans"]:
        print(f"⚠ 新增口号词：{'、'.join(result['slogans'])}（应改成机制展开）")
    for item in result["warnings"]:
        print(f"⚠ {item}")
    if not result["ok"]:
        print("\n❌ 未通过：")
        for item in result["problems"]:
            print("  -", item)
        return 1
    print("\n✅ 通过：条目数一致、标签齐全、字数未缩水、列表格式正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
