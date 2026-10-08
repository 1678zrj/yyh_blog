"""构建后自检：扫描 site/ 下所有 HTML 的内部链接，报告死链。

用法：.venv\\Scripts\\python.exe scripts\\check_links.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

HREF_RE = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"')
CODE_RE = re.compile(r"<(?:pre|code|script|style)\b[^>]*>.*?</(?:pre|code|script|style)>", re.S)
SKIP_PREFIX = ("http://", "https://", "mailto:", "tel:", "javascript:", "data:", "#", "&#")

# 站点部署在 https://<user>.github.io/<repo>/ 这样的子路径下，
# 页面里以 / 开头的绝对链接会带这个前缀，检查时要能识别
def read_base_path() -> str:
    config = Path(__file__).resolve().parent.parent / "mkdocs.yml"
    if config.is_file():
        match = re.search(r"^site_url:\s*(\S+)", config.read_text(encoding="utf-8"), re.M)
        if match:
            path = urlsplit(match.group(1)).path.rstrip("/")
            if path:
                return path
    return ""


BASE_PATH = read_base_path()


def target_exists(site: Path, page: Path, url: str) -> bool:
    path = unquote(urlsplit(url).path)
    if not path:
        return True

    candidates: list[Path] = []
    if path.startswith("/"):
        stripped = path
        if path.startswith(BASE_PATH + "/") or path == BASE_PATH:
            stripped = path[len(BASE_PATH):] or "/"
        candidates.append(site / stripped.lstrip("/"))
    else:
        candidates.append((page.parent / path).resolve())

    for candidate in candidates:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return True
        if candidate.is_file():
            return True
        if candidate.suffix == "":
            if candidate.with_suffix(".html").is_file():
                return True
    return False


def main() -> int:
    site = Path(__file__).resolve().parent.parent / "site"
    if not site.is_dir():
        print("找不到 site/，请先执行 mkdocs build")
        return 1

    pages = sorted(site.rglob("*.html"))
    broken: list[tuple[str, str]] = []
    for page in pages:
        html = page.read_text(encoding="utf-8", errors="replace")
        html = CODE_RE.sub("", html)          # 代码块里的示例链接不算
        for url in sorted(set(HREF_RE.findall(html))):
            if url.startswith(SKIP_PREFIX):
                continue
            if not target_exists(site, page, url):
                broken.append((str(page.relative_to(site)), url))

    print(f"检查 {len(pages)} 个页面，发现 {len(broken)} 条死链")
    for page, url in broken[:40]:
        print(f"  {page}  ->  {url}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
