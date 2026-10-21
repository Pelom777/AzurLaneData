"""从 AzurLaneLuaScripts 解析原始配置表。

这是本方案里最重要的一条改动：**不再依赖已经停更的 AzurLaneData**。

背景：`AzurLaneTools/AzurLaneData` 停在 2026-05，但同一个 org 的
`AzurLaneLuaScripts` 至今仍在每小时更新，而它里面的
`<SERVER>/sharecfgdata/*.lua` 就是 ship / skin / equip 这些表的原始数据 ——
AzurLaneData 的 json 本来就是从这里导出来的。

除了 35 张 `sharecfgdata`，还会从 `<SERVER>/sharecfg/` 精确取四张小表：
  - `ship_data_strengthen.lua`     → `ship-attr` 的 `strengthen` 字段（属性强化上限）
  - `attribute_info_by_type.lua`   → attrs 数组每一项的语义，用来固化索引含义
  - `ship_data_trans.lua`          → 每个改造组的节点列表（`transform_list`）
  - `transform_data_template.lua`  → 改造节点的属性增量（`transform`）+ 战力（`gear_score`）
用 `--no-cone` 稀疏检出按文件取，不会把整个 100 MB 的 sharecfg 拖下来。

所以直接读 lua 表，数据源立刻从"停了 5 个月"变成"几小时内"。

    python scripts/fetch_lua_tables.py --server CN --dst "$RUNNER_TEMP/lua" --out "$RUNNER_TEMP/raw"

产出：`--out` 下每个表一个 json，形如 ship_data_template.json = {"100001": {...}, ...}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import append_summary, git, log, set_output, warn  # noqa: E402
from luatable import LuaParseError, parse_file  # noqa: E402

UPSTREAM_URL = "https://github.com/AzurLaneTools/AzurLaneLuaScripts.git"
BRANCH = "main"

# sharecfg/ 整个目录 100 MB，但 `ship-attr` 只需要这几张。用 --no-cone 精确取文件，
# 别把整个目录拖下来。
SHARECFG_FILES = (
    "ship_data_strengthen.lua",      # 属性强化上限（strengthen 字段）
    "attribute_info_by_type.lua",    # attrs 数组每一项的语义（耐久/炮击/…）
    "ship_data_trans.lua",           # 改造：每个改造组的节点列表（transform_list）
    "transform_data_template.lua",   # 改造节点：effect（属性增量）+ gear_score（战力）
)


def ensure_repo(dst: Path, server: str) -> None:
    if not (dst / ".git").exists():
        dst.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=dst)
        git("remote", "add", "origin", UPSTREAM_URL, cwd=dst)
        log(f"初始化 lua 缓存：{dst}")

    git("-c", "protocol.version=2", "fetch", "-q", "--depth=1",
        "--filter=blob:none", "origin", BRANCH, cwd=dst)
    # 必须是 --no-cone：cone 模式只能整目录，拉 sharecfg 就是 100 MB。
    # pattern 不带前导斜杠，避免 Windows/Git Bash 的 MSYS 路径转换把它改坏。
    patterns = [f"{server}/sharecfgdata/"]
    patterns += [f"{server}/sharecfg/{name}" for name in SHARECFG_FILES]
    git("sparse-checkout", "set", "--no-cone", *patterns, cwd=dst)
    git("reset", "-q", "--hard", "FETCH_HEAD", cwd=dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="CN", choices=["CN", "EN", "JP", "KR", "TW"])
    ap.add_argument("--dst", required=True, help="lua 仓库缓存目录（放 $RUNNER_TEMP）")
    ap.add_argument("--out", required=True, help="解析后的 json 输出目录")
    ap.add_argument("--only", default="", help="只解析这些表（逗号分隔），默认全部")
    args = ap.parse_args()

    dst = Path(args.dst).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    ensure_repo(dst, args.server)
    sha = git("rev-parse", "HEAD", cwd=dst)
    date = git("log", "-1", "--format=%cI", cwd=dst)
    log(f"lua 源 commit {sha[:8]}（{date}）")

    src_dir = dst / args.server / "sharecfgdata"
    if not src_dir.is_dir():
        warn(f"{args.server}/sharecfgdata 不存在，上游可能改了目录结构")
        return 1

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    files = sorted(src_dir.glob("*.lua"))
    cfg_dir = dst / args.server / "sharecfg"
    for name in SHARECFG_FILES:
        extra = cfg_dir / name
        if extra.exists():
            files.append(extra)
        else:
            warn(f"sharecfg/{name} 不存在，依赖它的字段会退回到保留旧值")
    if wanted:
        files = [f for f in files if f.stem in wanted]

    total_entries = 0
    total_bytes = 0
    failed: list[str] = []
    truncated: list[str] = []
    started = time.time()

    for lua in files:
        # 整个文件级的处理都包在 try 里：解析失败、写出失败都只影响这一张表，
        # 不能让一张表把整条链带崩（实测上游表里确实有脏数据）。
        try:
            tables, cut = parse_file(lua)
            if cut:
                truncated.append(lua.name)
            for name, data in tables.items():
                target = out / f"{name}.json"
                with target.open("w", encoding="utf-8", newline="\n") as fh:
                    json.dump(data, fh, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
                total_entries += len(data)
                total_bytes += target.stat().st_size
        except (LuaParseError, UnicodeDecodeError, TypeError, ValueError) as exc:
            warn(f"{lua.name} 处理失败：{exc}")
            failed.append(lua.name)
            continue

    elapsed = time.time() - started
    log(f"解析 {len(files) - len(failed)}/{len(files)} 个表，{total_entries} 条记录，"
        f"{total_bytes / 1024 / 1024:.1f} MB，耗时 {elapsed:.1f}s")
    if truncated:
        # 不是错误：这些文件的数据部分已经完整保留，只是尾部附了代码
        log(f"以下表在尾部遇到非数据语句，已截断保留数据部分：{', '.join(truncated)}")
    if failed:
        warn(f"以下表解析失败，已跳过：{', '.join(failed)}")

    set_output(upstream_sha=sha, upstream_date=date,
               tables=len(files) - len(failed), entries=total_entries,
               failed=len(failed))
    append_summary(
        f"### 数据源：LuaScripts（{args.server}）\n"
        f"- commit：`{sha[:8]}`（{date}）\n"
        f"- 解析：`{len(files) - len(failed)}` 个表 / `{total_entries}` 条记录 / "
        f"`{total_bytes / 1024 / 1024:.1f} MB`\n"
        + (f"- ⚠️ 失败 `{len(failed)}` 个：{', '.join(failed[:5])}\n" if failed else "")
    )
    return 0 if not failed or len(failed) < len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
