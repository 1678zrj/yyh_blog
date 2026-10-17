"""证明「富化层只插入、不改原话」。

主线章节每次由 `build_interview.py` 从 `_parts/` 重新合并，新增的底层原理/类比放在
`interview/enrich/`，构建时纯插入。本脚本做严格审计：

    把 `interview/.enrich-audit/*.before.md`（插入前的原文）
    与当前生成物「去掉每个插入块后」的结果逐字节比对。

一致 → 原话一字未改，只有插入。

用法：
    .venv\\Scripts\\python.exe interview\\tools\\verify_enrich.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
AUDIT = INTERVIEW / ".enrich-audit"

sys.path.insert(0, str(Path(__file__).parent))
from md_fix import fix_markdown  # noqa: E402  构建时会对生成物做同一套归一化


def blocks_of(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    blocks: dict[str, str] = {}
    matches = list(re.finditer(r"(?m)^##\s+Q(\d+)\s*[.．、:：].*$", text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            blocks[match.group(1)] = body
    return blocks


def main() -> int:
    if not AUDIT.is_dir():
        print("还没有富化审计记录（先跑 build_interview.py）")
        return 0

    checked = failed = 0
    print(f"{'章节':<46}{'插入块':>7}{'原文字数':>10}  结论")
    print("-" * 84)
    for before_file in sorted(AUDIT.glob("*.before.md")):
        output = before_file.name[: -len(".before.md")].replace("__", "/")
        enrich_file = INTERVIEW / "enrich" / f"{output}.md"
        current = INTERVIEW / f"{output}.md"
        if not enrich_file.is_file() or not current.is_file():
            print(f"{output:<46}{'—':>7}{'—':>10}  ⚠ 缺富化层或生成物")
            continue

        blocks = blocks_of(enrich_file)
        enriched = current.read_text(encoding="utf-8")
        stripped = enriched
        missing = []
        for number, block in blocks.items():
            # 块有两种插入形态：锚点前插入（块 + 两个换行）、题末插入（两个换行 + 块）。
            # 两种都要能精确剥离，剥离后必须与原文逐字节一致。
            for needle in (block + "\n\n", "\n\n" + block, block):
                if needle in stripped:
                    stripped = stripped.replace(needle, "", 1)
                    break
            else:
                missing.append(number)

        before = before_file.read_text(encoding="utf-8")
        # 构建时会对生成物跑 md_fix（补列表前空行），所以基准也要过一遍同一个归一化，
        # 否则比的是两个不同变换的结果
        before = fix_markdown(before)
        checked += 1
        if stripped == before:
            print(f"{output:<46}{len(blocks):>7}{len(before):>10}  ✅ 原话未变（纯插入）")
        else:
            failed += 1
            print(f"{output:<46}{len(blocks):>7}{len(before):>10}  ❌ 不一致")
            if missing:
                print(f"    生成物里找不到这些块：{'、'.join('Q' + n for n in missing)}")
            # 给出第一个差异位置的上下文，便于排查
            limit = min(len(stripped), len(before))
            position = next((i for i in range(limit) if stripped[i] != before[i]), limit)
            print(f"    首个差异在偏移 {position}：")
            print(f"      原文：{before[max(0, position-40):position+60]!r}")
            print(f"      剥离：{stripped[max(0, position-40):position+60]!r}")

    print("-" * 84)
    print(f"检查 {checked} 章，{'全部通过' if not failed else f'{failed} 章不一致'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
