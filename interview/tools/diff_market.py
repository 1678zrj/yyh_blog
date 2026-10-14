"""把「市场题库清单」和候选人已有的题库做差集，算出覆盖率与缺口题。

流程：
  1. 从 interview/ 下已合并的专题文档里抽出全部已有问题（### Qn. xxx）
  2. 从 interview/market/_inventory/*.md 里解析市场题（- Q: xxx）
  3. 用中文字符二元组 Jaccard 相似度做匹配，判定「已覆盖 / 缺口」
  4. 输出 interview/market/_diff.json（给下一阶段答题用）和 00-覆盖率报告.md

用法：
    .venv\\Scripts\\python.exe interview\\tools\\diff_market.py [--threshold 0.62] [--samples 20]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
INVENTORY = INTERVIEW / "market" / "_inventory"
QUESTION_RE = re.compile(r"^###\s+Q\d+\s*[.．、:：]\s*(.+?)\s*$")
MARKET_RE = re.compile(r"^-\s*Q[:：]\s*(.+?)\s*$")
META_RE = re.compile(r"^\s+(次数|来源)[:：]\s*(.*)$")

SKIP_DIRS = {"_parts", "market", "_inventory", "tools", "prompts", "_owned"}

# 用于匹配时忽略的虚词/噪音
NOISE = re.compile(r"[\s，。、？?！!：:；;（）()【】\[\]「」“”\"'`~\-—_/\\|,.]+")


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"^(什么是|请说说|说说|谈谈|介绍一下|请介绍|如何理解|为什么|怎么)", "", text)
    return NOISE.sub("", text)


def bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text}
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(left: str, right: str) -> float:
    a, b = bigrams(left), bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def collect_owned() -> list[dict]:
    """已有题库：合并后的专题文档 + 联网补全的 gap 文档。"""
    owned: list[dict] = []
    for path in sorted(INTERVIEW.rglob("*.md")):
        if path.parent.name in SKIP_DIRS or path.name.startswith("00-"):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = QUESTION_RE.match(line)
            if match:
                owned.append({
                    "text": match.group(1).strip(),
                    "norm": normalize(match.group(1)),
                    "source": f"{path.parent.name}/{path.stem}",
                })
    return owned


def collect_market() -> dict[str, list[dict]]:
    """市场题库：按领域分组。"""
    result: dict[str, list[dict]] = {}
    if not INVENTORY.is_dir():
        return result
    for path in sorted(INVENTORY.glob("*.md")):
        items: list[dict] = []
        current = None
        for line in path.read_text(encoding="utf-8").splitlines():
            match = MARKET_RE.match(line)
            if match:
                current = {"text": match.group(1).strip(), "norm": normalize(match.group(1)),
                           "count": 0, "sources": ""}
                items.append(current)
                continue
            meta = META_RE.match(line)
            if meta and current is not None:
                if meta.group(1) == "次数":
                    digits = re.findall(r"\d+", meta.group(2))
                    current["count"] = int(digits[0]) if digits else 0
                else:
                    current["sources"] = meta.group(2).strip()
        result[path.stem] = items
    return result


def export_owned(owned: list[dict]) -> Path:
    """把现有题库按章节导出，供校准代理按需精读（比整份 82KB 清单省得多）。"""
    by_source: dict[str, list[str]] = {}
    for item in owned:
        by_source.setdefault(item["source"], []).append(item["text"])

    out_dir = INTERVIEW / "market" / "_owned"
    if out_dir.is_dir():
        for stale in out_dir.glob("*.md"):
            stale.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    index_lines = [
        "# 现有题库 · 按章节索引",
        "",
        f"共 {len(owned)} 题 / {len(by_source)} 个章节。校准市场题时按下面的文件名精读对应章节：",
        "",
    ]
    for source in sorted(by_source):
        name = source.replace("/", "-")
        path = out_dir / f"{name}.md"
        path.write_text(
            f"# {source}（{len(by_source[source])} 题）\n\n"
            + "\n".join(f"- {text}" for text in by_source[source])
            + "\n",
            encoding="utf-8", newline="\n",
        )
        index_lines.append(f"- `{name}.md` — {source}（{len(by_source[source])} 题）")

    path = INTERVIEW / "market" / "_owned_questions.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(index_lines) + "\n", encoding="utf-8", newline="\n")
    return path


def main() -> int:
    threshold = 0.62
    samples = 0
    for index, arg in enumerate(sys.argv):
        if arg == "--threshold" and index + 1 < len(sys.argv):
            threshold = float(sys.argv[index + 1])
        if arg == "--samples" and index + 1 < len(sys.argv):
            samples = int(sys.argv[index + 1])

    owned = collect_owned()

    if "--export-owned" in sys.argv:
        path = export_owned(owned)
        print(f"已导出 {len(owned)} 题到 {path.relative_to(ROOT)}")
        return 0

    market = collect_market()
    if not market:
        print("还没有市场题库清单（interview/market/_inventory/*.md），先跑研究代理。")
        print(f"当前已有题库：{len(owned)} 题")
        return 1

    domain_reports = []
    gaps_by_domain: dict[str, list[dict]] = {}
    total_market = total_covered = 0

    for domain, items in market.items():
        covered, gaps = [], []
        for item in items:
            best, best_text = 0.0, ""
            for candidate in owned:
                score = similarity(item["norm"], candidate["norm"])
                if score > best:
                    best, best_text = score, candidate["text"]
            if best >= threshold:
                covered.append({**item, "score": round(best, 3), "matched": best_text})
            else:
                gaps.append({**item, "score": round(best, 3), "closest": best_text})
        gaps_by_domain[domain] = gaps
        total_market += len(items)
        total_covered += len(covered)
        domain_reports.append({"domain": domain, "total": len(items),
                               "covered": len(covered), "gaps": len(gaps)})
        print(f"{domain:<16}{len(items):>4} 题  已覆盖 {len(covered):>3}  "
              f"({len(covered) / max(len(items), 1):>5.0%})  缺口 {len(gaps):>3}")

        if samples:
            print("   样例（判定为已覆盖）：")
            for entry in covered[:samples]:
                print(f"      [{entry['score']}] {entry['text'][:38]}")
                print(f"            ↔ {entry['matched'][:38]}")
            print("   样例（判定为缺口）：")
            for entry in gaps[:samples]:
                print(f"      [{entry['score']}] {entry['text'][:38]}")
                print(f"            最接近: {entry['closest'][:38]}")

    payload = {
        "threshold": threshold,
        "owned_total": len(owned),
        "market_total": total_market,
        "covered_total": total_covered,
        "domains": domain_reports,
        "gaps": gaps_by_domain,
    }
    out = INTERVIEW / "market" / "_diff.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("-" * 62)
    print(f"市场题合计 {total_market}，命中已有题库 {total_covered} "
          f"({total_covered / max(total_market, 1):.0%})，缺口 {total_market - total_covered}")
    print(f"已写出：{out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
