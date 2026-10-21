# 阶段 0–3 验收记录

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

## 阶段 3：资产链路（R2）

**代码就绪，未跑过真实桶**（缺 4 个 secrets）。方案与操作步骤见 `docs/CDN.md`。

### 链路本地实测

| 项 | 结果 |
|---|---|
| `--items squareicon --limit 2` | 12 s，产出 2 个 png（27,131 / 26,221 字节） |
| `--items painting --limit 1 --scope used` | 3.9 s，产出 png 813,051 + json 173 + obj 10,249 字节 |
| 引用集 | `public/data` 里认出 **2,581** 个 painting 名 |
| numpy 版本敏感性 | 2.5.4 与 2.4.6 产出**同一 md5**（`f42081cd…`），降版本不改输出 |

### 依赖版本坑（已修）

第一次 CI run `39606425719` 在「安装依赖」失败：

```
Could not find a version that satisfies the requirement numpy==2.5.4
```

按 PyPI metadata：`numpy 2.5.4` 是 `requires_python >=3.12`，而 workflow 统一用 3.11。
改成 `2.4.6`（最后一个支持 3.11 的）。`opencv-python-headless` 的 wheel 是
`cp37-abi3`，不受影响。

### CI run `39606838559`

| 步骤 | 结果 |
|---|---|
| 安装依赖 | ✅ `cv2 5.0.0 \| numpy 2.4.6` |
| 安装 rclone | ✅ `rclone v1.75.2` |
| 检查 R2 凭据 | ❌ 按设计报缺：`R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_ACCOUNT_ID R2_BUCKET` |
| 后续步骤 | skipped（符合预期，没有凭据不该往下走） |

### 空工作区陷阱（这轮最关键的发现）

`azl_assets.py` 的跳过判据要求 state 有 md5 **且** 产物文件在磁盘。CI 是干净
checkout、图被 gitignore，第二个条件永远为假 → **每轮都会全量重跑 5.9 GB**。

解法是 `scripts/mark_remote.py`：`rclone lsf` 拿远端 key，本地 `touch` 出同名空
marker。用 9 行假清单单测过：

| 输入 | 结果 |
|---|---|
| `painting/22/22.json`、`squareicon/2b.png`、`shipicon/abc.png` | ✅ 补齐 3 个 |
| `painting/22/22.png`、`*.obj`、`spine/*.skel`、`data/ship.json` | 忽略 4 个 |
| `../evil.png`、`/abs/x.png` | 拒绝 2 个 |
| 第二次运行 | 补齐 0 / 已存在 3（幂等） |

### 端到端联调（run `39611415936` / `39612271958`，凭据就位后）

桶 `azurlane-assets`（account `3e47c13...`），4 个 secrets 已写入，两次 dispatch 都是 11 步全绿。

第一次（`scope=used limit=2`，**真实上传**）：

| 观测点 | 值 |
|---|---|
| 远端对象（上传前） | 0 |
| 生成 / 上传 | 新增 2 / 待上传 2 |
| 桶内实际 key | `squareicon/22.png`、`squareicon/33.png` |

第二次（`limit=2 dry_run=true`，验证增量闭环）：

| 观测点 | 值 |
|---|---|
| 远端对象 | **2** |
| marker 补齐 | **2** |
| 生成 | **新增 0 / 跳过 2 / 失败 0** |
| 待上传 | 0 |

> 第一次跑时"远端已有 0 个对象"与"上传成功"是矛盾的，追下去发现两个真问题：
> `rclone lsf` 少了 `--recursive`（清单恒为空，§5.2 的解法**当时是静默失效**的），
> 以及 R2 受限 token 的 `HeadBucket` 403 会让 rclone 去 `CreateBucket`。
> 两个都已修（commit `20681d4`），第二次跑的「跳过 2」才是真的证明。
> 教训：**「步骤绿了」和「事情做了」是两件事**，必须拿桶里的实际对象做交叉验证。

### 公共读 r2.dev（2026-10-21）

