# -*- coding: utf-8 -*-
"""检查「为什么这么做」是否覆盖四要素：痛点 / 替代方案 / 代价 / 反例。"""
from __future__ import annotations

import re
from pathlib import Path

OUT = Path(r"D:\mycode\python\my_blog\interview\.staging\03-阿里与蚂蚁.md")
lines = OUT.read_text(encoding="utf-8-sig").split("\n")
heads = [i for i, l in enumerate(lines) if re.match(r"^### Q\d+\. ", l)]

PAIN = ["痛点", "失效", "失败模式", "挡住", "解决什么", "症状", "具体痛点", "挡住的"]
ALT = ["替代", "为什么不是", "为什么是它", "排除", "不选", "不用"]
COST = ["代价", "成本", "开销"]
FLIP = ["反过来", "变错", "会变成错", "什么时候", "前提一变", "转为错误", "反而是错", "前提"]

print("题号  痛点 替代 代价 反例")
miss = []
for k, start in enumerate(heads):
    end = heads[k + 1] if k + 1 < len(heads) else next(
        i for i in range(start, len(lines)) if lines[i].startswith("## 二、")
    )
    body = lines[start:end]
    qn = re.match(r"^### Q(\d+)\. ", lines[start]).group(1)
    try:
        s = next(i for i, l in enumerate(body) if l.strip().startswith("**为什么这么做**"))
    except StopIteration:
        print(f"Q{qn}: 缺少「为什么这么做」")
        continue
    e = len(body)
    for j in range(s + 1, len(body)):
        if body[j].strip().startswith("**底层原理**") or body[j].strip().startswith("**核实与纠错**"):
            e = j
            break
    seg = "\n".join(body[s:e])
    flags = []
    for name, kws in (("痛点", PAIN), ("替代", ALT), ("代价", COST), ("反例", FLIP)):
        hit = any(w in seg for w in kws)
        flags.append("Y" if hit else "-")
        if not hit:
            miss.append((qn, name))
    print(f"Q{qn:<4}  " + "   ".join(flags))
print("\n缺失项:", miss if miss else "无")
