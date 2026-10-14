"""把 interview/agent-baguwen/_paid/ 下的分品类调研合成一份可读的《付费内容地图》。

为什么单独生成：`_paid/` 以 `_` 开头，构建时会跳过（不发布），
但这份地图本身很有价值——它告诉用户市面上的付费 Agent 内容都在讲什么、
哪些我们已经覆盖、哪些确实要花钱买。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BAGUWEN = ROOT / "interview" / "agent-baguwen"
PAID = BAGUWEN / "_paid"

ORDER = ["geekbang", "juejin", "training", "community"]
LABELS = {
    "geekbang": "极客时间 · Agent 相关专栏",
    "juejin": "掘金小册 · Agent / 大模型应用",
    "training": "训练营式课程（拉勾、慕课、黑马等）",
    "community": "知识星球 / 付费社群 / 付费 PDF",
}


def main() -> int:
    if not PAID.is_dir():
        print("还没有 _paid/ 调研文件，先跑调研")
        return 1

    files = sorted(PAID.glob("*.md"),
                   key=lambda p: ORDER.index(p.stem) if p.stem in ORDER else 99)
    if not files:
        print("_paid/ 下没有 md 文件")
        return 1

    total_products = 0
    bodies: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8").strip()
        # 统计「### <产品名>」数量，去掉「本品类小结」这类小节
        headings = [h for h in re.findall(r"^###\s+(.+)$", text, re.M)
                    if "小结" not in h and "方案" not in h]
        total_products += len(headings)
        label = LABELS.get(path.stem, path.stem)
        # 规范化：把顶层标题统一成二级，避免和本文档结构冲突
        text = re.sub(r"^##\s+", "### ", text, flags=re.M)
        bodies.append(f"## {label}（{len(headings)} 个产品）\n\n{text}\n")

    lines = [
        "# 付费内容地图 · Agent 方向",
        "",
        "> **这份地图是什么**：把中文市场上主流的**付费** Agent 内容（课程、小册、训练营、星球）的",
        "> **公开目录/大纲**整理到一处，用来回答两个问题：",
        "> 1. 我该不该买？买了能不能补上我缺的？",
        "> 2. 它讲的东西，我是不是已经用免费资料覆盖了？",
        ">",
        "> **边界说明**：本文件只收录**公开可见**的信息（课程介绍页、公开目录、免费试读标题、第三方评测）。",
        "> 付费正文没有被获取、也没有被收录——需要正文请自行购买。",
        "",
        f"共整理 **{total_products}** 个付费产品，分 {len(files)} 个品类。",
        "",
        "## 怎么用这份地图",
        "",
        "1. 先看每个产品的「公开目录」，对照同目录下的 `07-大厂面经真题与八股速答.md`：",
        "   目录里反复出现而你答不上的，就是你的缺口。",
        "2. 再看「看起来和别人的差异点」：付费内容真正值钱的是**别人的工程经验**，",
        "   而不是知识点本身——知识点在官方文档里都能免费查到。",
        "3. 判断标准：如果某门课的目录里，超过一半你已经在主线或专项章节里覆盖了，那它对你性价比不高。",
        "",
    ]
    lines += bodies
    out = BAGUWEN / "00-付费内容地图.md"
    BAGUWEN.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"已合成 {out.relative_to(ROOT)}：{total_products} 个产品 / {len(files)} 个品类")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
