"""按名字删除 R2 上的立绘目录，并清掉对应的台账条目。

用法（CI 里由 update-assets 的 prune 输入驱动，也可本地跑）：

    # 只看看会删什么
    python scripts/prune_assets.py --data public/data \
        --state .azl/assets-state.json --names hu_2_asmr aimudeng_5_asmr
    # 真删
    python scripts/prune_assets.py ... --names hu_2_asmr --bucket azurlane-assets --apply

三道安全线，缺一不可：

 1. **不传 `--apply` 就只列不删** —— 先看到对象数和字节数，再决定动手。
 2. 名字必须形如 `[a-z0-9_]+`，不许带 `/` 或往上跳的路径片段。
    删除目标是 `painting/<name>/` 这一整个前缀，拼错一个字符就会删错一片。
 3. 名字出现在 `public/data` 引用集里的**直接拒绝**。
    引用集是「站点真正会去取的立绘名」的权威来源，命中就说明它还活着，
    删了就是线上缺图。这条是防手滑的最后一道，也是唯一能自动判的一道。

为什么不用 `rclone sync` 或 `rclone purge`：
  - `sync` 会顺手删掉桶里不在本地清单里的东西（spine 是手工搬的，会被误删）；
  - `purge` 需要路径是「空目录」语义，R2 上只有前缀，行为不如 `delete` 直观。

输出（供 workflow 摘要与提交信息用）：
    pruned / objects / bytes
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import die, log, set_output, warn  # noqa: E402

SAFE_NAME_RE = re.compile(r'^[a-z0-9_]+$')


def rclone(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(['rclone', *args], capture_output=True, text=True)


def list_objects(bucket: str, prefix: str) -> list[dict]:
    """列出某个前缀下的对象。lsjson 的输出是逐行 JSON（不是整体数组）。"""
    p = rclone(['lsjson', f'r2:{bucket}/{prefix}',
                '--files-only', '--recursive', '--s3-no-check-bucket'])
    if p.returncode != 0:
        die(f'列不出 r2:{bucket}/{prefix}：{p.stderr.strip()[:300]}')
    out = []
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            warn(f'lsjson 输出了一行不是 JSON 的内容，跳过：{line[:120]}')
            continue
        if obj.get('IsDir'):
            continue
        out.append(obj)
    return out


def load_used_names(data_dir: Path) -> set[str]:
    """public/data 里站点会去取的立绘名。与 azl_assets.load_used_names 同口径。"""
    used: set[str] = set()
    if not data_dir.is_dir():
        warn(f'{data_dir} 不存在，跳过"在用资源"这道安全检查')
        return used
    for p in sorted(data_dir.glob('*.json')):
        # 空文件不是数据：public/data 下就有几个 0 字节的历史文件，
        # 逐个报警只会在日志里堆噪音，还让人以为检查失效了。
        if p.stat().st_size == 0:
            continue
        try:
            doc = json.loads(p.read_text('u8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            warn(f'{p.name} 读不动（{exc}），这道检查对它失效')
            continue
        for name in _walk_names(doc):
            used.add(name.lower())
    return used


def _walk_names(node) -> set[str]:
    """把 json 里所有 `<...>.painting` 字段值收集出来。"""
    found: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == 'painting' and isinstance(v, str):
                found.add(v.lower())
            else:
                found |= _walk_names(v)
    elif isinstance(node, list):
        for v in node:
            found |= _walk_names(v)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--names', nargs='+', required=True,
                    help='要删的立绘名（不含 painting/ 前缀）')
    ap.add_argument('--bucket', default='', help='R2 桶名')
    ap.add_argument('--state', default='.azl/assets-state.json')
    ap.add_argument('--data', default='public/data', help='引用集目录')
    ap.add_argument('--apply', action='store_true', help='真的删（不传只列）')
    ap.add_argument('--prefix', default='painting',
                    help='类别目录，默认 painting（表情等由它顺带产出，不单独清理）')
    args = ap.parse_args()

    if args.apply and not args.bucket:
        die('--apply 必须配 --bucket（防手滑时把前缀拼到错地方）')

    names = [n.strip().lower() for n in args.names if n.strip()]
    bad = [n for n in names if not SAFE_NAME_RE.match(n)]
    if bad:
        die(f'名字不合法（只允许 [a-z0-9_]）：{bad}')

    used = load_used_names(Path(args.data))
    blocked = sorted(n for n in names if n in used)
    if blocked:
        die(f'这些名字还在 data 引用集里，拒绝删：{blocked}'
            '（要先在数据侧剔除，再回来清资源）')

    state_path = Path(args.state)
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text('u8'))
        except json.JSONDecodeError:
            warn('state 读不动，这次只删对象、不动台账')
            state = {}

    total_objects = total_bytes = 0
    removed_keys = 0
    for name in names:
        prefix = f'{args.prefix}/{name}'
        if args.bucket:
            objs = list_objects(args.bucket, prefix)
            size = sum(int(o.get('Size') or 0) for o in objs)
            log(f'{prefix}：{len(objs)} 个对象 / {size / 1048576:.2f} MB')
            if args.apply:
                p = rclone(['delete', f'r2:{args.bucket}/{prefix}',
                            '--s3-no-check-bucket', '--stats', '10s',
                            '--stats-one-line'])
                if p.returncode != 0:
                    warn(f'{prefix} 删除失败：{p.stderr.strip()[:300]}')
                    continue
                log(f'  已删 {prefix}（{len(objs)} 个对象）')
            total_objects += len(objs)
            total_bytes += size
        else:
            log(f'{prefix}：没给 --bucket，只做台账清理')

        # 台账：key 形如 `painting:painting/<name>:<face_md5>`，按前缀删
        for srv, items in (state.get('items') or {}).items():
            dead = [k for k in items if k.startswith(f'{args.prefix}:{args.prefix}/{name}:')]
            for k in dead:
                del items[k]
                removed_keys += 1
        if args.apply and state:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), 'u8')

    verb = '已清' if args.apply else '将清'
    log(f'合计：{len(names)} 个立绘 / {total_objects} 个对象 / '
        f'{total_bytes / 1048576:.2f} MB / 台账{verb} {removed_keys} 条'
        + ('' if args.apply else '（只列未删，加 --apply 才动手）'))
    set_output(pruned=len(names), objects=total_objects, bytes=total_bytes,
               keys=removed_keys)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
