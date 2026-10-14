"""核实「Agent 八股」候选源：是否存在、许可证、体积（走 GitHub API）。"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
UA = "personal-study-crawler/1.2 (local, single user)"

CANDIDATES = [
    # 官方 Agent 指南 / 示例（免费权威，付费课多以此为底）
    "anthropics/anthropic-cookbook",
    "anthropics/anthropic-quickstarts",
    "openai/openai-cookbook",
    "openai/openai-agents-python",
    # 免费课程 / 工程原则
    "microsoft/ai-agents-for-beginners",
    "humanlayer/12-factor-agents",
    "huggingface/agents-course",
    # 协议与框架文档
    "modelcontextprotocol/modelcontextprotocol",
    "langchain-ai/langgraph",
    "google/adk-python",
    "microsoft/autogen",
    "crewAIInc/crewAI",
    # 评测与实战案例
    "confident-ai/deepeval",
    "NirDiamant/GenAI_Agents",
    # 中文多智能体 / RAG 应用
    "datawhalechina/handy-multi-agent",
    "datawhalechina/llm-universe",
]

print(f"{'仓库':<46}{'★':>7}{'MB':>6}  {'许可证':<14}说明")
print("-" * 122)
results = []
for full_name in CANDIDATES:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{full_name}",
        headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
        license_name = (data.get("license") or {}).get("spdx_id") or "无"
        stars = data.get("stargazers_count", 0)
        size = data.get("size", 0) / 1024
        desc = (data.get("description") or "")[:40]
        results.append({"repo": full_name, "stars": stars, "size_mb": round(size, 1),
                        "license": license_name, "branch": data.get("default_branch")})
        print(f"{full_name:<46}{stars:>7}{size:>6.1f}  {license_name:<14}{desc}")
    except urllib.error.HTTPError as exc:
        print(f"{full_name:<46}{'—':>7}{'—':>6}  {'—':<14}HTTP {exc.code}")
    except Exception as exc:
        print(f"{full_name:<46}{'—':>7}{'—':>6}  {'—':<14}{type(exc).__name__}")
    time.sleep(0.3)

out = Path("interview/raw/_agent_candidates.json")
out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n可用 {len(results)} 个，已写出 {out}")
