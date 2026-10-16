"""检查「缩水」的题目到底是重组还是丢内容：用关键术语做覆盖对比。"""
import re
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")


def body(path: str, num: str) -> str:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    matches = list(re.finditer(r"(?m)^### Q(\d+)\.", text))
    for index, match in enumerate(matches):
        if match.group(1) == num:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            return text[match.end():end]
    return ""


CHECKS = [
    ("数据库 Q38", "interview/market/02-数据库.md", "interview/.staging/02-数据库.md", "38",
     ["隔离级别", "连接池", "JSONB", "扩展", "优化器", "分库分表", "主从", "MVCC", "GIS", "数组", "选型"]),
    ("RAG Q11", "interview/market/08-RAG与向量检索.md", "interview/.staging/08-RAG与向量检索.md", "11",
     ["并行", "缓存", "rerank", "prefill", "首 token", "量化", "向量检索", "改写", "端到端", "量测", "瓶颈"]),
]

for label, original_path, staged_path, number, terms in CHECKS:
    before, after = body(original_path, number), body(staged_path, number)
    print(f"--- {label}：{len(before)} → {len(after)} 字 ---")
    missing = []
    for term in terms:
        in_before = term in before
        in_after = term in after
        flag = "  ← 丢了" if (in_before and not in_after) else ""
        if in_before and not in_after:
            missing.append(term)
        print(f"   {term:<12} 原文 {'有' if in_before else '无'}   改写 {'有' if in_after else '无'}{flag}")
    print(f"   结论：{'关键术语未丢失（属于重组压缩）' if not missing else '缺失术语：' + '、'.join(missing)}")
    print()
