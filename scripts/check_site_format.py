"""校验产出是否仍然符合站点的资源格式。

这条检查是给未来的自己/给 CI 用的：格式一旦漂移（文件名大小写、缺 obj、json 少字段、
OBJ 换成 LF 换行……），站点不会报错，只会白屏或图层错位。所以宁可在这里失败。

用法：
    python scripts/check_site_format.py --public public            # 全量
    python scripts/check_site_format.py --public public --limit 20 # 抽查（CI 默认）
"""

import argparse
import json
import re
import struct
import sys
from pathlib import Path

from common import log, warn, die

NAME_RE = re.compile(r'^[a-z0-9_\-\.]+$')
LAYER_FIELDS = ('size', 'pivot', 'position', 'rawSize')
# 图标目录的宽高比：站点用 `width:100%` + `fit=contain` 摆图，比例错了不会报错只会错位
ICON_ASPECT = {
    'squareicon': (1, 1),
    'shipicon': (3, 4),
    'equipicon': (1, 1),
    'iconframe': (1, 1),
    'qicon': (1, 1),
}


def png_size(path: Path):
    data = path.read_bytes()[:33]
    if not data.startswith(b'\x89PNG'):
        return None
    w, h, depth, ctype = struct.unpack('>IIBB', data[16:26])
    return w, h, depth, ctype


def check_obj(path: Path, expect_name: str, problems: list) -> int:
    raw = path.read_bytes()
    if b'\r\n' not in raw:
        problems.append(f'{path}: OBJ 不是 CRLF 换行，客户端会把它切成一行')
        return 0
    lines = raw.decode('utf-8', 'replace').split('\r\n')
    if lines[0] != f'g {expect_name}-mesh':
        problems.append(f'{path}: 首行应为 "g {expect_name}-mesh"，实际 {lines[0]!r}')
    v = [l for l in lines if l.startswith('v ')]
    vt = [l for l in lines if l.startswith('vt ')]
    if not v or len(v) != len(vt):
        problems.append(f'{path}: v({len(v)}) 与 vt({len(vt)}) 数量不一致')
    if len(v) % 4:
        problems.append(f'{path}: 顶点数 {len(v)} 不是 4 的倍数，无法按四边形还原')
    for line in v[:1] + vt[:1]:
        parts = line.split()
        if len(parts) < 3:
            problems.append(f'{path}: 行格式异常 {line!r}')

    # 行序也要守：客户端是 `mesh[i]` 取顶点、`mesh[i + count]` 取 uv，纯按行号定位，
    # 完全不看 f 行。所以 v 段和 vt 段之间只要插了任何东西（vn、第二个 g 行、空行），
    # uv 就会整体错位 —— 画出来是花图，但控制台一声不响。
    n = len(v)
    if n and len(vt) == n:
        head = lines[1:n + 1]
        tail = lines[n + 1:2 * n + 1]
        if not all(line.startswith('v ') for line in head):
            bad = next(line for line in head if not line.startswith('v '))
            problems.append(f'{path}: 第 1..{n} 行应全是顶点，出现 {bad!r}（会顶掉后面的 uv 索引）')
        if not all(line.startswith('vt ') for line in tail):
            bad = next(line for line in tail if not line.startswith('vt '))
            problems.append(f'{path}: 第 {n + 1}..{2 * n} 行应全是 uv，出现 {bad!r}'
                            '（vn / g / f 插在中间会让 uv 整体错位）')
    return n


