"""调试稀疏检出失败的原因（cs-notes / llm-action）。"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def run(args, cwd=None):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()


print("sparse-checkout 是否支持 --stdin：")
code, out, err = run(["git", "sparse-checkout", "set", "-h"])
print("   ", "支持" if "--stdin" in (out + err) else "不支持")
print()

for name in ["cs-notes", "llm-action"]:
    repo = Path(".build-cache/crawl") / name
    if not (repo / ".git").is_dir():
        print(f"{name}: 没有缓存目录")
        continue
    code, out, _ = run(["git", "ls-tree", "-r", "--name-only", "-z", "HEAD"], cwd=repo)
    paths = [p for p in out.split("\0") if p.lower().endswith((".md", ".markdown"))]
    print(f"=== {name}：{len(paths)} 个 markdown ===")

    print("  init --no-cone :", run(["git", "sparse-checkout", "init", "--no-cone"], cwd=repo)[0])
    code, _, err = run(["git", "sparse-checkout", "set", "--no-cone"] + paths[:5], cwd=repo)
    print("  set（前 5 个） :", code, err[:120])
    code, out2, err = run(["git", "checkout", "-f"], cwd=repo)
    print("  checkout -f    :", code, (err or out2)[:200])
    checked_out = run(["git", "ls-files"], cwd=repo)[1].splitlines()
    print(f"  实际检出文件数 : {len(checked_out)}")
    print()
