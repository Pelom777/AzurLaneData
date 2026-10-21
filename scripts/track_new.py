"""维护 new.json —— 记录每条船 / 每个皮肤的首次出现时间。

为什么要单独一个文件：
    ship.json / skin.json 每次都是全量覆盖，看不出"哪个是这周新加的"。
    前端要做「新船角标」「本次更新新增」这类展示，就需要一份显式的增量台账。

为什么是累积台账而不是"只存本次"：
    只存本次的话，历史窗口一过就丢了；而且 CI 一旦漏跑一次，那批新增就永久丢失。
    累积台账的每一条都带首次出现时间，任何时刻都能自己算出任意时间窗的增量。

为什么不需要读旧版 data：
    台账自己就是判据 —— id 不在台账里就是新的。这样连 `git show HEAD:` 都不用，
    少一个失败面。

首次运行（`--bootstrap`）：
    仓库里没有台账时，不能把现有的 900 条全标成"今天新增"，否则前端会亮成一片。
    所以首次只建基线：写入全部 id，但 `first_seen` 留空，表示"早于台账起点"。

用法：
    python scripts/track_new.py --data public/data            # 首次：建基线
    python scripts/track_new.py --data public/data --bootstrap
    python scripts/track_new.py --data public/data --version 9.7.398
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import append_summary, log, set_output, warn  # noqa: E402

LEDGER = "new.json"


def load(path: Path):
    if not path.exists():
        return {}
    raw = path.read_text("utf-8").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        warn(f"{path.name} 不是合法 JSON，按空处理：{exc}")
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="public/data 目录")
    ap.add_argument("--version", default="", help="本次游戏版本号，写进台账头")
    ap.add_argument("--bootstrap", action="store_true",
                    help="首次建基线：只记 id，不标首次出现时间")
    ap.add_argument("--keep-days", type=int, default=0,
                    help="只保留最近 N 天的条目（0 = 全留）")
    args = ap.parse_args()

    data = Path(args.data).resolve()
    ship = load(data / "ship.json")
    skin = load(data / "skin.json")
    if not ship:
        warn("ship.json 为空，跳过台账更新")
        return 0

    ledger_path = data / LEDGER
    ledger = load(ledger_path)
    known_ships: dict = ledger.get("ships") or {}
    known_skins: dict = ledger.get("skins") or {}
    baseline = args.bootstrap or not ledger_path.exists()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_ships: list[str] = []
    for painting, row in ship.items():
        if painting in known_ships:
            continue
        known_ships[painting] = {
            "name": row.get("name", ""),
            "type": row.get("type"),
            "rarity": row.get("rarity"),
            "first_seen": None if baseline else now,
        }
        if not baseline:
            new_ships.append(painting)

    new_skins: list[str] = []
    for default_painting, group in skin.items():
        if not isinstance(group, dict):
            continue
        for painting, row in group.items():
            if painting in known_skins:
                continue
            known_skins[painting] = {
                "name": row.get("name", ""),
                "ship": default_painting,
                "first_seen": None if baseline else now,
            }
            # 默认皮肤与船同批出现，不重复计入"新皮肤"
            if not baseline and painting != default_painting:
                new_skins.append(painting)

    if args.keep_days > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - args.keep_days * 86400

        def recent(entry) -> bool:
            ts = entry.get("first_seen")
            if not ts:
                return False        # 基线条目永不淘汰
            try:
                return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc).timestamp() >= cutoff
            except ValueError:
                return True

        kept_s = {k: v for k, v in known_ships.items() if recent(v)}
        kept_k = {k: v for k, v in known_skins.items() if recent(v)}
        if len(kept_s) != len(known_ships):
            log(f"台账按 {args.keep_days} 天窗口修剪：船 {len(known_ships)} → {len(kept_s)}")
        known_ships, known_skins = kept_s, kept_k

    out = {
        "version": args.version or ledger.get("version", ""),
        "updated": now,
        "baseline": baseline,
        "ships": dict(sorted(known_ships.items())),
        "skins": dict(sorted(known_skins.items())),
    }
    ledger_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8")

    latest = {
        "version": args.version or ledger.get("version", ""),
        "updated": now,
        "ships": {k: known_ships[k] for k in new_ships},
        "skins": {k: known_skins[k] for k in new_skins},
    }
    (data / "new-latest.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    if baseline:
        log(f"建立台账基线：船 {len(known_ships)} / 皮肤 {len(known_skins)}（不标记为新增）")
    else:
        log(f"本次新增：船 {len(new_ships)} / 皮肤 {len(new_skins)}；"
            f"台账累计 船 {len(known_ships)} / 皮肤 {len(known_skins)}")

    set_output(new_ships=len(new_ships), new_skins=len(new_skins),
               total_ships=len(known_ships), total_skins=len(known_skins))
    append_summary(
        "### 增量台账\n"
        f"- 本次新增：船 `{len(new_ships)}` / 皮肤 `{len(new_skins)}`\n"
        f"- 累计：船 `{len(known_ships)}` / 皮肤 `{len(known_skins)}`\n"
        + (f"- 新船：{', '.join(new_ships[:10])}\n" if new_ships else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
