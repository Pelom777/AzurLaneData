# HANDOFF —— AzurLaneData 交接

> 给"新开一个任务"的你：这份文档只写**结论和边界**，不复述思考过程。
> 照着 §7 的阶段走，每一步都有验收标准；卡住的点集中在 §4。

---

## 1. 目标

在 `D:\Projects\AzurLaneData`（远端 `Pelom777/AzurLaneData`）建一个**数据与资产仓库**：

- 每小时由 GitHub Actions 检查游戏版本
- 版本变了就**自己去游戏 CDN 解包**出需要的 Lua 配置表，**不再经过 `AzurLaneLuaScripts`**
- 产出 `public/data/*.json`（与 `AzurLaneTools` 前端逐字节兼容）
- 维护 `new.json` 记录每条船 / 每个皮肤的首次出现时间
- 前端仓库 `AzurLaneTools` 只留源码，删掉 `public/`

产出必须能让前端零改动跑起来 —— 现有的 `build_data.py` / `check_data_contract.py`
是契约来源，**不要改它们的语义**。

---

## 2. 关键背景：为什么要解包

现在 `scripts/fetch_lua_tables.py` 是 `git sparse-checkout`
`AzurLaneTools/AzurLaneLuaScripts` 拿 `sharecfgdata/*.lua`。这条路能跑，但：

- 新鲜度由别人决定（对方的 cron 是每小时 40 分）
- 依赖仓库在，链路就在；仓库改名/停更，我们立刻失速
  （`AzurLaneData` 就是前车之鉴，停在 2026-05）

所以要自己解包。**已经确认这条路是通的**，代价是要带两个 32 位二进制。

---

## 3. 技术链路

游戏 CDN 上，`az` 清单（`$azhash$...`）同时包含 `scripts32` 和 32 个
`sharecfgdata/*`。两者格式完全不同，走两条链路。

### A. `sharecfgdata/<表名>` —— 32 个独立小包

```
bundle 文件
  = [uleb128 长度][LuaJIT 字节码]  × N        ← 自定义容器，不是 UnityFS
       ↓ 每段前面补 b"\x1bLJ\x02\x02"，后面补 b"\x00"
  一段合法的 LuaJIT BC dump
       ↓ tools/bcdec（stdin/stdout，输出仍是完整 dump，头尾不要剥）
  标准 LuaJIT 字节码
       ↓ 逐段 ljd.tools.process_bytes()，再拼接 lua 文本
  lua 源码  →  scripts/luatable.py 解析  →  <表名>.json
```

> **实测修正（2026-10-21，CN 9.7.398）**：原文写的是"全部拼接后一次 `ljd`"，
> 这条对 `sharecfgdata` **是错的**。每段 `bcdec` 之后本身就是一份**完整**的
> BC dump（实测 2422/2422 段都以 `1b 4c 4a 02 02` 开头、`00` 结尾），必须
> **逐段反编译再拼文本**。三种拼接字节码的写法全部失败，报
> `'NoneType' object has no attribute 'constants'`。同理 `bcdec` 的输出不要按
> `out[5:-1]` 剥头尾 —— 剥了 ljd 就无头可读。证据：`scripts/p0_probe.py` 的
> report（H1/H2 通过，H3/H4/H5 全挂，H6 生产路径通过）。


**分段逻辑已实测**：`barrage_template_2` 切出 219 段，`idx` 正好走到文件末尾，
无越界无残留。参考实现是 `AzurLaneExtractor` 的
`az_ex/tasks/scripts.py::iter_sharecfgdata` / `DecodeSharecfgData`。

> 注意：**不是** UnityPy。`sharecfgdata` 的 bundle 头不是 `UnityFS`
> （实测 `b0 02 03 03 00 00 01 05 ...`），用 UnityPy 打不开。

### B. `scripts32` —— 一个 39 MB 的大包

```
scripts32（整体加密的 UnityFS，头也不是 UnityFS）
       ↓ tools/uabdec <in> <out>
  明文 UnityFS
       ↓ UnityPy.load()
  遍历 TextAsset，container 形如
      assets/luabuilds/android/normal/sharecfg/<表名>.lua.bytes
       ↓ 只取需要的 4 张
  TextAsset.script → bcdec → ljd → lua 源码 → json
```

