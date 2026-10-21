"""立绘导出：产出与站点现有仓库完全一致的「三件套」。

站点（src/views/profile/index.vue）不是读一张合成图，而是在客户端用 pixi 逐层重建：

    painting/<name>/<name>.json         布局：每个图层一份 size/pivot/position/rawSize
    painting/<name>/<layer>.png         该图层的贴图（**未做网格变形**的原图集）
    painting/<name>/<layer>-mesh.obj    网格：把贴图切块摆到正确位置的四边形列表
    paintingface/<name>/<i>.png         表情，序号与 json 里 face.list 对应

JSON 的取值规则（由仓库现有 2167 个立绘反查，并用真实资源逐个验证过）：

    size     = 该图层 RectTransform.m_SizeDelta
    pivot    = m_Pivot（原样，不做 Y 翻转）
    position = m_AnchoredPosition（原样）
    rawSize  = MonoBehaviour.mRawSpriteSize
    view     = mainFullScreen 节点的 m_SizeDelta            （仅基底层）
    基底层 position = mainFullScreen 的 m_AnchoredPosition  （基底层自身 anchor 恒为 0）
    face     = face 节点的 size/pivot/position + 表情数量 list
    raw      = 该图层没有 mMesh 时为 true（客户端整张贴图，不写 obj）

OBJ 数值格式也按现有文件对齐：坐标用 %.7g，v 的 x 轴取反，行尾 CRLF。
"""

import json
import re
from pathlib import Path

from common import log, warn

SKIP_NODES = ('frameContain', 'Touch')
FRAME_NODE = 'mainFullScreen'
FACE_NODE = 'face'


def _np():
    import numpy as np
    return np


def _vec2(value) -> list:
    """UnityPy 不同版本的向量字段名不一致（x/y 或 X/Y），统一取值。"""
    if value is None:
        return [0.0, 0.0]
    for ax, ay in (('x', 'y'), ('X', 'Y')):
        if hasattr(value, ax):
            return [float(getattr(value, ax)), float(getattr(value, ay))]
    try:
        return [float(value[0]), float(value[1])]
    except Exception:
        return [0.0, 0.0]


def _raw_size(mono) -> list:
    raw = mono.get('mRawSpriteSize') or {}
    if isinstance(raw, dict):
        return [float(raw.get('x') or raw.get('X') or 0),
                float(raw.get('y') or raw.get('Y') or 0)]
    return _vec2(raw)


def _num(value: float) -> str:
    """OBJ 里的数字：7 位有效数字，进位用「四舍五入」而不是 Python 默认的银行家舍入。

    现有文件里 1/2048 写作 0.0004882813（而不是 .12），说明原工具是 half-up 取整，
    所以这里用 Decimal 精确控制，否则每张图都会有一个末位差异。
    """
    from decimal import Decimal, ROUND_HALF_UP
    d = Decimal(float(value))
    if d == 0:
        return '0'          # 顺手消掉 -0
    step = d.adjusted() - 6          # 7 位有效数字
    q = d.scaleb(-step).quantize(Decimal(1), rounding=ROUND_HALF_UP).scaleb(step)
    return format(q.normalize(), 'f')


def write_mesh_obj(path: Path, name: str, vertices, uvs) -> int:
    """按现有格式写 -mesh.obj，返回四边形数量。

    行布局（客户端靠它算索引，不能改）：
        0            g <name>-mesh
        1..N         v （每四边形 4 条，x 取反、y 原样、z=0）
        N+1..2N      vt（每四边形 4 条，0..1 归一化）
        2N+1         g <name>-mesh_0
        其后         每四边形 2 条 f
    """
    lines = [f'g {name}-mesh']
    count = len(vertices)
    if len(uvs) != count or count % 4:
        # 客户端按 4 顶点一组、i += 4 硬解，这里写歪了它不会报错，只会静默错位
        raise ValueError(f'{name}: 顶点 {count} / uv {len(uvs)}，必须相等且为 4 的倍数')
    for vx, vy in vertices:
        lines.append(f'v {_num(-vx)} {_num(vy)} 0')
    for u, v in uvs:
        lines.append(f'vt {_num(u)} {_num(v)}')
    lines.append(f'g {name}-mesh_0')
    for q in range(len(vertices) // 4):
        a = q * 4
        lines.append(f'f {a + 3}/{a + 3}/{a + 3} {a + 2}/{a + 2}/{a + 2} '
                     f'{a + 1}/{a + 1}/{a + 1}')
        lines.append(f'f {a + 1}/{a + 1}/{a + 1} {a + 4}/{a + 4}/{a + 4} '
                     f'{a + 3}/{a + 3}/{a + 3}')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('\r\n'.join(lines) + '\r\n').encode('utf-8'))
    return len(vertices) // 4


