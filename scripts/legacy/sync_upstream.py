"""把上游 AzurLaneData 某服的原始 json 同步到本地缓存目录（增量）。

不依赖任何凭据：直接读公开仓库，blobless + shallow + sparse-checkout，
第一次拉该服全部文件，之后只拉新增/变更的 blob。

    python scripts/sync_upstream.py --server CN --dst "$RUNNER_TEMP/upstream"

输出（$GITHUB_OUTPUT）：
    changed       上游是否有新提交（true/false）
    upstream_sha  上游最新 commit
    upstream_date 上游最新提交时间（ISO8601）
    stale         上游是否超过 --stale-days 天没更新
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import git, log, set_output, warn, append_summary  # noqa: E402

UPSTREAM_URL = "https://github.com/AzurLaneTools/AzurLaneData.git"
BRANCH = "main"


def ensure_repo(dst: Path, server: str) -> None:
    """初始化或复用上游缓存仓库，并只放行指定服 + versions 目录。"""
    if not (dst / ".git").exists():
        dst.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=dst)
        git("remote", "add", "origin", UPSTREAM_URL, cwd=dst)
        log(f"初始化上游缓存：{dst}")

    # 先只取树，不落盘
    git("-c", "protocol.version=2", "fetch", "-q", "--depth=1",
        "--filter=blob:none", "origin", BRANCH, cwd=dst)

    # cone 模式稀疏：只要这个服 + 版本徽章
    git("sparse-checkout", "init", "--cone", cwd=dst)
    git("sparse-checkout", "set", server, "versions", cwd=dst)
    git("reset", "-q", "--hard", "FETCH_HEAD", cwd=dst)


def read_marker(dst: Path) -> dict:
    marker = dst / ".azl-marker.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="CN", choices=["CN", "EN", "JP", "KR", "TW"])
    ap.add_argument("--dst", required=True, help="上游缓存目录（建议放在 $RUNNER_TEMP，别进仓库）")
    ap.add_argument("--stale-days", type=int, default=10, help="超过多少天没更新就告警")
    args = ap.parse_args()

    dst = Path(args.dst).resolve()
    ensure_repo(dst, args.server)

    sha = git("rev-parse", "HEAD", cwd=dst)
    # %cI 是 committer 时间，ISO8601，带时区
    date = git("log", "-1", "--format=%cI", cwd=dst)
    subject = git("log", "-1", "--format=%s", cwd=dst)

    marker = read_marker(dst)
    changed = marker.get("sha") != sha
    (dst / ".azl-marker.json").write_text(
        json.dumps({"sha": sha, "date": date}, ensure_ascii=False), encoding="utf-8"
    )

    # 新鲜度检查：上游停更时流水线本身不会报错，只会静默产出旧数据，
    # 所以必须主动把"上游已死"这件事喊出来。
    try:
        committed_at = dt.datetime.fromisoformat(date)
        age_days = (dt.datetime.now(dt.timezone.utc) - committed_at).total_seconds() / 86400
    except ValueError:
        age_days = 0.0

    stale = age_days > args.stale_days
    if stale:
        warn(
            f"上游 {args.server} 数据已经 {age_days:.1f} 天没更新（最后 {date}）。"
            f"数据链本身没坏，但数据源已经停了，需要改用自建提取。"
        )

    total = sum(1 for _ in (dst / args.server).rglob("*") if _.is_file())
    log(f"上游 {args.server}: {total} 个文件，commit {sha[:8]}（{date}），changed={changed}")

    set_output(
        changed=changed,
        upstream_sha=sha,
        upstream_date=date,
        stale=stale,
    )
    append_summary(
        "### 上游数据\n"
        f"- 服：`{args.server}`，文件数：`{total}`\n"
        f"- commit：`{sha[:8]}`（{date}）\n"
        f"- 提交信息：{subject}\n"
        f"- 是否更新：{'是' if changed else '否'}\n"
        + (f"- ⚠️ **上游已停更 {age_days:.0f} 天**\n" if stale else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
