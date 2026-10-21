"""按 R2 远端对象清单，在本地补出"产物已存在"的标记文件。

为什么需要这个脚本
------------------
`azl_assets.py` 判断某个资源「处理过没有」，要求**两个条件同时成立**：

1. state 里记着这个 key 的 md5（`prev.get(key) == info['md5']`）
2. 产物文件在磁盘上（`marker_file.exists()`）

CI 的每次运行都是干净 checkout，`public/painting` 等目录**被 .gitignore 挡在
git 之外**，所以条件 2 永远为假 —— 于是即使 state 完整，每一轮也会把 13,000
个资源全部重下重解（约 5.9 GB 下载 / 2~4 小时），并重复上传同一批对象。

state 是「已经产出并已推送」的账本，**远端存在同名对象就是条件 2 的等价证据**。
所以拿 `rclone lsf` 拉一份远端 key 列表，在本地 touch 出同名空文件，
判据就被还原成原本的语义：远端有的跳过，远端没有的才重新生成。

配合 `--seed` 后四种情况都正确：

| state | 远端 | 行为 |
|---|---|---|
| 有   | 有 | 跳过（正常增量） |
| 无   | 有 | `--seed` 补记录后跳过（手工搬上去的 spine 之类不会重下） |
| 有   | 无 | 重新生成 + 上传（远端丢了能自愈） |
| 无   | 无 | 生成 + 上传（新资源） |

只补两类 marker，与 `azl_assets.py` 的判据一一对应：
    painting/<name>/<name>.json      ← painting 的 marker 是 json，不是 png
    <item>/<name>.png                ← 其余类别

用法：
    # --recursive 必须加：rclone lsf 默认只列一层，产物都在 <item>/ 下
    rclone lsf r2:azurlane-assets --files-only --recursive > .azl/remote.lst
    python scripts/mark_remote.py --listing .azl/remote.lst --public public
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# azl_assets.py 里 CATEGORY_LIST 有产物的类别；marker 一律是 <name>.png
PNG_MARKER_ITEMS = {
    "paintingface", "squareicon", "shipicon", "equipicon", "iconframe",
    "item", "bg", "skillicon", "herohrzicon", "equips", "shipyardicon",
    "memoryicon", "neweducateicon", "qicon",
}


def log(msg: str) -> None:
    print(f"[mark] {msg}", flush=True)


def is_marker(key: str) -> bool:
    """远端 key 是否对应 azl_assets.py 会检查的那种 marker 文件。"""
    parts = key.split("/")
    if len(parts) == 3:
        item, folder, fname = parts
        return item == "painting" and fname == f"{folder}.json"
    if len(parts) == 2:
        item, fname = parts
        return item in PNG_MARKER_ITEMS and fname.endswith(".png")
    return False


def safe(key: str) -> bool:
    """挡住绝对路径和 ../，清单来自远端，但不该无条件信任。"""
    if not key or key.startswith("/") or "\\" in key:
        return False
    return ".." not in key.split("/")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", required=True,
                    help="rclone lsf --files-only 的输出，每行一个对象 key")
    ap.add_argument("--public", default="public", help="本地产物根目录")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.listing)
    if not src.exists():
        log(f"清单不存在: {src}（桶为空时属正常，跳过）")
        return 0

    public = Path(args.public)
    public.mkdir(parents=True, exist_ok=True)

    keys = [ln.strip() for ln in src.read_text("utf-8", "replace").splitlines() if ln.strip()]
    made = existed = ignored = bad = 0
    for key in keys:
        if not safe(key):
            bad += 1
            continue
        if not is_marker(key):
            ignored += 1
            continue
        dest = public / key
        if dest.exists():
            existed += 1
            continue
        made += 1
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.touch()

    log(f"远端对象 {len(keys)} 个 → marker 补齐 {made} / 已存在 {existed} / "
        f"非 marker {ignored} / 非法 {bad}")
    if args.dry_run:
        log("dry-run：未写入任何文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
