# -*- coding: utf-8 -*-
"""把 interview/.drafts/Qn.md 里新增的三节安全拼接到原文件副本，生成暂存文件。

设计要点（安全第一）：
1. 原文件只读，绝不写入。
2. 原文的每一行都按原顺序保留，新增内容只做「插入」，不做替换 —— 保证零丢失。
3. 每一步都有断言：题号连续、题数一致、锚点唯一、原文所有代码行仍存在。
4. 表格分隔行按列宽补齐，正文行保持原样。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(r"D:\mycode\python\my_blog\interview")
SRC = ROOT / "agent-real" / "03-阿里与蚂蚁.md"
DRAFT_DIR = ROOT / ".drafts"
OUT = ROOT / ".staging" / "03-阿里与蚂蚁.md"

QS = [
    (1, 29, 57), (2, 61, 87), (3, 91, 115), (4, 119, 147), (5, 151, 177),
    (6, 181, 205), (7, 209, 233), (8, 237, 265), (9, 269, 294), (10, 298, 329),
    (11, 333, 358), (12, 362, 385), (13, 389, 415), (14, 419, 442), (15, 446, 481),
    (16, 485, 512), (17, 516, 543), (18, 547, 578), (19, 582, 621), (20, 625, 660),
    (21, 664, 697), (22, 701, 728), (23, 732, 752),
]

ANCHOR_ANSWER = "**一句话回答**"
ANCHOR_PRINCIPLE = "**底层原理**"
ANCHOR_DERIVE = "**从原理推答案**"
ANCHOR_COUNTER = "**换个前提会怎样**"

# 草稿 <<<BLOCK xxx>>> 里的键名（不带 markdown 星号）
KEY_ANSWER = "一句话回答"
KEY_PRINCIPLE = "底层原理"
KEY_DERIVE = "从原理推答案"
KEY_COUNTER = "换个前提会怎样"

SECT_REASON = "**为什么这么做**"
SECT_CHECK = "**核实与纠错**"

BLOCK_RE = re.compile(r"<<<BLOCK\s*([^>\n]+?)\s*>>>")
END_RE = re.compile(r"<<<END>>>")

CURLY = re.compile(r"[\u201c\u201d]")


def norm_quotes(text: str) -> str:
    """新增内容里出现的弯引号统一成中文引号「」，与原文风格一致（反引号内不动）。"""
    out = []
    for ln in text.split("\n"):
        parts = ln.split("`")
        for i in range(0, len(parts), 2):
            seg = parts[i]
            if CURLY.search(seg):
                buf = []
                open_q = True
                for ch in seg:
                    if ch in "\u201c\u201d":
                        buf.append("「" if open_q else "」")
                        open_q = not open_q
                    else:
                        buf.append(ch)
                seg = "".join(buf)
            parts[i] = seg
        out.append("`".join(parts))
    return "\n".join(out)

errors: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def parse_blocks(text: str, qn: int) -> dict[str, str]:
    """把 Qn.md 解析成 {锚点名: 正文}。"""
    lines = text.replace("\ufeff", "").replace("\r\n", "\n").split("\n")
    blocks: dict[str, str] = {}
    cur_key: str | None = None
    buf: list[str] = []
    for ln in lines:
        m = BLOCK_RE.match(ln)
        if m:
            if cur_key is not None:
                fail(f"Q{qn}: 嵌套 <<<BLOCK>>>，格式错误")
            cur_key = m.group(1).strip()
            buf = []
            continue
        if END_RE.match(ln):
            if cur_key is None:
                fail(f"Q{qn}: 出现没有起始的 <<<END>>>")
                continue
            blocks[cur_key] = "\n".join(buf).strip("\n")
            cur_key = None
            buf = []
            continue
        if cur_key is not None:
            buf.append(ln)
    if cur_key is not None:
        fail(f"Q{qn}: 有未闭合的 <<<BLOCK>>>")
    return blocks


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def normalize_tables(lines: list[str]) -> list[str]:
    """表格：分隔行按列宽补齐；正文行保持原样（只做两端去空白）。"""
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if (
            ln.lstrip().startswith("|")
            and i + 1 < len(lines)
            and re.fullmatch(r"\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*", lines[i + 1])
        ):
            header = cells(ln)
            ncol = len(header)
            out.append("| " + " | ".join(header) + " |")
            out.append("| " + " | ".join(["---"] * ncol) + " |")
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cs = cells(lines[i])
                if len(cs) != ncol:
                    fail(f"表格列数不一致（表头 {ncol} 列，本行 {len(cs)} 列）：{lines[i][:70]}")
                    cs = (cs + [""] * ncol)[:ncol]
                out.append("| " + " | ".join(cs) + " |")
                i += 1
            continue
        out.append(ln.rstrip())
        i += 1
    return out


def split_reason_and_check(body: str, qn: int) -> tuple[str, str]:
    """把「为什么这么做 + 核实与纠错」拆成两段。"""
    body = body.strip("\n")
    idx = body.find(SECT_CHECK)
    if idx < 0:
        # 容错：按行匹配（忽略行首行尾空白与 BOM）
        lines = body.split("\n")
        hits = [k for k, l in enumerate(lines) if l.strip().strip("\ufeff") == SECT_CHECK]
        if len(hits) == 1:
            idx = len("\n".join(lines[: hits[0]]))
            if idx:
                idx += 1
        else:
            fail(f"Q{qn}: 在草稿里找不到「{SECT_CHECK}」（命中 {len(hits)} 次），无法拆分")
            return body, ""
    reason = body[:idx].strip("\n")
    check = body[idx:].strip("\n")
    if SECT_REASON not in reason:
        fail(f"Q{qn}: 草稿里缺少「{SECT_REASON}」小标题")
    return reason, check


def block_of(lines: list[str], start: int) -> tuple[int, int]:
    """给定 0-based 起始行（必须匹配 ^### Qn.），返回到下一个 ### 或文件尾的区间。"""
    n = len(lines)
    i = start + 1
    while i < n and not lines[i].startswith("### "):
        i += 1
    return start, i


