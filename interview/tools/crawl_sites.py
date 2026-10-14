"""按 sitemap 爬取静态八股站点，正文转 markdown 落到 interview/raw/（本地参考）。

为什么单独写一个：站点不是仓库，git 通道用不上；而且这类图解站（如小林coding）
的正文质量高、正好覆盖我们最弱的操作系统与数据库。

礼貌性：
  - 只访问 robots.txt 允许的路径（目标站点 Disallow 为空，允许抓取）
  - **1 请求/秒**的固定间隔，不并发
  - 带明确 User-Agent；失败重试 3 次并退避
  - 已抓过的文件默认跳过（--refresh 强制重抓），避免重复打扰

用法：
    .venv\\Scripts\\python.exe interview\\tools\\crawl_sites.py --list
    .venv\\Scripts\\python\\my_blog> .venv\\Scripts\\python.exe interview\\tools\\crawl_sites.py --only xiaolincoding
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "interview" / "raw"
UA = "personal-study-crawler/1.1 (single user, local notes; respects robots.txt)"
DELAY_SECONDS = 1.0

SITES = [
    {
        "id": "xiaolincoding",
        "name": "小林coding",
        "home": "https://xiaolincoding.com",
        "license": "",
        "note": "图解网络 / 操作系统 / MySQL / Redis，计算机基础最经典的图解八股（仅本地参考）",
        "content_selectors": ["div.theme-default-content", "main", "article", "div.content"],
        # 只要技术正文，跳过面经爆料 / 薪资 / 读者来信这类噪音
        "include": ["network/", "os/", "mysql/", "redis/", "cs_learn/", "openai/", "interview/"],
    },
]


def http_get(url: str, timeout: int = 25) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404, 410):
                return None
        except Exception:
            pass
        time.sleep(2.0 * (attempt + 1))
    return None


def sitemap_urls(home: str) -> list[str]:
    xml = http_get(f"{home}/sitemap.xml")
    if not xml:
        return []
    return re.findall(r"<loc>\s*(.*?)\s*</loc>", xml)


def extract_markdown(html: str, url: str, selectors: list[str]) -> str:
    """从 HTML 里取正文并转成 markdown。"""
    import html2text
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.split("|")[0].strip()

    node = None
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            break
    node = node or soup.body or soup

    converter = html2text.HTML2Text()
    converter.body_width = 0          # 不要按宽度硬折行
    converter.ignore_images = True    # 图片不落地
    converter.ignore_emphasis = False
    converter.protocol = ""
    text = converter.handle(str(node))
    # 正文开头的 h1 和标题重复，去掉一个
    text = re.sub(r"^\s*#\s+.*?\n", "", text, count=1) if title else text
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    header = f"# {title}\n\n> 来源：{url}\n\n" if title else f"> 来源：{url}\n\n"
    return header + text + "\n"


def main() -> int:
    if "--list" in sys.argv:
        for site in SITES:
            print(f"{site['id']:<16}{site['home']:<32}{site['note']}")
        return 0

    only = []
    for index, arg in enumerate(sys.argv):
        if arg == "--only":
            only = sys.argv[index + 1:]
    refresh = "--refresh" in sys.argv
    limit = 0
    for index, arg in enumerate(sys.argv):
        if arg == "--max":
            limit = int(sys.argv[index + 1])

    manifest: list[dict] = []
    for site in SITES:
        if only and site["id"] not in only:
            continue
        urls = sitemap_urls(site["home"])
        if not urls:
            print(f"[{site['id']}] 取不到 sitemap，跳过")
            continue
        include = site.get("include") or []
        if include:
            urls = [u for u in urls
                    if any(u.split(site["home"].replace("https://", "https://www."), 1)[-1]
                           .lstrip("/").startswith(prefix) for prefix in include)]
        if limit:
            urls = urls[:limit]
        out_dir = RAW / site["id"]
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"[{site['id']}] {len(urls)} 个页面，1 请求/秒，已抓过的跳过……", flush=True)
        files = chars = skipped = failed = 0
        for index, url in enumerate(urls, 1):
            path = urllib.parse.urlparse(url).path.strip("/")
            if not path or path.endswith("/"):
                path = (path + "index").strip("/")
            target = out_dir / (re.sub(r"[^\w\-./]", "_", path) + ".md")
            if target.exists() and not refresh:
                skipped += 1
                continue
            html = http_get(url)
            time.sleep(DELAY_SECONDS)          # 礼貌间隔
            if not html:
                failed += 1
                continue
            text = extract_markdown(html, url, site["content_selectors"])
            if len(text) < 400:                # 正文太短，多半是导航页
                failed += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            files += 1
            chars += len(text)
            if index % 25 == 0:
                print(f"    进度 {index}/{len(urls)}：已抓 {files} 篇", flush=True)

        manifest.append({
            "id": site["id"], "repo": site["home"], "license": site["license"],
            "note": site["note"], "commit": "", "files": files, "chars": chars,
            "pages": len(urls), "skipped": skipped, "failed": failed,
            "fetched_at": datetime.now().isoformat(timespec="seconds"), "ok": files > 0,
        })
        print(f"[{site['id']}] 完成：新抓 {files} 篇 / {chars / 10000:.1f} 万字"
              f"（跳过 {skipped}，失败 {failed}）")

    # 合并进总清单（站点条目单独记，避免覆盖仓库清单）
    path = RAW / "_sites.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写出：{path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
