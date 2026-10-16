# -*- coding: utf-8 -*-
"""暂存文件的最终质检：逐题字数、三节完整性、判定条目统计。"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

OUT = Path(r"D:\mycode\python\my_blog\interview\.staging\03-阿里与蚂蚁.md")
text = OUT.read_text(encoding="utf-8-sig")
lines = text.split("\n")

heads = [i for i, l in enumerate(lines) if re.match(r"^### Q\d+\. ", l)]
print(f"题数: {len(heads)}")
print(f"总字符数: {len(text)}  总行数: {len(lines)}")

# 声明与三节
for need in (
    "非官方题库",
    "## 一、真题与答案",
    "## 二、这家公司的考察偏好",
    "## 三、查不到的部分",
):
    print(f"包含「{need}」: {need in text}")

ok = len(re.findall(r"✅ 站得住", text))
warn = len(re.findall(r"⚠️ 不严谨", text))
err = len(re.findall(r"❌ 网上常见错说法", text))
nv = len(re.findall(r"\| 未核实", text))
print(f"✅ {ok} / ⚠️ {warn} / ❌ {err} / 未核实 {nv}")

print("\n题号  全文字数  为什么这么做  核实与纠错表行  ✅  ⚠️  ❌")
total_rows = 0
for k, start in enumerate(heads):
    end = heads[k + 1] if k + 1 < len(heads) else next(
        i for i in range(start, len(lines)) if lines[i].startswith("## 二、")
    )
    body = lines[start:end]
    qn = re.match(r"^### Q(\d+)\. ", lines[start]).group(1)
    chars = len("\n".join(body))
    # 三节分段
    def sect(label: str, stops: list[str]) -> list[str]:
        try:
            s = next(i for i, l in enumerate(body) if l.strip().startswith(label))
        except StopIteration:
            return []
        e = len(body)
        for j in range(s + 1, len(body)):
            if any(body[j].strip().startswith(x) for x in stops):
                e = j
                break
        return body[s:e]

    reason = sect("**为什么这么做**", ["**底层原理**", "**核实与纠错**"])
    check = sect("**核实与纠错**", ["**从原理推答案**", "**换个前提会怎样**", "**面经说法**", "**常见追问**", "**容易答错的边界**"])
    rows = [l for l in check if l.startswith("|") and "---" not in l]
    rows = [l for l in rows if not l.startswith("| 结论 ")]
    total_rows += len(rows)
    c_ok = len(re.findall(r"✅ 站得住", "\n".join(check)))
    c_warn = len(re.findall(r"⚠️ 不严谨", "\n".join(check)))
    c_err = len(re.findall(r"❌ 网上常见错说法", "\n".join(check)))
    print(f"Q{qn:<4} {chars:>7}  {len(''.join(reason)):>10}  {len(rows):>13}  {c_ok:>3} {c_warn:>3} {c_err:>3}")
print(f"\n核实表总行数: {total_rows}")
