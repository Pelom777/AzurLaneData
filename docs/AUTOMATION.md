# AzurLaneTools 自动更新流水线

## 一、先说四个结论

**1. 公开渠道已经没有可用的图片提取器，但代码还在你的凭据里。**

维护者说得很清楚：新方案是重新实现的 docker 镜像，**只出 json/lua，不做图片提取，也没上传 GitHub**。
`AzurLaneExtractor` 在公开 API 上是 404 —— 但用你自己的 git 凭据可以完整拉下来。
最后提交是 **2023-04-23**，里面 `az_ex/tasks/extract_image.py`、`extract_painting.py` 一应俱全，
**图片提取从来就在这个仓库里，只是"新方案"把它砍了**。

所以真正要回答的问题不是"去哪儿找提取器"，而是"这套 2023 年的代码，能不能对上 2026 年的游戏"。
我把这个问题实测掉了：能，但需要改。见第八节。

**2. 数据链根本不需要提取器 —— `LuaScripts` 里有原始配置表。**

`AzurLaneLuaScripts` 至今仍在每小时更新，而它的 `CN/sharecfgdata/*.lua` 里就是
`ship_data_template`、`ship_skin_template`、`equip_data_template`、`weapon_property` 这些表。
`AzurLaneData` 的 json 本来就是从这里导出来的。也就是说：

> 数据源不是"停了 5 个月"，而是"你一直盯错了仓库"。

这批 lua 是机械导出的纯表字面量，没有函数、没有循环、没有 `require`，**不需要 Lua 运行时**，
纯 Python 就能解析（已实测：5 MB 表 0.6 秒，中文/嵌套数组/空数组全部正确）。
所以 `update-data` 现在默认走 `SOURCE=lua`，数据新鲜度从"5 个月"变成"几小时"。

**3. 资产链也能全自动，而且不需要任何私有镜像或自托管机器。**

`scripts/azl_assets.py` 直接从游戏 CDN 取包、自己解 Unity 资源。整条链只有 Python + UnityPy +
opencv，托管 runner 就能跑。已实测：立绘能完整合成（背景 / 舰装 / 表情层都在），图标能批量导出，
文件名与站点现有文件一致。原理与实测数据见第八节。

| 环节 | 现在 | 能不能全自动 | 卡在哪 |
|---|---|---|---|
| 原始配置表（数据） | ✅ 已有活源 | ✅ 能 | 无（`SOURCE=lua`） |
| `public/data`（21 个 json） | 你本地脚本手工跑 | ✅ 能 | 需要把脚本接进来 |
| lua 脚本 | 上游仍在更新 | ✅ 能 | 无 |
| 立绘 / 图标 / 表情 | 你手工整 | ✅ 能 | 首次回填要分批（磁盘限制） |
| spine（小人动画） | 你手工整 | ❌ 还没做 | 是 skel/atlas，不是贴图，要另写解析 |
| 提交 / 推送 | 手动 | ✅ 能 | `GITHUB_TOKEN` 自带 |
| 部署 | 手动 | ✅ 能 | 取决于你服务器怎么布 |

**4. 6.8 GB 仓库不能整仓 checkout，但两条链都不需要整仓。**

19,508 个文件里 19,443 个是资产（6.82 GB），代码和数据只有 236 KB。
数据链用 `blobless + sparse`，资产链用 `blobless + 只 checkout 代码`，
按"本次写了哪些文件"逐个 `git add`。细节见第五节。

---

## 二、数据源的真实情况

这个项目的上游生态比较绕，先把关系厘清，后面所有判断都建立在这上面：

| 仓库 | 状态 | 内容 | 对本方案的意义 |
|---|---|---|---|
| `AzurLaneLuaScripts` | **活跃**，2026-10-17 仍在每小时提交 | `CN/` `EN/` 全量 lua，含 `sharecfgdata/` 原始表 | **数据链的活源** |
| `AzurLaneData` | **停更**，2026-05-08 | 五服 json，约 425 MB/服 | 已降级为 `SOURCE=data` 备选 |
| `AzurLaneExtractor` | 公开 404，**你的凭据可读** | 2023-04 的 json+lua+**图片**提取代码 | **资产链的参考实现** |
| 新 docker 方案 | 私有，未上传 | json + lua，**不含图片** | 不可用，除非维护者给镜像 |

`sharecfgdata` 里可直接用的表（实测）：`ship_data_template`（3,972 条）、
`ship_data_statistics`（4,123）、`ship_skin_template`（2,871）、`ship_data_breakout`（3,483）、
`weapon_name`（2,422），另有 `equip_data_*`、`weapon_property*`、`item_data_statistics`、
`ship_skin_words` 等共 35 个文件、约 180 MB。

**`sharecfg/` 也能拉，只是格式不同**（`pg.base.X[...]` 而非 `_G.pg.base.X[...]`，且文件头有
`setmetatable(...)` 之类的非数据语句）。整个目录 100 MB，所以用 `--no-cone` 按文件精确取，
目前取两张：`ship_data_strengthen`（863 条，属性强化上限）和 `attribute_info_by_type`
（12 项，固化 attrs 索引语义）。这两张让 `ship-attr` 从「做不到」变成了 100% 对齐。

**实测（2026-10-20，CN @ `5822b089`）：35/35 个表全部解析成功，333,662 条记录，
29 秒，0 失败。** 其中 `weapon_property` 文件尾部附了一段反编译代码，会被截断但数据部分完整保留。

**局限性要写清楚：**

- `LuaScripts` 只有 `CN` 和 `EN`，没有 JP/KR/TW。你的 `public/data` 里那些 `2017-jp.json`
  之类的历史分服文件，如果要继续保留，只能靠 `SOURCE=data` 或手工维护。
- 解析器针对这一种导出格式写成。上游如果哪天改了导出方式（加了函数调用、改成二进制），
  `fetch_lua_tables.py` 会把失败的表名打出来并跳过，不会静默产出半套数据。
- **图片不在 lua 里。** 表里只有 id 和资源名，立绘本体得靠资源包，这是两件独立的事。
  后者已用另一条链解决（第八节），两条链用同一份 id，靠 `public/data` 关联。

---

## 三、交付了什么

```
.github/workflows/update-data.yml     数据链：配置表 → public/data（每小时，零凭据）
.github/workflows/update-assets.yml   资产链：游戏 CDN → public/（每 6 小时，零凭据）
scripts/luatable.py                   lua 表 → Python 的解析器（纯标准库，两种目录格式都支持）
scripts/fetch_lua_tables.py           blobless 拉 LuaScripts，解析成每表一个 json
scripts/sync_upstream.py              blobless 拉 AzurLaneData（SOURCE=data 时用）
scripts/check_freshness.py            数据源停更判定
scripts/build_data.py                 public/data 适配层（调度 + 原子写盘 + 旧数据合并）
scripts/build_data_impl.py            ★ 从现有 data 反推出的推导实现（ship/skin/option/ship-attr）
scripts/data/namecode.json            ★ {namecode:N} → 国服和谐名基线（286 条，构建期资源）
scripts/check_data_diff.py            ★ 推导结果与仓库现有 data 的逐字段对照
scripts/check_data_contract.py        ★ public/data 的结构契约自检（防「前端会崩」，CI 里会跑）
scripts/azl_assets.py                 ★ 资源包解码：立绘 / 图标导出（UnityPy）
scripts/painting_site.py              ★ 立绘三件套写出（json + png + mesh.obj）
scripts/check_site_format.py          ★ 产出格式自检（防格式漂移，CI 里会跑）
scripts/commit.py                     只提交指定路径的 commit & push（支持文件清单）
scripts/common.py                     git / JSON / GH 输出等公共件，纯标准库
scripts/deploy.py                     webhook 触发部署（可选）
scripts/sync_assets.py                外部提取产物的增量同步（走 AZL_EXTRACT_CMD 时才用）
scripts/extract.py                    调用本机提取器（兜底方案，现已不需要）
```

