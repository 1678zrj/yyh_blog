"""验证 git cat-file --batch 能否一次性批量取回所需 blob（含按需拉取），并测速。"""
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

for name in ["cs-notes", "llm-action", "llm-everybody"]:
    repo = Path(".build-cache/crawl") / name
    if not (repo / ".git").is_dir():
        print(f"{name}: 无缓存")
        continue

    listing = subprocess.run(["git", "ls-tree", "-r", "-z", "HEAD"], cwd=repo,
                             capture_output=True).stdout.decode("utf-8", "replace")
    entries = []
    for record in listing.split("\0"):
        if not record.strip():
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) >= 3 and path.lower().endswith((".md", ".markdown")):
            entries.append((fields[2], path))          # (sha, path)

    print(f"=== {name}：{len(entries)} 个 markdown，尝试批量取回 ===")
    if not entries:
        continue

    payload = "\n".join(sha for sha, _ in entries) + "\n"
    started = time.time()
    result = subprocess.run(["git", "cat-file", "--batch"], cwd=repo, input=payload.encode(),
                            capture_output=True)
    elapsed = time.time() - started
    if result.returncode != 0:
        print(f"   ✗ 失败：{result.stderr.decode('utf-8', 'replace')[:200]}")
        continue

    # 解析 batch 输出
    stream = result.stdout
    cursor = 0
    got = total = 0
    while cursor < len(stream):
        newline = stream.find(b"\n", cursor)
        if newline < 0:
            break
        header = stream[cursor:newline].decode("utf-8", "replace").split()
        cursor = newline + 1
        if len(header) < 3:
            break
        size = int(header[2])
        body = stream[cursor:cursor + size]
        cursor += size + 1
        if header[1] == b"blob" or header[1] == "blob":
            got += 1
            total += size
    print(f"   ✓ 取回 {got} 个 blob / {total / 10000:.1f} 万字，耗时 {elapsed:.1f} 秒")
