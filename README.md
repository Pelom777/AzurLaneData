# AzurLaneData

碧蓝航线数据站的**数据与资产仓库**。前端在 [`Pelom777/AzurLaneTools`](https://github.com/Pelom777/AzurLaneTools)，
本仓库只负责把游戏里的东西变成 `public/data/*.json`。

- **每小时**检查游戏版本（一次 TCP 握手，几百毫秒）
- **版本变了**才下载资源包、自行解包 Lua、重新构建数据
- 产出与前端逐字节兼容，前端零改动

## 为什么自己解包

之前是 `git sparse-checkout` 拉 `AzurLaneTools/AzurLaneLuaScripts`。
能用，但新鲜度由别人决定、链路依赖别人的仓库是否还在。
现在直接从游戏 CDN 取 `scripts32` 和 `sharecfgdata/*`，用 `bcdec` / `uabdec`
还原 LuaJIT 字节码，再用 `ljd` 反编译。

链路、已验证事实、风险、分阶段步骤都在 **[`docs/HANDOFF.md`](docs/HANDOFF.md)**。

## 目录

```
scripts/     数据链路（解包 → 构建 → 校验 → 台账）
tools/       bcdec / uabdec 二进制，32 位 x86 ELF
public/data/ 产出（ship / skin / option / ship-attr / new.json）
data/        版本状态
docs/        HANDOFF.md（交接） / PROMPT.md（新任务提示词） / AUTOMATION.md（旧链路）
```

## 本地跑

```bash
# Linux / WSL，需要 i386 运行库
sudo apt install libc6:i386
pip install UnityPy
pip install https://github.com/AzurLaneTools/ljd/releases/download/wheel/ljd-0.1.7-py3-none-any.whl

python scripts/unpack_lua.py --server CN --out raw --work work --only weapon_name
python scripts/build_data.py --raw raw --out public/data
python scripts/check_data_contract.py --data public/data
```

## 容量

图片资产 6.82 GB，**不进 git**。数据 json 十几 MB，进。
细节和算账过程见 HANDOFF §6。
