"""版本比对：决定这一轮要不要真的解包。

为什么单独一步：
    解包 + 反编译是整个链路最贵的部分（几十张表、上百 MB 字节码、几分钟到几十分钟）。
    而检查版本只需要一次 TCP 握手，几百毫秒。所以每小时的 cron 应该先问一句
    "变了吗"，没变就直接结束 —— 这样 24 小时里真正干活的通常只有 1~2 次。

判据用 azhash：
    它是资源清单的版本号，任何一次热更（哪怕只加一张立绘）都会变。
    `paintinghash` / `lvh2dhash` 之类只管图片，用 azhash 能同时覆盖
    scripts32 和 sharecfgdata —— 两者都在 az 清单里。

用法：
    python scripts/check_version.py --server CN --state data/version.json
    python scripts/check_version.py --server CN --state data/version.json --write
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import log, set_output, warn  # noqa: E402

from azl_assets import get_version  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="CN")
    ap.add_argument("--state", required=True, help="版本状态文件，如 data/version.json")
    ap.add_argument("--write", action="store_true", help="把当前版本写入状态文件")
    args = ap.parse_args()

    info = get_version(args.server)
    versions = info.get("versions") or {}
    az = versions.get("az") or {}
    if not az:
        warn(f"{args.server} 版本信息里没有 az，无法比对")
        set_output(changed="true", version="")
        return 1

    raw = az["raw"]                       # 形如 $azhash$9$7$398$def4c26318af25f3
    parts = raw.strip("$").split("$")
    version = ".".join((["0", "0", "0"] + parts[1:-1])[-3:])
    stamp = parts[-1] if len(parts) > 1 else ""

    state_path = Path(args.state)
    prev = {}
    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text("utf-8"))
        except json.JSONDecodeError:
            warn(f"{state_path} 不是合法 JSON，按空处理")
    cur = (prev.get(args.server) or {}).get("az")
    changed = cur != raw

    log(f"{args.server} azhash {version} ({stamp})"
        + (" —— 未变化" if not changed else " —— 有更新"))

    set_output(changed="true" if changed else "false", version=version, stamp=stamp)

    if args.write:
        prev[args.server] = {
            "az": raw,
            "version": version,
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(prev, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
        log(f"已记录到 {state_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
