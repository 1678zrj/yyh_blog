"""把 interview/_parts/ 下的小节合并成专题文档，并生成总索引与自测清单。

用法（在仓库根目录执行）：
    .venv\\Scripts\\python.exe interview\\tools\\build_interview.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
PARTS = INTERVIEW / "_parts"
PLAN = INTERVIEW / "_plan.json"

QUESTION_RE = re.compile(r"^###\s+Q(\d+)\s*[.．、:：]\s*(.+?)\s*$")
CHAPTER_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")

# 索引分两大部分：**主线（你自己的笔记）永远排前面**，机器整理的补充材料放后面。
# 这样补充材料再多也不会把主线淹掉，而且两边各自给出规模，一眼能看出主次。
MAIN_GROUPS = [
    ("项目经历深挖", ["project/"]),
    ("Agent 与大模型应用", ["agent/"]),
    ("Python 与后端", ["backend/"]),
    ("速查表", ["cheatsheet/"]),
]
EXTRA_GROUPS = [
    # 「面试加分答案」是冲刺层：面试现场该怎么说 + 什么话加分，放在补充部分最前面
    ("面试加分答案（开口能说）", ["scoring/"]),
    ("Agent 八股专项（求职向）", ["agent-baguwen/"]),
    ("大厂 Agent 真题（按公司）", ["agent-real/"]),
    ("经典加强", ["deep/"]),
    ("市场高频题校准", ["market/"]),
    ("通用八股补全", ["gap/"]),
]
GROUPS = MAIN_GROUPS + EXTRA_GROUPS


def collect_extra_chapters(folder: str, suffix: str = "") -> list[dict]:
    """把不在 _plan.json 里的补充章节（gap/ 与 market/）也纳入索引与自测清单。"""
    directory = INTERVIEW / folder
    if not directory.is_dir():
        return []
    results = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        questions = []
        for line in text.splitlines():
            match = QUESTION_RE.match(line)
            if match:
                questions.append(match.group(2).strip())
        results.append({
            "chapter": path.stem.split("-", 1)[-1] + suffix,
            "output": f"{folder}/{path.stem}",
            "path": path,
            "questions": questions,
            "chars": len(text),
            "parts": 1,
            "missing": [],
            "sources": 0,
        })
    return results


def section_sort_key(section: dict) -> tuple:
    return (section["output"], section["part"])


def load_plan() -> dict:
    if not PLAN.is_file():
        raise SystemExit("找不到 interview/_plan.json，请先运行 scripts/plan_interview.py")
    return json.loads(PLAN.read_text(encoding="utf-8"))


def merge_chapter(chapter: str, output: str, sections: list[dict]) -> dict | None:
    """把同一专题的多个小节合并成一个文件，返回统计信息。"""
    bodies: list[str] = []
    questions: list[str] = []
    missing: list[str] = []
    total_chars = 0
    source_files: set[str] = set()

    for section in sorted(sections, key=section_sort_key):
        path = PARTS / f"{section['id']}.md"
        if not path.is_file():
            missing.append(section["id"])
            continue
        source_files.update(section["files"])
        raw = path.read_text(encoding="utf-8").strip()
        total_chars += len(raw)

        lines = raw.splitlines()
        # 丢掉小节自带的 `## 章节名`（合并后由大标题统一承担）
        for index, line in enumerate(lines[:6]):
            if CHAPTER_HEADING_RE.match(line):
                lines = lines[:index] + lines[index + 1:]
                break

        cleaned: list[str] = []
        for line in lines:
            match = QUESTION_RE.match(line)
            if match:
                questions.append(match.group(2))
                cleaned.append(f"### Q{len(questions)}. {match.group(2)}")
            else:
                cleaned.append(line)
        bodies.append("\n".join(cleaned).strip())

    if not bodies:
        return None

    nav = "\n".join(f"{i}. {text}" for i, text in enumerate(questions, start=1))
    header = [
        f"# {chapter}",
        "",
        f"> 素材：record/ 与 typeora/ 共 {len(source_files)} 篇笔记，约 {sum(s['chars'] for s in sections) / 10000:.1f} 万字；"
        f"共 {len(questions)} 个高频问题。",
        "> 标了 `> 补充：笔记未覆盖，标准八股` 的条目，是我按通用面试范围补的，不是你笔记里的原文。",
        "",
        "## 本章问题导航",
        "",
        nav,
        "",
        "---",
        "",
    ]
    content = "\n".join(header) + "\n\n".join(bodies) + "\n"

    # 富化层：主线章节是每次从 _parts/ 重新合并出来的，直接改生成物会被覆盖。
    # 所以「原话」永远只来自 _parts/（本函数绝不改它），新增的底层原理/类比放在
    # interview/enrich/<output>.md 里，这里按题号**只做插入**。
    content, enriched = apply_enrichment(content, output)

    # 列表前补空行：否则 Python-Markdown 会把「**展开**：\n- 要点」并成一个段落，
    # 渲染出来就是一大段带 "- " 的密排文字，读起来很累（构建时修，避免被下次重生成覆盖）
    sys.path.insert(0, str(Path(__file__).parent))
    from md_fix import fix_markdown

    target = INTERVIEW / f"{output}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(fix_markdown(content), encoding="utf-8", newline="\n")

    return {
        "chapter": chapter,
        "output": output,
        "path": target,
        "questions": questions,
        "chars": len(content),
        "parts": len(bodies),
        "missing": missing,
        "sources": len(source_files),
        "enriched": enriched,
    }


def parse_enrichment(output: str) -> dict[str, str]:
    """读取「富化层」：interview/enrich/<output>.md，按题号返回新增内容块。

    文件格式（每个块以 `## Qn.` 开头，块内容就是原样插入的 markdown）：

        ## Q1. <题面，仅用于人工核对，不参与匹配>
        **底层原理**：
        ...
        **类比**：
        ...
    """
    path = INTERVIEW / "enrich" / f"{output}.md"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    blocks: dict[str, str] = {}
    matches = list(re.finditer(r"(?m)^##\s+Q(\d+)\s*[.．、:：].*$", text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            blocks[match.group(1)] = body
    return blocks


def apply_enrichment(content: str, output: str) -> tuple[str, int]:
    """把富化内容**插入**到对应题目里，返回 (新内容, 插入了多少块)。

    插入位置优先 `**常见追问**` 之前，其次 `**项目落地**` 之前，都没有就放在该题正文末尾。
    **纯插入**：只在锚点处拼接 `块 + 空行`，不改动任何原有字符（包括原有空白），
    这样「去掉插入块后与原文逐字节一致」可以被程序证明（见 verify_enrich.py）。

    同时把插入前的原文存到 interview/.enrich-audit/，供审计比对。
    """
    blocks = parse_enrichment(output)
    if not blocks:
        return content, 0

    matches = list(re.finditer(r"(?m)^###\s+Q(\d+)\s*[.．、:：].*$", content))
    if not matches:
        return content, 0

    original = content
    pieces: list[str] = []
    cursor = 0
    inserted = 0
    for index, match in enumerate(matches):
        start, end = match.start(), (matches[index + 1].start() if index + 1 < len(matches) else len(content))
        number = match.group(1)
        body = content[start:end]
        block = blocks.get(number)
        if block:
            anchor = None
            for marker in ("**常见追问**", "**项目落地**"):
                position = body.find(marker)
                if position != -1:
                    anchor = position
                    break
            if anchor is None:
                # 放该题末尾：在「尾部空白」之前插入，保留原有尾随换行不动
                cut = len(body.rstrip())
                body = body[:cut] + "\n\n" + block + body[cut:]
            else:
                body = body[:anchor] + block + "\n\n" + body[anchor:]
            inserted += 1
        pieces.append(content[cursor:start])
        pieces.append(body)
        cursor = end
    pieces.append(content[cursor:])
    content = "".join(pieces)

    if inserted:
        audit = INTERVIEW / ".enrich-audit"
        audit.mkdir(parents=True, exist_ok=True)
        (audit / f"{output.replace('/', '__')}.before.md").write_text(
            original, encoding="utf-8", newline="\n")
    return content, inserted


def build_index(results: list[dict]) -> None:
    # 手工维护的补充文档（不来自 _parts 合并，但要在索引里出现）
    extras = [
        ("project/00-项目总览与面试话术", "项目总览与面试话术（3 分钟自我介绍逐字稿）"),
        ("project/00-项目讲述稿-20分钟", "项目讲述稿 · 20 分钟（分段时间轴逐字稿 + 白板画法）"),
        ("project/00-口径核对表", "项目口径核对表（三份文档矛盾点，面试前自核）"),
    ]
    for output, label in extras:
        path = INTERVIEW / f"{output}.md"
        if not path.is_file():
            continue
        results.append({
            "chapter": label,
            "output": output,
            "path": path,
            "questions": [],
            "chars": len(path.read_text(encoding="utf-8")),
            "parts": 0,
            "missing": [],
            "sources": 0,
        })

    def render(group_list: list[tuple[str, list[str]]], heading_level: str) -> list[str]:
        output: list[str] = []
        for group_name, prefixes in group_list:
            items = [r for r in results if any(r["output"].startswith(p) for p in prefixes)]
            if not items:
                continue
            output += [f"{heading_level} {group_name}", ""]
            for item in items:
                count = f"{len(item['questions'])} 题 · " if item["questions"] else ""
                output.append(
                    f"- **[{item['chapter']}]({item['output']}.md)** — "
                    f"{count}{item['chars'] / 10000:.1f} 万字"
                )
            output.append("")
        return output

    def summarize(group_list: list[tuple[str, list[str]]]) -> tuple[int, int, float]:
        items = [r for r in results
                 if any(r["output"].startswith(p) for _, prefixes in group_list for p in prefixes)]
        return (len(items), sum(len(r["questions"]) for r in items),
                sum(r["chars"] for r in items) / 10000)

    main_count, main_questions, main_chars = summarize(MAIN_GROUPS)
    extra_count, extra_questions, extra_chars = summarize(EXTRA_GROUPS)

    lines = [
        "# 秋招面试八股文 · 总索引",
        "",
        "> **怎么用**：先看「主线」——那是从你自己的项目与笔记整理出来的，面试时就讲这些；",
        "> 「补充」是机器整理的材料（经典资料加强、市面流行题校准、通用八股），用来**查漏补缺**，",
        "> 不要从头背。遇到主线没覆盖的题，先去补充里的「题目地图」搜。",
        "> 配套 `00-自测清单.md`（只有问题，用来遮住答案自测）。",
        "",
        f"共 {len(results)} 个专题，{sum(len(r['questions']) for r in results)} 个问题，"
        f"约 {sum(r['chars'] for r in results) / 10000:.1f} 万字。",
        "",
        "---",
        "",
        f"## 一、主线 · 你自己的笔记（{main_count} 个专题 · {main_questions} 题 · "
        f"{main_chars:.1f} 万字）",
        "",
        "> 这部分是**你的项目与经历的答案**，面试的绝大部分时间会花在这里。",
        "",
    ]
    lines += render(MAIN_GROUPS, "###")
    lines += [
        "---",
        "",
        f"## 二、补充 · 机器整理（{extra_count} 个专题 · {extra_questions} 题 · "
        f"{extra_chars:.1f} 万字）",
        "",
        "> 补充材料的体量比主线大是正常的（它是从经典资料和市面题库里扒出来的）。",
        "> **它的作用是兜底，不是主线**：主线的答案优先，补充用来补你没准备到的点。",
        "",
    ]
    lines += render(EXTRA_GROUPS, "###")

    lines += [
        "## 怎么用这份材料",
        "",
        "1. **第一遍（建框架）**：只读每章的「本章问题导航」，确认这些问题你都能说出结论。",
        "2. **第二遍（抠原理）**：逐题读「展开」，重点看取舍和背后的「为什么」。",
        "3. **第三遍（对项目）**：只读「项目落地」，把里面的做法整理成 2~3 个能讲 5 分钟的项目故事。",
        "4. **考前一周**：只用 `00-自测清单.md`，看到问题先自己讲一遍，讲不顺的回去翻对应章节。",
        "",
        "## 面试前最后检查",
        "",
        "- [ ] 每个专题都能说出 3 个高频问题的「一句话回答」",
        "- [ ] 项目经历能画出一条完整数据流（入口 → 编排 → 工具/检索 → 落库 → 返回）",
        "- [ ] 每个技术选型都能说出「为什么选它、不选什么、代价是什么」",
        "- [ ] 有 1~2 个能讲清楚的线上问题排查故事",
        "",
    ]
    (INTERVIEW / "00-索引.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def build_self_test(results: list[dict]) -> None:
    lines = [
        "# 面试自测清单",
        "",
        "> 只有问题，没有答案。看到问题先自己讲一遍（结论 → 原理 → 项目里的做法），讲不顺的回去翻对应章节。",
        "",
        f"共 {sum(len(r['questions']) for r in results)} 题。",
        "",
    ]
    for item in results:
        lines += [f"## {item['chapter']}", ""]
        for question in item["questions"]:
            lines.append(f"- [ ] {question}")
        lines.append("")
    (INTERVIEW / "00-自测清单.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    plan = load_plan()
    grouped: dict[str, list[dict]] = {}
    for section in plan["sections"]:
        grouped.setdefault(section["output"], []).append(section)

    results: list[dict] = []
    print(f"{'专题':<28}{'块':>4}{'问题':>6}{'字数(万)':>10}")
    print("-" * 52)
    for output, sections in grouped.items():
        chapter = sections[0]["chapter"]
        stats = merge_chapter(chapter, output, sections)
        if not stats:
            print(f"{chapter:<28}   —— 没有可用小节（{output}）")
            continue
        results.append(stats)
        flag = f"  ⚠缺{len(stats['missing'])}块" if stats["missing"] else ""
        print(f"{chapter:<28}{stats['parts']:>4}{len(stats['questions']):>6}"
              f"{stats['chars'] / 10000:>10.1f}{flag}")

    # 联网收集来的通用八股、市场题校准、经典加强、Agent 专项与大厂真题都不在 _plan.json 里，
    # 单独收集后并入索引。
    # 注意：不再往章节名后面加后缀——索引里已经分了两大部分，再加后缀会变成「经典加强（经典加强）」。
    for stats in (collect_extra_chapters("scoring")
                  + collect_extra_chapters("agent-baguwen")
                  + collect_extra_chapters("agent-real")
                  + collect_extra_chapters("deep")
                  + collect_extra_chapters("market")
                  + collect_extra_chapters("gap")):
        results.append(stats)
        print(f"{stats['chapter']:<28}{1:>4}{len(stats['questions']):>6}"
              f"{stats['chars'] / 10000:>10.1f}  （补充章节）")

    build_index(results)
    build_self_test(results)
    print("-" * 52)
    print(f"共 {len(results)} 个专题，{sum(len(r['questions']) for r in results)} 个问题，"
          f"{sum(r['chars'] for r in results) / 10000:.1f} 万字")
    print("已生成：interview/00-索引.md、interview/00-自测清单.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