需要的 4 张（都不在 `sharecfgdata/` 清单里，只存在于 `scripts32`）：

| 表 | 用途 |
|---|---|
| `ship_data_strengthen.lua` | `ship-attr` 的 `strengthen` |
| `attribute_info_by_type.lua` | 12 位属性的索引语义 |
| `ship_data_trans.lua` | 改造组 → 节点列表 |
| `transform_data_template.lua` | 改造节点 `effect` / `gear_score` |

---

## 4. 已验证 / 未验证

### 已验证（2026-10-21，CN 9.7.25）

| 事实 | 证据 |
|---|---|
| 登录服握手可用 | `line1-login-bili-blhx.bilibiligame.net:80`，回 `$azhash$9$7$398$...` |
| `az` 清单 64,649 条 | 含 `scripts32`(39.7 MB)、`scripts64`、32 个 `sharecfgdata/*` |
| `sharecfgdata` 是 uleb128 分段容器 | `barrage_template_2` 219 段，累计长度 = 文件长度 67,296 |
| `sharecfgdata` bundle **不是** UnityFS | 头 `b0 02 03 03 00 00 01 05` |
| 图片 bundle **是**明文 UnityFS | `painting/22` 头 `UnityFS\0\0\0\0\x085.x.` |
| `bcdec`/`uabdec` 为 32 位 x86 ELF | ELF header: `class=1, machine=3` |
| 官方镜像私有 | GHCR token 端点匿名返回 401 |
| **分段数 = 条目数，与官方产出对得上** | `sharecfgdata/barrage_template_2` 切出 **219 段**；`AzurLaneLuaScripts/CN/sharecfgdata/barrage_template_2.lua` 解析出 **219 条**，一一对应 |
| 旧链路 35 个文件里的 3 个是遗留 | `barrage_template` / `bullet_template` / `weapon_property`（无后缀）在 CDN 上没有对应 bundle，是拆分前的旧快照 |
| 下游只需要 7 张表 | `build_data_impl.py` 只用 `ship_data_statistics` / `ship_data_template` / `ship_skin_template` / `ship_data_strengthen` / `ship_data_trans` / `transform_data_template` / `attribute_info_by_type` |
| 现有下游可复用 | `build_data.py --raw raw --out public/data` 的入参就是 `{表名}.json` |

### 已通过（2026-10-21，数字固化在 `docs/VERIFICATION.md`）

| 项 | 结果 |
|---|---|
| `bcdec` 还原 9.7 字节码 | ✅ 2422/2422 段，1.0 s，输出均以 `1b 4c 4a 02 02` 开头 |
| `ljd` 逐段反编译 | ✅ 2422/2422，2.7 s |
| `weapon_name` 与旧链路比 | ✅ **逐字节相同**（`cmp` 无差异，2422 = 2422） |
| `uabdec` + UnityPy 解 `scripts32` | ✅ 4 张 sharecfg 表全部取出，与旧链路逐字节相同 |
| `ljd` 在 Python 3.11 | ✅ 能装能跑（0.1.7 是 `py3-none-any`） |
| 全量 36 张表 | ✅ 236,150 条 / 1266 s / 失败 0 |
| 新旧链路 21 个产物 | ✅ **全部一致，0 差异** |
| CI 端到端 | ✅ 三轮全绿，缓存命中后解包 50.5 s（25×） |

结论：**解包链路已跑通，不需要再"验证可行性"**。剩下的工作是发布与拆分，
见 §7 的阶段 3 起。

### 仍未验证

| 风险 | 等级 | 说明 |
|---|---|---|
| `data` 发布到 R2 的步骤 | **P0** | `update-data.yml` 新增的三步（装 rclone / 查凭据 / 发布）尚未跑过 |
| R2 上 `data/` 的缓存头 | P1 | 必须是 `max-age=300`，长缓存会让前端读到旧值 |
| `spine` 无法自动生成 | P1 | 1.10 GB，只能从旧仓库搬一次；新船的 Live2D 会缺，需前端 fallback |