两条链共用一把并发锁（`concurrency: azl-pipeline`），因为它们都写 `public/`，绝不能并行。


---

## 四、`public/data` 的情况（已提供实现，但有边界）

### 1. `scripts/build_data_impl.py`（已写好，覆盖 3 个文件）

这个文件是**从仓库现有 21 个 json 反推**出来的，不是照抄你的旧脚本。规则、证据、
偏差全部写在文件头的注释里。结论先摆在这里：

| 文件 | 结论 | 实测（CN 9.7.398 / lua 5822b089） |
|---|---|---|
| `ship.json` | ✅ 可推导 | 789 个键全覆盖，逐字段全等 **787/789（99.7%）** |
| `skin.json` | ✅ 可推导 | 内层完全一致 **668/789**；**无内容冲突 788/789**（差额全是新增皮肤） |
| `option.json` | ✅ 静态表 | **逐字节完全一致** |
| `ship-attr.json` | ✅ 可推导 + 保留人工 | 除 `base[6]` 外逐字段全等 **789/789**；`base[6]` 有 36 条潜艇旧值少 1（见下） |
| `ship-equip.json` | ⚠️ 部分 | 满星级 id 下 503/597（84%），92 处差异全在 `base[0]`，属**取法 bug** 非数据缺失，默认不启用 |
| 其余 16 个 | ❌ 不产出 | 人工维护 / 需 35 表之外的数据，见第 3 小节 |

`ship-attr` 这张表值得单独说，因为它是「游戏数据 + 人工字段」混在一起的典型，只能做**字段级合并**：

| 字段 | 来源 | 覆盖率 |
|---|---|---|
| `growth` | 满破形态（尾号 4）的 `attrs_growth` | 100% |
| `base[i≠6]` | 满破形态的 `attrs[i]` | 100% |
| `base[6]` | 满破形态的 `oil_at_end` | 100%（满破口径下公式退化为 `oil_at_end`；旧数据 36 条潜艇少 1） |
| `strengthen` | `sharecfg/ship_data_strengthen[strengthen_id].durability` | 普通船 663/789 |
| `transform` | 改造节点 `transform_data_template.effect` 逐项累加 | **101/101 逐位全等** |
| `gear_score` | 同一批节点的 `gear_score` 数组求和（改造技能战力） | **101/101 全等** |

三个容易踩的点：

- **`base[6]` 不是 `attrs[6]`**。`attribute_info_by_type` 里这一位叫 `armor`（装甲），游戏侧恒为 0；
  站点把它当「消耗」展示，实际取的是模板表的 `oil_at_end`。游戏公式（等级系数
  `0.5 + min(lv,99)×0.005`，满级恒 `0.995`）：

  | 类型 | 公式 |
  |---|---|
  | 水面舰 | `floor(1 + (最终油耗 − 2×未突破次数) × 等级系数)` |
  | 潜艇 | `floor(0 + (最终油耗 + 1 − 1×未突破次数) × 等级系数)` |

  未突破次数 = 0（满破）时两式都等于 `最终油耗`，即满破形态的 `oil_at_end` ⇒ **无条件可推导**。
  旧仓库对 36 艘潜艇记的是 2 破形态的值（`F − 1`），套公式后会 +1：这是**取错突破形态**
  而非人工口径（早期误判成「保留旧值」，已改为直接覆盖）。

  但「满破形态」不能按字面取尾号 `4`，要按组内结构定位（897 组全部命中，无一组落空）：

  | 顺序 | 取法 | 命中 | 说明 |
  |---|---|---|---|
  | ① | `max_ship_id`（组内星数最高的真实形态） | 873 | 实测恒为尾号 4 |
  | ② | `base_id + 3`（本体尾号 4） | 24 | 9xxxxx 未实装组只有替身 id，被 ① 排除 |
  | ③ | `base_id`（唯一形态） | 3 | `gin` / `kin` / `buli_super` 没有突破档 |
  | ④ | 旧值；仍无则不写 | 0 | 写 `0` 会让前端把消耗显示成 0 |

  **不能倒过来先查 ②**：有 4 艘特殊船（皇家财富号、白上吹雪、朱利奥·凯撒·META、DEAD MASTER）
  组内混进了别组的尾号 4，按字面 `+3` 会取到错形态，偏差可达 `−12`。
- **`transform` / `gear_score` 不是「无来源」**，只是藏在改造节点表里。链路是
  `ship_data_trans[group].transform_list` 拿节点 id，再到 `transform_data_template[id]`：
  `effect` 是「按第 N 级排列的属性增量数组」，逐项累加就是 `transform`；
  `gear_score` 同样按级排列，**求和**就是站点综合性能公式末项的「改造技能战力」。
  属性名经 `attribute_info_by_type.name` 落到 12 位下标（游戏 1-based、站点 0-based）。
  「战术启发」节点只有 `skill_id`，不参与属性累加，但战力照样计入。
  仓库 101 艘逐位全等。另有 8 艘（`moli` / `aisaikesi` / `yonggan` / `beierfasite` /
  `bailu` / `xipeierhaijunshangjiang` / `yingrui` / `zhaohe`）表里能推、仓库没录，
  默认不新增 —— 前端用「有没有 `transform`」判断改造是否开放，贸然补会直接显示改造后数值。
  确认实装后把 `build_data_impl.ADD_NEW_TRANSFORM` 置 `True`。
- **方案舰（90 艘）整段保留旧值**。它们各形态 `attrs` 完全相同，满破属性根本不在表里；
  仓库值是 `attrs × 2.4635` 量级的另一套口径，而且每个索引的系数都不一样，反推不出来。
- **`_alter`（49 艘）同样保留旧值**。9xxxxx 段的改造形态，仓库值比表里满破 `attrs` 大约 1.4 倍，
  同样每位系数不同。

因此这个构建器要读一次仓库现有文件 —— `build_data.py` 会把 `--out` 目录作为 `prev_dir` 传给
`build()`（靠参数名识别），所以**跑之前不能清空 `public/data`**。

`ship.json` 那两个不一致都要说一下，都是**游戏侧改动**不是推导错：
`siwangzhuzai` 仓库叫 `DEAD MASTER`、现在叫「死亡主宰」；`huangjiacaifu` 仓库 type 22、现在 23。

