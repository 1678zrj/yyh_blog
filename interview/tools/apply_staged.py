"""把通过闸门的暂存改写稿替换到正式文件（带快照与逐项校验）。

流程：代理只写 interview/.staging/<文件名> → 本脚本逐份校验 → 通过的先快照再替换，
不通过的原样保留、打印原因，绝不半途覆盖。

用法：
    .venv\\Scripts\\python.exe interview\\tools\\apply_staged.py [--dry-run]
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW = ROOT / "interview"
STAGING = INTERVIEW / ".staging"
SNAPSHOTS = INTERVIEW / ".snapshots"
SKIP_DIRS = {".staging", ".snapshots", "raw", "tools", "_parts"}

sys.path.insert(0, str(Path(__file__).parent))
from verify_rewrite import evaluate  # noqa: E402


def find_original(name: str) -> Path | None:
    for path in INTERVIEW.rglob(name):
        if path.is_file() and not any(part in SKIP_DIRS for part in path.parts):
            return path
    return None


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    if not STAGING.is_dir():
        print("没有暂存目录，无需替换")
        return 0

    staged_files = sorted(path for path in STAGING.glob("*.md") if path.is_file())
    if not staged_files:
        print("暂存目录里没有 md 文件")
        return 0

    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    applied = skipped = 0

    print(f"{'文件':<40}{'题数':>8}{'字数变化':>14}  结果")
    print("-" * 88)
    for staged in staged_files:
        original = find_original(staged.name)
        if original is None:
            print(f"{staged.name:<40}{'—':>8}{'—':>14}  ✗ 找不到对应正本")
            skipped += 1
            continue

        result = evaluate(original, staged)
        ratio = result["after_chars"] / max(result["before_chars"], 1)
        stat = f"{result['before_questions']}→{result['after_questions']}"
        change = f"{result['before_chars']}→{result['after_chars']}"

        if not result["ok"]:
            print(f"{staged.name:<40}{stat:>8}{change:>14}  ❌ 未通过")
            for item in result["problems"][:3]:
                print(f"    - {item}")
            skipped += 1
            continue

        if dry_run:
            print(f"{staged.name:<40}{stat:>8}{change:>14}  ✅ 可替换（{ratio:.0%}，dry-run）")
            applied += 1
            continue

        # 先快照正本，再替换
        backup = SNAPSHOTS / f"{original.stem}.{stamp}{original.suffix}"
        shutil.copy2(original, backup)
        shutil.copy2(staged, original)
        print(f"{staged.name:<40}{stat:>8}{change:>14}  ✅ 已替换（{ratio:.0%}）")
        applied += 1

    print("-" * 88)
    print(f"{'（dry-run）' if dry_run else ''}通过 {applied} 份，跳过 {skipped} 份")
    if applied and not dry_run:
        print("快照目录：interview/.snapshots/")
        print("替换后记得重跑：build_interview.py → build_private.py → mkdocs build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
