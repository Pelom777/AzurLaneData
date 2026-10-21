"""阶段 1 验收报告：新旧两条链路在同一版本下产出的 json 是否等价。

**临时脚本，阶段 1 验收完就删。**

比的是 `public/data` 产物（不是 lua 文本、也不是 raw json）—— 这才是前端真正读的
东西，也是 HANDOFF §4.1 定的口径。三组对比：

    1. out_new  vs  out_old      新链路 vs 旧链路（应当**完全一致**）
    2. out_new  vs  public/data  本次产出 vs 仓库现有（差异应只是游戏侧真实改动）
    3. raw_new 的表齐不齐         7 张必需表（3 张来自 sharecfgdata + 4 张来自 scripts32）

用法：
    python scripts/stage1_report.py \
        --new out_new --old out_old --repo public/data \
        --raw-new raw_new --raw-old raw_old --report stage1/report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# build_data_impl.py 真正消费的 7 张表，缺一张产出就是错的
REQUIRED_TABLES = {
    "sharecfgdata": ["ship_data_statistics", "ship_data_template", "ship_skin_template"],
    "scripts32": ["ship_data_strengthen", "ship_data_trans",
                  "transform_data_template", "attribute_info_by_type"],
}


def load(path: Path):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"__load_error__": f"{type(exc).__name__}: {exc}"}


def cmp_dirs(new: Path, old: Path, label: str) -> dict:
    new_files = {p.name for p in new.glob("*.json")}
    old_files = {p.name for p in old.glob("*.json")}
    common = sorted(new_files & old_files)
    identical, differing, detail = [], [], {}
    for name in common:
        a, b = new / name, old / name
        if a.read_bytes() == b.read_bytes():
            identical.append(name)
            continue
        differing.append(name)
        da, db = load(a), load(b)
        entry = {"size_new": a.stat().st_size, "size_old": b.stat().st_size}
        if isinstance(da, dict) and isinstance(db, dict):
            ka, kb = set(da), set(db)
            entry["only_in_new"] = sorted(ka - kb)[:10]
            entry["only_in_old"] = sorted(kb - ka)[:10]
            entry["key_count_new"] = len(ka)
            entry["key_count_old"] = len(kb)
            diff_keys = [k for k in (ka & kb) if da[k] != db[k]]
            entry["changed_keys"] = len(diff_keys)
            entry["changed_sample"] = sorted(diff_keys)[:10]
        elif isinstance(da, list) and isinstance(db, list):
            entry["len_new"], entry["len_old"] = len(da), len(db)
            entry["_sample"] = next(
                ({"i": i, "new": da[i], "old": db[i]} for i in range(min(len(da), len(db)))
                 if da[i] != db[i]), None)
        detail[name] = entry
    return {
        "label": label,
        "files_new": len(new_files),
        "files_old": len(old_files),
        "only_in_new": sorted(new_files - old_files),
        "only_in_old": sorted(old_files - new_files),
        "identical": identical,
        "differing": differing,
        "detail": detail,
        "equivalent": not differing and new_files == old_files,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--raw-new", required=True)
    ap.add_argument("--raw-old", default="")
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    raw_new = Path(args.raw_new)
    report: dict = {}

    # ---- 1. 7 张必需表齐不齐
    present = {p.stem for p in raw_new.glob("*.json")}
    tables = {}
    for src, names in REQUIRED_TABLES.items():
        tables[src] = {n: (n in present) for n in names}
    missing = [n for _, names in REQUIRED_TABLES.items() for n in names if n not in present]
    report["raw_new"] = {
        "table_count": len(present), "tables": tables, "missing_required": missing,
        "entries": {p.stem: len(load(p)) if isinstance(load(p), dict) else 0
                    for p in sorted(raw_new.glob("*.json"))},
        "total_bytes": sum(p.stat().st_size for p in raw_new.glob("*.json")),
    }
    if args.raw_old:
        raw_old = Path(args.raw_old)
        old_present = {p.stem for p in raw_old.glob("*.json")}
        report["raw_old"] = {"table_count": len(old_present),
                             "only_in_new": sorted(present - old_present),
                             "only_in_old": sorted(old_present - present)}

    # ---- 2. 新 vs 旧（关键结论）
    report["new_vs_old"] = cmp_dirs(Path(args.new), Path(args.old), "新链路 vs 旧链路")
    # ---- 3. 本次 vs 仓库
    report["new_vs_repo"] = cmp_dirs(Path(args.new), Path(args.repo), "本次产出 vs 仓库现有")

    verdict = "pass" if (report["new_vs_old"]["equivalent"] and not missing) else "fail"
    report["verdict"] = verdict

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    # ---- 打一份人看的摘要
    nvo, nvr = report["new_vs_old"], report["new_vs_repo"]
    print(f"7 张必需表：{'齐' if not missing else '缺 ' + ', '.join(missing)}")
    print(f"raw_new：{report['raw_new']['table_count']} 张表 / "
          f"{sum(report['raw_new']['entries'].values())} 条 / "
          f"{report['raw_new']['total_bytes'] / 1024:.0f} KB")
    print(f"\n[{nvo['label']}] 一致 {len(nvo['identical'])} 个 / 有差异 {len(nvo['differing'])} 个 "
          f"/ 仅在旧 {len(nvo['only_in_old'])} 个 / 仅在新 {len(nvo['only_in_new'])} 个")
    for name in nvo["differing"]:
        print(f"   ✗ {name}: {json.dumps(nvo['detail'][name], ensure_ascii=False)[:200]}")
    print(f"\n[{nvr['label']}] 一致 {len(nvr['identical'])} 个 / 有差异 {len(nvr['differing'])} 个")
    for name in nvr["differing"]:
        print(f"   ~ {name}: {json.dumps(nvr['detail'][name], ensure_ascii=False)[:200]}")
    print(f"\n结论：{verdict}")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