def save_png(path: Path, img) -> Path:
    import cv2 as cv
    path.parent.mkdir(parents=True, exist_ok=True)
    cv.imwrite(str(path), img, [cv.IMWRITE_PNG_COMPRESSION, 6])
    return path


def _natural(value: str):
    return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', (value or '').lower())]


class PaintingExporter:
    """把一个立绘包拆成站点要的三件套。merger 只用来加载依赖和解析 PPtr。"""

    def __init__(self, merger, base_name: str):
        import azl_assets as A
        self.m = merger
        self.base_name = base_name.lower().lower()
        self._to_bgra = A.to_bgra
        self._decode_mesh = A.decode_mesh
        self.layers = []
        self.frame = None
        self.face_node = None
        self.face_mono = None

    def _monos(self, go):
        for comp in go.m_Component:
            obj = comp.component if hasattr(comp, 'component') else comp.get_obj()
            if obj.type.name != 'MonoBehaviour':
                continue
            try:
                tt = obj.read_typetree()
            except Exception:
                continue
            if 'm_Sprite' in tt:
                yield tt

    def _walk(self, trans):
        if trans.object_reader.type.name != 'RectTransform':
            return
        go = trans.m_GameObject.read()
        name = go.m_Name
        if name in SKIP_NODES:
            return
        if name == FRAME_NODE:
            self.frame = trans
        elif name == FACE_NODE:
            self.face_node = trans
            for tt in self._monos(go):
                self.face_mono = tt
                break
        else:
            for tt in self._monos(go):
                self.layers.append({'name': name, 'trans': trans, 'mono': tt})
                break
        for child in trans.m_Children:
            self._walk(child.read())

    def _find_frame(self):
        """mainFullScreen 常常挂在另一个 UI 根下，不在立绘根的子树里，所以全局找。"""
        for obj in self.m.env.objects:
            if obj.type.name != 'RectTransform':
                continue
            try:
                if obj.read().m_GameObject.read().m_Name == FRAME_NODE:
                    return obj.read()
            except Exception:
                continue
        return None

    def collect(self):
        root = None
        for obj in self.m.env.objects:
            if obj.type.name != 'RectTransform':
                continue
            data = obj.read()
            if getattr(data.m_Father, 'path_id', 0) == 0:
                root = data
                break
        if root is None:
            raise ValueError('没有找到根 RectTransform')
        self._walk(root)
        if self.frame is None:
            self.frame = self._find_frame()
        return self.layers

    def _position_of(self, layer) -> list:
        # 基底层自身 anchor 恒为 (0,0)，真正的位置来自 mainFullScreen
        if layer['name'].lower() == self.base_name and self.frame is not None:
            return _vec2(self.frame.m_AnchoredPosition)
        return _vec2(layer['trans'].m_AnchoredPosition)

    def _one_layer(self, layer, used: dict, dest: Path):
        mono = layer['mono']
        reader = self.m._reader(mono.get('m_Sprite'))
        if reader is None:
            return None, '贴图不在已加载的依赖里'
        try:
            sprite = reader.read()
            texture = sprite.m_RD.texture.read()
            atlas = self._to_bgra(texture)
        except Exception as exc:
            return None, f'贴图解码失败 {type(exc).__name__}: {exc}'

        # 仓库里的目录名、文件名、json 键一律小写（游戏内是 2B，站点是 2b）
        base = layer['name'].lower()
        if base in used:
            used[base] += 1
            key = f'{base}_{used[base]}'
        else:
            used[base] = 1
            key = base

        entry = {
            'size': _vec2(layer['trans'].m_SizeDelta),
            'pivot': _vec2(layer['trans'].m_Pivot),
            'position': self._position_of(layer),
            'rawSize': _raw_size(mono),
        }
        files = [save_png(dest / f'{key}.png', atlas)]

        mesh_ptr = mono.get('mMesh') or {}
        has_mesh = bool(mesh_ptr.get('m_PathID'))
        decoded = None
        if has_mesh:
            try:
                decoded = self._decode_mesh(self.m._reader(mesh_ptr).read_typetree())
            except Exception as exc:
                warn(f'{key}: 网格解析失败 {type(exc).__name__}: {exc}')
        if decoded is None:
            if has_mesh:
                # 有 mMesh 却解不出来：落成 raw 会让客户端按整图铺满画布，
                # 出一张尺寸错误但不报错的图，比少一层更难查。宁可跳过并记进 skipped。
                return None, '有 mMesh 但解析失败'
            entry['raw'] = True
        else:
            write_mesh_obj(dest / f'{key}-mesh.obj', key, decoded[0], decoded[1])
            files.append(dest / f'{key}-mesh.obj')
        return (key, entry, files), None

    def export(self, dest: Path, face_images: list | None = None,
               face_size: list | None = None) -> dict:
        dest.mkdir(parents=True, exist_ok=True)
        used: dict = {}
        layout: dict = {}
        files: list = []
        skipped: list = []

        for layer in self.layers:
            item, err = self._one_layer(layer, used, dest)
            if item is None:
                skipped.append(f'{layer["name"]}({err})')
                continue
            key, entry, layer_files = item
            layout[key] = entry
            files.extend(layer_files)

        if self.base_name not in layout:
            raise ValueError('没有导出基底层')

        # view 只有基底层有，客户端用它算整张立绘的缩放
        layout[self.base_name]['view'] = _vec2(
            self.frame.m_SizeDelta) if self.frame is not None else [0.0, 0.0]

        if self.face_node is not None and self.face_mono is not None and face_images:
            layout[FACE_NODE] = {
                'size': _vec2(self.face_node.m_SizeDelta),
                'pivot': _vec2(self.face_node.m_Pivot),
                'position': _vec2(self.face_node.m_AnchoredPosition),
                'rawSize': face_size or _raw_size(self.face_mono),
                'list': list(range(len(face_images))),
            }
            files.extend(face_images)

        # 基底层排最前，其余按名字排序，保证多次运行字节稳定
        ordered = {self.base_name: layout.pop(self.base_name)}
        for key in sorted(layout):
            ordered[key] = layout[key]

        target = dest / f'{self.base_name}.json'
        target.write_text(json.dumps(ordered, ensure_ascii=False), 'u8')
        files.insert(0, target)
        return {'files': files, 'layers': len(ordered), 'skipped': skipped}


def export_faces(bundle: Path, dest: Path) -> list:
    """表情包 → paintingface/<name>/<i>.png，序号从 0 起，顺序与现有文件一致。"""
    import UnityPy
    import azl_assets as A
    env = UnityPy.load(str(bundle))
    textures = []
    for obj in env.objects:
        if obj.type.name != 'Texture2D':
            continue
        try:
            textures.append(obj.read())
        except Exception:
            continue
    textures.sort(key=lambda t: _natural(getattr(t, 'm_Name', '') or ''))
    written = []
    for index, data in enumerate(textures):
        try:
            img = A.to_bgra(data)
        except Exception as exc:
            warn(f'{bundle.name} 表情 {index} 解码失败: {exc}')
            continue
        written.append(save_png(dest / f'{index}.png', img))
    return written
