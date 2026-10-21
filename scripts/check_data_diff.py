"""把 build_data_impl 的产出和仓库现有 public/data 逐文件对照。

用途：每隔一段时间跑一次，确认推导规则没被游戏改版打坏。它会告诉你
「键覆盖率 / 逐字段一致率 / 具体不一致的条目」，而不是只说"不一样"。

用法：
    # 先拉表
    python scripts/fetch_lua_tables.py --server CN --dst /tmp/lua --out /tmp/tables
    # 再对照（--data 指向仓库里的 public/data）
    python scripts/check_data_diff.py --tables /tmp/tables --data public/data
    # 连未启用的 ship-attr / ship-equip 一起看
    python scripts/check_data_diff.py --tables /tmp/tables --data public/data --all
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_data_impl as impl  # noqa: E402
from common import log, warn  # noqa: E402


def compare_dict(mine: dict, repo: dict, fields=None, nested=False):
    """返回 (交集, 仓库独有, 我独有, 完全一致的键数, 各字段差异明细)"""
    km, kr = set(mine), set(repo)
    common = km & kr
    same = 0
    diffs: dict[str, list] = {}
    for key in sorted(common):
        a, b = mine[key], repo[key]
        if nested:
            # 内层是 {painting: {...}}，按内层键集合 + 每个内层对象的字段比
            same += a == b
            if a != b:
                only_a = sorted(set(a) - set(b))
                only_b = sorted(set(b) - set(a))
                changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
                diffs.setdefault('inner', []).append(
                    (key, {'我多': only_a[:3], '仓库多': only_b[:3], '内容不同': changed[:3]}))
            continue
        if isinstance(a, dict):
            bad = [f for f in (fields or a.keys()) if a.get(f) != b.get(f)]
            info = {f: (a.get(f), b.get(f)) for f in bad}
        else:
            bad = [] if a == b else ['值']
            info = {'值': (a, b)} if bad else {}
        if bad:
            diffs.setdefault(','.join(bad), []).append((key, info))
        else:
            same += 1
    return len(common), sorted(kr - km), sorted(km - kr), same, diffs


def report(name: str, mine: dict, repo: dict, fields=None, nested=False):
    common, only_repo, only_mine, same, diffs = compare_dict(mine, repo, fields, nested)
    total = len(repo)
    ok_rate = same / common * 100 if common else 0
    log(f'== {name}')
    log(f'   我 {len(mine)} | 仓库 {total} | 交集 {common} | '
        f'仓库有我无 {len(only_repo)} | 我有仓库无 {len(only_mine)}')
    log(f'   交集内完全一致 {same}/{common}（{ok_rate:.1f}%）')
    if only_repo:
        log(f'   仓库独有（多为旧版有、现已被游戏删除）: {only_repo[:6]}')
    if only_mine:
        log(f'   我独有（多为仓库快照之后新增）: {only_mine[:6]}')
    for field, items in diffs.items():
        log(f'   差异[{field}] {len(items)} 处')
        for key, detail in items[:3]:
            log(f'      {key}: {detail}')
    return ok_rate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--tables', required=True, help='fetch_lua_tables 的输出目录')
    ap.add_argument('--data', required=True, help='仓库的 public/data 目录')
    ap.add_argument('--all', action='store_true', help='连未启用的 ship-attr / ship-equip 一起看')
    ap.add_argument('--json-out', default='', help='把明细写成 json')
    args = ap.parse_args()

    tables = Path(args.tables)
    data_dir = Path(args.data)
    if not tables.is_dir() or not data_dir.is_dir():
        warn('目录不存在')
        return 2

    names = list(impl.ENABLED)
    if args.all:
        names = list(impl.BUILDERS)

    detail = {}
    for name in names:
        builder = impl.BUILDERS.get(name)
        if not builder:
            continue
        # 和 build_data.py 一样：有 prev_dir 参数的构建器要把仓库现有数据传进去，
        # 否则会退化成全量重建，把「保留旧值」的字段误报成差异。
        if name in getattr(impl, 'NEEDS_PREV', ()):
            mine = builder(tables, data_dir)
        else:
            mine = builder(tables)
        repo_path = data_dir / f'{name}.json'
        if not repo_path.exists():
            warn(f'{repo_path} 不存在，跳过')
            continue
        repo = json.loads(repo_path.read_text('utf-8'))
        fields = None
        if name == 'ship':
            fields = ('painting', 'name', 'type', 'rarity', 'nationality')
        report(name, mine, repo, fields=fields, nested=(name == 'skin'))
        detail[name] = {'mine': len(mine), 'repo': len(repo)}
        print()

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(detail, ensure_ascii=False, indent=1), 'utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