def check_painting(directory: Path, problems: list) -> dict:
    name = directory.name
    if not NAME_RE.match(name):
        problems.append(f'{directory}: 目录名不是全小写/含非法字符（站点按小写拼路径）')
    layout_file = directory / f'{name}.json'
    if not layout_file.exists():
        problems.append(f'{directory}: 缺少 {name}.json')
        return {}
    try:
        layout = json.loads(layout_file.read_text('u8'))
    except Exception as exc:
        problems.append(f'{layout_file}: JSON 解析失败 {exc}')
        return {}
    if name not in layout:
        problems.append(f'{layout_file}: 缺少基底层 {name}')
    for key, entry in layout.items():
        # 图层名会直接拼成 <key>.png，大小写错了站点 404
        if not NAME_RE.match(key):
            problems.append(f'{layout_file}: 图层名 {key!r} 不是全小写/含非法字符')
        for field in LAYER_FIELDS:
            if field not in entry:
                problems.append(f'{layout_file}: 图层 {key} 缺字段 {field}')
        if key == name and 'view' not in entry:
            problems.append(f'{layout_file}: 基底层缺 view')
        if key == 'face':
            if 'list' not in entry:
                problems.append(f'{layout_file}: face 缺 list')
            continue
        png = directory / f'{key}.png'
        if not png.exists():
            problems.append(f'{directory}: 缺图层贴图 {key}.png')
            png = None
        if entry.get('raw'):
            # raw 层没有 mesh，客户端把整张图铺到 (0,0)-(size)，靠 rawSize 换算缩放。
            # 图片尺寸和 rawSize 只要不等，图层就会错位，而且不会报错。
            if png is not None:
                info = png_size(png)
                raw_size = entry.get('rawSize') or [0, 0]
                if info and (round(float(raw_size[0])) != info[0]
                             or round(float(raw_size[1])) != info[1]):
                    problems.append(
                        f'{png}: raw 层 rawSize={list(raw_size)} 与图片 {info[0]}x{info[1]} 不一致'
                        '（客户端会把整图铺满 rawSize，尺寸不同就错位）')
            continue
        obj = directory / f'{key}-mesh.obj'
        if not obj.exists():
            problems.append(f'{directory}: 缺网格 {key}-mesh.obj')
        else:
            check_obj(obj, key, problems)
    return layout


def check_face(directory: Path, layout: dict, public: Path, problems: list):
    entry = layout.get('face')
    if not entry:
        return
    face_dir = public / 'paintingface' / directory.name
    if not face_dir.exists():
        problems.append(f'{directory}: json 里有 face，但缺 paintingface/{directory.name}/')
        return
    files = sorted(face_dir.glob('*.png'))
    want = len(entry.get('list') or [])
    if len(files) != want:
        problems.append(f'{face_dir}: 表情文件 {len(files)} 个，json list 声明 {want} 个')
    names = {f.name for f in files}
    for index in range(want):
        if f'{index}.png' not in names:
            problems.append(f'{face_dir}: 缺 {index}.png')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--public', default='public')
    ap.add_argument('--limit', type=int, default=0, help='只抽查前 N 个立绘')
    ap.add_argument('--icons', nargs='*', default=['squareicon', 'shipicon'],
                    help='顺带检查这些平铺图标目录的命名')
    args = ap.parse_args()

    public = Path(args.public)
    root = public / 'painting'
    problems: list[str] = []

    dirs: list[Path] = []
    total_layers = 0
    if root.exists():
        dirs = sorted(d for d in root.iterdir() if d.is_dir())
        if args.limit:
            dirs = dirs[:args.limit]
        for directory in dirs:
            layout = check_painting(directory, problems)
            total_layers += len(layout)
            check_face(directory, layout, public, problems)
    else:
        warn(f'{root} 不存在，跳过立绘检查')

    icon_report: list[str] = []
    for icon in args.icons:
        icon_dir = public / icon
        if not icon_dir.exists():
            continue
        pngs = sorted(p for p in icon_dir.iterdir()
                      if p.is_file() and p.suffix == '.png')
        bad = [p.name for p in pngs if not NAME_RE.match(p.stem)]
        if bad:
            problems.append(f'{icon_dir}: {len(bad)} 个文件名不是全小写，例如 {bad[:3]}')
        want = ICON_ASPECT.get(icon)
        sizes: dict[tuple, int] = {}
        for png in pngs[:max(args.limit, 50)]:
            info = png_size(png)
            if not info:
                problems.append(f'{png}: 不是有效 PNG')
                continue
            width, height, depth, ctype = info
            sizes[(width, height)] = sizes.get((width, height), 0) + 1
            if ctype != 6:
                problems.append(f'{png}: 色彩类型 {ctype}（应为 6 = RGBA），站点需要透明通道')
            if want and width * want[1] != height * want[0]:
                problems.append(f'{png}: {width}x{height} 宽高比不是 {want[0]}:{want[1]}')
        if sizes:
            desc = ', '.join(f'{w}x{h}×{n}' for (w, h), n in
                             sorted(sizes.items(), key=lambda kv: -kv[1]))
            icon_report.append(f'{icon} {len(pngs)} 个（{desc}）')

    if problems:
        for item in problems[:40]:
            warn(item)
        if len(problems) > 40:
            warn(f'……还有 {len(problems) - 40} 条')
        die(f'格式检查未通过：{len(problems)} 处问题')
        return 1

    log(f'格式检查通过：{len(dirs)} 个立绘，{total_layers} 个图层')
    for line in icon_report:
        log(f'  图标 {line}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