`ship-equip` 那 92 处也要说清楚，因为它**不是数据缺失**：全部落在 `base[0]`，方向一致
（我恒为 1，仓库是 2 或 3：86 处 3、6 处 2），`base[1]`/`base[2]` 和数组长度完全一致，
slot 只有 2 处不同。根因是 `max_ship_id()` 用 `group_type` 归组去找「满改造」id，
而改造形态的 group_type 并不总与本体相同 —— `101021(10102) → 101121(10112)` 是 **+10**，
`206034(20603) → 206134(20603)` 却**相同**。规律不统一，部分船的改造形态没进
`group_ids[group]`，base_list 就停在改造前的值上。

`skin.json` 的口径要分两层看，否则容易误判：

| | 组数 | 含义 |
|---|---|---|
| 内层完全相等 | 668 | 皮肤集合和名称都一致 |
| 只有「新增皮肤」 | 120 | 仓库快照（2025-06）之后游戏新出的皮肤，属正常增量 |
| 内容不一致 | 1 | `buli_super` 的尾空格，游戏侧删掉了，属正常跟随 |
| **仓库有我无** | **0** | 推导不会丢皮肤（除了 3 个已下线的机器人） |

#### `{namecode:N}` 和尾空格：两件容易搞错的事

**一、namecode 是客户端的全局术语表，不是「舰船名」字段。**

`{namecode:N}` 里的 N 指向一个**完整显示名**，存的是国服和谐名（日/德舰船被改名的结果）：
`257 → 伊490`、`528 → 圣地亚哥.改`、`524 → 圣地亚哥`。同一个编号只对应一个实体，所以
524 和 528 是两艘不同的「圣地亚哥」。这张表不在 `sharecfgdata` 的 35 张表里，拿不到原文，
只能反解。两条独立路径交叉验证、**0 冲突**：

| 路径 | 做法 | 条数 | 盲区 |
|---|---|---|---|
| A 默认皮肤自举 | 组内 skin id 最小的皮肤名若为 `{namecode:N}`，则 N 就是本船名 | 284 | **一次只能解出默认皮肤那一个编号**；同船的原版/改型/小形态编号拿不到。257 甚至不是时雨自己（台词里提到别的船） |
| B 仓库对齐 | 拿 `public/data/skin.json` 里已解析的中文名按占位符位置反推 | 261 | 只能覆盖仓库见过的 |

合并后 **286 条**，固化在 `scripts/data/namecode.json`，运行时再用 A 覆盖一遍（游戏改名时
A 会立刻反映，冲突会告警）。剩下 555/556 属于 `900xxx` 替身段，对应组会被
`base_ship_id` 整组丢弃，不会出现在产出里。

这条链路**不能只靠 A**：它结构上就缺「非默认皮肤编号」，实测缺的正是 257 / 528。
新船若又用了基线里没有的编号，`build_skin()` 会打一条 `namecode 未解析` 的告警，
把占位符原样写进仓库 —— 宁可喊出来，也别让站点上显示 `{namecode:996}`。

**二、尾空格不要统一 strip。**

游戏表本身就有 15 处 `name` 带尾空格（`纽卡斯尔 `、`盛夏的Seirios `、`拉·加利索尼埃·META `…），
仓库如实保留了其中 7 处。这是**上游原文**，不是脏数据：

- 站点名字要跟游戏内一致，`strip()` 会改变显示内容
- 一旦 strip，14 条已存档数据会被无意义改写，制造一次性大 diff
- 唯一那处不一致（`buli_super`，仓库 `特装型布里MKIII ` → 现在 `特装型布里MKIII`）
  是**游戏侧真的删了空格**，跟着改才是对的

所以规则是：**逐条忠实搬运，不做全局清洗。** `check_data_diff.py` 报出来的尾空格差异，
先去看游戏表现在是什么，再决定要不要动。

接口没变，还是 `build(raw_dir) -> {文件名: 数据}`（键**不带** `.json`，
适配层会自己补 —— 写成 `ship.json` 会得到 `ship.json.json`）：

```python
def build(raw_dir) -> dict:
    return {"ship": {...}, "skin": {...}, "option": {...}}
```

想启用没打开的 `ship-equip`，改文件里的 `ENABLED` 元组即可；
先跑 `check_data_diff.py --all` 看差异再决定。

如果某个构建器需要读旧文件（`ship-attr` 就是），把它的名字加进 `NEEDS_PREV`，
并在签名里写 `prev_dir=None`。`build_data.py` 靠**参数名**识别：见到 `prev_dir`
就把 `--out` 目录传进去，因此那种表跑之前不能清空输出目录。

### 2. 序列化必须和仓库对齐（踩过）

仓库 `public/data/*.json` 的实际字节格式是：

```
json.dumps(obj, ensure_ascii=True)   # 中文写成 \u6cdb 这种
默认分隔符 ', ' / ': '                # 不是紧凑模式
无末尾换行
键按船组 id 升序
```

而适配层默认的 `common.write_json` 是「不转义中文 + 紧凑 + 末尾换行 + 键排序」。
两者混用，每次运行都会把这三个文件整份重写，diff 变成全文件，仓库历史迅速膨胀。

所以 `build_data_impl.py` 自带一个 `write_json`，`build_data.py` 检测到就优先用它。
验证方式很直接：`option.json` 内容本来就完全一致，产出后 `cmp` 一下是**逐字节相同**。

`raw_dir` 的两种形态取决于 `SOURCE`：

| | 目录内容 | 文件形态 |
|---|---|---|
| `SOURCE=lua` | `fetch_lua_tables.py` 的输出 | 每个表一个文件，`ship_data_statistics.json = {"100001": {...}, ...}` |
| `SOURCE=data` | `AzurLaneData/CN/*.json` | 上游原始 json |

实现是按 `SOURCE=lua` 写的（读 `ship_data_statistics` / `ship_data_template` /
`ship_skin_template` 三张表）。`SOURCE=data` 的上游 json 已经停更 5 个月，不建议再用。

随时可以复核推导有没有被游戏改版打坏：

```bash
python scripts/fetch_lua_tables.py --server CN --dst /tmp/lua --out /tmp/tables
python scripts/check_data_diff.py --tables /tmp/tables --data public/data        # 只看启用的
python scripts/check_data_diff.py --tables /tmp/tables --data public/data --all  # 连未启用的
```

### 3. 哪些文件**不能**从游戏表推导

这不是"还没做"，是"结构上做不到"，需要外部数据源或人工维护：

| 文件 | 原因 |
|---|---|
| `cg.json` | 图床链接列表（`s2.loli.net/...`），人工挑选上传 |
| `new.json` | 首页轮播，人工挑选 |
| `nickname.json` | 频道昵称，人工整理 |
| `server.json` | 服务器列表，人工维护 |
| `vote.json` + `2017/2019/2021-*.json` | 历年投票统计结果，外部数据 |
| `bgm.json` | 419 条 `[id, 名称]`。数据源其实在 `sharecfg/voice_bgm.lua`，能按同样方式精确取，但**收录筛选规则没验证**（游戏表里的曲目远多于 419） |
| `exp.json` | 等级经验曲线。`sharecfg/ship_level.lua` 有对应数据，未验证口径 |
| `equip.json` | 753 条，需 `equip_data_statistics` + `weapon_property` 的 `cd`，且**收录筛选规则没反推出来**（游戏有 10703 条） |
| `ship-equip.json` | 可产出但只有 84%，92 处 `base[0]` 是取法 bug，未修 |