def main() -> int:
    src_text = SRC.read_text(encoding="utf-8-sig")
    if "\r\n" in src_text:
        fail("原文件含 CRLF，预期为 LF")
    src_lines = src_text.split("\n")

    # ---- 1. 定位 23 个题块 ----
    heads = [(i, ln) for i, ln in enumerate(src_lines) if re.match(r"^### Q\d+\. ", ln)]
    if len(heads) != len(QS):
        fail(f"原文件题数 {len(heads)} != 预期 {len(QS)}")
        return report(None)
    spans: list[tuple[int, int, int]] = []
    for (qn, lo, hi), (idx, head) in zip(QS, heads):
        m = re.match(r"^### Q(\d+)\. ", head)
        if int(m.group(1)) != qn:
            fail(f"题号不连续：期望 Q{qn}，实际 {head[:20]}")
        if idx + 1 != lo:
            fail(f"Q{qn}: 期望起始行 {lo}，实际 {idx + 1}（原文件可能已变动）")
        s, e = block_of(src_lines, idx)
        if not (e - 1 >= hi):
            fail(f"Q{qn}: 题块结束行 {e - 1} 早于预期 {hi}")
        spans.append((qn, s, e))

    if errors:
        return report(None)

    # ---- 2. 解析草稿 ----
    reason_by_q: dict[int, str] = {}
    check_by_q: dict[int, str] = {}
    misc_by_q: dict[int, str] = {}
    for qn, lo, hi in QS:
        path = DRAFT_DIR / f"Q{qn}.md"
        if not path.exists():
            fail(f"Q{qn}: 缺少草稿文件 {path}")
            continue
        blocks = parse_blocks(path.read_text(encoding="utf-8"), qn)
        if KEY_PRINCIPLE not in blocks:
            fail(f"Q{qn}: 草稿缺少「{KEY_PRINCIPLE}」块")
            continue
        if KEY_DERIVE not in blocks:
            fail(f"Q{qn}: 草稿缺少「{KEY_DERIVE}」块")
            continue
        # 草稿把「为什么这么做」+「核实与纠错」同时放在 底层原理 块里；
        # 从原理推答案 块只承载「核实与纠错」的续写部分（通常为空）。
        merged = blocks[KEY_PRINCIPLE] + "\n" + blocks[KEY_DERIVE]
        reason, check = split_reason_and_check(merged, qn)
        reason_by_q[qn] = reason
        check_by_q[qn] = check
        for k in blocks:
            if k not in (KEY_PRINCIPLE, KEY_ANSWER, KEY_DERIVE, KEY_COUNTER):
                fail(f"Q{qn}: 未知块「{k}」")
        if KEY_COUNTER not in blocks or "网上常见误区" not in blocks.get(KEY_COUNTER, ""):
            fail(f"Q{qn}: 草稿缺少「网上常见误区」")
        if blocks.get(KEY_COUNTER, "").strip() == "":
            fail(f"Q{qn}: 「网上常见误区」为空")

    if errors:
        return report(None)

    # ---- 3. 逐题插入 ----

    def find(labels: list[str], target: str, qn: int) -> int:
        """标签行以 target 开头（形如 `**一句话回答**：内容...` 或独立一行）。"""
        hits = [k for k, l in enumerate(labels) if l.strip().startswith(target)]
        if len(hits) != 1:
            fail(f"Q{qn}: 原文标签「{target}」命中 {len(hits)} 次（期望 1 次）")
            return -1
        return hits[0]

    out_lines: list[str] = []
    cursor = 0
    for qn, s, e in spans:
        out_lines.extend(src_lines[cursor:s])
        seg = list(src_lines[s:e])
        i_ans = find(seg, ANCHOR_ANSWER, qn)
        if i_ans < 0:
            return report(None)
        # 「为什么这么做」：紧跟在 **一句话回答** 那一行之后、其正文之前
        seg = seg[: i_ans + 1] + [""] + reason_by_q[qn].split("\n") + [""] + seg[i_ans + 1 :]
        # 「核实与纠错」：放在「从原理推答案」小节正文之后。
        # 该小节的结束位置 = 其后第一个「节标签」行；没有则整题末尾（题块已在 `---` 前收尾）。
        i_der = find(seg, ANCHOR_DERIVE, qn)
        if i_der < 0:
            return report(None)
        stop_labels = (
            ANCHOR_COUNTER,
            "**常见追问**",
            "**容易答错的边界**",
            "**面经说法**",
            "---",
        )
        i_stop = len(seg)
        for j in range(i_der + 1, len(seg)):
            if any(seg[j].strip().startswith(x) for x in stop_labels):
                i_stop = j
                break
        seg = seg[:i_stop] + check_by_q[qn].split("\n") + [""] + seg[i_stop:]
        # 4. 本题所有新增内容统一引号风格
        seg = norm_quotes("\n".join(seg)).split("\n")
        out_lines.extend(seg)
        cursor = e
    out_lines.extend(src_lines[cursor:])

    # ---- 4. 表格规范化 + 压缩多余空行 ----
    out_lines = normalize_tables(out_lines)
    final: list[str] = []
    for ln in out_lines:
        if ln == "" and final and final[-1] == "":
            continue
        final.append(ln)
    if final and final[-1] != "":
        final.append("")
    text = "\n".join(final)

    # ---- 5. 断言：结构 + 零丢失 ----
    check_lines = text.split("\n")
    heads2 = [(i, ln) for i, ln in enumerate(check_lines) if re.match(r"^### Q\d+\. ", ln)]
    if len(heads2) != len(QS):
        fail(f"产出题数 {len(heads2)} != {len(QS)}")
    for (qn, _, _), (_, head) in zip(QS, heads2):
        if not re.match(rf"^### Q{qn}\. ", head):
            fail(f"产出题号错序：期望 Q{qn}，实际 {head[:20]}")

    for idx, head in heads2:
        qn = int(re.match(r"^### Q(\d+)\. ", head).group(1))
        end = idx + 1
        while end < len(check_lines) and not check_lines[end].startswith("### ") and not check_lines[end].startswith("## "):
            end += 1
        body = check_lines[idx:end]
        # 要求顺序：一句话回答 → 为什么这么做 → 底层原理 → 从原理推答案 → 核实与纠错
        #          → 网上常见误区 → 换个前提会怎样 → 常见追问 → 容易答错的边界
        order = [
            (ANCHOR_ANSWER, "一句话回答"),
            (SECT_REASON, "为什么这么做"),
            (ANCHOR_PRINCIPLE, "底层原理"),
            (ANCHOR_DERIVE, "从原理推答案"),
            (SECT_CHECK, "核实与纠错"),
            ("**网上常见误区**", "网上常见误区"),
            (ANCHOR_COUNTER, "换个前提会怎样"),
            ("**常见追问**", "常见追问"),
            ("**容易答错的边界**", "容易答错的边界"),
        ]
        pos = -1
        for label, name in order:
            hits = [k for k, l in enumerate(body) if l.strip().startswith(label)]
            if len(hits) == 0:
                # 少数题缺 **换个前提会怎样** 这类可选小节，只提示不报错
                continue
            if len(hits) > 1:
                fail(f"Q{qn}: 标签「{name}」命中 {len(hits)} 次（期望 1 次）")
                continue
            if hits[0] <= pos:
                fail(f"Q{qn}: 标签「{name}」顺序错误（第 {hits[0] + 1} 行）")
            pos = hits[0]
        # 「为什么这么做」「核实与纠错」「网上常见误区」三节必须存在
        for label, name in (
            (SECT_REASON, "为什么这么做"),
            (SECT_CHECK, "核实与纠错"),
            ("**网上常见误区**", "网上常见误区"),
        ):
            hits = [k for k, l in enumerate(body) if l.strip().startswith(label)]
            if len(hits) != 1:
                continue
            # 该节后面必须有实际内容（至少 5 行非空）
            after = [l for l in body[hits[0] + 1 :] if l.strip()]
            if len(after) < 5:
                fail(f"Q{qn}: 「{name}」节内容过少（后续非空行 {len(after)} 行）")
        # 列表项 / 表格前必须有空行
        for k, l in enumerate(body):
            if re.match(r"^\s*([-*+] |\d+\. )", l) or l.startswith("|"):
                if k > 0 and body[k - 1].strip() != "" and not body[k - 1].lstrip().startswith("|"):
                    if not re.match(r"^\s*([-*+] |\d+\. )", body[k - 1]):
                        fail(f"Q{qn}: 列表项/表格前缺空行 -> {l[:50]}")

    # 原文所有非空行必须仍然存在（按多重集比较）
    from collections import Counter

    src_nonblank = Counter(l for l in src_lines if l.strip())
    out_nonblank = Counter(l for l in check_lines if l.strip())
    missing = src_nonblank - out_nonblank
    if missing:
        fail(f"原文有 {sum(missing.values())} 行丢失，示例：{list(missing.items())[:3]}")
    # 三节必须保留
    for need in ("## 一、真题与答案", "## 二、这家公司的考察偏好", "## 三、查不到的部分"):
        if need not in text:
            fail(f"缺少原有章节：{need}")
    # 表格列数统计
    tables = len(re.findall(r"^\| 结论 \| 判定 \| 依据 \|$", text, flags=re.M))

    stats = {
        "bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "questions": len(heads2),
        "tables": tables,
        "check_rows": len(re.findall(r"^\| (?!结论 \| 判定)", text, flags=re.M)),
        "ok": len(re.findall(r"✅ 站得住", text)),
        "warn": len(re.findall(r"⚠️ 不严谨", text)),
        "err": len(re.findall(r"❌ 网上常见错说法", text)),
        "unverified": len(re.findall(r"未核实", text)),
        "lines": len(check_lines),
    }

    if errors:
        return report(None)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    return report(stats)


def report(stats: dict | None) -> int:
    if errors:
        print("FAILED")
        for e in errors[:40]:
            print(" -", e)
        print(f"共 {len(errors)} 处问题")
        return 1
    if stats is None:
        print("NO STATS")
        return 1
    print("OK")
    for k, v in stats.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
