# 阶段 0–2 验收记录

> 2026-10-21，CN 服 `$azhash$9$7$398$def4c26318af25f3`。
> CI 日志只保留 90 天，这里固化数字。原始证据在 `p0-artifacts` / `stage1-artifacts` /
> `scripts32-artifacts` 三个分支上。

## 阶段 0：打通解包（P0）

| 项 | 值 |
|---|---|
| 表 | `sharecfgdata/weapon_name` |
| bundle | 208,532 字节，md5 `ee668bc95362b504c58881755224e052`（与 az 清单一致） |
| uleb128 分段 | **2422 段**，游标止于 208,532 = 文件长度（无残留） |
| `bcdec` | 2422/2422 段成功，1.0 s，输出全部以 `1b 4c 4a 02 02` 开头、`00` 结尾 |
| `ljd` 逐段反编译 | 2422/2422 段成功，2.7 s |
| 产出 | 2422 条，85,011 字节，md5 `ed8329b1b0601b00b64e13b8e6a24f53` |
| 与旧链路比 | **逐字节相同**（`cmp` 无差异，2422 = 2422） |

顺带把 32 张 `sharecfgdata` 的分段全预检了一遍：**233,910 段 / 118.9 MB，每张都覆盖精确、
md5 全对**。

**交接文档纠错**（实测与原文相反，已改 HANDOFF §3/§9）：

1. `bcdec` 输出是**完整** BC dump，不能按 `out[5:-1]` 剥头尾；
2. `sharecfgdata` 每段本身就是独立 dump，必须**逐段反编译再拼 lua 文本**。
   把字节码拼起来再一次性 `ljd` 的三种写法全部失败
   （`'NoneType' object has no attribute 'constants'`）。

## 阶段 0.5：基线数据

`public/data` 从 `Pelom777/AzurLaneTools` 稀疏克隆迁入：**21 个文件 / 781 KB**，
与 HANDOFF 一致；6.82 GB 图片资产未触碰。

## 阶段 1：全量数据

| 项 | 值 |
|---|---|
| 解包 | 36 张表 / **236,150 条** / 1266 s / 失败 0 |
| 7 张必需表 | 齐（3 张来自 sharecfgdata + 4 张来自 scripts32） |
| **新链路 vs 旧链路** | **21 个产物全部一致，0 差异** |
| 契约校验 | 全部通过（ship 897 / skin 2491 形态 / ship-attr 897，键集对齐） |
| scripts32 四张表 | 与旧链路**逐字节相同**（863 / 110 / 1254 / 13 条） |

与仓库现有 `public/data` 的差异（均为预期）：

- `ship.json` 897 vs 789 —— 仓库数据停在 2026-05，**108 艘新船**；
- `changed_keys = 2`，正是已知的 `siwangzhuzai` 改名、`huangjiacaifu` type 22→23；
- `skin.json` 少 3 个键（`gulite_robot1/3/4`，游戏侧已删）；
- `ship-attr.json` 36 个键变动（新增船的属性 + 潜艇油耗）。

## 阶段 2：CI

| 轮次 | 输入 | 结果 |
|---|---|---|
| 1 | `force=true, dry_run=true` | 全绿；解包 1265.9 s；契约通过；台账建基线（897/2491，不标新增）；**提交按 dry_run 跳过** |
| 2 | 不带 force | **15 秒**结束；`未变化`；第 7~13 步全部 skipped，无空提交 |
| 3 | `force=true, dry_run=true` | `Cache hit for: lua-CN-def4c26318af25f3`；解包 **50.5 s**（对比 1265.9 s，**25×**）；结果仍是 236,150 条 |

缓存只有 7.9 MB（zstd 后），因为缓存的只是反编译出来的 lua 文本，按内容 md5 分文件。

## 已知遗留

- 每小时 cron 只握手比版本，没变就退；真正提交频率 = 游戏更新频率。
- `get_version()` 没有重试：登录服偶发抖动会让那一次 run 变红（不影响数据）。
- 阶段 3/4（资产搬迁、前端切换）未开始。
- `p0-probe.yml` / `stage1-verify.yml` / `scripts32-probe.yml` 与
  `scripts/p0_probe.py` / `scripts/stage1_report.py` 是**验收用临时件**，
  阶段 4 切换完成后可删。

## 复现

```bash
# 单表 P0
gh workflow run p0-probe.yml            # 或 push 改动触发
# 全量等价比对
gh workflow run stage1-verify.yml
# 正式流水线（不提交）
gh workflow run update-data.yml -f force=true -f dry_run=true
```
