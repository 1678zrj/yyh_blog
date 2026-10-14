"""可行性探测：确认爬取所需的工具链与目标站点可达性（只读，不落盘）。"""
import importlib
import socket
import ssl
import sys
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

print("=== 1) Python 库 ===")
for name in ["requests", "bs4", "lxml", "html2text", "markdownify", "yaml"]:
    try:
        module = importlib.import_module(name)
        print(f"  {name:<12} OK   {getattr(module, '__version__', '')}")
    except Exception as exc:
        print(f"  {name:<12} 缺失（{type(exc).__name__}）")

print("\n=== 2) 目标站点可达性（各取一个 GET，看状态与体积）===")
UA = "Mozilla/5.0 (compatible; personal-study-crawler/1.0; +local)"
TARGETS = [
    ("GitHub raw 文件", "https://raw.githubusercontent.com/taizilongxu/interview_python/master/README.md"),
    ("GitHub API 目录", "https://api.github.com/repos/CyC2018/CS-Notes/contents/"),
    ("小林coding 站点", "https://xiaolincoding.com/"),
    ("JavaGuide 站点", "https://javaguide.cn/"),
    ("牛客网", "https://www.nowcoder.com/"),
]
for label, url in TARGETS:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            print(f"  {label:<18} HTTP {response.status}  {len(body) / 1024:>8.1f} KB  {response.headers.get('Content-Type', '')[:40]}")
    except urllib.error.HTTPError as exc:
        print(f"  {label:<18} HTTP {exc.code}（{exc.reason}）")
    except Exception as exc:
        print(f"  {label:<18} 失败：{type(exc).__name__} {exc}")

print("\n=== 3) 礼貌性：看目标站点是否给了 robots.txt 与 sitemap ===")
for url in ["https://xiaolincoding.com/robots.txt", "https://xiaolincoding.com/sitemap.xml",
            "https://javaguide.cn/robots.txt", "https://javaguide.cn/sitemap.xml"]:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
            head = " ".join(body.split())[:150]
            print(f"  {url:<48} HTTP {response.status}  {len(body):>6} 字节  {head}")
    except urllib.error.HTTPError as exc:
        print(f"  {url:<48} HTTP {exc.code}")
    except Exception as exc:
        print(f"  {url:<48} 失败：{type(exc).__name__}")

print("\n=== 4) 本机网络出口 ===")
try:
    print("  出网 IP:", urllib.request.urlopen(
        urllib.request.Request("https://api.ipify.org", headers={"User-Agent": UA}), timeout=15
    ).read().decode())
except Exception as exc:
    print("  取出口 IP 失败：", type(exc).__name__)