**已排除**：原来怀疑"CDN 只有 32 张、旧链路 35 张对不上"。查清了 —— 差的 3 张是
`barrage_template` / `bullet_template` / `weapon_property`（无后缀），是游戏把大表
拆成 `_1/_2/_3` 之前的**遗留快照**，CDN 上本来就没有对应 bundle。而 `build_data_impl.py`
只用 7 张表，一张都不涉及那三个，所以这个差异对产出没有任何影响。

### 4.1 我们的产出和 `AzurLaneLuaScripts` 一样吗

**已经是实测结论，不再是推断**：

| 层 | 结论 | 依据 |
|---|---|---|
| **数据**（json 字段值） | **完全一样** | 21 个产物新旧链路 diff，0 差异（`VERIFICATION.md` 阶段 1） |
| **lua 文本** | 这个样本**逐字节相同** | `cmp` 无差异；但换版本后 `bcdec`/`ljd` 版本差异可能导致文本不同，届时仍以 json 为准 |
| **文件集合** | 少 3 个遗留表 | 见上，不影响产出 |

---

## 5. 部署架构：三个角色，别混

拆分之后一共三样东西，各自只干一件事。**搞混任何一对都会带出多余的成本。**

| 角色 | 是什么 | 干什么 | 谁读它 |
|---|---|---|---|
| `Pelom777/AzurLaneTools` | **前端仓库** | 只放源码。`public/` 会在拆分时清空 | Cloudflare Pages（唯一构建源） |
| `Pelom777/AzurLaneData` | **自动构建仓库**（本仓库） | 跑 Actions：解包 → 构建 → 推 R2。**不对外提供任何内容** | 没有人 |
| R2 bucket | **唯一的内容源** | 19,422 个图片资产 + `data/` 下 21 个 json | 浏览器（经 `cdn.al.pelom.cn`） |

```
     游戏 CDN
        │  解包 + 构建（GitHub Actions，每小时检查）
        ▼
  AzurLaneData ──push──► R2 bucket ──CNAME──► cdn.al.pelom.cn ──┐
        │                                                      │
        └─ 只 commit 数据快照与状态（供下次构建当输入）          │  fetch
                                                               ▼
  AzurLaneTools ──git push──► Cloudflare Pages ──构建──► al.pelom.cn
        （前端源码，不含 public/）                    （js/css/html）
```

三条由此推出的结论：

1. **`AzurLaneData` 的 `public/` 里只有 `data/`**。资产不进工作区（生成在临时目录
   直接推 R2），所以这个仓库始终只有十几 MB。`public/data` 是 `build_data.py`
   的输出目录，同时也是下一轮的 `prev_dir`。
2. **Pages 不连接 `AzurLaneData`**，所以这个仓库公开还是私有，跟站点能不能跑
   没有半点关系。
3. **`AzurLaneTools` 的 `public/` 清空后必须改 `.env.development`**。
   原来 `vite dev` 靠 `public/` 当静态根（`VITE_CDN=http://127.0.0.1:5173`），
   目录没了会 404 一片。改成 `VITE_CDN=https://cdn.al.pelom.cn` 即可 ——
   这是拆分后前端唯一需要改的配置。

---

## 5.1 仓库结构

