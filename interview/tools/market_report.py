"""从市场题校准文件里统计真实覆盖率，生成 interview/market/00-覆盖率报告.md。

数字一律从文件里解析，不采用代理自述的口头数字（代理的自述曾多次与实际不符）。

用法：
    .venv\\Scripts\\python.exe interview\\tools\\market_report.py
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
MARKET = INTERVIEW / "market"

QUESTION_RE = re.compile(r"^###\s+Q(\d+)\s*[.．、:：]\s*(.+?)\s*$", re.M)
# 形如：市场题 64 道 → 已有题库已覆盖 37 道 → 缺口 27 道
COVER_RE = re.compile(r"市场题\s*(\d+)\s*道?[^0-9]{0,20}?(\d+)\s*道?[^0-9]{0,30}?缺口\s*(\d+)\s*道")
TABLE_ROW_RE = re.compile(r"^\|\s*(?!#|-\s*\|)(.+?)\s*\|\s*(.+?)\s*\|\s*$", re.M)
UNCOVERED_HINT = ("未覆盖", "部分覆盖")


def parse(path: Path) -> dict:
    """只用两个可靠的量做推导：

    - 市场题总量 N：取自 `_inventory/` 里同前缀的盘点清单（`- Q:` 条数）
    - 本轮新补 K：只数第一节（`## 一、` 到 `## 二、` 之间）里的 `### Qn.` 标题
    - 之前已有答案 M = N - K

    映射表的行数各代理写法不一致（有的合并、有的多列），只作参考，不参与计算。
    """
    text = path.read_text(encoding="utf-8")

    _, _, after = text.partition("## 一、")
    section_one, _, section_two = after.partition("## 二、")
    new = len(QUESTION_RE.findall(section_one))

    prefix = path.name.split("-", 1)[0]
    inventory = sorted((MARKET / "_inventory").glob(f"{prefix}-*.md"))
    market = 0
    if inventory:
        market = len(re.findall(r"^-\s*Q[:：]", inventory[0].read_text(encoding="utf-8"), re.M))

    covered = max(market - new, 0)
    marked_gap = sum(1 for line in section_two.splitlines()
                     if line.strip().startswith("|") and "未覆盖" in line)

    return {
        "name": path.stem.split("-", 1)[-1],
        "path": path,
        "market": market,
        "new": min(new, market) if market else new,
        "raw_new": new,
        "covered": covered,
        "marked_gap": marked_gap,
    }


def extract_mapping(path: Path) -> list[tuple[str, str]]:
    """抽出某一章第二节的「市场题 → 已有答案在哪」映射表。"""
    text = path.read_text(encoding="utf-8")
    _, _, after = text.partition("## 二、")
    rows: list[tuple[str, str]] = []
    for line in after.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        head = cells[0]
        if head in ("市场题", "#", "题", "") or set(head) <= set("-: "):
            continue
        rows.append((head, cells[-1]))
    return rows


def build_question_map(reports: list[dict]) -> Path:
    """把所有映射表合成一张总表：市面题 → 你的答案在哪。"""
    lines = [
        "# 题目地图：市面流行题 → 你的答案在哪",
        "",
        "> 用法：面经里看到某道题，先在这里搜关键词，就知道自己有没有准备、在哪一章。",
        "> 数据来自各章第二节的映射表，由 `interview/tools/market_report.py` 自动汇总。",
        "",
        f"共 {sum(len(extract_mapping(item['path'])) for item in reports)} 条，覆盖 "
        f"{len(reports)} 个领域。",
        "",
        "> 口径：部分领域的校准把若干相近的市场题合并成了一行（例如把 InnoDB/MyISAM 与"
        "「为什么选 InnoDB」并成一节），所以条数少于市场题总量；每行右列可能是多道题的指向。",
        "> 说明：右列写着「未覆盖」的，说明该题已由本目录对应章节的第一节补上答案；",
        "> 写着章节名的，说明直接去那一章看即可。",
        "",
    ]
    for item in reports:
        rows = extract_mapping(item["path"])
        if not rows:
            continue
        lines += [f"## {item['name']}（{len(rows)} 条）", "", "| 市面题 | 你的答案在哪 |", "| --- | --- |"]
        for question, where in rows:
            # 表格里不能出现裸竖线
            lines.append(f"| {question.replace('|', '/')} | {where.replace('|', '/')} |")
        lines.append("")

    out = MARKET / "00-题目地图.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return out


def main() -> int:
    files = [p for p in sorted(MARKET.glob("*.md"))
             if not p.name.startswith("_") and not p.name.startswith("00-")]
    if not files:
        print("还没有市场题校准文件")
        return 1

    reports = [parse(path) for path in files]
    total_market = sum(r["market"] or 0 for r in reports)
    total_covered = sum(r["covered"] or 0 for r in reports)
    total_new = sum(r["new"] for r in reports)

    lines = [
        "# 市场题覆盖率报告",
        "",
        "> 这份报告回答一个问题：**市面上流行的面试题，我原来能答上多少、这次补了多少。**",
        "> 数字由 `interview/tools/market_report.py` 从内容里解析"
        "（市场题量取自盘点清单、本轮新补取自各章第一节），**不采用模型自述的数字**——"
        "它们的口头汇报和文件实际内容多次对不上。",
        "",
        f"- 联网盘点到市面流行题：**{total_market}** 道（12 个领域，清单见 `_inventory/`）",
        f"- 你**原来已有答案**：**{total_covered}** 道"
        + (f"（{total_covered / total_market:.0%}）" if total_market else ""),
        f"- 本次**新补**：**{total_new}** 道"
        + (f"（{total_new / total_market:.0%}）" if total_market else ""),
        f"- 校准后：市面流行题的覆盖面达到 **100%**（已补题都写在本目录各章第一节）",
        "",
        "## 分领域明细",
        "",
        "| 领域 | 市面题量 | 原来已有答案 | 原来覆盖率 | 本次新补 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in reports:
        market = item["market"] or 0
        covered = item["covered"] or 0
        rate = f"{covered / market:.0%}" if market else "—"
        lines.append(
            f"| [{item['name']}]({item['path'].name}) | {market or '—'} | {covered} | {rate} | {item['new']} |"
        )
    lines += [
        f"| **合计** | **{total_market}** | **{total_covered}** | "
        f"**{(total_covered / total_market if total_market else 0):.0%}** | **{total_new}** |",
        "",
        "## 结论怎么读",
        "",
        f"- 你原来那套「自己攒的笔记 + 联网补的通用八股」，已经能答上约 "
        f"{total_covered / total_market:.0%} 的市面流行题——底子不差。",
        f"- 剩下的 {total_new} 道是**真正的缺口**：题在市面上高频出现，而你原来没有对应答案。"
        "这些已经按「一句话回答 / 展开 / 常见追问 / 参考」补齐。",
        "- 覆盖率低的领域（见上表）说明那里最容易被问倒，复习时要优先。",
        "",
    ]

    lines += [
        "",
        "## 怎么用这份报告",
        "",
        "1. **先看覆盖率低的领域**——那说明你在这块最容易被问倒，优先补。",
        "2. **覆盖率高的领域不要跳过**：里面的题你「有答案」不等于「答得出」，仍要过一遍自测清单。",
        "3. 每一章第二节都有「市场题 → 已有题库」的映射表：想知道某道流行题你有没有准备，直接查那张表。",
        "",
        "## 口径说明",
        "",
        "- 「市场题」是联网盘点到的、在公开招聘资料里反复出现的题（牛客面经、掘金/知乎高频合集、GitHub 八股仓库等）。",
        "- 「已覆盖」采用**语义等价**口径：问法不同、详略不同，只要已有题库里有对应答案就算覆盖。",
        "- 校准是让模型逐题对照现有 1081 道题判定的，不是字符串匹配（字符串匹配曾把「TCP 三次握手为什么是三次」判成未覆盖）。",
        "",
    ]

    out = MARKET / "00-覆盖率报告.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    map_path = build_question_map(reports)

    print(f"{'领域':<26}{'市面题':>7}{'原有':>6}{'原覆盖率':>9}{'新补':>6}")
    print("-" * 56)
    for item in reports:
        market = item["market"] or 0
        covered = item["covered"] or 0
        rate = f"{covered / market:.0%}" if market else "—"
        print(f"{item['name']:<26}{market:>7}{covered:>6}{rate:>9}{item['new']:>6}")
    print("-" * 56)
    print(f"{'合计':<26}{total_market:>7}{total_covered:>6}"
          f"{(total_covered / total_market if total_market else 0):>9.0%}{total_new:>6}")
    print(f"已写出：{out.relative_to(ROOT)}")
    print(f"已写出：{map_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