`https://pub-85b4b8af407244bc8f1502127fc530c3.r2.dev`

| 检查 | 结果 |
|---|---|
| `GET /squareicon/22.png` | `200` / `image/png` / 27,131 字节 |
| 下载后 md5 | `68c03aebc3a214113f228fa31b2a84de`，与桶内对象一致 → 原样托管，无转码 |
| `Cache-Control` | `public, max-age=86400`（上传时设的元数据原样返回） |
| 不存在的 key | `404`（不是 `403`，说明匿名读确实生效） |
| 带 `Origin` 的 GET | **无任何 `Access-Control-*` 头** |
| `OPTIONS` 预检 | **`403`** |

最后两行是真问题：`<img>` 不受影响，但 `fetch` 拉 `spine/*.skel`、`painting/*.obj`
会被浏览器拦。S3 API 的 `GetBucketCors` / `PutBucketCors` 均返回 `AccessDenied`（已实测），
只能去 Dashboard 配，规则见 `docs/CDN.md` §3.1。

## 已知遗留

- 每小时 cron 只握手比版本，没变就退；真正提交频率 = 游戏更新频率。
- `get_version()` 没有重试：登录服偶发抖动会让那一次 run 变红（不影响数据）。
- 阶段 3 的**首次 6.8 GB 播种**要本地做（R2 凭据就位后）；阶段 4（前端切 CDN）未开始。
- `spine` 1.10 GB 无法自动更新，只能从旧仓库搬一次。
- `p0-probe.yml` / `stage1-verify.yml` / `scripts32-probe.yml` 与
  `scripts/p0_probe.py` / `scripts/stage1_report.py` 是**验收用临时件**，
  阶段 4 切换完成后可删。

## 阶段 3：资源上 CDN

### rclone 两个 bug（都是静默的）

| 现象 | 根因 | 证据 |
|---|---|---|
| 「上传 2 个对象」成功，下一轮却报「远端已有 0 个」 | `rclone lsf` 默认只列一层，产物全在 `<item>/` 子目录下 | 加 `--recursive` 后同一对象可见 |
| `S3: CreateBucket ... 403 AccessDenied` | R2 对限定单桶的 token，`HeadBucket` 返回 403，rclone 误判桶不存在 | 加 `--s3-no-check-bucket` 后写入成功 |

修好后重跑（run `39612271958`）：远端 2 个对象 → marker 补齐 2 →
**新增 0 / 跳过 2**。这是「空工作区陷阱真的被堵住」的第一个可信证据。

### 公共读（r2.dev）

`GET /squareicon/22.png` → `200` `image/png` 27,131 字节，md5 `68c03aeb…`
与桶内对象逐字节一致；不存在 key → `404`。
CORS 默认关闭，`OPTIONS` 预检 `403`，且 S3 API 的 `Get/PutBucketCors` 也是
`AccessDenied`（只能在控制台配）——见 `docs/CDN.md` §3.1。

### regen-assets 管路连通性（run `39640672963`）

用 `items=squareicon shards=2 limit=3` 把成本压到最小，验证整条链路：

```
远端已有 5233 个对象 → marker 补齐 4826 / 非 marker 407
基线 state：CN=2
分片 0：新增 3, 跳过 1, 他片 6      ← "他片 6" 证明 crc32 分片互斥且完整
merge：片段 2 个 → 应用 6 条，冲突 0 条；state 现有 8 条
提交 32fe98a3 assets: 重建 squareicon（2 片）
```

四个 job 全部 success，state 真的落库。

### painting 全量重建

run `39641164165`，6 片并行。截至记录时六片均已通过
marker / state 继承 / 磁盘预检，进入生成阶段。
期望：`新增` 合计 ≈ 2,491，`失败` = 0，每片产物约 0.9 GB。

## 复现

```bash
# 单表 P0
gh workflow run p0-probe.yml            # 或 push 改动触发
# 全量等价比对
gh workflow run stage1-verify.yml
# 正式流水线（不提交）
gh workflow run update-data.yml -f force=true -f dry_run=true
```
