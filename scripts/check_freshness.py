"""判断数据源是否已经"事实上停更"。

流水线的一个隐性失败模式：上游没有新提交时，同步步骤只会 `changed=false` 空跑，
CI 全绿、站点照旧、没人知道数据已经旧了。这个检查就是专门用来喊这件事的。
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import append_summary, log, set_output, warn  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="上游最新提交时间，ISO8601")
    ap.add_argument("--days", type=float, default=7, help="超过多少天算停更")
    ap.add_argument("--label", default="数据源")
    args = ap.parse_args()

    if not args.date.strip():
        warn(f"{args.label}没有返回提交时间，无法判断新鲜度")
        set_output(stale=False, age_days="?")
        return 0

    try:
        when = dt.datetime.fromisoformat(args.date)
    except ValueError:
        warn(f"无法解析时间：{args.date}")
        set_output(stale=False, age_days="?")
        return 0

    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 86400

    stale = age > args.days
    if stale:
        warn(f"{args.label}最后更新于 {args.date}，已 {age:.1f} 天（阈值 {args.days} 天）")
    else:
        log(f"{args.label}新鲜度：{age:.1f} 天（阈值 {args.days} 天）")

    set_output(stale=stale, age_days=f"{age:.1f}")
    append_summary(f"- 新鲜度：`{age:.1f}` 天（阈值 `{args.days}`）{'⚠️ 已停更' if stale else ''}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
