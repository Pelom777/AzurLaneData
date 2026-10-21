"""把提取产物增量同步进仓库的 public/ 目录。

关键点：**不下载仓库里已有的 7 GB 资产也能判断"哪些是新的"**。
git 索引里存着每个文件的 blob sha，本地用同样的算法算一遍提取产物的 sha，
相等就跳过、不等才落盘。所以一次常规同步只处理新船那几个文件。

用法：
    # 1) 提取产物目录结构与 public/ 一致时（painting/、shipicon/ ...）
    python scripts/sync_assets.py --src D:/extract/out --dest public

    # 2) 结构不一致时显式映射（可多组）
    python scripts/sync_assets.py --src D:/extract/out --dest public \
        --map painting=painting --map icon/ship=shipicon

    # 3) 允许删除"这次产物里没有"的历史文件（默认关闭，慎用）
    python scripts/sync_assets.py ... --prune painting
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import append_summary, file_blob_sha1, git, index_tree, log, set_output, warn  # noqa: E402

# 这些目录都是"从游戏包里生成出来的"，可以整体覆盖；src 里显式列出的也会被采纳
KNOWN_KINDS = [
    "painting", "paintingface", "shipicon", "squareicon", "equipicon",
    "iconframe", "iconbackground", "shipbackground", "squareback",
    "voteicon", "juusui", "spine", "spinebase",
]


def parse_maps(pairs: list[str], src_root: Path) -> list[tuple[str, str]]:
    if pairs:
        out = []
        for pair in pairs:
            if "=" not in pair:
                raise SystemExit(f"--map 需要 src=dst 形式，收到：{pair}")
            a, b = pair.split("=", 1)
            out.append((a.strip("/"), b.strip("/")))
        return out
    # 没给映射就用约定：src/<kind>/ → public/<kind>/
    found = [(k, k) for k in KNOWN_KINDS if (src_root / k).is_dir()]
    if not found:
        entries = sorted(p.name for p in src_root.iterdir() if p.is_dir())
        raise SystemExit(
            f"{src_root} 下没有找到约定的资产目录，实际一级目录：{entries}\n"
            f"请用 --map src=dst 显式指定映射。"
        )
    return found


def iter_source(src_root: Path, src_prefix: str, patterns: list[str]):
    base = src_root / src_prefix
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src_root).as_posix()
        if patterns and not any(fnmatch.fnmatch(rel, p) for p in patterns):
            continue
        yield path, rel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="提取产物根目录")
    ap.add_argument("--dest", default="public", help="仓库内目标根目录，默认 public")
    ap.add_argument("--map", action="append", default=[], metavar="SRC=DST")
    ap.add_argument("--prune", action="append", default=[], metavar="SRC_DIR",
                    help="允许在该源目录范围内删除仓库里多出来的文件")
    ap.add_argument("--include", action="append", default=[], metavar="GLOB",
                    help="额外只同步匹配的文件，例如 --include '*.png'")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="", help="把结果写成 json")
    args = ap.parse_args()

    repo = Path.cwd()
    src_root = Path(args.src).resolve()
    dest_root = (repo / args.dest).resolve()
    if not src_root.is_dir():
        warn(f"提取产物目录不存在：{src_root}")
        set_output(changed=False, skipped=True)
        return 1

    maps = parse_maps(args.map, src_root)
    log(f"映射：{maps}")
    log("读取仓库索引（不下载文件内容）…")
    existing = index_tree(repo, args.dest)
    log(f"仓库 {args.dest}/ 下已有 {len(existing)} 个文件")

    added: list[str] = []
    updated: list[str] = []
    unchanged = 0
    produced: dict[str, set[str]] = {}

    for src_prefix, dst_prefix in maps:
        produced.setdefault(src_prefix, set())
        for path, rel in iter_source(src_root, src_prefix, args.include):
            tail = rel[len(src_prefix):].lstrip("/")
            dest_rel = f"{args.dest}/{dst_prefix}/{tail}" if dst_prefix else f"{args.dest}/{tail}"
            produced[src_prefix].add(dest_rel)

            sha = file_blob_sha1(path)
            old = existing.get(dest_rel)
            if old == sha:
                unchanged += 1
                continue

            target = repo / dest_rel
            if not args.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            (updated if old else added).append(dest_rel)

    # ---- 删除：只在你显式 --prune 的源目录范围内，删"这次没产出"的历史文件
    removed: list[str] = []
    for src_prefix in args.prune:
        dst_prefix = dict(maps).get(src_prefix)
        if dst_prefix is None:
            warn(f"--prune {src_prefix} 不在本次映射里，忽略")
            continue
        prefix = f"{args.dest}/{dst_prefix}/"
        keep = produced.get(src_prefix, set())
        for path in existing:
            if path.startswith(prefix) and path not in keep:
                removed.append(path)

    log(f"新增 {len(added)}，更新 {len(updated)}，删除 {len(removed)}，未变 {unchanged}")

    if (added or updated or removed) and not args.dry_run:
        if added or updated:
            listfile = repo / ".azl-add.txt"
            listfile.write_text("\0".join(added + updated), encoding="utf-8")
            git("add", "--pathspec-from-file", str(listfile), "--pathspec-file-nul", cwd=repo)
            listfile.unlink()
        for i in range(0, len(removed), 400):
            git("update-index", "--force-remove", "--", *removed[i:i + 400], cwd=repo)

    changed = bool(added or updated or removed)
    set_output(changed=changed, added=len(added), updated=len(updated), removed=len(removed))

    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {"added": added, "updated": updated, "removed": removed},
                ensure_ascii=False, indent=1,
            ),
            encoding="utf-8",
        )

    append_summary(
        "### 资产同步\n"
        f"- 源：`{src_root}`\n"
        f"- 新增 `{len(added)}` / 更新 `{len(updated)}` / 删除 `{len(removed)}` / 未变 `{unchanged}`\n"
        + (f"- 示例：{', '.join((added + updated)[:8])}\n" if (added or updated) else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
