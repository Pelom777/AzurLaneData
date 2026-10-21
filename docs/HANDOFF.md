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

### 未验证（新任务里必须最先打掉的）

| 风险 | 等级 | 怎么验 |
|---|---|---|
| `bcdec` 能否还原 9.7 的字节码 | **P0** | 解开一个 `sharecfgdata`，看 `ljd` 能否出源码 |
| `ljd` 反编译质量与耗时 | **P0** | 先只跑 `ship_data_statistics`，记录耗时与条数 |
| `uabdec` 能否解开 9.7 的 `scripts32` | **P0** | 解开后 UnityPy 能否 load、TextAsset 数量是否合理 |
| `ljd` 0.1.7 在 Python 3.11 能否装 | P1 | `pip install` 试；不行就降 3.8（官方用 3.8） |
| 反编译出的 lua 是否被 `luatable.py` 吃下 | P1 | `parse_text` 返回的表数与字段是否齐全 |

**已排除**：原来怀疑"CDN 只有 32 张、旧链路 35 张对不上"。查清了 —— 差的 3 张是
`barrage_template` / `bullet_template` / `weapon_property`（无后缀），是游戏把大表
拆成 `_1/_2/_3` 之前的**遗留快照**，CDN 上本来就没有对应 bundle。而 `build_data_impl.py`
只用 7 张表，一张都不涉及那三个，所以这个差异对产出没有任何影响。

### 4.1 我们的产出和 `AzurLaneLuaScripts` 一样吗

分三层，别混为一谈：

| 层 | 结论 | 依据 |
|---|---|---|
| **数据**（json 里的字段值） | **应当完全一样** | 同一个 bundle（md5 相同）、同一条链路（uleb128 分段 → bcdec → ljd）。已用一个样本交叉验证：219 段 = 219 条 |
| **lua 文本**（中间产物） | **不保证逐字节一样，也不重要** | `bcdec` 版本可能不同（本地是 2023 年的，官方镜像可能是新版）、`ljd` 版本不同、浮点格式化差异 |
| **文件集合** | 少 3 个遗留表 | 见上，不影响产出 |

所以验收不能比 lua 文本，要比 **json**：

```bash
# 同一版本下，新链路与旧链路各跑一次，diff 两份 json
python scripts/unpack_lua.py --server CN --out raw_new --work work
python scripts/legacy/fetch_lua_tables.py --server CN --dst /tmp/lua --out raw_old
python scripts/build_data.py --raw raw_new --out out_new
python scripts/build_data.py --raw raw_old --out out_old
diff -r out_new out_old        # 期望：无差异，或只有已知的游戏侧改动
```

这条命令是阶段 1 的**真正验收标准**，比对条数更能说明问题。
若出现差异，优先怀疑 `bcdec` 版本 —— 那是唯一一个"输入相同但行为可能变"的环节。

**P0 全部通过之前，不要动 `AzurLaneTools` 的 `public/`。** 现有链路是保底方案。

---

## 5. 仓库结构

```
AzurLaneData/
├── .github/workflows/
│   └── update-data.yml          # 每小时：查版本 → 变了才解包 → 建数据 → 提交
├── docs/
│   ├── HANDOFF.md               # 本文
│   ├── PROMPT.md                # 新任务的提示词
│   └── AUTOMATION.md            # 旧仓库的链路文档（资产部分仍然有效）
├── scripts/
│   ├── check_version.py         # ★新：azhash 比对，决定要不要干活
│   ├── unpack_lua.py            # ★新：自解包（两条链路）
│   ├── track_new.py             # ★新：new.json 台账
│   ├── build_data.py            # 搬入：适配层，不改
│   ├── build_data_impl.py       # 搬入：12 位属性 / 油耗 / transform，不改
│   ├── check_data_contract.py   # 搬入：前端契约门禁，不改
│   ├── check_data_diff.py       # 搬入：字段级差异
│   ├── luatable.py              # 搬入：lua 表解析器
│   ├── common.py                # 搬入：日志 / git / 输出
│   ├── commit.py                # 搬入：只提交明确列出的路径
│   ├── azl_assets.py            # 搬入：图片链路（立绘 / 图标），已实测可用
│   └── legacy/                  # 旧链路（sparse-checkout 拉第三方仓库），仅回退用
│       ├── fetch_lua_tables.py
│       └── sync_upstream.py
├── tools/
│   ├── bcdec / uabdec           # 32 位 x86 ELF，见 tools/README.md
│   └── README.md
├── data/
│   ├── version.json             # {服务器: {az, version, checked_at}}
│   └── new-ledger.json          # new.json 的累积台账（前端不读）
├── public/data/                 # ★ 产出：ship / skin / option / ship-attr / new.json
└── .gitignore                   # 资产目录全部忽略
```

`scripts/` 里除标 ★ 的四个，其余是从 `azurlane-automation` 原样搬来的，
**逻辑一行没改**。改动只发生在外部接口层。

---

## 6. 容量：GitHub 放得下什么

这是本方案最容易翻车的地方，先算账。

| 东西 | 体积 | 进 git？ |
|---|---|---|
| `public/data/*.json` | 十几 MB | ✅ 必须进（前端要读，且需要历史） |
| `new.json` | < 1 MB | ✅ 进 |
| Lua 中间产物（`.lj` / 反编译 `.lua`） | 数百 MB | ❌ 进 `actions/cache` |
| 图片资产（`painting/` `spine/` 等 16 个目录） | **6.82 GB / 19,443 文件** | ❌ **绝对不能进** |

