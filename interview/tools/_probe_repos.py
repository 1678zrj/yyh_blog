"""核实候选爬取源的许可证、规模与体量（走 GitHub API，不落盘）。"""
import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
UA = "personal-study-crawler/1.0 (local, single-user)"

CANDIDATES = [
    # 计算机基础 / 后端八股
    "CyC2018/CS-Notes",
    "Snailclimb/JavaGuide",
    "donnemartin/system-design-primer",
    # LLM / Agent
    "wdndev/llm_interview_note",
    "datawhalechina/self-llm",
    "liguodongiot/llm-action",
    "luhengshiwo/LLMForEverybody",
    # 算法 / 手撕
    "afatcoder/LeetcodeTop",
    "youngyangyang04/leetcode-master",
    # Python
    "taizilongxu/interview_python",
    "chinesehuazhou/python-whydo",
    "Prodesire/Python-Guide-CN",
]

print(f"{'仓库':<42}{'★':>7}{'体积MB':>8}  {'许可证':<18} 说明")
print("-" * 118)
results = []
for full_name in CANDIDATES:
    url = f"https://api.github.com/repos/{full_name}"
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        license_name = (data.get("license") or {}).get("spdx_id") or "无"
        results.append({
            "repo": full_name,
            "stars": data.get("stargazers_count"),
            "size_mb": round(data.get("size", 0) / 1024, 1),
            "license": license_name,
            "desc": (data.get("description") or "")[:44],
            "branch": data.get("default_branch"),
        })
        print(f"{full_name:<42}{data.get('stargazers_count', 0):>7}"
              f"{data.get('size', 0) / 1024:>8.1f}  {license_name:<18} {(data.get('description') or '')[:44]}")
    except urllib.error.HTTPError as exc:
        print(f"{full_name:<42}{'—':>7}{'—':>8}  {'—':<18} HTTP {exc.code}")
    except Exception as exc:
        print(f"{full_name:<42}{'—':>7}{'—':>8}  {'—':<18} {type(exc).__name__}")
    time.sleep(0.4)          # 对 API 也保持礼貌

out = __import__("pathlib").Path("interview/raw/_candidates.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n已写出候选清单：{out}（{len(results)} 个可用）")
