"""从游戏 CDN 自行解包 Lua 配置表 —— 替代 fetch_lua_tables.py。

原方案从 `AzurLaneTools/AzurLaneLuaScripts` 仓库取 `sharecfgdata/*.lua`，
好处是省事，坏处是数据新鲜度被别人决定、且链路里有第三方仓库。
本脚本改为一手解包，只依赖游戏自己的 CDN。

两条链路（细节见 docs/HANDOFF.md）：

A. `sharecfgdata/<表名>`   —— 32 个独立 bundle，自定义容器
       文件 = 连续的 [uleb128 长度][LuaJIT 字节码] 片段
       每段补上 `\\x1bLJ\\x02\\x02` 头就是一个合法的 LuaJIT BC dump
       → bcdec 还原标准字节码 → ljd 反编译 → lua 源码 → luatable 解析成 json

B. `scripts32`             —— 39 MB，整体加密的 UnityFS
       → uabdec 整包解密 → UnityPy 取 TextAsset
       → 只要 `assets/luabuilds/android/normal/sharecfg/*.lua.bytes`
       → bcdec → ljd → lua 源码

产出与 fetch_lua_tables.py **完全一致**：`--out` 下每个表一个 `<表名>.json`，
内容是 {"id": {...}}。下游 build_data.py 无需任何改动。

用法：
    python scripts/unpack_lua.py --server CN --out raw --work cache
    python scripts/unpack_lua.py --server CN --out raw --only ship_data_statistics

⚠️ 验证状态（截至 2026-10-21，CN 9.7.25）
    已实测：登录服握手 / hash 清单 / bundle 下载 / sharecfgdata 的 uleb128 分段
            （219 段正好铺满文件）/ 图片 bundle 是明文 UnityFS
    待实测：bcdec 与 ljd 在本机与 runner 上的实际行为（见 HANDOFF 的「未验证项」）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import append_summary, die, log, set_output, warn  # noqa: E402
from luatable import LuaParseError, parse_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

# 与 azl_assets.py 共用同一份 CDN / 握手逻辑，避免两处漂移
from azl_assets import SERVERS, SEND_PAYLOAD, _parse_proto, download_bundle, fetch_hash_list  # noqa: E402

LJ_HEADER = b"\x1bLJ\x02\x02"

# sharecfgdata 之外，从 scripts32 里取的 sharecfg 表。
# 这四张都不在 sharecfgdata 清单里，只存在于 scripts32 的 sharecfg/ 目录。
SHARECFG_WANTED = (
    "ship_data_strengthen",      # 属性强化上限
    "attribute_info_by_type",    # 属性索引语义
    "ship_data_trans",           # 改造组 → 节点列表
    "transform_data_template",   # 改造节点 effect / gear_score
)
TEXTASSET_PREFIX = "assets/luabuilds/android/normal/"


# --------------------------------------------------------------------- 工具链

def _tool(name: str) -> Path:
    for cand in (TOOLS / name, TOOLS / (name + ".exe")):
        if cand.exists():
            return cand
    die(f"缺少 {name}，请在 tools/ 下放好（来源见 tools/README.md）")


def bcdec(data: bytes, timeout: int = 120) -> bytes:
    """bcdec：魔改 LuaJIT 字节码 → 标准 LuaJIT 字节码。

    走 stdin/stdout，不落盘 —— sharecfgdata 有几百个片段，落盘会让 IO 变瓶颈。
    输出前后各有一个字节的包装（与 azex 的 trim_output 一致）。
    """
    proc = subprocess.run(
        [str(_tool("bcdec"))], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"bcdec 失败({proc.returncode}): "
                           f"{proc.stderr.decode('u8', 'replace')[:200]}")
    out = proc.stdout
    # 首 5 字节 + 尾 1 字节是容器包装，与 azex bcdec_pipe(trim_output=True) 一致
    return out[5:-1] if len(out) > 6 else out


def decompile(bytecode: bytes) -> str:
    """ljd：标准 LuaJIT 字节码 → lua 源码。"""
    try:
        import ljd.tools  # noqa: PLC0415
    except ImportError:
        die("缺少 ljd，请 pip install "
            "https://github.com/AzurLaneTools/ljd/releases/download/wheel/"
            "ljd-0.1.7-py3-none-any.whl")
        raise
    return ljd.tools.process_bytes(bytecode)


def uabdec(src: Path, dst: Path) -> None:
    """uabdec：加密的 UnityFS → 明文 UnityFS。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([str(_tool("uabdec")), str(src), str(dst)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"uabdec 失败({proc.returncode}): "
                           f"{proc.stderr.decode('u8', 'replace')[:200]}")


# ------------------------------------------------------------------ sharecfgdata

def read_uleb128(data: bytes, idx: int = 0) -> tuple[int, int]:
    result = shift = 0
    for _ in range(5):
        b = data[idx]
        result += (b & 0x7F) << shift
        idx += 1
        shift += 7
        if b < 0x80:
            return idx, result
    raise ValueError(f"uleb128 过长 @ {idx}")


def iter_segments(data: bytes):
    """按容器格式切分，产出可喂给 bcdec 的完整 BC dump。"""
    idx = 0
    size = len(data)
    while idx < size:
        start = idx
        new_idx, num = read_uleb128(data, idx)
        idx = new_idx + num
        if idx > size:
            # 尾部残段：不报错，交给上层判空
            break
        yield LJ_HEADER + data[start:idx] + b"\x00"


def unpack_sharecfgdata(bundle: Path, cache: Path) -> str:
    """一个 sharecfgdata bundle → lua 源码。"""
    key = hashlib.md5(bundle.read_bytes()).hexdigest()
    hit = cache / f"{key}.lua"
    if hit.exists():
        return hit.read_text("utf-8")

    raw = bundle.read_bytes()
    parts = [bcdec(seg) for seg in iter_segments(raw)]
    if not parts:
        raise ValueError(f"{bundle.name}: 切分出 0 个片段")
    text = decompile(b"".join(parts))
    cache.mkdir(parents=True, exist_ok=True)
    hit.write_text(text, encoding="utf-8")
    return text


# -------------------------------------------------------------------- scripts32

def unpack_scripts32(bundle: Path, work: Path, cache: Path) -> dict[str, str]:
    """scripts32 → {表名: lua 源码}，只取 SHARECFG_WANTED 里的表。"""
    dec = work / "scripts32.dec"
    if not dec.exists():
        log("uabdec 解密 scripts32（约 40 MB）")
        uabdec(bundle, dec)

    import UnityPy  # noqa: PLC0415

    env = UnityPy.load(str(dec))
    found: dict[str, str] = {}
    for reader in env.objects:
        if reader.type.name != "TextAsset":
            continue
        path = str(reader.container).replace("\\", "/")
        if not path.startswith(TEXTASSET_PREFIX):
            continue
        rel = path[len(TEXTASSET_PREFIX):]
        if not (rel.startswith("sharecfg/") and rel.endswith(".lua.bytes")):
            continue
        name = rel[len("sharecfg/"):-len(".lua.bytes")]
        if name not in SHARECFG_WANTED:
            continue
        raw = bytes(reader.read().script)
        key = hashlib.md5(raw).hexdigest()
        hit = cache / f"sc-{name}-{key}.lua"
        if hit.exists():
            found[name] = hit.read_text("utf-8")
            continue
        try:
            text = decompile(bcdec(raw))
        except Exception as exc:  # noqa: BLE001
            warn(f"sharecfg/{name} 反编译失败：{exc}")
            continue
        cache.mkdir(parents=True, exist_ok=True)
        hit.write_text(text, encoding="utf-8")
        found[name] = text
    return found


# ------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="CN", choices=list(SERVERS))
    ap.add_argument("--out", required=True, help="解析后的 json 输出目录")
    ap.add_argument("--work", required=True, help="临时目录（放解密后的 scripts32）")
    ap.add_argument("--cache", default="", help="反编译缓存目录，默认 work/cache")
    ap.add_argument("--only", default="", help="只解析这些表（逗号分隔）")
    ap.add_argument("--keep-work", action="store_true", help="保留 work 下的中间文件")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    work = Path(args.work).resolve()
    cache = Path(args.cache).resolve() if args.cache else work / "cache"
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}

    host, port, cdn = SERVERS[args.server]
    log(f"连接登录服 {host}:{port}")
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(20)
        s.connect((host, port))
        s.send(SEND_PAYLOAD)
        raw = s.recv(20480)
    if not raw:
        die("登录服没有返回数据")
    info = _parse_proto(raw[7:])
    ver_items = [v for v in info.get("version", []) if v.startswith("$")]
    az_raw = next((v for v in ver_items if v.startswith("$azhash")), None)
    if not az_raw:
        die("版本信息里没有 azhash")

    listing = fetch_hash_list(cdn, az_raw)
    log(f"az 清单 {len(listing)} 条")

    tables: dict[str, dict] = {}
    stats = {"bundles": 0, "failed": []}
    started = time.time()

    # ---- A. sharecfgdata
    sc_items = sorted(
        (p, i) for p, i in listing.items() if p.startswith("sharecfgdata/"))
    if wanted:
        sc_items = [(p, i) for p, i in sc_items if p.split("/", 1)[1] in wanted]
    log(f"sharecfgdata 待解 {len(sc_items)} 个")

    for path, meta in sc_items:
        name = path.split("/", 1)[1]
        bundle = work / f"scd-{name}"
        try:
            if not bundle.exists() or bundle.stat().st_size != meta["size"]:
                download_bundle(cdn, meta["md5"], bundle, meta["size"])
            text = unpack_sharecfgdata(bundle, cache)
            parsed, cut = parse_text(text)
            for tname, data in parsed.items():
                tables[tname] = data
            stats["bundles"] += 1
            log(f"  {name}: {len(parsed)} 表 / {sum(len(v) for v in parsed.values())} 条"
                + ("（尾部截断）" if cut else ""))
        except Exception as exc:  # noqa: BLE001
            warn(f"{name} 处理失败：{exc}")
            stats["failed"].append(name)

    # ---- B. scripts32 → sharecfg
    sc32 = listing.get("scripts32")
    if sc32 and (not wanted or wanted & set(SHARECFG_WANTED)):
        try:
            bundle = work / "scripts32"
            if not bundle.exists() or bundle.stat().st_size != sc32["size"]:
                log(f"下载 scripts32（{sc32['size'] / 1e6:.1f} MB）")
                download_bundle(cdn, sc32["md5"], bundle, sc32["size"])
            texts = unpack_scripts32(bundle, work, cache)
            for name, text in texts.items():
                parsed, _ = parse_text(text)
                for tname, data in parsed.items():
                    tables[tname] = data
                log(f"  sharecfg/{name}: {sum(len(v) for v in parsed.values())} 条")
            miss = set(SHARECFG_WANTED) - set(texts)
            if miss:
                warn(f"scripts32 里没找到：{', '.join(sorted(miss))}")
        except Exception as exc:  # noqa: BLE001
            warn(f"scripts32 处理失败：{exc}")
            stats["failed"].append("scripts32")

    # ---- 落盘
    if wanted:
        tables = {k: v for k, v in tables.items() if k in wanted}
    total_entries = 0
    for name, data in sorted(tables.items()):
        target = out / f"{name}.json"
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        total_entries += len(data)

    if not args.keep_work:
        for item in ("scripts32", "scripts32.dec"):
            p = work / item
            if p.exists():
                p.unlink()

    elapsed = time.time() - started
    log(f"共 {len(tables)} 张表 / {total_entries} 条记录 / 耗时 {elapsed:.1f}s")
    if stats["failed"]:
        warn(f"失败 {len(stats['failed'])} 项：{', '.join(stats['failed'][:8])}")

    set_output(tables=len(tables), entries=total_entries,
               failed=len(stats["failed"]), seconds=f"{elapsed:.0f}")
    append_summary(
        f"### Lua 解包（{args.server}）\n"
        f"- 版本：`{az_raw.strip('$')}`\n"
        f"- 产出：`{len(tables)}` 张表 / `{total_entries}` 条记录\n"
        f"- 耗时：`{elapsed:.0f}s`\n"
        + (f"- ⚠️ 失败 `{len(stats['failed'])}` 项："
           f"{', '.join(stats['failed'][:5])}\n" if stats["failed"] else "")
    )
    return 0 if tables and not stats["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