### 为什么资产不能进 git

- 单文件 100 MB 是**硬拒绝**，50 MB 起警告 —— 大立绘可能踩线
- 仓库 > 1 GB 会收到警告，> 5 GB 基本没法 clone
- 更致命的是**每次热更都会换 md5**：游戏一次大版本更新，可能几千个文件全变，
  一年下来历史轻松几十 GB

### 为什么"每小时 commit"也会撑爆

数据 json 每次改 1~3 个文件、约 5 MB 新 blob。若真的每小时 commit 一次，
一年就是 8760 × 5 MB ≈ 43 GB。

**所以 cron 只做"检查"，不无条件 commit。** `check_version.py` 先比 `azhash`，
没变就直接结束（几百毫秒，零成本）。真正 commit 的频率 = 游戏更新频率
（CN 实测约每周 1~2 次），一年约 100~200 次，累计几 GB 以内。

再加两条保险：

1. **每年做一次孤支重置**（旧历史还在 `AzurLaneData-legacy` 备份 tag 里）：
   ```bash
   git checkout --orphan clean
   git commit -m "chore: 压缩历史"
   git branch -M clean master && git push -f
   ```
2. **资产走服务器，不走 git**：`azl_assets.py` 生成到 `public/` 后直接
   `rsync` 到 `cdn.al.pelom.cn` 的根目录，CI 只传增量（脚本已有 state 文件）。
   仓库里只留 `assets-state.json`。

> 如果暂时没有服务器可写，退路是用 **GitHub Release** 挂资产包（每个版本一个
> release，`gh release upload`），仍然比进 git 强得多。

### 6.1 两种"全量"要分开看

| | 触发 | 体积 | 应对 |
|---|---|---|---|
| **首次全量** | 仓库刚建，state 为空 | 19,443 文件 / 6.82 GB | `--seed` + 分批 |
| **定期全量** | 游戏大版本更新，CDN 上 md5 大面积更换 | 不可预测，可能几千个 | 靠 state 的 md5 比对，只下变了的 |

**首次全量**在托管 runner 上是有风险的：`ubuntu-latest` 可用磁盘约 14 GB，
6.82 GB 资产 + 3~4 GB 中间文件 + 代码，余量很小。所以：

- `azl_assets.py` 已支持 `--items` 按类别分批（`painting` / `squareicon` / `spine` …）
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

### 阶段 0：打通解包（**先做这个，其余都靠它**）

目标：本地能从一个 `sharecfgdata` bundle 拿到 lua 源码。

```bash
# 环境
pip install -r requirements.txt      # UnityPy + ljd（版本已锁定）
chmod +x tools/bcdec tools/uabdec    # 静态 32 位 ELF，实测不需要 libc6:i386
# 纯 Windows 跑不了 ELF：用 WSL，或者直接交给 CI（p0-probe.yml）

# 只解一张最小的表
python scripts/unpack_lua.py --server CN --out raw --work work --only weapon_name
```

验收：

- [ ] `raw/weapon_name.json` 生成，条目数 > 1000
- [ ] 日志里出现 `weapon_name: 1 表 / N 条`，`failed` 为 0
- [ ] 与旧链路对比：`fetch_lua_tables.py` 产出的 `weapon_name.json` 条数一致

**卡住时的排查顺序**：`bcdec` 返回码 → `bcdec` 输出前 4 字节是否为 `\x1bLJ`
→ `ljd` 是否抛异常 → 分段数是否为 0。

### 阶段 0.5：先迁基线数据（**不能跳过**）

`build_data_impl.py` 的 `build_ship_attr(raw, prev_dir)` 是**合并式**的：
能从游戏推的字段覆盖，推不出的（`transform` / `gear_score` 等）保留旧值。
如果 `public/data` 是空目录，这些字段会静默退化成"不存在"。

所以搬家顺序必须是：

```bash
# 从 AzurLaneTools 把现有数据搬过来
git clone --depth=1 https://github.com/Pelom777/AzurLaneTools.git /tmp/front
cp -r /tmp/front/public/data/. public/data/
git add public/data && git commit -m "chore: 迁入基线数据"
```

验收：`git ls-files public/data | wc -l` 与前端仓库一致（21 个文件）。

### 阶段 1：全量数据

```bash
python scripts/unpack_lua.py --server CN --out raw --work work
python scripts/build_data.py --raw raw --out public/data
python scripts/check_data_contract.py --data public/data
```

验收：

- [ ] 7 张表齐全（`sharecfgdata` 里 3 张 + `scripts32` 里 4 张）
- [ ] 4 个 json 全部生成，`check_data_contract` 通过
- [ ] **新旧链路互比无差异**（见 §4.1 的四条命令）—— 这条最重要
- [ ] 与仓库现有 `public/data` 做 `check_data_diff.py`，差异只应该是游戏侧真实改动
      （已知：`siwangzhuzai` 改名、`huangjiacaifu` type 22→23、36 艘潜艇油耗 +1）

### 阶段 2：CI 跑通

- [ ] `update-data.yml` 手动触发一次（`force=true`），全绿
- [ ] 再触发一次不带 force，`check_version` 判定未变，秒级结束
- [ ] `actions/cache` 命中的 run 里，反编译耗时显著下降

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