```
AzurLaneData/                    ← 纯构建机，无 public/
├── .github/workflows/
│   └── build.yml                # 每小时查版本 → 变了才解包 → 构建 → 推 R2
├── docs/
│   ├── HANDOFF.md               # 本文
│   ├── PROMPT.md                # 新任务的提示词
│   └── AUTOMATION.md            # 旧仓库链路文档（资产部分仍有效）
├── scripts/
│   ├── check_version.py         # ★新：azhash 比对，决定要不要干活
│   ├── unpack_lua.py            # ★新：自解包（两条链路）
│   ├── track_new.py             # ★新：new.json 台账
│   ├── sync_r2.py               # ★新：把产物推到 R2（rclone 或 boto3）
│   ├── build_data.py            # 搬入：适配层，不改
│   ├── build_data_impl.py       # 搬入：12 位属性 / 油耗 / transform，不改
│   ├── check_data_contract.py   # 搬入：前端契约门禁，不改
│   ├── check_data_diff.py       # 搬入：字段级差异
│   ├── luatable.py              # 搬入：lua 表解析器
│   ├── common.py                # 搬入：日志 / git / 输出
│   ├── commit.py                # 搬入：只提交明确列出的路径
│   ├── azl_assets.py            # 搬入：图片链路，已实测可用
│   └── legacy/                  # 旧链路，仅回退用
├── tools/
│   ├── bcdec / uabdec           # 32 位 x86 ELF，见 tools/README.md
│   └── README.md
├── data/                        # 状态与台账（不进 R2，只进 git）
│   ├── version.json             # {服务器: {az, version, checked_at}}
│   └── new-ledger.json          # 新增台账的累积记录
├── public/data/                 # 产物：ship / skin / option / ship-attr /
│                                #       new.json / new-latest.json
└── .gitignore
```

`public/data/` 为什么既进 git 又要推 R2：`build_data_impl.build_ship_attr(raw, prev_dir)`
是合并式的，`transform` / `gear_score` 推不出时保留旧值 —— 它需要上一版的
`ship-attr.json` 当输入。所以这个目录不是"发布物"，是**构建输入 + 台账**，
顺便推一份给浏览器读。数据量只有 0.7 MB / 21 个文件，git 装得下。

`scripts/` 里除标 ★ 的，其余从 `azurlane-automation` 原样搬来，**逻辑一行没改**。

---

## 6. 容量：三处存储各放什么

这是本方案最容易翻车的地方，先算账。

| 东西 | 体积 | 去哪 | 理由 |
|---|---|---|---|
| `public/data/*.json`（21 个） | **0.7 MB** | **git + R2 `data/`** | git 当构建输入与台账，R2 给浏览器读 |
| 版本 / 资产状态 | < 100 KB | **git `state/`** | 没有它下一轮就退化成全量 |
| Lua 中间产物（`.lj` / 反编译 `.lua`） | 数百 MB | **`actions/cache`** | 纯中间态，可随时重建 |
| 图片资产 | **6.82 GB / 19,443 文件** | **只进 R2** | 见下 |

### 6.0 三个上限，卡的是不同环节

| 上限 | 数值 | 卡住谁 |
|---|---|---|
| Pages 文件数 | **20,000 / 部署** | 资产绝不能放 Pages —— 现在就 19,443，余量 557 |
| Pages 构建次数 | 500 / 月 | 数据不能放 Pages 仓库 —— 每次数据更新都会触发前端重建 |
| R2 免费存储 | **10 GB-month** | 6.82 GB 资产 + 数据，占 68%，安全 |
| GitHub 私有仓库 Actions | 2,000 分钟 / 月 | 决定 cron 频率；公开仓库无限 |

### 为什么资产不能进 git

- 单文件 100 MB 是**硬拒绝**，50 MB 起警告 —— 大立绘可能踩线
- 仓库 > 1 GB 会收到警告，> 5 GB 基本没法 clone
- 更致命的是**每次热更都会换 md5**：游戏一次大版本更新，可能几千个文件全变，
  一年下来历史轻松几十 GB

### 为什么"每小时 commit"也会撑爆

**实测数据只有 0.7 MB / 21 个文件**，所以"撑爆仓库"这条其实不成立：
即使每小时都 commit，一年 720 次 × 0.7 MB ≈ 500 MB，git 扛得住。

但仍然坚持 cron 只做"检查"，理由换成了另外两条：

- **Actions 分钟**：私有仓库 2,000 分钟/月，720 次空跑就是 720 分钟（每次按
  1 分钟取整），占掉 36%。公开仓库无限，但没必要浪费。
- **提交噪声**：一年 700 多个「什么都没变」的提交，`git log` 就没法看了。

