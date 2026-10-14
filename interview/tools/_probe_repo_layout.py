"""诊断各仓库的真实结构：文件类型分布 + 顶层目录 + markdown 存放位置。

用 `git ls-tree -r -z` 取原始路径（避免非 ASCII 路径被引号转义），全部在 Python 里处理。
"""
import collections
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
CACHE = Path(".build-cache/crawl")


def ls_tree(repo: Path) -> list[str]:
    result = subprocess.run(["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"],
                            cwd=repo, capture_output=True)
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


for repo in sorted(CACHE.iterdir()):
    if not (repo / ".git").is_dir():
        continue
    paths = ls_tree(repo)
    ext = collections.Counter(Path(p).suffix.lower() or "(无扩展名)" for p in paths)
    md = [p for p in paths if p.lower().endswith((".md", ".markdown"))]
    print(f"=== {repo.name}：{len(paths)} 个文件，其中 markdown {len(md)} 个 ===")
    print("   类型分布：" + "  ".join(f"{k}×{v}" for k, v in ext.most_common(6)))
    if md:
        # 展示 markdown 的目录分布（取前两层）
        dirs = collections.Counter("/".join(p.split("/")[:2]) for p in md)
        print("   markdown 所在目录（前 5）：")
        for name, count in dirs.most_common(5):
            print(f"      {name:<42}{count} 个")
    else:
        print("   没有 markdown，样本路径：")
        for sample in paths[:6]:
            print(f"      {sample[:88]}")
    print()