`ship-attr.json` **不在这张表里**了 —— 它已经做到 789/789 完全一致，
只剩方案舰 / `_alter` 的属性向量无法反推（走合并、沿旧值），
详见第 4 小节开头的字段来源表。

`build_data.py` 的语义正好适配这种情况：**只写返回了的文件**，没返回的原样留在仓库里。
`--prune` 默认关闭，就是为了不误删这些人工文件。

### 4. `AZL_EXTRACT_CMD`（已不需要，留作兜底）

**资产链现在不依赖这个变量了** —— `scripts/azl_assets.py` 直接从游戏 CDN 取包解码，
`update-assets.yml` 里已经没有 `AZL_EXTRACT_CMD` 这一步。这一节只说明它在什么情况下还会用到：

你自己已经有一套跑得顺的本地提取工具，且只想要"把产物同步进仓库"这件事（`sync_assets.py` 负责增量拷
贝、比对 hash、不下载历史文件）。那种情况下：

```
AZL_EXTRACT_CMD = <你的提取命令，支持 {out} {server} {python} 占位符>
AZL_RAW_SUBPATH   = json      # 原始 json 在产物里的子目录
AZL_ASSET_SUBPATH = assets    # 资产在产物里的子目录
```

### 5. 资产目录映射（只在走 `sync_assets.py` 时相关）

`scripts/sync_assets.py` 默认按同名目录找（`assets/painting/` → `public/painting/`）。
对不上就显式映射：

```bash
python scripts/sync_assets.py --src <out>/assets --dest public \
  --map painting=painting \
  --map icon/ship=shipicon \
  --map live2d=spine
```

**默认不删文件。** 想删要显式 `--prune painting` 这种。

走 `azl_assets.py` 时不需要映射：它按清单里的路径前缀直接对应 `public/<目录>/`。

### 6. `DEPLOY_HOOK`（可选）

`cdn.al.pelom.cn` 和 `al.pelom.cn` 都解析到 `154.204.253.52`，是你自建的，不是 Cloudflare Pages
（`public/_headers` 是遗留配置，云服务商那边现在没生效）。
所以部署只有两种接法：

- **webhook**：服务器上跑个接收端点（或用宝塔/1Panel 的 webhook 插件），secret 填 `DEPLOY_HOOK`
- **服务器侧拉**：服务器上 `cron` 定时 `git -C /path pull`，流水线只负责推，`DEPLOY_HOOK` 留空即可

顺便：**`cdn.al.pelom.cn` 现在返回 502，源站是挂的。** 这件事和流水线无关，但值得先查。

---

## 五、6.8 GB 仓库怎么处理

### 数据链：blobless + 稀疏

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 1
    filter: blob:none        # 只下 tree，不下任何文件内容
    sparse-checkout: |       # 只要这三个目录落盘
      src
      scripts
      public/data
