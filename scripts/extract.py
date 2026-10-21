"""调用提取器，把游戏资源落到 --out。

为什么不把提取命令硬编码在 workflow 里：能跑提取的机器和凭据形态因人而异
（本地 exe / 私有 docker 镜像 / 自建 runner），把它收成一个可替换的命令模板，
workflow 就不用在"提取方式变了"的时候重写。

命令来源优先级：
    1. --cmd
    2. 环境变量 AZL_EXTRACT_CMD

模板占位符：
    {out}     目标目录
    {server}  服务器（CN/EN/JP/KR/TW）
    {python}  当前解释器

例：
    AZL_EXTRACT_CMD='python -m azex all {server} -o {out}'
    AZL_EXTRACT_CMD='"C:/azex/AzurLaneExtractor.exe" extract --server {server} --out {out}'
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import append_summary, die, log, set_output, warn  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", default=os.environ.get("AZL_EXTRACT_CMD", ""))
    ap.add_argument("--out", required=True)
    ap.add_argument("--server", default="CN")
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()

    if not args.cmd:
        warn(
            "未配置提取命令（AZL_EXTRACT_CMD）。这一步是数据与资产的源头，"
            "没有它流水线只能消费上游公开仓库。"
        )
        append_summary(
            "### 提取步骤被跳过\n"
            "未设置 `AZL_EXTRACT_CMD`。如果你只想要数据链，删掉本 job 即可；"
            "想要全自动，需要把提取器接进来（见 docs/AUTOMATION.md 第四节）。\n"
        )
        set_output(skipped=True)
        return 0

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    cmd = (
        args.cmd.replace("{out}", str(out))
        .replace("{server}", args.server)
        .replace("{python}", sys.executable)
    )
    log(f"执行提取：{cmd}")
    proc = subprocess.run(cmd, shell=True, cwd=str(out))
    if proc.returncode != 0:
        die(f"提取失败，退出码 {proc.returncode}")

    files = [p for p in out.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    if not files:
        die(f"提取命令跑完了，但 {out} 里没有任何产出，检查命令参数")
    log(f"提取产物：{len(files)} 个文件，{total / 1024 / 1024:.1f} MB")

    append_summary(
        "### 提取\n"
        f"- 命令：`{cmd}`\n"
        f"- 产物：`{len(files)}` 个文件，`{total / 1024 / 1024:.1f} MB`\n"
    )
    set_output(skipped=False, files=len(files), bytes=total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
