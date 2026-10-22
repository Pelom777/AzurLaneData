"""从游戏 CDN 直接提取资源（立绘 / 图标 / 表情），输出到 public/。

为什么不用 AzurLaneExtractor：
    维护者已确认新版 azex 只出 json/lua、不做图片，旧源码已删除。
    但旧源码里的立绘合成逻辑（extract_painting.py）是有效的，这里按 UnityPy 1.25
    的 API 重新实现了一遍，只保留图片链路，去掉了 luajit / crid_mod / ffmpeg 依赖。

数据来源（实测可用，2026-10-20，CN 9.7.25）：
    1. 登录服 protobuf 握手拿版本号与各类 hash 列表的地址
    2. <cdn>/android/hash/<raw>           → path,size,md5 清单
    3. <cdn>/android/resource/<md5>       → AssetBundle 本体
    4. 依赖解析：AssetBundle 的 m_Dependencies 形如 cab-<md5(资源路径)>，
       所以只要手上有主清单，就能把依赖名反查回路径，不必打开每个包。

用法：
    python scripts/azl_assets.py --items painting --limit 3 --dry-run
    python scripts/azl_assets.py --items painting squareicon paintingface
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (append_summary, die, is_censored,  # noqa: E402
                    is_n_variant, log, set_output, warn)

# --------------------------------------------------------------------------- 常量

SERVERS = {
    'CN': ('line1-login-bili-blhx.bilibiligame.net', 80, 'https://line3-patch-blhx.bilibiligame.net'),
    'EN': ('blhxusgate.yo-star.com', 80, 'https://blhxusstatic.akamaized.net'),
    'JP': ('blhxjploginapi.azurlane.jp', 80, 'https://blhxstatic.yo-star.com'),
    'KR': ('bl-kr-gate.xdg.com', 80, 'http://blcdn.imtxwy.com'),
    'TW': ('prod-all-login.azurlane.tw', 10080, 'http://blhxstatic.azurlane.tw'),
}

SEND_PAYLOAD = bytes.fromhex('000a002a300000082d120130')

# sc_10801 的字段表。未知字段一律跳过 —— 2026 的协议比 2023 多了字段 10，
# 旧代码在这里直接 KeyError。
FIELDS = {1: ('gateway_ip', 9), 2: ('gateway_port', 13), 3: ('url', 9),
          4: ('version', 9), 5: ('proxy_ip', 9), 6: ('proxy_port', 13),
          7: ('is_ts', 13), 8: ('timestamp', 13),
          9: ('monday_0oclock_timestamp', 13)}

# hash 清单后缀名 ↔ 版本号里的键名。2026 已扩展到 10 类，旧的 5 类照旧兼容。
HASH_KEYS = {
    'az': 'azhash', 'cv': 'cvhash', 'l2d': 'l2dhash', 'pic': 'pichash',
    'bgm': 'bgmhash', 'painting': 'paintinghash', 'manga': 'mangahash',
    'cipher': 'cipherhash', 'dorm': 'dormhash', 'map': 'maphash',
}

# 每个种类从哪份清单里找 bundle
CATEGORY_LIST = {
    'painting': 'painting',
    'paintingface': 'az',
    'squareicon': 'az',
    'shipicon': 'az',
    'shipyardicon': 'az',
    'equipicon': 'az',
    'qicon': 'az',
    'equips': 'az',
    'iconframe': 'az',
    'item': 'az',
    'bg': 'az',
    'skillicon': 'az',
    'herohrzicon': 'az',
}

# 站点目录名 ↔ 游戏清单里的资源前缀。
# 站点沿用早期命名，和游戏里的目录不总是一致，这里显式写死，别去猜。
#   public/shipicon/<name>.png    ← shipyardicon/<name>   （216x288，仓库旧快照是 192x256）
#   public/equipicon/<id>.png     ← equips/<id>
SOURCE_PREFIX = {
    'shipicon': 'shipyardicon',
    'equipicon': 'equips',
}

# 这些种类的产出名以 public/data 的引用集为准（见 load_used_names）。
# 仓库现有图标目录只收录站点真正用到的资源：shipicon 2157 个文件 100% 落在引用集内，
# 而游戏清单有 2671 个 —— 全量导出会多出几百个没人引用的文件。
USED_SCOPED = {'painting', 'squareicon', 'shipicon', 'equipicon'}

# 不再作为独立类别导出的条目。
# `paintingface` 单独导出会写成扁平文件 paintingface/<name>_<i>.png，
# 而前端读的是子目录布局 paintingface/<name>/<i>.png（由 painting 导出时
# 经由 export_faces 产出）。两套布局各自导出过一次，2026-10-22 清掉了
# 13,799 个扁平文件 / 1.4 GB。留着这个护栏，避免有人手滑又跑一遍。
OBSOLETE_ITEMS = {'paintingface'}

UA = {'User-Agent': 'Mozilla/5.0 (compatible; azl-assets/1.0)'}

# 当前资源包的 Unity 版本（实测 2022.3.62f3），用于少数没写版本号的包
FALLBACK_UNITY_VERSION = '2022.3.62f3'


# --------------------------------------------------------------------------- 版本

def _read_varint(buf: bytes, i: int):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return result, i


def _parse_proto(data: bytes) -> dict:
    out: dict = {}
    i, n = 0, len(data)
    while i < n:
        num, i = _read_varint(data, i)
        wtyp = num & 7
        num >>= 3
        if wtyp == 0:
            val, i = _read_varint(data, i)
        elif wtyp == 2:
            size, i = _read_varint(data, i)
            val = data[i:i + size]
            i += size
        elif wtyp == 1:
            val = data[i:i + 8]
            i += 8
        elif wtyp == 5:
            val = data[i:i + 4]
            i += 4
        else:
            raise ValueError(f'未知 wire type {wtyp}')
        if num not in FIELDS:
            continue
        name, typ = FIELDS[num]
        if typ == 9 and isinstance(val, bytes):
            val = val.decode('u8', 'replace')
        if num == 4:
            out.setdefault(name, []).append(val)
        else:
            out[name] = val
    return out


def get_version(server: str) -> dict:
    """登录服握手，返回 {'apk_url': ..., 'versions': {类型: {'version','raw'}}}"""
    host, port, _cdn = SERVERS[server]
    log(f'连接登录服 {host}:{port}')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(15)
        s.connect((host, port))
        s.send(SEND_PAYLOAD)
        raw = s.recv(20480)
    if not raw:
        die('登录服没有返回数据')
    info = _parse_proto(raw[7:])
    result = {'apk_url': info.get('url', ''), 'versions': {}}
    for item in info.get('version', []):
        if not item.startswith('$'):
            continue
        parts = item.strip('$').split('$')
        key = parts[0]
        if key.endswith('hash'):
            key = key[:-4]
        ver = '.'.join((['0', '0', '0'] + parts[1:-1])[-3:])
        result['versions'][key] = {'version': ver, 'raw': item}
    return result


# --------------------------------------------------------------------------- HTTP

def http_get(url: str, retries: int = 3, timeout: int = 60) -> bytes:
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f'下载失败 {url}: {last}')


def fetch_hash_list(cdn: str, raw: str) -> dict:
    """path -> {'size','md5'}"""
    data = http_get(f'{cdn}/android/hash/{raw}', timeout=120)
    res = {}
    for line in data.decode('u8', 'replace').splitlines():
        if not line:
            continue
        try:
            path, size, md5 = line.split(',')
        except ValueError:
            continue
        res[path] = {'size': int(size), 'md5': md5}
    return res


def download_bundle(cdn: str, md5: str, dest: Path, expect_size: int | None = None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + '.part')
    tmp.write_bytes(http_get(f'{cdn}/android/resource/{md5}', timeout=180))
    if expect_size and tmp.stat().st_size != expect_size:
        tmp.unlink()
        raise RuntimeError(f'大小不符 {dest.name}: 期望 {expect_size}')
    tmp.replace(dest)


# --------------------------------------------------------------------------- 图像工具

def _np():
    import numpy as np
    return np


def to_bgra(texture):
    import cv2 as cv
    import numpy as np
    img = np.array(texture.image.convert('RGBA'))
    return cv.cvtColor(img, cv.COLOR_RGBA2BGRA)


def decode_mesh(tt: dict):
    """Unity 2022 的 Mesh 用 m_VertexData + m_Channels 描述顶点流。

    只处理 format=0(float32) 的未压缩单流 —— 立绘正是这种。压缩流(m_CompressedMesh)
    交给 UnityPy 的 helper 也不划算，直接跳过该图层即可。
    """
    np = _np()
    vd = tt.get('m_VertexData') or {}
    count = vd.get('m_VertexCount') or 0
    raw = bytes(vd.get('m_DataSize') or b'')
    channels = vd.get('m_Channels') or []
    if not count or not raw or len(channels) < 5:
        return None
    stride = 0
    for ch in channels:
        if ch.get('dimension'):
            stride = max(stride, ch['offset'] + ch['dimension'] * 4)
    if stride <= 0 or len(raw) < count * stride:
        return None
    pos_ch, uv_ch = channels[0], channels[4]
    vertices = np.zeros((count, 2))
    uvs = np.zeros((count, 2))
    for i in range(count):
        base = i * stride
        vertices[i] = np.frombuffer(raw, '<f4', 3, base + pos_ch['offset'])[:2]
        uvs[i] = np.frombuffer(raw, '<f4', 2, base + uv_ch['offset'])
    dtype = '<u2' if tt.get('m_IndexFormat', 0) == 0 else '<u4'
    indices = np.frombuffer(bytes(tt['m_IndexBuffer']), dtype=dtype)
    total = sum(sb['indexCount'] // 3 for sb in tt.get('m_SubMeshes') or [])
    if total <= 0 or len(indices) < total * 3:
        return None
    return vertices, uvs, indices[: total * 3].reshape(total, 3)


def render_mesh_layer(atlas, mesh_tt):
    """按 Mesh 的三角形把图集还原成独立图层，返回 (图, 图层在原始 sprite 里的包围盒)"""
    import cv2 as cv
    np = _np()
    decoded = decode_mesh(mesh_tt)
    if decoded is None:
        return None, None
    verts, uvs, tris = decoded

    def box_of(points):
        return np.array([points.min(0), points.max(0)])

    vbox = box_of(verts)
    vertex = np.round(verts - vbox[0]).astype('int')
    w, h = np.round(vbox[1] - vbox[0]).astype('int')
    if w <= 0 or h <= 0:
        return None, None
    hi_img, wi_img = atlas.shape[:2]
    uv = np.floor(uvs * np.array([wi_img, hi_img]) + 0.5).astype('int')

    canvas = np.zeros((h, w, 4), dtype='uint8')
    flipped = cv.flip(atlas, 0)
    for tri in tris:
        sb = box_of(uv[tri])
        db = box_of(vertex[tri])
        if np.any(db[0] >= (w, h)):
            continue
        db[1] = np.minimum(db[1], (w, h))
        if np.any(db[1] <= db[0]):
            continue
        sb[1] = sb[0] + (db[1] - db[0])
        x0, y0 = int(db[0][0]), int(db[0][1])
        x1, y1 = int(db[1][0]), int(db[1][1])
        sx0, sy0 = int(sb[0][0]), int(sb[0][1])
        sx1, sy1 = int(sb[1][0]), int(sb[1][1])
        piece = flipped[sy0:sy1, sx0:sx1]
        if piece.size == 0:
            continue
        if piece.shape[:2] != (y1 - y0, x1 - x0):
            piece = cv.resize(piece, (x1 - x0, y1 - y0), interpolation=cv.INTER_LINEAR)
        canvas[y0:y1, x0:x1] = piece
    return cv.flip(canvas, 0), vbox


def paste(dst, src, x, y):
    """带 alpha 的叠加（对齐 extract_painting.paste_image 的语义）"""
    import numpy as np
    h1, w1 = dst.shape[:2]
    h2, w2 = src.shape[:2]
    x, y = int(max(0, x)), int(max(0, y))
    if x >= w1 or y >= h1:
        return dst
    w2 = min(w2, w1 - x)
    h2 = min(h2, h1 - y)
    if w2 <= 0 or h2 <= 0:
        return dst
    src = src[:h2, :w2]
    region = dst[y:y + h2, x:x + w2]
    a1 = src[:, :, 3:4].astype(np.float32) / 255
    a2 = region[:, :, 3:4].astype(np.float32) / 255
    a = a1 + a2 * (1 - a1)
    safe = np.where(a == 0, 1, a)
    rgb = (src[:, :, :3] * a1 + region[:, :, :3] * a2 * (1 - a1)) / safe
    dst[y:y + h2, x:x + w2, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    dst[y:y + h2, x:x + w2, 3:4] = np.clip(a * 255, 0, 255).astype(np.uint8)
    return dst


# --------------------------------------------------------------------------- 立绘

class PaintingMerger:
    """把一个立绘的布局包 + 依赖贴图合成一张完整立绘"""

    def __init__(self, layout: Path, workdir: Path, path_map: dict, cdn: str):
        import UnityPy
        self.workdir = workdir
        self.path_map = path_map      # md5(资源路径) -> 资源路径
        self.cdn = cdn
        self.env = UnityPy.load(str(layout))
        self.unitypy = UnityPy
        self._index()
        self.layout_file = next(iter(self.env.objects)).assets_file
        self._load_deps()

    def _index(self):
        from UnityPy.files.SerializedFile import SerializedFile
        self.cab = {}
        for f in self.env.files.values():
            if isinstance(f, SerializedFile):
                self.cab[f.name] = f
            for k, v in getattr(f, 'files', {}).items():
                if isinstance(v, SerializedFile):
                    self.cab[k] = v

    def _load_deps(self):
        from UnityPy.files.SerializedFile import SerializedFile
        for ext in self.layout_file.externals:
            cab = ext.name
            if not cab.lower().startswith('cab-'):
                continue
            rel = self.path_map.get(cab[4:].lower())
            if not rel:
                continue
            cand = self.workdir / rel
            if not cand.exists():
                continue
            self.env.load([str(cand)])
        self._index()

    def _reader(self, pptr):
        if not pptr or not pptr.get('m_PathID'):
            return None
        fid = pptr.get('m_FileID', 0)
        sf = self.layout_file if fid == 0 else self.cab.get(self.layout_file.externals[fid - 1].name)
        if sf is None:
            return None
        return sf.objects.get(pptr['m_PathID'])

    def _stack(self, trans, prev):
        np = _np()

        def pt(p):
            if hasattr(p, 'X'):
                return np.array([p.X, p.Y], dtype='float64')
            if hasattr(p, 'x'):
                return np.array([p.x, p.y], dtype='float64')
            return np.array(p, dtype='float64')

        size = pt(trans.m_SizeDelta)
        if prev is None:
            target = cur = size / 2
        else:
            target = cur = prev[0]
            base_scale = prev[1]
        anchor = pt(trans.m_AnchoredPosition)
        anchor[1] *= -1
        pivot = pt(trans.m_Pivot)
        pivot[1] = 1 - pivot[1]
        pivot_pos = size * pivot
        pivot_target = target + anchor
        pos = pivot_target - pivot_pos
        if prev is None:
            pos = np.array([0.0, 0.0])
            scale = np.array([1.0, 1.0])
            box = np.array([pos, pos + size])
        else:
            scale = base_scale * pt(trans.m_LocalScale)
            box = np.array([pos, pos + size])
            if np.any(scale != 1):
                box = (box - pivot_target) * scale + pivot_target
        return box, (target, scale)

    def collect(self):
        roots = []
        for o in self.env.objects:
            if o.type.name != 'RectTransform':
                continue
            d = o.read()
            if getattr(d.m_Father, 'path_id', 0) == 0:
                roots.append(d)
        if len(roots) != 1:
            raise ValueError(f'根节点数量异常: {len(roots)}')
        self.layers = []
        self.face_box = None
        self._walk(roots[0], None)
        return self.layers

    def _walk(self, trans, prev):
        np = _np()
        go = trans.m_GameObject.read()
        name = go.m_Name
        if name in ('frameContain', 'Touch'):
            return
        if trans.object_reader.type.name != 'RectTransform':
            return
        box, cur = self._stack(trans, prev)
        if name == 'face':
            if self.face_box is None:
                self.face_box = box
            return
        layer = None
        for comp in go.m_Component:
            cobj = comp.component if hasattr(comp, 'component') else comp.get_obj()
            if cobj.type.name != 'MonoBehaviour':
                continue
            try:
                tt = cobj.read_typetree()
            except Exception:
                continue
            if 'm_Sprite' in tt:
                layer = self._load_layer(tt, box)
                break
        if layer is not None:
            self.layers.append({'name': name, **layer})
        for child in trans.m_Children:
            self._walk(child.read(), cur)

    def _load_layer(self, mono, box):
        np = _np()
        reader = self._reader(mono.get('m_Sprite'))
        if reader is None:
            return None
        sprite = reader.read()
        tex_pptr = getattr(sprite.m_RD, 'texture', None)
        if tex_pptr is None:
            return None
        atlas = to_bgra(tex_pptr.read())
        raw = mono.get('mRawSpriteSize') or {}
        size = np.array([float(raw.get('x') or 0), float(raw.get('y') or 0)])
        mesh_reader = self._reader(mono.get('mMesh'))
        if mesh_reader is None:
            return {'img': atlas, 'box': box}
        img, vbox = render_mesh_layer(atlas, mesh_reader.read_typetree())
        if img is None:
            return None
        # 图层在图集里的位置 → UI 坐标
        if size[0] <= 0 or size[1] <= 0:
            return None
        h = size[1]
        vb = np.array([vbox[0], vbox[1]], dtype='float64')
        vb[0, 1], vb[1, 1] = h - vbox[1, 1], h - vbox[0, 1]
        scale = (box[1] - box[0]) / size
        realbox = vb * scale + box[0]
        if np.max(np.abs(realbox)) > 20000:
            return None
        return {'img': img, 'box': realbox}

    def compose(self):
        np = _np()
        import cv2 as cv
        layers = [l for l in self.layers if l['img'] is not None and l['img'].size > 0]
        if not layers:
            return None
        boxes = np.array([l['box'] for l in layers], dtype='float64')
        offset = boxes[:, 0].min(0)
        box_sizes = boxes[:, 1] - boxes[:, 0]
        img_sizes = np.array([[l['img'].shape[1], l['img'].shape[0]] for l in layers], dtype='float64')
        with np.errstate(divide='ignore', invalid='ignore'):
            scales = np.where(img_sizes > 0, box_sizes / img_sizes, np.inf)
        rescale = np.min(scales)
        if not np.isfinite(rescale) or rescale <= 0:
            rescale = 1.0
        boxes = np.round((boxes - offset) / rescale).astype('int')
        w, h = boxes[:, 1].max(0)
        if w <= 0 or h <= 0:
            return None
        canvas = np.zeros((int(h), int(w), 4), dtype='uint8')
        for layer, bx in zip(layers, boxes):
            target = (int(bx[1][0] - bx[0][0]), int(bx[1][1] - bx[0][1]))
            img = layer['img']
            if target[0] <= 0 or target[1] <= 0:
                continue
            if (img.shape[1], img.shape[0]) != target:
                img = cv.resize(img, target, interpolation=cv.INTER_LINEAR)
            canvas = paste(canvas, img, bx[0][0], bx[0][1])
        # 低分辨率图层说明解析到了占位资源，不值得写出去
        if min(canvas.shape[:2]) < 200:
            return None
        return canvas


# --------------------------------------------------------------------------- 导出


def load_used_names(data_dir: Path) -> set[str]:
    """从 public/data 里收集站点真正引用的资源名。

    站点所有图片都用 `${cdn}/<目录>/${painting}.png` 拼路径，
    所以 data 里每个 `painting` 字段（含嵌套）就是一份必需的资源名清单。
    实测：这份清单覆盖仓库 shipicon 全部 2157 个文件（0 漏），
    仅比仓库多 7 个 NPC 图标（仓库额外收录的，我们只增不删，不受影响）。
    """
    used: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if key == 'painting' and isinstance(val, str):
                    used.add(val.lower())
                else:
                    walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)

    if not data_dir.is_dir():
        return used
    for path in sorted(data_dir.glob('*.json')):
        raw = path.read_text('utf-8').strip()
        if not raw:            # 仓库里有几个 0 字节的占位文件
            continue
        try:
            walk(json.loads(raw))
        except Exception as exc:
            warn(f'{path.name} 解析失败，跳过引用集统计: {exc}')
    return used


def export_texture_bundle(bundle: Path, dest_dir: Path,
                          base_name: str) -> list[Path] | None:
    """贴图类资源 → `<dir>/<资源名>.png`。

    站点是按 `${cdn}/<目录>/${painting}.png` 直接拼路径取图的，
    所以文件名必须是资源名的全小写形式；只有包里含多张贴图时才接贴图名。

    返回 `None` 表示**包里根本没有 Texture2D**（纯 prefab 包，如
    `iconframe/10003`）—— 调用方应当跳过并记入 state，而不是算失败：
    否则每轮都会重试它、每轮都报「失败 1」。
    返回空列表才表示「有贴图但一张也没导出来」，那才是真失败。
    """
    import UnityPy
    from painting_site import save_png
    env = UnityPy.load(str(bundle))
    textures = []
    for obj in env.objects:
        if obj.type.name != 'Texture2D':
            continue
        try:
            textures.append(obj.read())
        except Exception as exc:
            warn(f'{bundle.name} 贴图读取失败: {exc}')
    if not textures:
        return None
    written: list[Path] = []
    for data in textures:
        tex_name = (getattr(data, 'm_Name', '') or '').strip()
        name = base_name if len(textures) == 1 else (
            f'{base_name}_{tex_name}' if tex_name else base_name)
        try:
            written.append(save_png(dest_dir / f'{name}.png', to_bgra(data)))
        except Exception as exc:
            warn(f'{bundle.name} 贴图导出失败: {exc}')
    return written


def export_painting(bundle: Path, cache: Path, path_map: dict, cdn: str,
                    public: Path, name: str, face_bundle: Path | None = None,
                    out_dir: str | None = None) -> dict:
    """立绘 → public/painting/<name>/{json, png, obj}（格式与站点仓库逐项对齐）。

    `<name>_ex` 是改造版（同人改造/换装），游戏里是**独立资源包**，包内基底层
    叫 `<name>_ex`。产物写进本体目录（`out_dir` 给本体名），json 名仍是
    `<name>_ex.json`；但 json 里的基底层 key 必须写成本体名 —— 前端取基底
    尺寸与枢轴用的是 `json[name]`，name 是本体重绘名。

    为什么敢写进本体目录：`_ex` 包的 externals 直接指向本体的 `<name>_tex`
    （8 个变体全部如此，见 docs/CDN.md §4.9），它没有自己的基底贴图，
    所以基底层写出来的字节与本体的完全一致，覆盖是空操作。下面留了一道
    兜底：万一下次真不一致，还原本体文件并告警，而不是静默改掉本体。
    """
    import painting_site as P
    merger = PaintingMerger(bundle, cache, path_map, cdn)
    base_key = name[:-3] if name.endswith('_ex') else name
    exporter = P.PaintingExporter(merger, name, base_key)
    exporter.collect()

    faces: list[Path] = []
    face_size = None
    if face_bundle is not None:
        faces = P.export_faces(face_bundle, public / 'paintingface' / name)
        if faces:
            import cv2 as cv
            first = cv.imread(str(faces[0]), cv.IMREAD_UNCHANGED)
            if first is not None:
                face_size = [float(first.shape[1]), float(first.shape[0])]

    dest = public / 'painting' / (out_dir or name)
    guard = None
    if out_dir and out_dir != name:
        shared = dest / f'{base_key}.png'
        if shared.exists():
            guard = (shared, shared.read_bytes())

    info = exporter.export(dest, faces, face_size)

    if guard is not None:
        shared, old_bytes = guard
        if shared.read_bytes() != old_bytes:
            shared.write_bytes(old_bytes)
            warn(f'{name}: 基底层与本体重名但内容不同，已还原本体文件 —— '
                 f'这个 _ex 得改用独立目录')
            info['skipped'].append(f'{base_key}(基底贴图与本体不一致)')
            info['files'] = [f for f in info['files'] if f != shared]

    info['files'] = list(info['files']) + faces
    return info


# --------------------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--server', default='CN')
    ap.add_argument('--public', default='public')
    ap.add_argument('--cache', default='.azl/bundles',
                    help='bundle 缓存目录，放在 runner 缓存里可显著加速')
    ap.add_argument('--state', default='.azl/assets-state.json')
    ap.add_argument('--items', nargs='*', default=None,
                    help='要处理的资源类别，默认 painting + 两种图标')
    ap.add_argument('--limit', type=int, default=0, help='每类最多处理几个（调试用）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force', action='store_true', help='忽略增量状态，全部重跑')
    ap.add_argument('--report-files', default='',
                    help='把本次写出的文件写成 NUL 分隔清单，供 commit.py --paths-from 使用')
    ap.add_argument('--no-face', action='store_true', help='不导出表情')
    ap.add_argument('--seed', action='store_true',
                    help='状态播种：目标文件已存在就视为已同步，并把当前 md5 记进 state。'
                         '状态文件丢失/换了仓库（CI 缓存不跨仓库）时用，'
                         '否则会把已入库的几十 GB 资源重新下一遍')
    ap.add_argument('--scope', choices=('used', 'all'), default='used',
                    help='used=只导 public/data 引用到的资源（与仓库收录规则一致）；'
                         'all=清单里的全导')
    ap.add_argument('--shard', default='', metavar='I/N',
                    help='分片：只处理 crc32(名字) %% N == I 的那一份（I 从 0 起）。'
                         '给磁盘紧张的环境分批全量重建用；按名字取模而不是按顺序切，'
                         '所以分片结果与清单顺序无关，重跑时同一批还是同一批')
    ap.add_argument('--plan', action='store_true',
                    help='只盘点不生成：走与正式运行**完全相同**的枚举与过滤，'
                         '数出 state 里已过期/缺失的资源量，写到 $GITHUB_OUTPUT。'
                         '用来决定该分几片，而不是让人提前猜')
    args = ap.parse_args()

    shard_i, shard_n = 0, 1
    if args.shard:
        try:
            _i, _n = args.shard.split('/')
            shard_i, shard_n = int(_i), int(_n)
        except ValueError:
            die(f'--shard 需要 I/N 形式，收到 {args.shard!r}')
        if not 0 <= shard_i < shard_n:
            die(f'--shard 越界: {args.shard}（要求 0 <= I < N）')

    # 盘点模式不碰解码，也就不需要 UnityPy / cv2 —— 这让 CI 的 prep job
    # 可以完全不装依赖（省掉约 40 秒的 pip install）。
    if not args.plan:
        import UnityPy
        import cv2  # noqa: F401  提前失败，避免下完几十 GB 才发现缺依赖
        UnityPy.config.FALLBACK_UNITY_VERSION = FALLBACK_UNITY_VERSION

    server = args.server.upper()
    if server not in SERVERS:
        die(f'未知服务器 {server}')
    _host, _port, cdn = SERVERS[server]
    public = Path(args.public)
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)

    version = get_version(server)
    apk = version['apk_url']
    log(f'版本 {server}: ' + ', '.join(f'{k}={v["version"]}' for k, v in version['versions'].items()))

    lists: dict[str, dict] = {}

    def get_list(kind: str) -> dict:
        if kind not in lists:
            info = version['versions'].get(kind)
            if not info:
                warn(f'服务器没有 {kind} 的版本信息，跳过')
                return {}
            log(f'拉取 {kind} 清单 ...')
            lists[kind] = fetch_hash_list(cdn, info['raw'])
            log(f'  {len(lists[kind])} 条')
        return lists[kind]

    state = {}
    if Path(args.state).exists() and not args.force:
        try:
            state = json.loads(Path(args.state).read_text('u8'))
        except json.JSONDecodeError:
            warn('状态文件损坏，按全量处理')
    state.setdefault('version', {})[server] = {
        k: v['version'] for k, v in version['versions'].items()}
    prev = state.setdefault('items', {}).setdefault(server, {})

    report = {'new': 0, 'changed': 0, 'skipped': 0, 'seeded': 0, 'failed': 0,
              'files': 0, 'unused': 0, 'other_shard': 0, 'no_texture': 0,
              'censored': 0, 'n_variant': 0,
              'pending': 0, 'pending_bytes': 0}
    written: list[Path] = []
    items = args.items or ['painting', 'squareicon', 'shipicon']
    if shard_n > 1:
        log(f'分片 {shard_i}/{shard_n}：只处理 crc32(名字) % {shard_n} == {shard_i} 的部分')

    used = set()
    if args.scope == 'used':
        used = load_used_names(public / 'data')
        if used:
            log(f'引用集: public/data 里 {len(used)} 个 painting 名')
        else:
            warn('public/data 不可用，回退为全量导出（--scope all）')

    for item in items:
        if item in OBSOLETE_ITEMS:
            warn(f'{item} 不再作为独立类别导出（表情由 painting 顺带产出），已跳过')
            continue
        mapping = get_list(CATEGORY_LIST.get(item, 'az'))
        if not mapping:
            continue
        prefix = SOURCE_PREFIX.get(item, item)
        targets = {p: v for p, v in mapping.items() if p.startswith(prefix + '/')}
        # 判断 `_ex` 变体是否成立：<name>_ex 的 `<name>` 必须本身是个立绘。
        # 这样 `yan_n_ex` 会归到 `yan_n`（清单里确实有 yan_n），而不是 `yan_n` 之外的野路径。
        base_names = ({p.split('/', 1)[1].lower() for p in mapping
                       if p.startswith('painting/')
                       and not p.endswith(('_tex', '-mesh'))}
                      if item == 'painting' else set())
        if not targets:
            warn(f'清单里没有 {prefix}/ 开头的资源')
            continue
        path_map = {hashlib.md5(p.encode()).hexdigest(): p for p in mapping}
        face_map = {}
        if item == 'painting' and not args.no_face:
            face_map = get_list(CATEGORY_LIST['paintingface'])
        scoped = bool(used) and item in USED_SCOPED
        log(f'== {item}' + (f'（源 {prefix}）' if prefix != item else '')
            + f': {len(targets)} 个资源' + ('，按引用集过滤' if scoped else ''))

        done = 0
        for path, info in sorted(targets.items()):
            # 盘点模式不吃 limit：要的是全量口径，不是"这一轮打算做几个"
            if not args.plan and args.limit and done >= args.limit:
                break
            # 立绘的贴图包和网格包是依赖，不单独产出
            if item == 'painting' and path.endswith(('_tex', '-mesh')):
                continue
            name = path.split('/', 1)[1].lower()
            # 分片：crc32 而不是 hash()，后者按进程随机化，换一次进程分片就变了
            if shard_n > 1 and zlib.crc32(name.encode()) % shard_n != shard_i:
                report['other_shard'] += 1
                continue
            # 和谐版资源一律不要：名字里带 `_hx` 的立绘、图标、表情全跳过。
            # 对照旧前端仓库 public/（2,159 个 painting 目录、0 个含 _hx）确认过，
            # 站点从不收录这类资源，属于硬规则。放在引用集判断之前，
            # 这样 --scope all 也不会把它们导出来。
            if is_censored(name):
                report['censored'] += 1
                if scoped and name in used:
                    warn(f'{path} 是和谐版但被 data 引用了 —— 跳过会缺图，需要人工确认')
                continue
            # `_n` 变体（`2b_n` / `yan_n` / `2b_n_hx` …）是独立包，站点从不收录：
            # 旧仓库 2,159 个目录含 `_n` 的 0 个，当前引用集里也是 0 个。
            # 放在引用集判断之前，所以 --scope all 也不会去下这些包。
            # 只管立绘目录，不管包内图层（nengdai_idol 里那个 _n 图层要留）。
            if item == 'painting' and is_n_variant(name):
                report['n_variant'] += 1
                if scoped and name in used:
                    warn(f'{path} 是 _n 变体但被 data 引用了 —— 跳过会缺图，需要人工确认')
                continue
            # 改造版（`<name>_ex`）是独立资源包，本身不进引用集，跟随本体：
            # 本体被 data 引用就导，否则跳过。
            is_ex = name.endswith('_ex') and name[:-3] in base_names
            scope_name = name[:-3] if is_ex else name
            if scoped and scope_name not in used:
                report['unused'] += 1
                continue

            face_path = f'paintingface/{name}'
            face_info = face_map.get(face_path) if face_map else None
            key = f'{item}:{path}:{face_info["md5"] if face_info else "-"}'

            if item == 'painting':
                marker_file = (public / 'painting' / scope_name / f'{name}.json'
                               if is_ex else
                               public / 'painting' / name / f'{name}.json')
            else:
                marker_file = public / item / f'{name}.png'

            # 盘点：判据只看 state 里的 md5 是否与当前清单一致。
            # 刻意不看 marker_file.exists() —— CI 是干净 checkout，产物本来就不在
            # 磁盘上，带了这一半判据会把"state 已是最新"的资源全部误算成待处理。
            # 这样得到的是**下界**：远端对象若丢失也会多出一些真实工作量，
            # 但那属于自愈范畴，不该影响分片决策。
            if args.plan:
                if prev.get(key) != info['md5']:
                    report['pending'] += 1
                    report['pending_bytes'] += info['size']
                else:
                    report['skipped'] += 1
                continue

            if prev.get(key) == info['md5'] and marker_file.exists() and not args.force:
                report['skipped'] += 1
                continue
            # 播种：产物已经在磁盘上，但 state 里没有这条记录 —— 说明这份资源是别的
            # 途径入库的（最典型是换了仓库，`actions/cache` 不跟着走）。
            # 不做这一步，首次运行会把已存在的几十 GB 全部重下一遍，
            # 而托管 runner 只有 14 GB 磁盘，必然中途写满。
            #
            # `key not in prev` 这个条件是关键：它把播种限制在「从没见过这个资源」上。
            # 不加的话，上游更新（key 已在 prev、md5 已变）也会落到这里被跳过，
            # 结果就是资源永远停在搬家那天的版本。
            if args.seed and key not in prev and marker_file.exists() and not args.force:
                prev[key] = info['md5']
                report['skipped'] += 1
                report['seeded'] += 1
                continue

            cached = cache / path
            if args.dry_run:
                log(f'  [dry-run] {path} {info["size"]}B -> {public / item / name}')
                done += 1
                continue

            try:
                if not cached.exists() or args.force:
                    download_bundle(cdn, info['md5'], cached, info['size'])
                    if item == 'painting':
                        for dep in self_deps(cached, path_map):
                            dep_info = mapping.get(dep)
                            if not dep_info:
                                continue
                            dep_file = cache / dep
                            if not dep_file.exists():
                                download_bundle(cdn, dep_info['md5'], dep_file,
                                                dep_info['size'])
                if item == 'painting':
                    face_bundle = None
                    if face_info:
                        face_bundle = cache / face_path
                        if not face_bundle.exists() or args.force:
                            download_bundle(cdn, face_info['md5'], face_bundle,
                                            face_info['size'])
                    result = export_painting(cached, cache, path_map, cdn,
                                             public, name, face_bundle,
                                             out_dir=scope_name if is_ex else None)
                    files = result['files']
                    if result['skipped']:
                        log(f'  {name}: 跳过图层 {result["skipped"]}')
                else:
                    files = export_texture_bundle(cached, public / item, name)
            except Exception as exc:
                warn(f'{path} 处理失败: {type(exc).__name__}: {exc}')
                report['failed'] += 1
                continue
            if files is None:
                # 包里没有 Texture2D（纯 prefab）。不是失败，但必须记进 state，
                # 否则每轮都会重试、每轮都报一次失败。
                log(f'  {path}: 无贴图，跳过')
                report['no_texture'] += 1
                prev[key] = info['md5']
                continue
            if not files:
                # 一定要留痕：这个分支以前只把计数加一，结果 2026-10-22 那轮
                # 报「失败 1」却完全查不到是哪个资源、为什么。
                warn(f'{path} 处理失败: 有贴图但一张也没导出来'
                     f'（bundle {info["size"]} B）')
                report['failed'] += 1
                continue
            written.extend(files)
            report['changed' if key in prev else 'new'] += 1
            prev[key] = info['md5']
            report['files'] += len(files)
            done += 1
            if done % 20 == 0:
                log(f'  已处理 {done} 个')

    # 盘点：只报数，不写 state、不写清单。调用方拿这个数决定分几片。
    if args.plan:
        log(f'盘点结果：待处理 {report["pending"]} 个 / '
            f'{report["pending_bytes"] / 1048576:.1f} MB（主体包，不含依赖），'
            f'已是最新 {report["skipped"]} 个，未被引用 {report["unused"]} 个')
        set_output(pending=report['pending'],
                   pending_bytes=report['pending_bytes'],
                   up_to_date=report['skipped'])
        return 0

    if not args.dry_run:
        Path(args.state).parent.mkdir(parents=True, exist_ok=True)
        Path(args.state).write_text(
            json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), 'u8')

    # 只列本次真正写出的文件。commit.py 按清单 add，不碰工作区里没落盘的历史资产。
    if args.report_files:
        target = Path(args.report_files)
        target.parent.mkdir(parents=True, exist_ok=True)
        root = Path.cwd().resolve()
        entries = []
        for p in written:
            try:
                entries.append(p.resolve().relative_to(root).as_posix())
            except ValueError:
                entries.append(p.as_posix())
        target.write_bytes(b''.join(i.encode('u8') + b'\0' for i in entries))
        log(f'写出文件清单 {target}: {len(entries)} 个')

    log(f'完成: 新增 {report["new"]}, 变更 {report["changed"]}, '
        f'跳过 {report["skipped"]}, 失败 {report["failed"]}, '
        f'无贴图 {report["no_texture"]}, 和谐版 {report["censored"]}, '
        f'_n 变体 {report["n_variant"]}, 未被引用 {report["unused"]}'
        + (f', 他片 {report["other_shard"]}' if shard_n > 1 else ''))
    if report['seeded']:
        log(f'  其中 {report["seeded"]} 个是状态播种（产物已在磁盘，只补 state 记录）')
    append_summary(
        '### 资源提取\n'
        f'- 版本：`{server}` `{version["versions"].get("az", {}).get("version")}`'
        f'（painting `{version["versions"].get("painting", {}).get("version")}`）\n'
        f'- 分片：`{args.shard or "1/1"}`\n'
        f'- 新增 `{report["new"]}` / 变更 `{report["changed"]}` / '
        f'跳过 `{report["skipped"]}` / 失败 `{report["failed"]}`\n'
        f'- 未被 data 引用而跳过 `{report["unused"]}`\n'
        f'- 写入文件 {report["files"]} 个\n'
        f'- 安装包：{apk}\n'
    )
    set_output(
        changed='true' if (report['new'] or report['changed']) else 'false',
        new=report['new'], changed_count=report['changed'], failed=report['failed'],
        files=report['files'], written=len(written),
    )
    return 0


def self_deps(bundle: Path, path_map: dict) -> list[str]:
    """读布局包声明的依赖，反查回资源路径"""
    import UnityPy
    try:
        env = UnityPy.load(str(bundle))
        sf = next(iter(env.objects)).assets_file
        res = []
        for ext in sf.externals:
            cab = ext.name
            if cab.lower().startswith('cab-'):
                rel = path_map.get(cab[4:].lower())
                if rel:
                    res.append(rel)
        return res
    except Exception as exc:
        warn(f'{bundle.name} 依赖解析失败: {exc}')
        return []


if __name__ == '__main__':
    raise SystemExit(main())
