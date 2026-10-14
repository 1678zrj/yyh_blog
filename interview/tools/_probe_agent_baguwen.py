"""核实「Agent 八股」专项候选（免费但高度对口），并预览关键文件质量。"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
UA = {"User-Agent": "personal-study-crawler/1.2 (local, single user)",
      "Accept": "application/vnd.github+json"}

CANDIDATES = [
    "ranxi2001/zero2Agent",
    "datawhalechina/hello-agents",
    "youngyangyang04/llm-master",
    "wdndev/llm_interview_note",
    "datawhalechina/handbook-multi-agent",
]

for full_name in CANDIDATES:
    try:
        info = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://api.github.com/repos/{full_name}", headers=UA), timeout=25).read())
        license_name = (info.get("license") or {}).get("spdx_id") or "无"
        print(f"{full_name:<40}★{info.get('stargazers_count', 0):>6}  "
              f"{info.get('size', 0) / 1024:>6.1f}MB  {license_name:<12} "
              f"{(info.get('description') or '')[:46]}")
    except Exception as exc:
        print(f"{full_name:<40}失败：{type(exc).__name__}")
    time.sleep(0.3)

print("\n=== 这三个仓库里的「八股/面试」相关文件 ===")
for full_name in ["ranxi2001/zero2Agent", "datawhalechina/hello-agents", "youngyangyang04/llm-master"]:
    try:
        info = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://api.github.com/repos/{full_name}", headers=UA), timeout=25).read())
        branch = info.get("default_branch", "main")
        tree = json.loads(urllib.request.urlopen(urllib.request.Request(
            f"https://api.github.com/repos/{full_name}/git/trees/{branch}?recursive=1",
            headers=UA), timeout=30).read())
        paths = [n["path"] for n in tree.get("tree", []) if n.get("type") == "blob"]
        md = [p for p in paths if p.lower().endswith((".md", ".mdx"))]
        hits = [p for p in md if any(k in p.lower() for k in
                                     ("interview", "面试", "八股", "question", "faq", "总结"))]
        print(f"\n{full_name}：{len(paths)} 个文件，其中 md {len(md)} 个，"
              f"面试/八股相关 {len(hits)} 个")
        for path in hits[:12]:
            print(f"    {path}")
    except Exception as exc:
        print(f"{full_name} 列目录失败：{type(exc).__name__}")
    time.sleep(0.3)