`check_version.py` 先比 `azhash`，没变就直接结束（几百毫秒）。真正 commit 的
频率 = 游戏更新频率（CN 实测约每周 1~2 次），一年 100~200 次。

再加一条保险：**每年做一次孤支重置**（旧历史打 tag 留档）：

```bash
git checkout --orphan clean
git commit -m "chore: 压缩历史"
git branch -M clean master && git push -f
```

资产不进 git（`azl_assets.py` 生成到临时目录后 `rclone` 同步到 bucket），
仓库里只留 `.azl/assets-state.json`（见 `CDN.md` §5.4）。

### 6.0.1 资产推 R2 的两个细节

**用 rclone，别手写 S3 客户端。** R2 兼容 S3，rclone 自带并发、断点续传、校验：

```bash
rclone sync out/assets r2:azl-cdn \
  --transfers 32 --checkers 32 --fast-list \
  --size-only                       # 关键，见下
```

`--size-only` 不是偷懒：`azl_assets.py` 已经用 md5 判断过"要不要重新生成"，
没变就不会重写文件，所以**大小相同 = 内容相同**。而 rclone 默认按 mtime 比对，
本地文件的 mtime 是生成时间、R2 对象的 mtime 是上传时间，两个永远对不上 ——
不加 `--size-only` 会导致**每次全量重传 19,443 个文件**。

**操作数完全够。** 首次全量约 19,443 次 PutObject = 19,443 Class A，
限额 100 万/月；日常增量通常 0~200 个。浏览器读走 Cloudflare 缓存后，
Class B 基本不怎么消耗。

> 如果 R2 还没配好，退路是用 **GitHub Release** 挂资产包
> （每个版本一个 release，`gh release upload`），仍然比进 git 强得多。
> 但那会把每个版本打成几个大包，增量更新体验差，只适合过渡。

### 6.1 两种"全量"要分开看

| | 触发 | 体积 | 应对 |
|---|---|---|---|
| **首次全量** | 仓库刚建，state 为空 | 19,443 文件 / 6.82 GB | `--seed` + 分批 |
| **定期全量** | 游戏大版本更新，CDN 上 md5 大面积更换 | 不可预测，可能几千个 | 靠 state 的 md5 比对，只下变了的 |

**首次全量**在托管 runner 上是有风险的：`ubuntu-latest` 可用磁盘约 14 GB，
6.82 GB 资产 + 3~4 GB 中间文件 + 代码，余量很小。所以：

- `azl_assets.py` 已支持 `--items` 按类别分批（`painting` / `squareicon` /
  `shipicon` / `equipicon` / `paintingface` / `iconframe` 共 6 类）。
  **`spine` 不在支持范围**（脚本里没有任何 spine 代码），只能从旧仓库搬一次，
  见 `docs/CDN.md` §2
- 首次建议**分 3~4 次 run**，每次只跑一两个大目录，state 会自动累积
- `--seed` 的语义是"文件已在磁盘、但 state 没这条记录 → 补记录不下载"；
  如果资产根本不在磁盘上，`--seed` 帮不上忙，只能真下

**定期全量**不需要特殊处理，但要接受一个现实：一次大版本更新可能让
`painting` 的 md5 换掉一半，那一次 run 会跑满 55 分钟。所以：

- `concurrency` 已经设成不取消进行中的任务，避免两次更新撞车
- 图片链路的 `timeout-minutes` 要给足（现在 workflow 里的 55 分钟是给数据链的）
- 如果服务器可写，资产应该**边生成边 rsync**，而不是全下完再传

> 数据链路的"全量"完全不是问题：只有 32 个 `sharecfgdata`（合计约 100 MB）
> 加一个 39 MB 的 `scripts32`。真正贵的是反编译，而反编译有 md5 缓存。

---

## 7. 分阶段实施

> **阶段 0 / 0.5 / 1 / 2 已完成**，数字固化在 `docs/VERIFICATION.md`，
> 原始证据在 `p0-artifacts` / `stage1-artifacts` / `scripts32-artifacts` 三个分支。
> 下面保留它们是因为**换版本或换机器时可能需要重跑**，不是待办。

