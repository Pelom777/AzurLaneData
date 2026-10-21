"""P0 探针：证明 `bcdec` → `ljd` → `luatable` 在 9.7 上还能走通。

**临时脚本，只用于阶段 0 验收，不参与每小时流水线。**

为什么要单独写一个探针，而不是直接跑 `unpack_lua.py`：

    unpack_lua.py 里有三个"文档里写死的假设"，任何一个不成立都会让整条链失败，
    但失败时只看到一句异常，分不清是哪一层坏了：

      1. `bcdec` 输出首 5 字节 + 尾 1 字节是容器包装  →  `out[5:-1]`
      2. `bcdec` 是**逐段**调用的，每段独立可解
      3. 全部段拼起来再**一次** `ljd`（HANDOFF 说是为了跨段引用）

    所以这里把同一份 bundle 用多种假设各跑一遍，逐一记录结果，用数据决定
    哪条假设成立，而不是猜。

产出：
    --report  一份 json，含每一步的原始证据（返回码 / 字节长度 / 头尾 hex /
              每种假设的 lua 长度与条目数 / 与旧链路基线的比对结论）

用法：
    python scripts/p0_probe.py --server CN --table weapon_name \
        --work work --report p0/report.json --fixture p0/weapon_name.legacy.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import log, warn  # noqa: E402
from luatable import LuaParseError, parse_text  # noqa: E402
from unpack_lua import LJ_HEADER, TOOLS, iter_segments, read_uleb128  # noqa: E402

from azl_assets import (  # noqa: E402
    SERVERS, SEND_PAYLOAD, _parse_proto, download_bundle, fetch_hash_list,
)


def _tool(name: str) -> Path:
    for cand in (TOOLS / name, TOOLS / (name + ".exe")):
        if cand.exists():
            return cand
    raise SystemExit(f"缺少 tools/{name}")


def run_bcdec(data: bytes, timeout: int = 120) -> dict:
    """跑一次 bcdec，返回原始证据（不裁剪、不猜测包装）。"""
    started = time.time()
    try:
        proc = subprocess.run([str(_tool("bcdec"))], input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
    except OSError as exc:
        # 例如在 Windows 上直接跑 32 位 ELF：WinError 193 / 找不到解释器
        return {"rc": -1, "in_len": len(data), "out_len": 0, "out_head": "", "out_tail": "",
                "starts_with_lj": False, "ends_with_nul": False,
                "stderr": f"无法执行 bcdec: {type(exc).__name__}: {exc}"[:300],
                "seconds": round(time.time() - started, 3), "_out": b""}
    out = proc.stdout
    return {
        "rc": proc.returncode,
        "in_len": len(data),
        "out_len": len(out),
        "out_head": out[:8].hex(" "),
        "out_tail": out[-4:].hex(" ") if out else "",
        "starts_with_lj": out.startswith(LJ_HEADER),
        "ends_with_nul": out.endswith(b"\x00"),
        "stderr": proc.stderr.decode("u8", "replace")[:300],
        "seconds": round(time.time() - started, 3),
        "_out": out,
    }


def try_ljd(dump: bytes) -> dict:
    """尝试反编译一份 dump，记录成功与否和原因。"""
    import ljd.tools  # noqa: PLC0415

    started = time.time()
    try:
        text = ljd.tools.process_bytes(dump)
        return {"ok": True, "lua_len": len(text), "seconds": round(time.time() - started, 2),
                "_text": text}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "seconds": round(time.time() - started, 2)}


def summarize_text(text: str) -> dict:
    """lua 文本 → 表数与条目数（解析失败只记录原因，不中断）。"""
    try:
        tables, cut = parse_text(text)
        return {"tables": len(tables), "entries": sum(len(v) for v in tables.values()),
                "names": sorted(tables)[:5], "truncated": cut}
    except (LuaParseError, ValueError, TypeError) as exc:
        return {"tables": 0, "entries": 0, "parse_error": str(exc)[:300]}


def compare_with_fixture(tables: dict, fixture_path: Path) -> dict:
    """把解析结果与旧链路 json 做字段级比对。"""
    if not fixture_path or not fixture_path.exists():
        return {"compared": False, "reason": "没有基线文件"}
    expected = json.loads(fixture_path.read_text("utf-8"))
    name = next(iter(tables)) if tables else None
    actual = tables.get(name, {}) if name else {}
    exp_keys, act_keys = set(expected), set(actual)
    diff_ids = sorted(exp_keys ^ act_keys)
    mismatch = [k for k in (exp_keys & act_keys) if expected[k] != actual[k]]
    return {
        "compared": True,
        "table": name,
        "fixture_entries": len(expected),
        "actual_entries": len(actual),
        "only_in_fixture": sorted(exp_keys - act_keys)[:10],
        "only_in_actual": sorted(act_keys - exp_keys)[:10],
        "id_diff_count": len(diff_ids),
        "value_mismatch_count": len(mismatch),
        "value_mismatch_sample": mismatch[:5],
        "identical": exp_keys == act_keys and not mismatch,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="CN")
    ap.add_argument("--table", default="weapon_name")
    ap.add_argument("--work", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--fixture", default="")
    ap.add_argument("--per-seg-budget", type=int, default=240,
                    help="逐段反编译的时间预算（秒），超了就记 partial")
    args = ap.parse_args()

    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    report: dict = {"server": args.server, "table": args.table, "steps": []}

    # ---- 1. 版本与清单
    import socket  # noqa: PLC0415

    host, port, cdn = SERVERS[args.server]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(20)
        sock.connect((host, port))
        sock.send(SEND_PAYLOAD)
        raw = sock.recv(20480)
    info = _parse_proto(raw[7:])
    az = next((v for v in info.get("version", []) if v.startswith("$azhash")), "")
    listing = fetch_hash_list(cdn, az)
    report["azhash"] = az.strip("$")
    report["listing_entries"] = len(listing)
    log(f"azhash {report['azhash']}，清单 {len(listing)} 条")

    # ---- 2. 下载 + 分段覆盖校验
    meta = listing[f"sharecfgdata/{args.table}"]
    bundle = work / f"scd-{args.table}"
    if not bundle.exists() or bundle.stat().st_size != meta["size"]:
        download_bundle(cdn, meta["md5"], bundle, meta["size"])
    data = bundle.read_bytes()
    md5 = hashlib.md5(data).hexdigest()

    idx = 0
    segs = []
    for seg in iter_segments(data):
        segs.append(seg)
        new_idx, num = read_uleb128(data, idx)
        idx = new_idx + num
    report["bundle"] = {
        "size": len(data), "md5": md5, "md5_matches_listing": md5 == meta["md5"],
        "segments": len(segs), "cursor_end": idx,
        "coverage_exact": idx == len(data),
        "head_hex": data[:8].hex(" "),
    }
    log(f"bundle {len(data)} 字节 / {len(segs)} 段 / 游标 {idx} "
        f"({'覆盖完整' if idx == len(data) else '有残留'})")
    if not segs:
        report["verdict"] = "fail: 切成 0 段"
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        return 1

    # ---- 3. bcdec 逐段解码，同时取样证据
    started = time.time()
    outs = []
    failures = []
    for i, seg in enumerate(segs):
        try:
            res = run_bcdec(seg)
        except subprocess.TimeoutExpired:
            failures.append({"segment": i, "error": "timeout"})
            continue
        if res["rc"] == 0 and res["_out"]:
            outs.append(res["_out"])
        else:
            failures.append({"segment": i, "rc": res["rc"], "stderr": res["stderr"]})
        if i == 0:
            res.pop("_out")
            report["bcdec_sample"] = res
    bcdec_seconds = round(time.time() - started, 1)
    report["bcdec"] = {
        "segments_decoded": len(outs), "failures": len(failures),
        "failure_sample": failures[:3], "seconds": bcdec_seconds,
        "all_start_with_lj": all(o.startswith(LJ_HEADER) for o in outs),
        "all_end_with_nul": all(o.endswith(b"\x00") for o in outs),
        "total_out_bytes": sum(len(o) for o in outs),
    }
    log(f"bcdec：{len(outs)}/{len(segs)} 段成功，失败 {len(failures)}，{bcdec_seconds}s")
    if not outs:
        report["verdict"] = "fail: bcdec 一段都没成功"
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        return 1

    # ---- 4. 各种拼接/裁剪假设，逐一验证
    payloads = [o[5:-1] if (o.startswith(LJ_HEADER) and o.endswith(b"\x00")) else o
                for o in outs]
    hypotheses = {
        # bcdec 输出本身就是完整 dump（最可能），逐段反编译后拼文本
        "H1_per_segment_full_dump": ("per_seg", outs),
        # 去掉 5+1 包装后逐段重新补头
        "H2_per_segment_rewrap": ("per_seg", [LJ_HEADER + p + b"\x00" for p in payloads]),
        # HANDOFF 说的"全部拼接后一次反编译"（原样拼）
        "H3_concat_raw": ("concat", b"".join(outs)),
        # 拼 payload 但补一个头（解掉 unpack_lua 里看似缺头的写法）
        "H4_concat_payload_with_header": ("concat", LJ_HEADER + b"".join(payloads) + b"\x00"),
        # unpack_lua.py 原样：拼 payload、不补头
        "H5_concat_payload_no_header": ("concat", b"".join(payloads)),
    }

    results = {}
    for name, (mode, blob) in hypotheses.items():
        if mode == "concat":
            res = try_ljd(blob)
            item = {k: v for k, v in res.items() if not k.startswith("_")}
            if res["ok"]:
                item.update(summarize_text(res["_text"]))
                item["_text"] = res["_text"]
            results[name] = item
            log(f"{name}: {'ok' if res['ok'] else 'fail'} "
                f"{item.get('error', '')} lua={item.get('lua_len', 0)}")
        else:
            # 逐段反编译：有时间预算，超时记 partial
            started = time.time()
            texts, errors, done = [], [], 0
            for i, dump in enumerate(blob):
                if time.time() - started > args.per_seg_budget:
                    break
                res = try_ljd(dump)
                done += 1
                if res["ok"]:
                    texts.append(res["_text"])
                else:
                    errors.append({"segment": i, "error": res["error"]})
            text = "".join(texts)
            item = {"ok": bool(texts) and not errors, "segments_tried": done,
                    "segments_ok": len(texts), "segments_failed": len(errors),
                    "error_sample": errors[:2],
                    "lua_len": len(text), "partial": done < len(blob),
                    "seconds": round(time.time() - started, 1)}
            if texts:
                item.update(summarize_text(text))
                item["_text"] = text
            results[name] = item
            log(f"{name}: {len(texts)}/{done} 段反编译成功，"
                f"条目 {item.get('entries', '?')}，{item['seconds']}s")

    # ---- 4.5 生产路径：直接调用 unpack_lua，验证 CI 真正跑的那段代码
    #      前面几个假设是"手工试出来的"，这一段才是将来每小时跑的东西。
    try:
        import shutil  # noqa: PLC0415

        import unpack_lua  # noqa: PLC0415

        cache_dir = work / "cache-probe"
        shutil.rmtree(cache_dir, ignore_errors=True)   # 清缓存，保证真的重算
        t0 = time.time()
        text = unpack_lua.unpack_sharecfgdata(bundle, cache_dir)
        prod = {"ok": True, "lua_len": len(text),
                "seconds": round(time.time() - t0, 1), "_text": text}
    except Exception as exc:  # noqa: BLE001
        prod = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    prod_item = {k: v for k, v in prod.items() if not k.startswith("_")}
    if prod["ok"]:
        prod_item.update(summarize_text(prod["_text"]))
        prod_item["_text"] = prod["_text"]
    results["H6_production_unpack_lua"] = prod_item
    log(f"H6_production_unpack_lua: {'ok' if prod['ok'] else 'fail'} "
        f"{prod_item.get('error', '')} 条目 {prod_item.get('entries', '?')}")

    report["hypotheses"] = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                            for k, v in results.items()}

    # ---- 5. 挑最优：生产路径优先，其次按条目数
    best_name, best = None, None
    for name, res in results.items():
        if res.get("ok") and res.get("entries"):
            if best is None or res["entries"] >= best["entries"]:
                best_name, best = name, res

    if best is None:
        report["verdict"] = "fail: 所有假设都无法产出可解析的 lua"
        report["step"] = "ljd 或 luatable"
    else:
        fixture = Path(args.fixture) if args.fixture else None
        cmp = compare_with_fixture({args.table: parse_text(best["_text"])[0].get(args.table, {})},
                                   fixture) if fixture else {"compared": False}
        report["best"] = best_name
        report["fixture_compare"] = cmp
        ok = bool(cmp.get("identical"))
        report["verdict"] = "pass" if ok else f"unverified: {cmp.get('reason') or '与基线不一致'}"

    # 保存最优 lua 原文 + 解析出的 json，便于人工核对与独立 diff
    if best is not None:
        outdir = Path(args.report).parent
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "best.lua").write_text(best["_text"], encoding="utf-8")
        parsed_all, _cut = parse_text(best["_text"])
        if args.table in parsed_all:
            with (outdir / f"{args.table}.json").open("w", encoding="utf-8", newline="\n") as fh:
                json.dump(parsed_all[args.table], fh, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"))

    report["total_seconds"] = round(time.time() - t_start, 1)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    print(json.dumps({k: v for k, v in report.items() if k != "hypotheses"},
                     ensure_ascii=False, indent=2)[:4000])
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
