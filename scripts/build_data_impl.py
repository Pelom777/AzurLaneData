"""public/data 的构建实现 —— 从游戏配置表推导站点要的 json。

构建方式是从仓库里现有的 21 个 `public/data/*.json` 反推出来的，每条规则都标了
它的证据与已知偏差，方便以后游戏改结构时定位。实测口径（CN 9.7.398 / lua commit
5822b089，2026-10-17）：

| 文件            | 结论 | 实测 |
|-----------------|------|------|
| `ship.json`     | ✅ 可推导 | 键 789/789 全覆盖，逐字段全等 787/789 |
| `skin.json`     | ✅ 可推导 | 键 789/792，内层 namecode 自举 284/288 |
| `ship-equip.json` | ⚠️ 部分 | 满星级 id 取法下内层全等 503/597 |
| `ship-attr.json`  | ⚠️ 部分 | growth 784/789；base 除索引 6 外 681/789 |
| `option.json`   | ✅ 静态表 | 硬编码，游戏没有对应 cfg |
| 其余 16 个       | ❌ 不产出 | 人工维护 / 需 35 表之外的数据，见文件末尾 |

设计上**只产出能推导的文件**，`build_data.py` 按返回值逐个覆盖，没返回的文件
（cg / new / nickname / server / vote / 2017·2019·2021 年份文件）原样留在仓库里。

用法（由 build_data.py 调用）：
    build(raw_dir) -> {文件名: 数据}
    raw_dir 是 fetch_lua_tables.py --out 的输出目录（每张表一个 json）
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:                                     # 由 build_data.py / check_data_diff.py 加载
    from common import is_asmr, warn
except ImportError:                      # 被当独立模块 import 时 scripts/ 可能不在 sys.path
    _ASMR_RE = re.compile(r'_asmr($|_)')

    def is_asmr(name):                   # 与 common.is_asmr 同一条正则，别各改各的
        return bool(_ASMR_RE.search(name.lower()))

    def warn(message):
        print(f'[build_data_impl] {message}', file=sys.stderr)

# ---------------------------------------------------------------------------
# 表名（都和 sharecfgdata 里的文件名一致，不带 .lua）
# ---------------------------------------------------------------------------
T_SHIP_STAT = 'ship_data_statistics'     # 舰船数值：name / type / rarity / nationality / attrs
T_SHIP_TPL = 'ship_data_template'        # 舰船模板：group_type / equip_1..5 / star_max / oil_at_end
T_SKIN = 'ship_skin_template'            # 皮肤：ship_group / painting / name
T_STRENGTHEN = 'ship_data_strengthen'    # 属性强化上限（来自 sharecfg，见 fetch_lua_tables.py）
T_TRANS = 'ship_data_trans'              # 改造：每个改造组的节点列表（来自 sharecfg）
T_TRANS_NODE = 'transform_data_template'  # 改造节点：effect 属性增量 + gear_score 战力（sharecfg）

# 站点把 attrs[6] 当成「消耗」展示，而游戏这一位恒为 0 —— 实际取值是模板表的 oil_at_end
ARMOR_IDX = 6
# 曾经以为推不出来、只能原样保留的两项，其实都在 `transform_data_template` 里：
#   transform  = 该组所有改造节点的 `effect` 逐项累加
#   gear_score = 该组所有改造节点的 `gear_score` 数组求和（即「改造技能战力」）
KEEP_ONLY_FIELDS = ()
# 仓库没录 transform、但表里能推的船（实测 8 艘：moli / aisaikesi / yonggan /
# beierfasite / bailu / xipeierhaijunshangjiang / yingrui / zhaohe），
# 多是「改造数据已进客户端、正式服尚未开放」。默认不新增 —— 前端拿 `transform`
# 是否存在来判断「改造已开」，贸然补上会让这 8 艘直接显示改造后数值。
# 确认它们已实装后把这里改成 True 即可。
ADD_NEW_TRANSFORM = False

# 站点 option.json 的三个静态表。游戏里没有对应 cfg（在 sharecfg，不在 sharecfgdata），
# 而且改动极少，所以直接照抄仓库现有值 —— 这三张表是"标签文字"，没有推导余地。
#
# 注意两个结构细节（都是抄的时候容易踩的）：
#   1. type 里 22/23/24 共用一条，键位置写成了列表 `[[22, 23, 24], "风帆"]`
#   2. rarity 这张正好说明 ship.json 的稀有度口径：
#      7 = 最高方案(PR) / 8 = 决战方案(DR)，即方案舰在站点是「游戏稀有度 + 2」
OPTION = {
    'type': [
        [1, '驱逐'], [2, '轻巡'], [3, '重巡'], [18, '超巡'], [4, '战巡'], [5, '战列'],
        [6, '轻航'], [7, '航母'], [8, '潜艇'], [10, '航战'], [12, '维修'], [13, '重炮'],
        [17, '潜母'], [19, '运输'], [[22, 23, 24], '风帆'],
    ],
    'rarity': [
        [2, '普通'], [3, '稀有'], [4, '精锐'], [5, '超稀有'], [6, '海上传奇'],
        [7, '最高方案'], [8, '决战方案'],
    ],
    # 编号取自 sharecfg 的 nationality 字段（游戏只存数字，没有名字表），
    # 名字按 bwiki「舰船图鉴」的阵营列表。
    #
    # 100 以上是联动作品，按实装顺序递增；**116 至今没有船用到**，所以空着 ——
    # 补一个永远查不到的编号只会让前端多一个空筛选项。
    # 2026-10-23 补：12（晶环联盟，Plan047 瓦尔帕莱索起）+ 111~115、117 六个联动。
    'nationality': [
        [1, '白鹰'], [2, '皇家'], [3, '重樱'], [4, '铁血'], [5, '东煌'], [6, '撒丁帝国'],
        [7, '北方联合'], [8, '自由鸢尾'], [9, '维希教廷'], [11, '郁金王国'],
        [12, '晶环联盟'], [96, '飓风'],
        [97, 'META'], [98, '其他'], [101, '海王星'], [102, '哔哩哔哩'], [103, '传颂之物'],
        [104, 'KizunaAI'], [105, 'hololive'], [106, '维纳斯假期'], [107, '偶像大师'],
        [108, 'SSSS'], [109, 'Atelier Ryza'], [110, '闪乱神乐'],
        # 以下六个是 2024 之后的新联动，按 wiki 的叫法写（109 是历史遗留的英文名，
        # 与这里的中文名风格不一致，要统一的话单独提一次，别顺手改）。
        [111, '出包王女'], [112, '黑岩射手'], [113, '优米雅的炼金工房'],
        [114, '地城邂逅'], [115, '约会大作战'], [117, 'NieRAutomata'],
    ],
}

NAMECODE_RE = re.compile(r'\{namecode:(\d+)\}')

# `{namecode:N}` 是客户端的全局术语表：N 指向一个完整显示名，存的是国服和谐名
# （257 → 伊490、528 → 圣地亚哥.改）。这张表不在 sharecfgdata 的 35 张表里，
# 拿不到原始文件，只能反解。两个来源互相独立、交叉验证 0 冲突：
#
#   A 默认皮肤自举 —— 组内 skin id 最小的皮肤名若是 `{namecode:N}`，则 N 就是本船名
#   B 仓库对齐    —— 拿 public/data/skin.json 里已解析的中文名，按占位符位置反推
#
# 单靠 A 有结构性盲区：它一次只能解出「默认皮肤那一个编号」，同一艘船的原版 / 改型 /
# 小形态编号都拿不到。257 甚至不是时雨自己 —— 它是台词里提到的另一艘船；528 是 524
# 的改型（圣地亚哥.改 = `{namecode:528}`，而 524 = 圣地亚哥）。所以 A 实测只到
# 284/288，缺的正是这类。B 补上后 286/288，剩下 555/556 属于 900xxx 替身段，
# 对应组会被 `base_ship_id` 整组丢弃，不会出现在产出里。
#
# 固化表存的是 B 的全量 + A 的独有项；运行时再用 A 覆盖一遍 —— 游戏若给某船改名，
# A 会立刻反映出来，固化表则兜住 A 解不出的编号。两边不一致时告警但不阻断。
#
# 放在 scripts/data/ 而不是仓库根：它只在构建期用，不是站点资源，跟脚本放一起
# 才不会在「把 scripts/ 整个拷进仓库」时漏掉。
NAMECODE_FILE = Path(__file__).resolve().parent / 'data' / 'namecode.json'

_NAMECODE_CACHE: dict[int, str] | None = None


def namecode_baseline() -> dict[int, str]:
    """固定基线的 namecode 映射；文件缺失/损坏时返回空表（退化为只靠 A）。"""
    global _NAMECODE_CACHE
    if _NAMECODE_CACHE is not None:
        return _NAMECODE_CACHE
    try:
        raw = json.loads(NAMECODE_FILE.read_text('utf-8'))
        _NAMECODE_CACHE = {int(k): str(v) for k, v in raw.items()}
    except FileNotFoundError:
        warn(f'缺少 {NAMECODE_FILE}，namecode 只能靠默认皮肤自举（改型/META 名会残留占位符）')
        _NAMECODE_CACHE = {}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        warn(f'{NAMECODE_FILE} 读取失败（{exc}），同上')
        _NAMECODE_CACHE = {}
    return _NAMECODE_CACHE


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def load(raw_dir: Path, table: str) -> dict:
    path = Path(raw_dir) / f'{table}.json'
    if not path.exists():
        return {}
    return json.loads(path.read_text('utf-8'))


def _int_keyed(d: dict) -> dict[int, dict]:
    """配置表的键是字符串化数字，统一转 int 便于排序比较。"""
    out = {}
    for key, val in d.items():
        try:
            out[int(key)] = val
        except (TypeError, ValueError):
            continue
    return out


def _load_prev(prev_dir, name: str) -> dict:
    """读仓库现有的 public/data/<name>.json，作为「人工字段」的兜底。

    读不到就当空表：首次接入（或没给 --prev）时退化成全量重建，
    此时推不出的字段直接不写，不会写出错误值。
    """
    if not prev_dir:
        return {}
    path = Path(prev_dir) / f'{name}.json'
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_stand_in(ship_id: int) -> bool:
    """900000-999999 是敌方/替身段，和真实船共用 painting 名，必须排除。

    实测：不排除时 kelifulan 会被 900188（只有 1 个皮肤）覆盖掉 10209（9 个皮肤）。
    """
    return 900000 <= ship_id <= 999999


def _is_research_ship(ship_id: int) -> bool:
    """方案舰（PR/DR 科研船）：id 形如 199011 / 299011 / 399011。

    站点把它们放在稀有度 7/8 上（见 OPTION['rarity']），即游戏值 + 2。
    实测：这条规则让 789 艘船的 rarity 全部对上，用 `star==3 and star_max==6`
    会误伤 276 艘（很多普通船也是 3 星起、6 星满）。
    """
    return (ship_id // 1000) % 100 == 99


def build_index(raw_dir: Path):
    """把三张表编织成按「船组」索引的结构。"""
    stat = _int_keyed(load(raw_dir, T_SHIP_STAT))
    tpl = _int_keyed(load(raw_dir, T_SHIP_TPL))
    skin = _int_keyed(load(raw_dir, T_SKIN))

    group_ids: dict[int, list[int]] = defaultdict(list)
    for ship_id, row in tpl.items():
        try:
            group_ids[int(row['group_type'])].append(ship_id)
        except (KeyError, TypeError, ValueError):
            continue

    group_skins: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for skin_id, row in skin.items():
        try:
            group_skins[int(row['ship_group'])].append((skin_id, row))
        except (KeyError, TypeError, ValueError):
            continue

    return stat, tpl, skin, group_ids, group_skins


def base_ship_id(group_ids, group: int):
    """组内「本体」id：尾号为 1 的那个（100001 / 199011 / 907011 都符合）。

    没有尾号 1 的组一律视为替身/敌方组，直接丢弃（实测丢弃 374 个组）。
    """
    for ship_id in sorted(group_ids.get(group, [])):
        if ship_id % 10 == 1:
            return ship_id
    return None


def max_ship_id(stat, group_ids, group: int):
    """组内「满改造」id：星数最高的那个，用于属性/装备栏。"""
    best, best_star = None, -1
    for ship_id in group_ids.get(group, []):
        if _is_stand_in(ship_id):
            continue
        row = stat.get(ship_id)
        if not row:
            continue
        star = row.get('star') or 0
        if star > best_star:
            best, best_star = ship_id, star
    return best


def _oil_at_end(tpl, ship_id):
    """某个形态的出击油耗（`oil_at_end` 的内容就是该形态的满级油耗）。

    `oil_at_end` 是「该形态满级、满突破」时的最终油耗，而游戏公式里
    `未突破次数 = 0` 正好把两个分支都退化成它本身，所以可以直接当展示值用。
    """
    if not ship_id:
        return None
    return (tpl.get(ship_id) or {}).get('oil_at_end')


def _attr_name_index(raw_dir) -> dict[str, int]:
    """`attribute_info_by_type` 的「字段名 → base 下标」。

    游戏这张表是 1-based（1 耐久 … 12 反潜），站点 `base` 是 0-based，所以减 1。
    改造节点 `effect` 里的 `durability` / `cannon` 等键就靠它落到对应位置。
    """
    out = {}
    for idx, row in _int_keyed(load(raw_dir, 'attribute_info_by_type')).items():
        name = (row or {}).get('name')
        if name:
            out[name] = idx - 1
    return out


def _transform_of(trans: dict, nodes: dict, group: int, name2idx: dict):
    """改造组的 `transform_list` → (属性增量向量, 改造技能战力)。

    每个节点在 `transform_data_template` 里：
      - `effect`     是数组，元素按「第 N 级」排列，每项是属性增量
      - `gear_score` 同样按级排列，是该级带来的战力
    两者都**逐级累加**（不是取最高级）。卡辛 11 个节点累加正好等于仓库的
    `transform=[165,25,10,0,0,20,0,0,25,3,0,0]` / `gear_score=250`；
    仓库 101 艘有改造数据的船实测**逐位全等 101/101**。

    `gear_score` 就是站点「综合性能」公式末项「改造技能战力」，含「战术启发」
    节点（该节点 `effect` 是 `skill_id`，不参与属性累加，但战力照样计入）。
    """
    entries = (trans.get(str(group)) or {}).get('transform_list') or []
    if not entries:
        return None, None
    tf = [0] * 12
    score = 0
    for slot in entries:
        for _icon, node_id in slot:
            node = nodes.get(str(node_id))
            if not node:
                return None, None
            score += sum(node.get('gear_score') or [])
            for eff in node.get('effect') or []:
                for key, val in eff.items():
                    idx = name2idx.get(key)
                    if idx is not None and isinstance(val, (int, float)):
                        tf[idx] += val
    return tf, score


def namecode_map(stat, group_ids, group_skins) -> dict[int, str]:
    """`{namecode:N}` → 中文名（纯名字）。固定基线打底，再用默认皮肤自举覆盖。

    皮肤表里大量名字是 `{namecode:47}` 这种占位符，而 `ship_data_statistics.name`
    是解析后的真名。默认皮肤（组内 skin id 最小）的占位符正好能对上本体名字，
    于是可以反解出一张映射表：实测解出 284 条，皮肤表需要 288 条，缺 4 条 ——
    缺的 257 / 528 是「同组里非默认皮肤」的编号（台词里提到别的船、改型后缀），
    自举这条路结构上就拿不到，靠 `data/namecode.json` 的固定基线补。

    坑：占位符外面可能还拼了别的东西，例如 `{namecode:198}·META`、
    `{namecode:X}(μ兵装)`；而 META 船的 statistics.name 本身已经带「·META」。
    如果直接拿 statistics.name 当映射，替换后就会变成「博伊西·META·META」。
    所以要先按「模板里占位符之外的字面量」把 statistics.name 洗干净，只存纯名字。
    """
    baseline = dict(namecode_baseline())
    derived: dict[int, str] = {}
    for group in sorted(group_skins):
        skins = group_skins[group]
        ship_id = base_ship_id(group_ids, group)
        if not ship_id:
            continue
        row = stat.get(ship_id)
        if not row:
            continue
        default = min(skins)[1]
        text = str(default.get('name') or '')
        found = NAMECODE_RE.search(text)
        if not found:
            continue
        prefix, suffix = text[:found.start()], text[found.end():]
        pure = str(row.get('name', ''))
        if suffix and pure.endswith(suffix):
            pure = pure[: -len(suffix)]
        if prefix and pure.startswith(prefix):
            pure = pure[len(prefix):]
        derived[int(found.group(1))] = pure

    # 冲突说明游戏改名了：以当前表算出来的为准，但要说出来，别让基线悄悄压住新值
    clash = {k: (baseline[k], derived[k]) for k in set(baseline) & set(derived)
             if baseline[k] != derived[k]}
    if clash:
        sample = '; '.join(f'{k}: 基线={a!r} 自举={b!r}' for k, (a, b) in
                           sorted(clash.items())[:3])
        warn(f'namecode 固定表与自举冲突 {len(clash)} 条，采用自举值（{sample}）')
    baseline.update(derived)
    return baseline


def resolve_name(text: str, mapping: dict[int, str]) -> str:
    """解析 `{namecode:N}`。

    META 船还有个偶发情况：statistics.name 已带「·META」，模板又拼了一次，
    替换后出现「长门·META·META」，这里顺手收一下双后缀。
    """
    def repl(m):
        return mapping.get(int(m.group(1)), m.group(0))

    name = NAMECODE_RE.sub(repl, str(text or ''))
    suffix = '·META'
    while name.count(suffix) > 1:
        name = name.replace(suffix + suffix, suffix)
    return name


def write_json(path, obj) -> None:
    """按仓库现有格式写 json —— 逐字节对齐，不是"差不多"。

    实测仓库 `public/data/*.json` 的序列化是：
        json.dumps(obj, ensure_ascii=True)    # 中文写成 \\uXXXX
        默认分隔符 ', ' / ': '                 # 不是紧凑模式
        无末尾换行
        键按船组 id 升序
    用 `common.write_json`（不转义中文 + 紧凑 + 末尾换行）会导致这三个文件
    每次全量重写、diff 变成整文件，所以这里自带一个写入器。
    build_data.py 检测到本模块导出 write_json 时会优先用它。
    """
    import json as _json
    from pathlib import Path as _Path

    target = _Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='\n') as fh:
        fh.write(_json.dumps(obj, ensure_ascii=True))
    tmp.replace(target)


# ---------------------------------------------------------------------------
# 各文件的构建
# ---------------------------------------------------------------------------
def build_ship(raw_dir: Path) -> dict:
    """{默认皮肤 painting(小写): {painting, name, type, rarity, nationality}}

    实测：789 个键全部覆盖仓库，键序外逐字段全等 787/789。两处差异都是游戏侧改动：
      - siwangzhuzai：仓库 'DEAD MASTER'，现在叫 '死亡主宰'（联动改名）
      - huangjiacaifu：仓库 type 22，现在是 23（风帆细分）
    """
    stat, _tpl, _skin, group_ids, group_skins = build_index(raw_dir)
    out = {}
    for group in sorted(group_skins):
        skins = group_skins[group]
        ship_id = base_ship_id(group_ids, group)
        if not ship_id:
            continue
        row = stat.get(ship_id)
        if not row:
            continue
        painting = (min(skins)[1].get('painting') or '').lower()
        if not painting:
            continue
        rarity = row.get('rarity') or 0
        out[painting] = {
            'painting': painting,
            'name': row.get('name', ''),
            'type': row.get('type'),
            'rarity': rarity + 2 if _is_research_ship(ship_id) else rarity,
            'nationality': row.get('nationality'),
        }
    return out


def build_skin(raw_dir: Path) -> dict:
    """{默认皮肤 painting: {该组每个皮肤 painting: {painting, name}}}"""
    stat, _tpl, _skin, group_ids, group_skins = build_index(raw_dir)
    codes = namecode_map(stat, group_ids, group_skins)
    out = {}
    skipped_asmr = 0
    for group in sorted(group_skins):
        skins = group_skins[group]
        if not base_ship_id(group_ids, group):
            continue
        key = (min(skins)[1].get('painting') or '').lower()
        if not key:
            continue
        entry = {}
        for _sid, row in sorted(skins):
            painting = (row.get('painting') or '').lower()
            if not painting:
                continue
            # ASMR 版皮肤：立绘与本体重合，站点不收录。
            # 留着它，前端会列出两条同名皮肤（`hu_2` 与 `hu_2_asmr` 都叫「临水的辰星」）。
            if is_asmr(painting):
                skipped_asmr += 1
                continue
            entry[painting] = {
                'painting': painting,
                'name': resolve_name(row.get('name'), codes),
            }
        out[key] = entry

    # 新船若用了基线里没有的编号，这里会亮出来 —— 否则占位符会被原样写进仓库，
    # 站点上显示成 `{namecode:996}` 也没人报错
    left = sorted({m.group(0) for group in out.values() for row in group.values()
                   for m in NAMECODE_RE.finditer(row['name'])})
    if left:
        warn(f'skin.json 仍有 {len(left)} 个 namecode 未解析：{", ".join(left[:8])}'
             '（补进 data/namecode.json 或等它成为某组默认皮肤）')
    if skipped_asmr:
        warn(f'skin.json 剔除 {skipped_asmr} 个 `_asmr` 皮肤（立绘与本体重合，站点不收录）')
    return out


def build_ship_attr(raw_dir: Path, prev_dir=None) -> dict:
    """{painting: {base, growth, strengthen?, transform?, gear_score?}}

    这个表是「可推导 + 人工」混在一起的，所以采用**合并**方式：
    能推的字段用游戏数据覆盖，推不出的字段保留仓库旧值。

    字段来源（实测 CN 9.7.398 对仓库 789 艘）：

      - `growth`   = 满破形态的 `attrs_growth`，普通船 100% 一致
      - `base[i]`  = 满破形态的 `attrs[i]`（i ≠ 6），普通船 100% 一致
                      · 索引含义由 `attribute_info_by_type` 固化：
                        0 耐久 / 1 炮击 / 2 雷击 / 3 防空 / 4 航空 / 5 装填
                        / 6 装甲 / 7 命中 / 8 机动 / 9 航速 / 10 幸运 / 11 反潜
      - `base[6]`  = 满破形态的 `oil_at_end`（该形态的满级出击油耗）。
                      · 游戏 `attrs[6]` 恒为 0，站点把这一位当「消耗」展示，
                        所以取的是模板表的 oil_at_end 而不是 attrs
                      · 游戏公式（等级系数 = 0.5 + min(lv,99)×0.005，满级恒 0.995）：
                            水面舰 = floor(1 + (最终油耗 − 2×未突破次数) × 0.995)
                            潜艇   = floor(0 + (最终油耗 + 1 − 1×未突破次数) × 0.995)
                        未突破次数 = 0（满破）时两式都退化为 `最终油耗` 本身，
                        而 `最终油耗` 就是满破形态的 oil_at_end ⇒ **无条件可推导**
                      · 旧仓库对 36 艘潜艇记的是 2 破形态的值（F−1），
                        套本规则会 +1：这是「取错突破形态」，不是历史人工口径
                      · **「满破形态」不等于字面上的尾号 4**：873 组取
                        `max_ship_id`（组内星数最高的真实形态，实测恒为尾号 4）；
                        24 个 9xxxxx 未实装组组内只有 stand-in id 会被排除，
                        要退到 `base_id + 3`；3 个只有单形态的船退到 `base_id`。
                        反过来先查 `base_id + 3` 会踩 4 艘特殊船（组内混进了
                        别组的尾号 4），详见主循环里的取值链注释。
      - `strengthen` = `ship_data_strengthen[strengthen_id].durability`，5 个值，
                     即属性强化上限。普通船 663/789 精确一致；方案舰走蓝图强化、
                     `_alter` 在 9xxxxx 段查不到记录，这两类仓库都记全 0。
                     **兜底恒为 `[0,0,0,0,0]`** —— 前端读 `strengthen[i-1]` 没有
                     可空保护，键缺失会直接抛错，所以这一项不允许缺席。
      - `transform` = 该组所有改造节点的 `effect` 逐项累加（sharecfg 的
                     `ship_data_trans.transform_list` → `transform_data_template.effect`），
                     属性名经 `attribute_info_by_type` 落到 12 位下标
      - `gear_score` = 同一批节点的 `gear_score` 数组求和，即「改造技能战力」
                     （站点综合性能公式的最后一项；「战术启发」节点只有 `skill_id`，
                      不计属性，但战力照样算进去）。仓库 101 艘**逐位全等 101/101**

    **两类整段保留旧值**：
      - 方案舰（`(id//1000)%100==99`，90 艘）：各形态 attrs 完全相同，
        满破属性不在表里，仓库值是另一套口径（`attrs × 2.4635` 量级）
      - `_alter`（49 艘）：9xxxxx 段的改造形态，仓库值比表里满破 attrs 大约 1.4 倍，
        且每个索引的系数都不一样，没有任何表能对上

    实测结果：除 `base[6]`（见上，36 艘潜艇旧值少 1）外，
    与仓库 789 艘**逐字段完全一致 789/789**。

    参数 `prev_dir` 指向仓库现有的 `public/data`。为 None 时按「全量重建」处理，
    推不出的字段就不写（新船首次接入时会出现这种情形）。
    """
    stat, tpl, _skin, group_ids, group_skins = build_index(raw_dir)
    strengthen = _int_keyed(load(raw_dir, T_STRENGTHEN))
    trans = load(raw_dir, T_TRANS)
    nodes = load(raw_dir, T_TRANS_NODE)
    name2idx = _attr_name_index(raw_dir)
    prev = _load_prev(prev_dir, 'ship-attr')
    out = {}
    for group in sorted(group_skins):
        skins = group_skins[group]
        base_id = base_ship_id(group_ids, group)
        if not base_id:
            continue
        key = (min(skins)[1].get('painting') or '').lower()
        if not key:
            continue
        old = prev.get(key) or {}
        ship_id = max_ship_id(stat, group_ids, group)
        row = stat.get(ship_id) if ship_id else None
        if not row:
            # 组内找不到满破条目（24 个 9xxxxx 未实装组，组内只有 stand-in id，
            # 会被 max_ship_id 排除）。属性只能沿旧值，但消耗有确定来源：
            # 本体尾号 4（base_id + 3）的 oil_at_end。
            if old:
                entry = dict(old)
                oil = _oil_at_end(tpl, base_id + 3)
                if oil is None:
                    oil = _oil_at_end(tpl, base_id)
                if oil is not None and entry.get('base') and len(entry['base']) > ARMOR_IDX:
                    entry['base'] = list(entry['base'])
                    entry['base'][ARMOR_IDX] = oil
                out[key] = entry
            continue

        research = _is_research_ship(base_id)
        if (research or key.endswith('_alter')) and old.get('base'):
            # 这两类整段沿用旧值：
            #   · 方案舰：各形态 attrs 完全相同，满破属性不在表里，仓库值来自另一套口径
            #   · `_alter`：9xxxxx 段的改造形态，仓库值比表里满破 attrs 大（约 1.4 倍，
            #     且每位系数不同），没有任何表能对上
            entry = {
                'base': list(old['base']),
                'growth': list(old.get('growth') or row.get('attrs_growth') or []),
            }
        else:
            entry = {
                'base': list(row.get('attrs') or []),
                'growth': list(row.get('attrs_growth') or []),
            }

        # 消耗统一由「满破形态的 oil_at_end」推导。它是 base 里唯一有确切游戏来源的
        # 一位，所以「方案舰 / `_alter` 整段保留旧值」对它不适用，两支都要覆盖。
        #
        # 取值链（实测 897 组全部命中，无一组落空）：
        #   ① 组内星数最高的真实形态（max_ship_id，873 组，恒等于尾号 4）
        #   ② 本体尾号 4 = base_id + 3（9xxxxx 未实装组用，24 组）
        #   ③ 唯一形态 base_id（gin / kin / buli_super 这种没有突破档的，3 组）
        #   ④ 旧值；仍无则不写（写 0 会让前端把「消耗」显示成 0）
        #
        # 注意不能倒过来先查 base_id + 3：有 4 艘特殊船（皇家财富号、白上吹雪、
        # 朱利奥·凯撒·META、DEAD MASTER）组内混进了别组的尾号 4，
        # 按字面 +3 会取到错形态（偏差 +3 / -12 不等）。
        if len(entry['base']) > ARMOR_IDX:
            oil = _oil_at_end(tpl, ship_id)
            if oil is None and not key.endswith('_alter'):
                oil = _oil_at_end(tpl, base_id + 3)
            if oil is None:
                oil = _oil_at_end(tpl, base_id)
            if oil is None and old.get('base') and len(old['base']) > ARMOR_IDX:
                oil = old['base'][ARMOR_IDX]
            if oil is not None:
                entry['base'][ARMOR_IDX] = oil

        # 前端读的是 `attr['strengthen'][i-1]`，**没有可空保护**（默认 strengthen=true），
        # 这个键一旦缺失就抛错白屏。所以每条记录都保证有值。
        record = strengthen.get((tpl.get(ship_id) or {}).get('strengthen_id'))
        if research or key.endswith('_alter'):
            # 方案舰走蓝图强化；`_alter`（9xxxxx 改造段）在 ship_data_strengthen 里查不到。
            # 仓库现有 49 艘 `_alter` 全是 [0,0,0,0,0]，补零既对齐口径又满足前端。
            if old.get('strengthen'):
                entry['strengthen'] = list(old['strengthen'])
            else:
                entry['strengthen'] = [0, 0, 0, 0, 0]
        elif record and isinstance(record.get('durability'), list):
            entry['strengthen'] = list(record['durability'])
        elif old.get('strengthen'):
            entry['strengthen'] = list(old['strengthen'])
        else:
            entry['strengthen'] = [0, 0, 0, 0, 0]

        # 改造增量与改造技能战力：都来自该组的改造节点列表。推不出时才沿旧值
        # （早期以为「无来源」，实际是没找到 sharecfg 里这两张表）。
        tf, score = _transform_of(trans, nodes, group, name2idx)
        if old.get('transform') is not None or (ADD_NEW_TRANSFORM and tf is not None):
            entry['transform'] = tf if tf is not None else old.get('transform')
            score_val = score if tf is not None else old.get('gear_score')
            if score_val is not None:
                entry['gear_score'] = score_val
        out[key] = entry
    return out


def build_ship_equip(raw_dir: Path) -> dict:
    """{painting: {base: base_list, slot: [equip_1..equip_5]}}

    ⚠️ 部分对齐（内层全等 503/597）。装备栏随改造增加，所以必须取「满改造」id；
    用本体 id 只有 6/597。

    剩下 92 处差异**全部落在 base[0]**，方向一致：我恒为 1，仓库是 2 或 3
    （86 处 3、6 处 2）；base[1] / base[2] 和数组长度完全一致，只有 2 处 slot 不同。
    根因已定位：`max_ship_id()` 靠 `group_type` 归组去找满改造 id，但改造形态的
    group_type 并不总与本体相同 —— 101021(10102) → 101121(10112) 是 +10，
    而 206034(20603) → 206134(20603) 却相同。规律不统一，导致部分船的改造形态
    根本没进 `group_ids[group]`，base_list 就停在改造前的值上。

    所以这是取法问题而不是数据缺失，修好预期能到 95%+。修法要重做「同船归属」的判据
    （不能只看 group_type），会同时影响 `build_ship_attr`，所以没在本轮顺手改。
    """
    stat, tpl, _skin, group_ids, group_skins = build_index(raw_dir)
    out = {}
    for group in sorted(group_skins):
        skins = group_skins[group]
        if not base_ship_id(group_ids, group):
            continue
        ship_id = max_ship_id(stat, group_ids, group)
        if not ship_id:
            continue
        tpl_row = tpl.get(ship_id)
        stat_row = stat.get(ship_id)
        if not tpl_row or not stat_row:
            continue
        slot = [tpl_row.get(f'equip_{i}') for i in range(1, 6)]
        if all(item is None for item in slot):
            continue
        key = (min(skins)[1].get('painting') or '').lower()
        if not key:
            continue
        out[key] = {'base': stat_row.get('base_list'), 'slot': slot}
    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
# 能推导到什么程度就产出什么程度：只放已验证到可用的文件。
# ship-attr 需要 sharecfg/ship_data_strengthen.lua，且依赖读旧文件合并人工字段。
ENABLED = ('ship', 'skin', 'option', 'ship-attr')

BUILDERS = {
    'ship': build_ship,
    'skin': build_skin,
    'option': lambda _raw: OPTION,
    'ship-attr': build_ship_attr,
    'ship-equip': build_ship_equip,
}

# 明确不产出、依赖人工维护的文件（会在 build_data.py 里原样保留）
MANUAL = {
    'cg': '图床链接列表，人工挑选',
    'new': '首页轮播，人工挑选',
    'nickname': '频道昵称，人工整理',
    'server': '服务器列表，人工维护',
    'vote': '年度投票结果，人工录入',
    '2017-cn/2017-jp/2019-*/2021-*': '历年活动统计，外部数据，不在游戏表里',
}


# 需要读旧文件才能补全的构建器（人工字段 / 无来源字段）。
NEEDS_PREV = ('ship-attr',)


def build(raw_dir, prev_dir=None) -> dict:
    """build_data.py 调用的入口。

    键是**不带扩展名**的文件名 —— build_data.py 内部会自己补 `.json`，
    写成 `ship.json` 会得到 `ship.json.json`（踩过一次）。

    `prev_dir` 是仓库现有的 public/data，只有 NEEDS_PREV 里的构建器会用：
    它们有一部分字段（油耗、改造增加值、方案舰属性）推不出来，必须沿用旧值。
    """
    raw_dir = Path(raw_dir)
    result = {}
    for name in ENABLED:
        builder = BUILDERS.get(name)
        if not builder:
            continue
        data = builder(raw_dir, prev_dir) if name in NEEDS_PREV else builder(raw_dir)
        if data:
            result[name] = data
    return result