### 阶段 0：打通解包 ✅

本地能从一个 `sharecfgdata` bundle 拿到 lua 源码。

```bash
pip install -r requirements.txt      # UnityPy + ljd（版本已锁定）
chmod +x tools/bcdec tools/uabdec    # 静态 32 位 ELF，实测不需要 libc6:i386
# 纯 Windows 跑不了 ELF：用 WSL，或者直接交给 CI
# （dispatch update-data.yml，它自带 force / dry_run 两个开关）

python scripts/unpack_lua.py --server CN --out raw --work work --only weapon_name
```

验收：

- [x] `raw/weapon_name.json` 生成，2422 条
- [x] `failed` 为 0
- [x] 与旧链路对比：**逐字节相同**（`cmp` 无差异）

**卡住时的排查顺序**：`bcdec` 返回码 → `bcdec` 输出前 4 字节是否为 `\x1bLJ`
→ `ljd` 是否抛异常 → 分段数是否为 0。

### 阶段 0.5：迁基线数据 ✅

`build_data_impl.py` 的 `build_ship_attr(raw, prev_dir)` 是**合并式**的：
能从游戏推的字段覆盖，推不出的（`transform` / `gear_score` 等）保留旧值。
如果 `public/data` 是空目录，这些字段会静默退化成"不存在"。

```bash
git clone --depth=1 https://github.com/Pelom777/AzurLaneTools.git /tmp/front
cp -r /tmp/front/public/data/. public/data/
git add public/data && git commit -m "chore: 迁入基线数据"
```

验收：**21 个文件 / 781 KB**，与前端仓库一致。

### 阶段 1：全量数据 ✅

```bash
python scripts/unpack_lua.py --server CN --out raw --work work
python scripts/build_data.py --raw raw --out public/data
python scripts/check_data_contract.py --data public/data
```

验收：

- [x] 36 张表 / 236,150 条 / 1266 s / 失败 0
- [x] 契约校验全通过（ship 897 / skin 2491 / ship-attr 897，键集对齐）
- [x] **新旧链路 21 个产物全部一致，0 差异**
- [x] 与仓库旧数据的差异均为预期（108 艘新船、2 个改名/改类、3 个已删皮肤）

### 阶段 2：CI ✅

- [x] `update-data.yml` 手动触发（`force=true`），全绿
- [x] 不带 force 再触发，`check_version` 判定未变，**15 秒**结束，无空提交
- [x] `actions/cache` 命中后解包 **50.5 s**（对比 1265.9 s，25×）

### 阶段 2.5：data 发布到 R2 ⬅ **待验证，这是当前唯一 P0**

`update-data.yml` 新增了三步（装 rclone / 查凭据 / `rclone copy public/data`），
**还没跑过**。验收：

- [ ] 跑一次 `force=true, dry_run=false`，R2 的 `data/` 出现 21 个对象
- [ ] `curl -I https://<cdn>/data/ship.json` 返回 `Cache-Control: public, max-age=300`
- [ ] 内容与本地 `public/data/ship.json` 逐字节一致
- [ ] 同一个 json 第二次跑不会重复上传（`--checksum` 生效）

### 阶段 3：资产与拆分

- [ ] `azl_assets.py --seed` 首次播种，`seeded ≈ 19,443`，`new = 0`
- [ ] 第二次不带 `--seed`，`new` 仍为 0（state 接上了）
- [ ] `AzurLaneTools` 删 `public/`，`vite.config.ts` 加 `copyPublicDir: false`
- [ ] `.env.development` 改 `VITE_CDN=https://cdn.al.pelom.cn`
- [ ] 站点全量回归：图片全部来自 `cdn.al.pelom.cn`，数据面板无 NaN / 白屏

### 阶段 4：切换

- [ ] 新仓库连续 3 天 cron 正常
- [ ] 停掉 `azurlane-automation` 的 `update-data.yml`
- [ ] `fetch_lua_tables.py` 标记 `@deprecated`，保留但不再被调用

---

## 8. new.json 设计

