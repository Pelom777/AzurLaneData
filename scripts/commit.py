"""提交并推送（只提交明确列出的文件）。

为什么不用 `git add -A`：
在"跳过 7 GB 资产"的工作副本里，绝大多数资产文件并不在磁盘上，
`git add -A` 会把它们全部当成删除提交上去 —— 一次就能删掉两万多个文件。
所以这里要么给文件清单（--paths-from），要么给目录但明确知道目录下内容都已落盘。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import commit_and_push, log, set_output  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", nargs="*", default=[], help="要提交的目录（相对仓库根）")
    ap.add_argument("--paths-from", default="",
                    help="文件清单（NUL 分隔，相对仓库根），优先于 --paths")
    ap.add_argument("--message", "-m", required=True)
    ap.add_argument("--branch", default="")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    if not args.paths and not args.paths_from:
        ap.error("--paths 与 --paths-from 至少要给一个")

    committed = commit_and_push(
        cwd=Path.cwd(),
        message=args.message,
        paths=args.paths or None,
        branch=args.branch or None,
        push=not args.no_push,
        paths_from=args.paths_from or None,
    )
    set_output(committed=committed)
    log("完成" if committed else "无变更")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
