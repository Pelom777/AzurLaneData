"""校验 public/data 是否满足前端硬编码假设。

和 `check_site_format.py` 分工：那个管立绘/图标等**资产**格式，这个管**数据 json** 的结构。
两者都源于同一条原则 —— 前端冻结，所有契约由导出侧守。前端不会因为数据缺字段而报错提示，
它只会白屏或算出 NaN，所以宁可在这里失败。

这些检查不是凭空写的，每一条都对应 `src/views/**` 里的真实读法：

  1. `ship-attr[*].base` 恰好 12 项
     `base.map()` 决定循环次数，短了漏项、长了 `growth[i]` 越界成 NaN。
  2. `ship-attr[*].growth` ≥ 12 项且无 null
     `growth[i]` 没有兜底，null 会算出 NaN。
  3. `ship-attr[*].strengthen` 必须存在且是数组
     `attr['strengthen'][i-1]` 无可空保护（默认 strengthen=true），键缺失直接抛错。
  4. `ship-attr[*].transform` 若存在则长度 ≥ 12
     `transform[i]` 只在「有该字段」时才读，但没有逐位兜底。
  5. `skin` 里不允许残留 `{namecode:N}` 占位符
     前端不解析占位符，残留会原样显示成一串花括号。
  6. `skin` 的键（painting）必须与 `ship-attr` / `ship` 同源
     三者按同一个 painting 索引，键集不一致会出现「有船没属性」的空面板。
  7. 数值必须是有限数
     json 里的 NaN/Infinity 能让整个 `JSON.parse` 失败，站点直接白屏。

用法：
    python scripts/check_data_contract.py --data public/data
    python scripts/check_data_contract.py --data public/data --skip skin   # 跳过某类
退出码非 0 表示有违约项，可直接当 CI 门禁。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import log, warn  # noqa: E402

ATTR_LEN = 12          # 前端写死的 12 位属性向量
STRENGTHEN_LEN = 5     # 强化上限只有 5 个值，喂给 i=1..5
NAMECODE_RE = re.compile(r'\{namecode:\d+\}')


def load(path: Path):
    if not path.exists():
        return None
    raw = path.read_text('utf-8')
    if not raw.strip():
        # 仓库里确实有几个占位空文件（2017-cn.json 等），不是错误但要知道
        warn(f'{path.name} 是空文件')
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f'[azl] {path.name} 不是合法 JSON：{exc}') from exc


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _finite(v, where: str, problems: list) -> None:
    if isinstance(v, float) and not math.isfinite(v):
        problems.append(f'{where}: 非有限数 {v!r}（会让 JSON.parse 失败）')


def check_ship_attr(data: dict, problems: list) -> dict:
    """返回「有 transform 的船数」等统计，供调用方打印。"""
    stat = {'total': 0, 'with_transform': 0, 'with_gear_score': 0}
    for key, row in data.items():
        stat['total'] += 1
        if not isinstance(row, dict):
            problems.append(f'ship-attr[{key}]: 不是对象')
            continue

        base = row.get('base')
        if not isinstance(base, list):
            problems.append(f'ship-attr[{key}]: 缺 base')
        elif len(base) != ATTR_LEN:
            problems.append(f'ship-attr[{key}]: base 长度 {len(base)} ≠ {ATTR_LEN}')
        else:
            for i, v in enumerate(base):
                if not _is_num(v):
                    problems.append(f'ship-attr[{key}].base[{i}]: 不是数字 {v!r}')

        growth = row.get('growth')
        if not isinstance(growth, list):
            problems.append(f'ship-attr[{key}]: 缺 growth')
        elif len(growth) < ATTR_LEN:
            problems.append(f'ship-attr[{key}]: growth 长度 {len(growth)} < {ATTR_LEN}')
        else:
            for i, v in enumerate(growth[:ATTR_LEN]):
                if not _is_num(v):
                    problems.append(f'ship-attr[{key}].growth[{i}]: 不是数字 {v!r}')

        st = row.get('strengthen')
        if st is None:
            problems.append(f'ship-attr[{key}]: 缺 strengthen（前端会抛错）')
        elif not isinstance(st, list):
            problems.append(f'ship-attr[{key}]: strengthen 不是数组 {type(st).__name__}')
        elif len(st) < STRENGTHEN_LEN:
            problems.append(f'ship-attr[{key}]: strengthen 长度 {len(st)} < {STRENGTHEN_LEN}')
        else:
            for i, v in enumerate(st):
                if not _is_num(v):
                    problems.append(f'ship-attr[{key}].strengthen[{i}]: 不是数字 {v!r}')

        tf = row.get('transform')
        if tf is not None:
            stat['with_transform'] += 1
            if not isinstance(tf, list):
                problems.append(f'ship-attr[{key}]: transform 不是数组')
            elif len(tf) < ATTR_LEN:
                problems.append(f'ship-attr[{key}]: transform 长度 {len(tf)} < {ATTR_LEN}')
            else:
                for i, v in enumerate(tf[:ATTR_LEN]):
                    if not _is_num(v):
                        problems.append(f'ship-attr[{key}].transform[{i}]: 不是数字 {v!r}')
        if row.get('gear_score') is not None:
            stat['with_gear_score'] += 1
            _finite(row['gear_score'], f'ship-attr[{key}].gear_score', problems)

        # 只允许这几个键：多出来的字段不会报错，但会掩盖拼写错误（如 transform_）
        allowed = {'base', 'growth', 'strengthen', 'transform', 'gear_score'}
        extra = set(row) - allowed
        if extra:
            problems.append(f'ship-attr[{key}]: 未知字段 {sorted(extra)}')
    return stat


def check_skin(data: dict, problems: list) -> dict:
    stat = {'total': 0, 'forms': 0}
    for key, forms in data.items():
        stat['total'] += 1
        if not isinstance(forms, dict):
            problems.append(f'skin[{key}]: 不是对象')
            continue
        stat['forms'] += len(forms)
        for name, meta in forms.items():
            if not isinstance(meta, dict):
                problems.append(f'skin[{key}][{name}]: 不是对象')
                continue
            text = meta.get('name')
            if not isinstance(text, str):
                problems.append(f'skin[{key}][{name}]: 缺 name')
                continue
            hit = NAMECODE_RE.search(text)
            if hit:
                problems.append(f'skin[{key}][{name}]: 残留占位符 {hit.group(0)} → {text!r}')
    return {'total': stat['total'], 'forms': stat['forms'],
            'max_forms': max((len(v) for v in data.values() if isinstance(v, dict)), default=0)}


def check_ship(data: dict, problems: list) -> dict:
    stat = {'total': 0}
    for key, row in data.items():
        stat['total'] += 1
        if not isinstance(row, dict):
            problems.append(f'ship[{key}]: 不是对象')
            continue
        if not isinstance(row.get('name'), str):
            problems.append(f'ship[{key}]: 缺 name')
        for field in ('type', 'rarity', 'nationality'):
            v = row.get(field)
            if not isinstance(v, int) or isinstance(v, bool):
                problems.append(f'ship[{key}].{field}: 不是整数 {v!r}')
    return stat


def check_cross(ship: dict, skin: dict, attr: dict, problems: list) -> dict:
    """三者按同一个 painting 索引，键集必须对齐。

    严格程度分两档：
      · `ship` / `ship-attr` 必须完全同源 —— 差一个就是「有船没属性」或「有属性没船」，
        前端会渲染出空面板或 NaN。
      · `skin` 允许多，不允许多缺：仓库现在就有 3 个已删除机器人（gulite_robot1/3/4）
        只存在于 skin.json。多出来只是多一张图，缺了才是「有船没皮肤」。
    """
    a, b, c = set(attr), set(skin), set(ship)
    for label, x, y, xn, yn in (
        ('ship-attr vs ship', a, c, 'ship-attr', 'ship'),
    ):
        only_x, only_y = sorted(x - y), sorted(y - x)
        if only_x:
            problems.append(f'{label}: 只有 {xn} 有 {len(only_x)} 个 → {only_x[:5]}')
        if only_y:
            problems.append(f'{label}: 只有 {yn} 有 {len(only_y)} 个 → {only_y[:5]}')

    missing_skin = sorted(c - b)
    if missing_skin:
        problems.append(f'ship vs skin: 这 {len(missing_skin)} 艘船没有皮肤 → {missing_skin[:5]}')
    extra_skin = sorted(b - c)
    if extra_skin:
        warn(f'skin 多出 {len(extra_skin)} 个已不在 ship 里的键（仓库现状如此）：{extra_skin[:5]}')
    return {'ship': len(c), 'skin': len(b), 'attr': len(a), 'extra_skin': len(extra_skin)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='public/data')
    ap.add_argument('--skip', nargs='*', default=[],
                    help='跳过某些检查：ship ship-attr skin cross')
    args = ap.parse_args()

    root = Path(args.data)
    if not root.is_dir():
        raise SystemExit(f'[azl] 目录不存在：{root}')

    problems: list[str] = []
    ship = load(root / 'ship.json')
    skin = load(root / 'skin.json')
    attr = load(root / 'ship-attr.json')

    if ship is None or skin is None or attr is None:
        missing = [n for n, v in
                   (('ship.json', ship), ('skin.json', skin), ('ship-attr.json', attr)) if v is None]
        raise SystemExit(f'[azl] 缺文件：{", ".join(missing)}')

    if 'ship' not in args.skip:
        s = check_ship(ship, problems)
        log(f'ship       {s["total"]} 艘')
    if 'skin' not in args.skip:
        s = check_skin(skin, problems)
        log(f'skin       {s["total"]} 艘 / {s["forms"]} 个形态（单船最多 {s["max_forms"]}）')
    if 'ship-attr' not in args.skip:
        s = check_ship_attr(attr, problems)
        log(f'ship-attr  {s["total"]} 艘，{s["with_transform"]} 艘有 transform，'
            f'{s["with_gear_score"]} 艘有 gear_score')
    if 'cross' not in args.skip:
        s = check_cross(ship, skin, attr, problems)
        log(f'键集对齐   ship={s["ship"]} skin={s["skin"]} ship-attr={s["attr"]}'
            f'（skin 多 {s["extra_skin"]}）')

    # public/data 里其余文件至少要是合法 JSON —— 它们由人维护，但坏掉一样白屏
    for path in sorted(root.glob('*.json')):
        if path.name in ('ship.json', 'skin.json', 'ship-attr.json'):
            continue
        load(path)

    if problems:
        warn(f'发现 {len(problems)} 处违约：')
        for line in problems[:60]:
            warn(f'  · {line}')
        if len(problems) > 60:
            warn(f'  …… 还有 {len(problems) - 60} 处')
        return 1
    log('全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