> **实测修正（2026-10-21）**：下面这版把 `new.json` 当台账，**和前端冲突**。
> 前端首页 `src/views/home/index.vue` 把 `new.json` 读成
> `{cg:[图片url], ship:[painting], skin:{皮肤:船}}`（轮播 + 新增角标），
> 台账形状会让 `info['cg']` 变成 undefined，首页直接白。
> 所以实际实现是**拆成两个文件**：
>
> | 文件 | 形状 | 谁读 |
> |---|---|---|
> | `data/new-ledger.json` | 下面这个累积台账 | 只有 `track_new.py` |
> | `public/data/new.json` | `{cg, ship, skin}`，前端契约 | 前端首页 |
>
> 建基线那一轮不会清空 `ship`/`skin`，而是原样保留旧值；只有真的算出新增才替换。

```jsonc
// data/new-ledger.json —— 累积台账（不进 public/）
{
  "version": "9.7.398",          // 最近一次记录的版本
  "updated": "2026-10-21T10:00:00Z",
  "baseline": false,             // true 表示"台账起点"，此前的条目不标时间
  "ships": {
    "shinano": { "name": "信浓", "type": 18, "rarity": 6, "first_seen": "2026-10-21T10:00:00Z" }
  },
  "skins": {
    "shinano_2": { "name": "信浓·泳装", "ship": "shinano", "first_seen": "2026-10-21T10:00:00Z" }
  }
}
```

外加 `public/data/new-latest.json`，只装本次新增（`{version, updated, ships, skins}`），
供日志/审计用；前端目前不读它。

三个设计取舍：

1. **累积台账，不是只存本次** —— 只存本次的话，一旦漏跑一次，那批新增永久丢失。
   累积台账每条带 `first_seen`，任意时间窗的增量都能现算。
2. **不读旧版 data 判断新旧** —— 台账自己就是判据（id 不在台账里 = 新），
   省掉 `git show HEAD:` 这个失败面。
3. **首次运行只建基线** —— 不把现有 900 条全标成今天新增，否则前端亮成一片。
   基线条目的 `first_seen` 为 `null`，表示"早于台账起点"。

---

## 9. 已知坑

| 坑 | 表现 | 处理 |
|---|---|---|
| 32 位 ELF 缺解释器 | `No such file or directory`（文件明明在） | 只影响**动态**链接的 32 位 ELF；`tools/` 里这两个是静态的，实测不需要 i386 运行库 |
| `sharecfgdata` 误用 UnityPy | `UnityPy.load` 抛异常 / 0 objects | 它就不是 UnityFS，走 uleb128 分段 |
| `scripts32` 忘了 uabdec | UnityPy 也打不开，头不是 `UnityFS` | 先 `uabdec` |
| 反编译拼接方式 | 拼字节码再一次性反编译会失败 | **实测反之**：`sharecfgdata` 每段都是完整 dump，逐段反编译再拼文本（见 §3 修正） |
| `bcdec` 输出首尾包装 | 前 5 字节 + 尾 1 字节是容器 | **实测反之**：输出就是完整 dump，`out[5:-1]` 会把头剥掉导致 ljd 报 `constants` 错 |
| `actions/cache` 是仓库级 | 换仓库后 state 清零 → 全量重下 | `azl_assets.py --seed` |
| 资产误提交 | `git add -A` 把未落盘的资产当删除 | 只用 `commit.py --paths` |

---

## 10. 参考来源

| 主题 | 位置 |
|---|---|
| 解包参考实现 | `AzurLaneTools/AzurLaneExtractor`，`az_ex/tasks/scripts.py` |
| 字节码 / 整包解密 | `azurlane-tools/AzurLaneDecTools`（`uabdec.cpp` / `AssetDecoder.cpp`） |
| 官方实际跑法 | `AzurLaneTools/AzurLaneLuaScripts` 的 `.github/workflows/update.yml`（走私有 Docker 镜像） |
| 旧链路文档 | `docs/AUTOMATION.md` |
| 前端契约 | `scripts/check_data_contract.py` 文件头注释（逐条对应 `src/views/**` 的真实读法） |