```

一次 run 拉下来的东西从 7 GB 降到 1 MB 级。这是数据链能在托管 runner 上每小时跑的前提。

### 资产链：不能用 sparse

资产链要往 `public/painting/` 里写**新文件**，而 sparse 之外的新文件 `git add` 会直接报
`pathspec is outside of the sparse-checkout definition`。所以资产链用的是
**blobless 但不开 sparse**：只 `git checkout` 出 `scripts/ .github/ public/data`，
资产文件留在远端不落盘。

配套的第二个约束是**提交必须按文件清单**。在这种工作副本里 `git add -A -- public`
会把没落盘的两万多个资产当成删除，一次提交删干净整个仓库。所以：

- `azl_assets.py --report-files <清单>` 把本次真正写出的文件记成 NUL 分隔清单
- `commit.py --paths-from <清单>` 用 `git add --pathspec-from-file` 只加这些文件

`scripts/common.py` 里两种模式是分开的：数据链可以给目录（那些文件都落盘了），
资产链必须给清单。**这条别图省事改回去。**

### 全量回填与日常更新的差别

| | 日常更新 | 首次全量回填 |
|---|---|---|
| 变化文件 | 几十个 | 10,772 张立绘 + 8,000 多个图标 |
| 产出体积 | 几十 MB | 约 20 GB |
| 托管 runner | ✅ 跑得动 | ❌ 14 GB 磁盘会写满 |
| 做法 | 定时跑 | `limit=300` 分批跑几十次，或换自托管 |

增量状态（每个资源的 md5）存在 `.azl/assets-state.json`，由 `actions/cache` 跨 run 保留 ——
不写进仓库，否则每次运行都会产生一个几百 KB 的提交，历史膨胀得比资产还快。

### 但仓库总量还是会涨

不用回避这个问题。GitHub 的硬指标：

| 限制 | 值 | 影响 |
|---|---|---|
| 单次 push | 2 GB | 首次全量导入资产时必须**分批**，否则直接被拒 |
| 仓库推荐体积 | < 5 GB | 你已 6.8 GB，会收到告警邮件，但不影响功能 |
| 仓库硬上限 | 100 GB | 远未到 |
| Actions cache | 10 GB / 仓库 | 上游缓存 + 构建产物要留意别互相挤掉 |

要真正止住增长，只有三条路，按侵入性排序：

1. **维持单仓库，接受慢速增长**（本次方案）：只在出新船时增加几 MB，可撑很久
2. **拆出 assets 仓库**，主库只留代码 + data，CDN 指向新库 —— 干净，但要改一次部署
3. **资产迁对象存储**（R2/OSS），仓库彻底瘦身 —— 最彻底，改动也最大

你选了 1，方案就是按 1 写的，2/3 随时可以在这个骨架上平移。

---

## 六、启用步骤

1. **推上去**：把 `.github/workflows/` 和 `scripts/` 整个目录提交到 `master`。
2. **补脚本**：放 `scripts/build_data_impl.py`（第四节第 1 条）。
3. **先跑 `SOURCE=data` 保平**：Actions → `update-data` → Run workflow，`source` 选 `data`、勾 `force`。
   这一步的目的是验证"接进来的脚本产出的东西和你手工跑的一致"，用旧字符集做基线。
4. **再跑 `SOURCE=lua`**：同样手动触发，`source` 选 `lua`。
   把这次和上次的 `public/data/*.json` diff 一下 —— 多出来的应该是新船/新皮肤，那就算通了。
5. **切默认定时**：确认无误后把 workflow 里的 `SOURCE` 默认值保持 `lua`（本来就是），每小时自动跑。
6. **资产链（可选）**：在这台机器上注册一个 self-hosted runner（Settings → Actions → Runners → New self-hosted runner，选 Windows），
   配好 `AZL_EXTRACT_CMD` 等 Variables，手动跑一次 `update-assets`。

第一次跑 `update-data` 时，如果 `public/data` 目录里已有文件而你的脚本又只产出其中一部分，
先不要开 `--prune`，确认 diff 里没有意外的删除。

---

## 七、坑与注意事项

**`git add -A` 是这里最容易踩的雷。** 在稀疏/部分检出的工作区里，`-A` 会把"没落盘的资产"当成删除，
一个提交就能把 23,000 个文件从仓库里删掉。`scripts/commit.py` 强制用 pathspec 提交，就是为了这个。
你以后自己改流水线时也别图省事。

**lua 解析器是针对性写的。** `scripts/luatable.py` 只认这一种导出格式：

```lua
_G.pg.base.<表名>[<id>] = { ... }     -- 纯字面量，无函数调用、无循环、无 require
```

上游哪天改了导出方式（比如改成序列化二进制、或用函数动态构造表），解析会失败。
失败是**显式**的：`fetch_lua_tables.py` 会把表名打出来跳过，并在 summary 里标红，
不会静默产出一半数据。真遇到就先看是不是多文件格式变了。

**只有 CN 和 EN。** `LuaScripts` 没有 JP/KR/TW。你 `public/data` 里那些历史分服文件
（`2017-jp.json` 之类）如果还要维护，只能继续用 `SOURCE=data` 或手工。

**开销比想象中大一点。** `CN/sharecfgdata` 约 180 MB，首次冷缓存要下载全套，
之后靠 git 增量。解析 35 个表约 30 秒，产出 129.7 MB / 333,662 条记录。第一次 run 会明显慢，别以为卡住了。
`sharecfg/` 那四张附加表约 1.2 MB（`transform_data_template` 占大头），可以忽略。

**`ship-attr` 仍依赖旧文件，别清空输出目录。** 方案舰（`(id//1000)%100==99`）与
`_alter`（9xxxxx 段）的属性向量在游戏表里对不上（各形态 `attrs` 相同 / 系数每项不同），
这两类只能沿旧值；清空 `public/data` 后再跑，它们的 `base` / `growth` 会缺。

**上游停更不会让流水线报错。** 没有新数据时会静默产出旧数据、CI 全绿。
所以 `check_freshness.py` 是独立一层：lua 源阈值 2 天、data 源 10 天，超了就 `::warning::` + 开 issue。
**这个告警是本方案里唯一能告诉你"数据其实已经旧了"的机制，别关掉。**

**公开仓库跑托管 runner 不计费**，所以每小时一次不用心疼额度。
如果以后改成私有仓库，720 次/月 × 约 3 分钟 ≈ 2160 分钟，会超过免费 2000 分钟 —— 那时候把 cron 调成每 3 小时。

**自托管 runner 有安全含义。** 它是跑在你自己机器上的进程，能访问本机文件系统。
只给自己这一个仓库用（注册时不要选 org 级），不要给公开 PR 触发的 workflow 用
（本方案的两条链都只有 `schedule` + `workflow_dispatch`，没有 `pull_request`，是安全的）。

**如果哪天 `AzurLaneData` 被修好了**，不需要改任何代码，把 `source` 切成 `data` 就是它的格式；
两个源可以随时互切，因为下游看到的都是"每表一个 json"的目录。


---

## 八、附录：资产链是怎么跑通的（实测记录）

这一节记录"2023 年的提取器怎么对上 2026 年的游戏"。如果你以后要动 `azl_assets.py`，
先看这里的结论，能省掉大量试错。

### 1. 起点：旧仓库不是"只出 lua"

`AzurLaneExtractor` 公开 API 是 404，但用你的 git 凭据能完整拉下来：57 个文件，最后提交
**2023-04-23**。里面除了 `scripts.py` / `scripts_json.py`，还有：

```
az_ex/tasks/extract_image.py        Texture2D / Sprite → 图片（UnityPy）
az_ex/tasks/extract_painting.py     立绘合成（Mesh 还原 + 多图层叠加 + 表情坐标）
az_ex/tasks/extract_audio.py        acb → wav → mp3（依赖 CriTools + ffmpeg）
az_ex/tasks/extract_video.py        cpk → m2v → mkv（依赖 CriTools + crid_mod）
```

所以"图片提取"一直在旧仓库里，是**新方案砍掉的功能**，不是从未实现。这条代码路径的价值在于：
它把立绘的合成算法写清楚了，照着搬就行。

### 2. 四个环节的实测对照

| 环节 | 2023 年的假设 | 2026-10-20 实测 |
|---|---|---|
| 登录服握手 | protobuf `sc_10801`，9 个字段 | ✅ 通，但**多了字段 10**，旧代码直接 KeyError |
| hash 清单 | 5 类：az/cv/l2d/pic/bgm | **10 类**，多了 painting/manga/cipher/dorm/map，旧代码 KeyError |
| CDN 资源 | `/android/resource/<md5>` | ✅ 未变；az 清单 64,648 条，立绘清单 10,772 条 |
| 资源包本体 | UnityPy 1.8 | Unity **2022.3.62f3**，贴图 ASTC(`format=48`)，UnityPy 1.25 直接能解 |

游戏版本 `az=9.7.398`，APK `blhx_9.7.25_...20260920...apk`。

### 3. 必须改的四处（都已在 `azl_assets.py` 里处理）

**① 依赖名不是路径。** `AssetBundle.m_Dependencies` 是 `cab-<32位md5>`，而

```
md5("painting/2b_n_hx_tex") == 14f8d91ec96685563462de3af2c92238
```

也就是 **`cab-<md5(资源路径)>`**。所以只要手上有该类别的清单，就能把依赖名反查回路径，
不必打开每个包 —— 一行 dict 的事，但没验证之前只能靠猜。

**② Mesh 顶点流换了格式。** Unity 2022 的 Mesh 不再有 `m_Vertices` / `m_UV0`，
改成了 `m_VertexData` + `m_Channels` 描述的多通道流。立绘用的是最简单的单流：
`stride=20`，前 12 字节是 3 个 float32 的位置，后 8 字节是 2 个 float32 的 UV。
`decode_mesh()` 就按这个读，遇到压缩流（`m_CompressedMesh`）返回 None 跳过该图层。

**③ 坐标锚点必须逐层往下传。** `stack_transform` 返回的 `cur_info`（父节点的尺寸与累计缩放）
是子节点计算的基准。第一版忘了传，结果每个图层都按自己的尺寸当画布，合成出来人物和舰装分家。

**④ 少数资源包没写 Unity 版本号**，UnityPy 会抛 `UnityVersionFallbackError`。
设 `UnityPy.config.FALLBACK_UNITY_VERSION = '2022.3.62f3'` 即可 —— 那几个包的内容本来就是
这个版本格式。

### 4. 站点要的不是"一张图"，是"三件套"

这一条是整件事里最容易做错的地方。站点（`src/views/profile/index.vue`）**在浏览器里用 pixi 逐层重建立绘**，
它读的是：

```
painting/<name>/<name>.json          图层布局
painting/<name>/<layer>.png          该图层的原图集（未做网格变形）
painting/<name>/<layer>-mesh.obj     网格：把图集切块摆到正确位置
paintingface/<name>/<i>.png          表情，序号与 json 里 face.list 对应
```

所以"合成一张完整立绘存成 `painting/<name>.png`"是错的 —— 站点不会看那个路径。
三件套的取值规则是我拿仓库现有 2167 个立绘反查、再对着真实资源逐项验证出来的：

| JSON 字段 | 来源 |
|---|---|
| `size` | 该图层 `RectTransform.m_SizeDelta` |
| `pivot` | `m_Pivot` **原样**（不做 Y 翻转） |
| `position` | `m_AnchoredPosition` **原样** |
| `rawSize` | `MonoBehaviour.mRawSpriteSize`（不是图集尺寸） |
| `view` | `mainFullScreen` 节点的 `m_SizeDelta`，**只有基底层有** |
| 基底层 `position` | `mainFullScreen` 的 `m_AnchoredPosition`（基底层自身 anchor 恒为 0） |
| `face` | `face` 节点的 size/pivot/position + `list`（表情序号） |
| `raw: true` | 该图层没有 `mMesh`，客户端整张铺开，不写 obj |

OBJ 的格式比 json 更苛刻，因为它靠**行号**做索引：

```
0            g <layer>-mesh
1..N         v  x y 0        （每四边形 4 条，x 轴取反）
N+1..2N      vt u v          （每四边形 4 条，0..1 归一化）
2N+1         g <layer>-mesh_0
其后         每四边形 2 条 f
```

三个细节错一个就白屏：
**① 换行必须是 CRLF**（站点用 `split('\r\n')` 切行，LF 会把整个文件当成一行）；
**② 数字是 7 位有效数字且 half-up 进位**（`1/2048` 写作 `0.0004882813` 而不是 `.12`，Python 默认的银行家舍入会对不上）；
**③ 文件名、目录名、json 键全小写**（游戏里是 `2B`，站点拼的是 `2b`）。

### 5. 实测对照

拿新导出器和仓库现有文件逐个比（同一张立绘，各比 json / obj / png）：

| 项 | 结果 |
|---|---|
| OBJ | **byte 级完全一致**（abeikelongbi / 22 / xinzexi / beiqi / bisimai / chicheng_alter 共 7 张、14 个 obj） |
| JSON | 数值全等，无一处差异；唯一的"多"是 `face` —— 游戏新增了表情，旧仓库还没跟上 |
| PNG | 尺寸完全一致；不透明区平均差 0.4–3.2/255，是 2025-06 与 2026-10 两个版本的贴图重压缩（ASTC 有损），不是格式差异 |
| 图标（squareicon） | 116×116 PNG，命名与站点一致（`2b.png`、`2b_2.png`） |
| 图标（shipicon） | 见下方"尺寸漂移"，站点目录名和游戏目录名不同，需要映射 |
| 速度 | 单个立绘约 5-6 秒（含 4 个依赖包下载），表情另算 |

#### 站点目录名 ≠ 游戏目录名

站点沿用早期命名，有两处对不上，写死在 `SOURCE_PREFIX` 里：

| 站点目录 | 游戏清单前缀 | 产物 | 说明 |
|---|---|---|---|
| `public/shipicon/` | `shipyardicon/` | `<name>.png` 216×288 | 游戏里**没有** `shipicon/` 这个前缀，直接按同名找会一条都找不到 |
| `public/equipicon/` | `equips/` | `<id>.png` | 同上 |

**尺寸漂移（不用处理）**：仓库里 `shipicon` 全是 192×256，游戏现在是 216×288
（正好 8/9，同一个 3:4 比例）。仓库那批是 2025-06-06 一次性加的，当时游戏就是 192×256，
后来官方提了分辨率。站点是 `width:100%` + `fit="contain"` 摆图，**图片自身的像素尺寸不影响布局**，
所以直接出原生 216×288，不做缩放——缩放等于自己造一份有损数据，而且每次更新都会再损一次。
`check_site_format.py` 只校验宽高比 3:4，不校验绝对尺寸。

#### 只导"站点真正用到的"资源

仓库的图标目录**不是游戏的完整镜像**：`shipicon` 2157 个文件，而游戏清单里有 2671 个；
`iconframe` 只有 9 个，游戏里有 133 个。说明当初收录时是按站点引用集裁过的。

站点所有图片都是 `${cdn}/<目录>/${painting}.png` 拼路径，所以 `public/data/*.json` 里
每个 `painting` 字段（含嵌套）就是一份必需的资源名清单。实测比对：

| 目录 | 仓库文件数 | 引用集 | 交集 | 仓库多 | 引用多 |
|---|---|---|---|---|---|
| `shipicon` | 2157 | 2161 | **2157** | 0 | 4 |
| `squareicon` | 2164 | 2161 | 2157 | 7 | 4 |

`shipicon` 的引用集**完整覆盖**仓库全部文件，一个不漏；多出来的 4 个
（`changdao_4`、`gulite_robot1/3/4`）游戏清单里根本没有对应资源，自然被跳过；
`squareicon` 多出的 7 个是 NPC 图标（`npc*`），仓库额外收的，我们只增不删，不影响。

所以默认 `--scope used`：以 `public/data` 的引用集为准，产出集合和仓库现有集合对齐，
同时把工作量从"游戏全量"降到"站点需要"。`--scope all` 保留全量导出（`public/data`
读不到时会自动回退到它）。

代价：这条链依赖 `public/data` 先更新。data 没更新的新船，其图标要等 data 更新后的下一轮才出来，
这恰好和站点"只显示 data 里的船"的行为一致。

### 6. 本地怎么跑

```bash
python -m pip install unitypy numpy opencv-python-headless

# 试跑两个立绘
python scripts/azl_assets.py --items painting --limit 2 --dry-run

# 真正跑（立绘会自动带上表情；默认按 public/data 引用集过滤）
python scripts/azl_assets.py \
  --items painting squareicon shipicon \
  --public public \
  --cache /tmp/azl-bundles \
  --report-files /tmp/files.txt

# 想导全量（会多出几百个没人引用的图标、上千个立绘目录）
python scripts/azl_assets.py --items painting --scope all --limit 5

# 产出格式自检（挡格式漂移用，CI 里也是这一步）
python scripts/check_site_format.py --public public \
  --icons squareicon shipicon equipicon iconframe

# 提交（必须按清单，别用 git add -A）
python scripts/commit.py --paths-from /tmp/files.txt -m "chore(assets): 同步资源"
```

### 7. 还没覆盖的部分

| 目录 | 状态 | 说明 |
|---|---|---|
| `public/painting` | ✅ | 三件套，已逐字节比对 |
| `public/paintingface` | ✅ | 按 `<i>.png` 编号，和 json 的 `list` 对应 |
| `public/squareicon` | ✅ | 平铺 png，116×116，命名全小写 |
| `public/shipicon` | ✅ | 平铺 png，源 `shipyardicon`，216×288 |
| `public/equipicon` · `iconframe` | ✅ | 已加 `CATEGORY_LIST` / `SOURCE_PREFIX`，命名沿用游戏资源名 |
| `public/shipbackground` · `squareback` · `voteicon` · `juusui` · `iconbackground` | ❌ | 游戏清单里没有同名前缀，是 `ui/` 图集里的 **sprite**，需要另做图集切片，不能按整包导贴图 |
| `public/spine` · `spinebase` | ❌ | skel/atlas 二进制，不是贴图，要另写解析 |
| 音频 / 视频 | ❌ | acb / cpk，需要 CriTools(node) + ffmpeg + crid_mod |
| Live2D | ❌ | 同上，旧代码也标为"待实现" |
| `_ex.json` | ❌ | 仓库里有 8 个 `<name>_ex.json`（换装全屏布局），生成规则未验证，暂不产出 |

补一个贴图类目录的成本很低：`CATEGORY_LIST` 加 `'目录名': 'az'`；如果站点目录名和游戏前缀
不一致，再往 `SOURCE_PREFIX` 加一行。确认清单里有对应前缀后 `--limit 3` 跑一遍看命名，再放大。

---

## 九、附录：前端合成 vs 提取器合成（算法逐项对比）

站点和 `AzurLaneExtractor` 都能"出图"，但两者的目标和坐标系不同，把它们的差异搞清楚
才能明白为什么仓库里存的是「三件套」而不是一张合成图。

- 前端：`src/views/profile/index.vue` 的 `composeSprite()` / `loadPainting()`，pixi 实时渲染
- 提取器：`az_ex/tasks/extract_painting.py` 的 `fix_mesh_image()` / `paste_image()` / `normalize_boxes()`

### 1. 根本差异：运行时分层 vs 离线摊平

| | 前端 | 提取器 |
|---|---|---|
| 产物 | 不合成，逐层放 sprite | 一张合成好的 PNG + 各层摊平图 |
| mesh | **运行时**由 pixi 切四边形贴上 | **离线**用 `cv.resize` 把三角形 crop 贴到画布 |
| 层级 | 只有一层：所有层是 base 的兄弟 | 用 Unity Transform 树逐级叠 box |

所以仓库里的 `painting/<name>/` 必须是「未变形的图集 + obj + 布局 json」，
存不了提取器那种已经摊平的层图 —— 这是这份资产链**必须自己写**、不能直接用提取器的原因。

### 2. mesh 的解析方式

| | 前端 | 提取器 |
|---|---|---|
| 索引 | **完全忽略** `f` 行 | 用 `m_Indices` 组三角形 |
| 分组 | 硬编码 4 个顶点一组（`i += 4`） | 按三角形逐个处理 |
| 取点 | 只取 `v[i]` 和 `v[i+2]` 两个点算矩形 | 对三角形 3 个点取 `min/max` |
| 缩放 | **不缩放**，直接 `new Rectangle(uv 换算的像素矩形)` | 尺寸不符时 `cv.resize` 到目标 box |
| uv 取整 | `Math.round(vt * 尺寸)` | `np.floor(uv * 尺寸 + 0.5)` |

前端的实现依赖几个**未写进代码的假设**，实测都成立，但一旦游戏侧变化就会静默出错：

| 假设 | 实测（4 个立绘 / 933 个四边形） |
|---|---|
| v 数量是 4 的倍数 | 成立，0 例外 |
| v 数量 == vt 数量（`vt` 段起点 = `count+1`） | 成立 |
| 顶点跨度 == 纹理像素跨度（即 mesh 单位 = 像素） | 成立，0 例外 |
| 四边形 uv 方向恒定（`vt[i+2] ≥ vt[i]` 逐分量） | 成立，0 例外 |

第 3 条是关键：`rawSize` 是图集的**逻辑画布**尺寸（如 606×979），而 PNG 是打散的
**图集**（如 1024×418），30 个四边形各映射一块。所以顶点必然在 `rawSize` 空间里，
再由 `layer.scale = size/rawSize` 放大到真实显示尺寸（实测普通层 1.0，多层层 1.91-1.94）。

### 3. 坐标系与翻转

两边都在 Unity 的 y-up 里取值，但**翻转位置不同**，最终等价：

| | 前端 | 提取器 |
|---|---|---|
| anchoredPosition.y | 不翻转 | `anchor[1] *= -1` |
| pivot.y | 不翻转 | `pivot[1] = 1 - pivot[1]` |
| 贴图 y | 每个四边形 `sprite.scale(1,-1)` + `position.y = v1[1]` | `cv.flip(image,0)` 两次（进出各一次） |
| 整体 | `painting.scale.set(baseScale, -baseScale)` 一次性翻转 | 逐层算 box，不整体翻转 |

提取器那两处取反是因为它在**图像空间**（y-down、左上原点）里算布局；前端保留 Unity 原值、
最后把整个容器翻一次。**两处翻转互相抵消**，所以拿 `m_Pivot` / `m_AnchoredPosition`
原值写进 json 是对的 —— 我们出的 json 与仓库逐字节一致，反向印证了这一点。

### 4. 图层定位公式（两边一致）

```
base 中心 = baseSize / 2
子层左上角 = baseSize/2 - size*pivot + anchoredPosition
子层缩放  = size / rawSize          // size = m_SizeDelta, rawSize = mRawSpriteSize
```

前端 `loadPainting()` 里 `if (file !== name)` 分支就是这条；提取器
`stack_transform()` 里 `pivot_target = target + anchor; pos = pivot_target - pivot` 是同一件事。
差别只在提取器多算了嵌套 Transform 的 `m_LocalScale`，而站点 json 是**拍平**的（不存父级缩放）。

### 5. 表情（face）

| | 前端 | 提取器 |
|---|---|---|
| 是否总叠表情 | **总是**叠 `list[0]` | **仅当**脸区近似纯色（`np.std < 40`）才补 |
| 文件名 | `<i>.png` 序号，与 json `list` 对齐 | 贴图自身的 `m_Name`（如 `face_0.png`） |
| 坐标 | `size/pivot/position` + `list` | 一个 `face_box`（`[x0,y0],[x1,y1]`） |
| 默认脸 | 不特殊处理 | 把原图追加为 `'default'` 并覆盖上去 |

这条差异最直接：提取器的表情布局**没法**直接喂给前端，必须重新算成
`size/pivot/position`（就是 `painting_site.py` 干的活）。

### 6. 单图层特例

- 提取器：`len(layers)==1` 时直接存该层图，尺寸 = 顶点 bbox，**不缩放**到 `m_SizeDelta`
- 前端：仍然走 `layer.scale = size/rawSize`

实测仓库里大多数立绘都是单层（22 / 33 / abeikelongbi 系列全是单层），
所以这条会直接影响合成图尺寸 —— 又一个"提取器的图不能直接当站点资源"的理由。

### 7. 图层命名

| | 前端 | 提取器 |
|---|---|---|
| 名字来源 | json 的 key（节点名，全小写） | `sprite.name` / `texture.name`，空则用 `父-子` 路径拼接 |
| 重名 | 追加 `_2`、`_3` | 追加 `_2`、`_3`，且**完全相同的层直接丢弃** |

实测游戏里节点名大小写混用（`2B`），而仓库是全小写 `2b` —— 前端按小写拼 URL，
所以导出时必须 `lower()`。

### 8. 一句话总结

> 提取器是「给人看的一张图」，前端要的是「给浏览器重建的零件」。

两者都从同一份 Unity 资源出发、用同一套 UI 布局公式，但因为**mesh 在前端是运行时解的**，
所以站点只需要图集 + obj + 布局；提取器那些摊平、缩放、补表情的处理，
在站点这条链上全部由 pixi 在浏览器里完成。

### 9. 那前端要改吗：不用改，但要守住 6 条导出契约

结论：`composeSprite()` / `loadPainting()` 的算法**不需要动**。佐证：

| 检查项 | 实测结果 |
|---|---|
| `uv * 图集尺寸` 是否落在整数像素上 | 小数部分最大 `3.8e-5`（`22` 的 47 个四边形），所以 `Math.round` 是无损的，不会取整出缝 |
| 取整后矩形是否合法 | 47/47 合法：无负宽高、无越界（`0 ≤ x0 < x1 ≤ W`） |
| `v` / `vt` / `f` 数量关系 | `188 / 188 / 94`，即 47 个四边形、每边形 2 个三角形 |
| `raw` 层的 `rawSize` 是否等于 png 尺寸 | `biaoqiang_9` 主层：`rawSize [2048,1651]` == png `2048×1651`，`scale = 3304/2048` 后正好铺满 `size` |

所以「两边算法不一致」不是缺陷，是目标不同，不要去「统一」。

但前端不改是有代价的：它把 6 条假设**硬编码**进了实现，导出侧一旦违反就静默出错（白屏 / 花图 / 404），
而且报错信息都指向不了真正原因。**这 6 条就是 `painting_site.py` 必须满足的契约：**

| # | 契约 | 前端消费处 | 违反后的表现 |
|---|---|---|---|
| 1 | obj 用 `\r\n` 换行 | `obj.data.split('\r\n')` | 整个文件变一行 → `count` 为 0 → 白屏 |
| 2 | `v` 段在前、`vt` 段紧随其后，中间不插 `vn` | `mesh[i + count]` | uv 错位 → 花图 |
| 3 | `#v == #vt`，且是 4 的倍数 | `for (i = 1; i <= count; i += 4)` | 读到 `f` 行或越界 → 静默错位 |
| 4 | 图层文件名、目录名全小写 | `painting/<name>/<file>.png` 直接拼 | 404（游戏内是 `2B`，站点是 `2b`） |
| 5 | `raw: true` 时 `rawSize == png 尺寸` | `position(0, h)` + `scale(1,-1)`，之后整层缩放 | 图层错位或尺寸不符 |
| 6 | `face` 层必须有 `list`，且 `paintingface/<皮肤>/<i>.png` 存在 | `changeFace(list[0])` | 表情加载失败，整块画不出来 |

`write_mesh_obj()` 的 docstring 已经把这套行布局写死了（1–5 由它保证）；
第 6 条由 `export()` 的 `face_images` 参数保证 —— **导立绘时必须一起导表情目录**，漏了就只在浏览器里炸。

#### 前端已冻结，所以契约由 CI 守

既然决定不动前端，这 6 条就不能只写在文档里靠人记 —— `check_site_format.py` 已经把它们
变成可执行检查（`update-assets.yml` 每次跑）：

| 契约 | 检查点 | 失败提示 |
|---|---|---|
| 1 CRLF | `check_obj()` 首行判 `b'\r\n'` | `OBJ 不是 CRLF 换行，客户端会把它切成一行` |
| 2 行序 | 校验第 `1..n` 行全是 `v`、第 `n+1..2n` 行全是 `vt`，中间插任何东西都报 | `vn / g / f 插在中间会让 uv 整体错位` |
| 3 数量 | `#v == #vt`，`#v % 4 == 0` | `顶点数不是 4 的倍数` |
| 4 小写 | 目录名 + **json 里的图层 key** 都过 `^[a-z0-9_\-\.]+$` | `图层名不是全小写` |
| 5 raw 尺寸 | `rawSize` 与 png 实际宽高逐值比对 | `raw 层 rawSize 与图片不一致，客户端会错位` |
| 6 表情 | json `list` 长度 == `paintingface/<皮肤>/*.png` 个数，且序号连续 | `表情文件 N 个，json list 声明 M 个` |

原来的版本只查了 1、3、4（目录名）和 6，漏掉的正是最容易静默出错的三处：
**行序（2）**、**图层 key 大小写（4 的后半）**、**raw 层尺寸（5）**。

用仓库现有 3 个立绘（`22` 单层 mesh、`biaoqiang_9` 的 raw + mesh 混合、`ajiakesi_2` 带 face）
实跑通过，说明契约与存量一致；再注入三类故障验证能抓住：

```
注入 LF 换行   → OBJ 不是 CRLF 换行，客户端会把它切成一行
注入 vn 行     → 第 361..720 行应全是 uv，出现 'vn 0 0 1'
改错 rawSize   → rawSize=[3000.0, 2664.0] 与图片 2048x1651 不一致
```

这也反过来约束自己：`write_mesh_obj()` 现在会在顶点数/uv 数不等或非 4 倍数时直接
`raise ValueError`；`_one_layer()` 也不再在「有 mMesh 但解析失败」时降级成 `raw: true`
（那样会出一张尺寸错误却不报错的图），改为跳过该层并记进 `skipped` 报告。

如果哪天解冻前端，只有两处值得改，都属于「让静默失败变得可诊断」而不是修算法：
`split('\r\n')` → `split(/\r?\n/)`；以及 `count % 4` 时打一条 `console.warn`。

#### 数据侧是另一套契约，由 `check_data_contract.py` 守

上面 6 条针对**资产**（png / obj / 图层 json）。`public/data` 的结构是另一套假设，
全部来自 `src/views/profile/` 和列表页，同样是硬编码：

| # | 契约 | 前端消费处 | 违反后的表现 |
|---|---|---|---|
| 1 | `ship-attr[*].base` 恰好 12 项 | `base.map()` 决定循环次数 | 短了漏项、长了 `growth[i]` 越界成 `NaN` |
| 2 | `growth` ≥ 12 项且无 null | `growth[i]` 没有兜底 | 整列数值变 `NaN` |
| 3 | `strengthen` 必须存在且是数组 | `attr['strengthen'][i-1]`，开关默认开 | **直接抛错，整页白屏** |
| 4 | `transform` 若存在则长度 ≥ 12、无 null | `transform[i]` 按需读 | 属性加成算成 `NaN` |
| 5 | `skin` 里不能残留 `{namecode:N}` | 前端不解析占位符 | 原样显示成一串花括号 |
| 6 | `ship` / `ship-attr` 键集必须同源 | 两处按同一个 `painting` 索引 | 「有船没属性」的空面板 |

第 3 条是最容易漏的：`strengthen` 缺席不会报「缺字段」，而是在 `props.attr['strengthen'][i-1]`
处抛 `Cannot read properties of undefined`。`build_ship_attr` 因此**每一条都保证有值** ——
普通船取 `ship_data_strengthen.durability`，方案舰和 `_alter` 补 `[0,0,0,0,0]`
（仓库现有 49 艘 `_alter` 实测全是这个值）。

`update-data.yml` 里这一步在「生成 public/data」之后、「提交并推送」之前，
`commit` 的 `if` 同时要求 `steps.contract.outcome == 'success'` —— 契约不过就不许进仓库。
用仓库现有 data 实跑全绿；注入了 5 类故障（删 `strengthen`、`base` 截成 11、
`transform[n]=null`、`{namecode:999}` 残留、未知字段 `transform_`）全部命中，退出码 1。
