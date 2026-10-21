"""public/data 生成适配层。

这层只负责三件"不该重复写"的事：
  1. 解析参数、校验原始数据目录
  2. 稳定格式写 JSON（键排序 + 中文不转义），保证 diff 只反映真实内容变化
  3. 没有实现时给出明确提示，而不是抛一堆栈

你已有的本地转换脚本需要做的最小改动：
    放到 scripts/build_data_impl.py，暴露一个函数

        def build(raw_dir: pathlib.Path) -> dict[str, object]:
            ...
            return {"ship": [...], "skin": [...], ...}   # 键名 = 输出文件名（不含 .json）

如果你的脚本是"直接往目录里写文件"的老式脚本，也可以：
        def build(raw_dir, out_dir):   # 接受两个参数时自动传入输出目录
            ... 自己写文件 ...

有些表是「游戏数据 + 人工字段」混在一起的（例如 ship-attr 的 transform / gear_score
在配置表里根本没有）。这种表把参数写成 `prev_dir`：

        def build(raw_dir, prev_dir=None) -> dict:
            ...

本脚本见到参数名 `prev_dir` 时，会把 `--out` 目录**同时当作输入**传进去，
让实现方读到现有 public/data 做字段级合并。注意此时输出目录不能是空的，
否则那些人工字段会退化成"不存在"。
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import append_summary, log, set_output, warn, write_json  # noqa: E402

IMPL_NAME = "build_data_impl.py"


def load_impl(scripts_dir: Path):
    """加载 build_data_impl.py。返回模块本身（build / write_json 都从模块上取）。

    这么设计是为了让实现自带序列化方式：仓库现有的 public/data 是
    `ensure_ascii=True` + 默认分隔符 + 无末尾换行，和本脚本默认的
    「不转义中文 + 紧凑 + 末尾换行」不一样。实现方如果要逐字节对齐仓库，
    自己导出一个 write_json 就会被优先使用。
    """
    impl_path = scripts_dir / IMPL_NAME
    if not impl_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("build_data_impl", impl_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_data_impl"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, help="原始 json 目录，例如上游的 CN/")
    ap.add_argument("--out", default="public/data", help="输出目录")
    ap.add_argument("--prune", action="store_true",
                    help="删除输出目录中本次未生成的 .json（默认关闭，避免误删手工维护的文件）")
    args = ap.parse_args()

    raw_dir = Path(args.raw).resolve()
    out_dir = Path(args.out).resolve()
    if not raw_dir.is_dir():
        warn(f"原始数据目录不存在：{raw_dir}")
        set_output(skipped=True)
        return 1

    module = load_impl(Path(__file__).resolve().parent)
    build = getattr(module, "build", None) if module else None
    if build is None:
        warn(
            f"未找到 scripts/{IMPL_NAME}，跳过 public/data 生成。"
            f"把本地转换脚本放进该文件并暴露 build(raw_dir) 即可接入。"
        )
        append_summary(
            "### public/data 未生成\n"
            f"缺少 `scripts/{IMPL_NAME}`，需要把已有的本地转换脚本接进来（见 docs/AUTOMATION.md 第四节）。\n"
        )
        set_output(skipped=True)
        return 0

    # 实现方可以自带序列化方式（逐字节对齐仓库现有文件）；没有就用默认的
    dump = getattr(module, "write_json", None) or write_json
    out_dir.mkdir(parents=True, exist_ok=True)
    sig = inspect.signature(build)
    names = list(sig.parameters)

    log(f"调用 {IMPL_NAME}.build()，参数={names}，raw={raw_dir}")
    if "prev_dir" in sig.parameters:
        # 部分表（ship-attr）有推不出来的字段，需要读输出目录里的旧文件做合并。
        # 这里把 out_dir 同时当作输入，注意：先读完再写，不能边读边写。
        data = build(raw_dir, prev_dir=out_dir)
        if not isinstance(data, dict):
            warn("build() 应该返回 {文件名: 数据} 形式的 dict")
            return 1
        for name, payload in data.items():
            dump(out_dir / f"{name}.json", payload)
        produced = set(data)
    elif len(names) >= 2:
        build(raw_dir, out_dir)
        produced = {p.stem for p in out_dir.glob("*.json")}
    else:
        data = build(raw_dir)
        if not isinstance(data, dict):
            warn("build() 应该返回 {文件名: 数据} 形式的 dict")
            return 1
        for name, payload in data.items():
            dump(out_dir / f"{name}.json", payload)
        produced = set(data)

    if args.prune:
        for stale in out_dir.glob("*.json"):
            if stale.stem not in produced:
                stale.unlink()
                log(f"删除多余的 {stale.name}")

    total = sum(p.stat().st_size for p in out_dir.glob("*.json"))
    log(f"生成 {len(produced)} 个 json，共 {total / 1024:.0f} KB")
    append_summary(
        "### public/data\n"
        f"- 源目录：`{raw_dir}`\n"
        f"- 产出：`{len(produced)}` 个 json，`{total / 1024:.0f} KB`\n"
    )
    set_output(skipped=False, files=len(produced))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
