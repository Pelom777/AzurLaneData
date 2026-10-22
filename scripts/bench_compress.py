"""测图片编码效率：无损 WebP / 近无损 / 有损，以及对前端展示的影响。

用法：
    python scripts/bench_compress.py --dir .tmp/sample --limit 12

两个必须分开看的指标
--------------------
1. **不透明区域的偏差**（alpha > 8 的像素）—— 决定画面会不会变。
2. **透明区域的 RGB** —— 立绘 png 是**贴图集**，三角形靠 UV 采样，
   双线性过滤会在边缘吃到邻近像素；透明填充区 RGB 被改动会产生渗色（bleeding）。

所以「无损」不等于「字节一致」：libwebp 的无损模式默认会把全透明像素的 RGB
归一化来换压缩率（Pillow 对应 exact=False）。这一档必须单独量。
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

# (档位名, save 参数)
PRESETS: list[tuple[str, dict]] = [
    ("webp 无损 m4 exact", {"format": "WEBP", "lossless": True,
                            "quality": 100, "method": 4, "exact": True}),
    ("webp 无损 m6 exact", {"format": "WEBP", "lossless": True,
                            "quality": 100, "method": 6, "exact": True}),
    ("webp 无损 m4 默认", {"format": "WEBP", "lossless": True,
                            "quality": 100, "method": 4}),
    ("webp 近无损 80", {"format": "WEBP", "lossless": True,
                           "quality": 80, "method": 6}),
    ("webp 有损 q85", {"format": "WEBP", "lossless": False,
                       "quality": 85, "method": 6}),
    ("webp 有损 q75", {"format": "WEBP", "lossless": False,
                       "quality": 75, "method": 6}),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def bench_one(im: Image.Image) -> dict:
    """对一张图跑完所有档位。

    返回 {档位: (字节数, 编码ms, 解码ms, 不透明区最大差, 透明区最大差)}
    """
    src = im.convert("RGBA")
    arr = np.frombuffer(src.tobytes(), dtype=np.uint8).reshape(-1, 4)
    opaque = arr[:, 3] > 8
    clear = ~opaque
    out = {}
    for name, kw in PRESETS:
        kw = dict(kw)
        fmt = kw.pop("format")
        buf = io.BytesIO()
        t0 = time.perf_counter()
        src.save(buf, fmt, **kw)
        enc = (time.perf_counter() - t0) * 1000
        data = buf.getvalue()
        # 解码：取 3 次里最快的，减少噪声
        best = None
        for _ in range(3):
            t1 = time.perf_counter()
            with Image.open(io.BytesIO(data)) as back:
                back.load()
                got = back.convert("RGBA").tobytes()
            ms = (time.perf_counter() - t1) * 1000
            best = ms if best is None else min(best, ms)
        g = np.frombuffer(got, dtype=np.uint8).reshape(-1, 4)
        d = np.abs(g.astype(np.int16) - arr.astype(np.int16)).max(axis=1)
        d_op = int(d[opaque].max()) if opaque.any() else -1
        d_cl = int(d[clear].max()) if clear.any() else -1
        out[name] = (len(data), enc, best, d_op, d_cl)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".tmp/sample", help="待压样本目录")
    ap.add_argument("--limit", type=int, default=12, help="最多测几张")
    args = ap.parse_args()

    src = Path(args.dir)
    files = sorted(src.rglob("*.png"))
    if not files:
        log(f"目录里没有 png: {src}")
        return 1
    files = files[: args.limit]

    names = [n for n, _ in PRESETS]
    log(f"样本 {len(files)} 张")
    log("")
    log("%-22s %10s " % ("文件", "PNG") +
        " ".join("%20s" % n for n in names))
    log("-" * (34 + 21 * len(names)))

    tot = {n: [0, 0.0, 0.0] for n in names}
    tot_png = 0
    worst_op = {n: 0 for n in names}
    worst_cl = {n: 0 for n in names}

    for f in files:
        with Image.open(f) as im:
            im.load()
            res = bench_one(im)
        sz = f.stat().st_size
        tot_png += sz
        for n in names:
            b, e, d, dop, dcl = res[n]
            tot[n][0] += b
            tot[n][1] += e
            tot[n][2] += d
            worst_op[n] = max(worst_op[n], dop)
            worst_cl[n] = max(worst_cl[n], dcl)
        log("%-22s %10d " % (f.name, sz) +
            " ".join("%13.3fx(%2.0f%%)" % (res[n][0] / sz,
                                           res[n][0] / sz * 100)
                             for n in names))

    k = len(files)
    log("")
    log("汇总")
    log("%-22s %12s %8s %12s %12s %10s %10s" %
        ("档位", "样本字节", "压缩比", "编码ms/张", "解码ms/张", "不透明差", "透明差"))
    for n in names:
        b, e, d = tot[n]
        log("%-22s %12d %7.3fx %12.0f %12.1f %10d %10d" %
            (n, b, b / max(tot_png, 1), e / k, d / k, worst_op[n], worst_cl[n]))
    log("")
    log(f"样本 PNG 合计 {tot_png} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
