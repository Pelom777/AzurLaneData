# CDN 与资产分发

> 状态：**已落地并端到端跑通**。唯一入口 `.github/workflows/update-assets.yml`
> （热更新与全量重建同一份代码，只差 `shards` 参数），见 §4.5。
> 最后核对：2026-10-22。

## 0. 一页纸结论

| 结论 | 依据 |
|---|---|
| 用 **Cloudflare R2** 托管，原样上传，**不做任何图片处理** | 6.82 GB / 免费 10 GB，占 68% |
| **Cloudflare Pages 不能托管图片** | 免费版 20,000 文件/站，我们有 19,443，已占 97% |
| 没有域名先用 `pub-<桶ID>.r2.dev` | 官方定性"非生产"，但个人站量级够用 |
| **spine（1.10 GB）无法自动生成**，从旧仓库搬一次 | `azl_assets.py` 里没有任何 spine 代码 |
| 首次播种必须在**本地**做，不要放 CI | 6.8 GB 上传 + 生成耗时长，且只需一次 |
| **CI 必须先从远端补 marker**，否则每轮全量重跑 5.9 GB | 跳过判据要求产物文件在磁盘，而 CI 是干净 checkout |

## 1. 体积实测

数据来自 `GET /repos/Pelom777/AzurLaneTools/git/trees/<sha>?recursive=1` 的 blob size，
不需要下载文件就能算准。

| 目录 | 文件数 | 体积 | 占比 | 能否自动 |
|---|---:|---:|---:|---|
| `painting` | 7,832 | 5,577.9 MB | 79.9% | ✅ |
| `spine` | 6,363 | 1,096.3 MB | 15.7% | ❌ |
| `shipicon` | 2,157 | 130.9 MB | 1.9% | ✅ |
| `paintingface` | 290 | 85.2 MB | 1.2% | ✅ |
| `shipbackground` | 70 | 43.9 MB | 0.6% | ❌ |
| `squareicon` | 2,164 | 42.1 MB | 0.6% | ✅ |
| `equipicon` | 495 | 5.9 MB | 0.1% | ✅ |
| `voteicon` | 8 | 1.6 MB | 0.0% | ❌ |
| `data` | 21 | 0.7 MB | — | 进 R2 的 `data/`（前端 `VITE_CDN` 指向它） |
| `iconbackground` / `iconframe` / `squareback` / `spinebase` / `juusui` / `_headers` | 43 | 0.6 MB | 0.0% | 部分 |
| **合计** | **19,443** | **6,985.2 MB** | | |

三个大头的内部构成：

- `painting/<name>/` = `<name>.png`（平均 1.9 MB）+ `<name>.json`（图层）+ `<name>-mesh.obj`
- `spine/<name>/` = `<name>.skel` + `<name>.atlas` + `<name>.png`
- 图标类单文件 12~62 KB，合计不到 180 MB

## 2. 三类资产，三种处理方式

| 类别 | 文件数 | 体积 | 来源 | 更新方式 |
|---|---:|---:|---|---|
| **A. 自动生成** | 12,947 | 5.71 GB | `azl_assets.py` | 每次热更增量跑 |
| **B. 一次性搬运** | 6,475 | 1.12 GB | 旧仓库 `public/` | 搬一次，此后不动 |
| **C. 数据** | 21 | 0.7 MB | `build_data.py` | 进 R2 的 `data/`，由 `update-data.yml` 发布 |

A 类对应 `CATEGORY_LIST` 里实际有产物的 6 个目录：`painting`、`paintingface`、
`squareicon`、`shipicon`、`equipicon`、`iconframe`。

B 类 = `spine` + `shipbackground` + `voteicon` + `iconbackground` + `squareback` +
`spinebase` + `juusui`。这些要么是二进制动画格式（skel/atlas），要么脚本里没有对应
类别映射。

**B 类的后果要认**：游戏出新皮肤时，`spine` 不会自动补。前端取不到时需要有 fallback
（退回静态立绘 `painting/<name>/<name>.png`），否则新船的 Live2D 会是空白。

## 3. 没有域名：r2.dev 的取舍

R2 桶开启 Public Development URL 后，地址是：

```
https://pub-<32位桶ID>.r2.dev
```

**本项目实际值**（2026-10-21 启用，已实测）：

```
https://pub-85b4b8af407244bc8f1502127fc530c3.r2.dev
```

实测证据：`GET /squareicon/22.png` 返回 `200`、`Content-Type: image/png`、
`Content-Length: 27131`，下载后 md5 `68c03aebc3a214113f228fa31b2a84de`，
与桶内对象**逐字节一致**；上传时设的 `Cache-Control: public, max-age=86400`
原样返回；不存在的 key 返回 `404` 而非 `403`，说明匿名读确实生效。

这个域名绑桶固定，以后买了域名只改 `VITE_CDN`，前端代码不用动。

官方文档的原话是"not intended for production usage"、"variable rate limit"：

| 限制 | 具体 | 对我们的影响 |
|---|---|---|
| 速率 | 几百 req/s 会 429，带宽也可能被限 | 个人站日活千人 ≈ 几 req/s，够用 |
| 缓存 | **不能配 Cloudflare Cache** | 每次图片请求都是一次 Class B |
| 安全/分析 | 无 WAF、无 URL 级分析 | 被爬时无法限流 |

地址绑桶固定，以后买了域名只改 `VITE_CDN`，前端代码不用动。

### 3.1 CORS：默认是关的，必须手工开一次

实测（2026-10-21）：`r2.dev` 对带 `Origin` 的请求**不返回任何 `Access-Control-*` 头**，
`OPTIONS` 预检直接 `403`。这不是 bug，是 R2 的默认状态。

| 前端用法 | 受影响 |
|---|---|
| `<img src>`、`<video>`、CSS `url()` | 否 —— 这些走 no-cors，不加头也能显示 |
| `fetch()` 拉 `painting/*.obj`、`spine/*.skel`、`*.atlas`、`*.json` | **是，会被浏览器拦** |
| canvas 读像素（`getImageData`） | **是**，需要 CORS 且显式设 `crossOrigin="anonymous"` |

**S3 API 改不了**：`GetBucketCors` / `PutBucketCors` 都返回 `AccessDenied`
（已实测），R2 只允许在 Dashboard 配。

Dashboard → 桶 → Settings → **CORS Policy** → Add，粘贴：

```json
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
```

`AllowedOrigins` 先给 `*`（还没有固定前端域名），买了域名后收紧成具体来源。
配完用 `curl -I -H "Origin: https://example.com"` 确认响应里出现
`Access-Control-Allow-Origin`，再 `curl -X OPTIONS` 确认预检不再是 403。

这一步**不做，前端拿不到 spine 和 obj** —— 图片会正常显示，所以很容易被误判成
"CDN 没问题"，直到加载立绘骨骼时才炸。

如果决定买：Cloudflare Registrar 的 `.com` 是成本价约 $10/年，`.top`/`.xyz` 首年更便宜
但续费贵。绑到 R2 是 Dashboard → R2 → 桶 → Settings → Custom Domains。

**开通门槛**：R2 需要绑定支付方式才能启用（免费额度内不扣款）。

## 4. 首次播种（本地，只做一次）

> **2026-10-22 起，可生成的类别不要再走本节 —— 改走 §4.5 的 CI 全量重生。**
>
> 原因是 `--seed`（§4.2）把「磁盘上这份 = 官方最新版」这个**不可验证的假设**
> 写进 state：state 里记的是当前 CDN 的 md5（`azl_assets.py`
> `prev[key] = info['md5']`），但磁盘上的字节来自 2025-06-06 的旧快照。
> 若这 16 个月里官方替换过某张老立绘，seed 之后它会**永久停在旧版本且不报错**。
>
> 实测反驳了「必须依赖旧快照」的前提：CDN 清单是**全量文件表**而非补丁列表
> （旧仓库 2025-06 的 2,159 个立绘在当前清单里 100% 还在；当前 painting 清单
> 有 6,700 个基础名，是旧仓库的 3.1 倍），且当前数据引用的 2,491 张立绘
> **一张都不缺**。所以从 CDN 现取的原料是齐的，没必要吃这个假设。
>
> 本节只在一种情况下还有用：** `spine` 等脚本没有生成器的类别**（§2 的 B 类）。

### 前置：本地依赖

```bash
python -m venv .venv
.venv/Scripts/activate                 # Windows；Linux/macOS 是 source .venv/bin/activate
pip install -r requirements.txt        # UnityPy / ljd / numpy / opencv-headless

# rclone 是 Go 写的单文件二进制，不是 pip 包
winget install Rclone.Rclone           # Windows
# brew install rclone                  # macOS
# curl -fsSL https://rclone.org/install.sh | sudo bash   # Linux
```

`pip install -r requirements.txt` 在 py3.11 和 3.13 上都实测通过；
`numpy` 锁 2.4.6 是因为 2.5.x 要求 Python >=3.12（见文件内注释）。

### 4.0 先建桶（Dashboard，一次性）

1. R2 → **Create bucket**，名字 `azurlane-assets`，location 选 Automatic
2. 桶 → Settings → **Public Development URL** → Enable，记下 `pub-<32位ID>.r2.dev`
   ✅ 已完成：`https://pub-85b4b8af407244bc8f1502127fc530c3.r2.dev`（见 §3）
3. 右上 **Manage R2 API Tokens** → Create API token
   - Permission: **Object Read & Write**
   - 限定到 `azurlane-assets` 这一个桶
4. 记下 `Access Key ID` / `Secret Access Key` / `Account ID`（secret 只显示一次）

### 顺序不能错

`--seed` 的语义是「产物已经在磁盘上，但 state 里没有这条记录 → 只补记录、不下载」
（见 `azl_assets.py` 第 731 行附近的注释）。**磁盘上没有的东西，`--seed` 帮不上忙。**

所以顺序是：**先搬文件，再 seed，最后补缺口**。

### 4.1 把旧仓库的产物拉到本地

```bash
# 6.8 GB，用 filter 只取需要的
git clone --filter=blob:none --sparse https://github.com/Pelom777/AzurLaneTools.git .tmp/legacy
cd .tmp/legacy
git sparse-checkout set public
cd ../..

# 拷进来，data 由本仓库自己生成，不要旧版
cp -r .tmp/legacy/public/. public/
rm -rf public/data
```

### 4.2 建 state 基线

```bash
python scripts/azl_assets.py --server CN \
  --public public \
  --state .azl/assets-state.json \
  --items painting paintingface squareicon shipicon equipicon iconframe \
  --seed
```

预期：`seeded` 接近 A 类文件对应的资源数，`new = 0`。

这一步不会下载任何 bundle，只读 CDN 清单做 md5 比对。

### 4.3 补 2025-06 之后的缺口

旧仓库停在 2025-06-06，`public/data` 我们已经是新版本，所以这一步会真实产出新图。

```bash
python scripts/azl_assets.py --server CN \
  --public public \
  --cache .azl/bundles \
  --state .azl/assets-state.json \
  --items painting paintingface squareicon shipicon equipicon iconframe \
  --report-files .azl/written.list
```

预期 `new` 在几百量级（对应 108 艘新船及其皮肤/图标）。

### 4.4 全量上传

```bash
rclone copy public r2:azurlane-assets \
  --exclude "data/**" \
  --s3-no-check-bucket --transfers 16 --checkers 32 -P \
  --header-upload "Cache-Control: public, max-age=86400"
```

> 这里排除 `data/**` 是有意的：资产走播种（一次性、6.8 GB），
> 数据走 `update-data.yml`（每小时、21 个文件、短缓存）。
> 两条链路的缓存头不同，混一次传会把 json 也标成 `max-age=86400`。
> 播种完之后跑一次 `update-data.yml`（`force=true`）就会把 `data/` 补上。

耗时参考（6.8 GB）：

| 上行带宽 | 耗时 |
|---|---|
| 10 Mbps | 约 93 分钟 |
| 20 Mbps | 约 47 分钟 |
| 50 Mbps | 约 19 分钟 |
| 100 Mbps | 约 9 分钟 |

## 4.5 资源同步：唯一入口 `.github/workflows/update-assets.yml`

这是现在的**主路径**。热更新和全量重建走同一份代码，而且**不需要人区分** ——
`prep` job 先盘点「有多少资源已过期或缺失」，再按每片 400 个算出分几片。

| 场景 | 盘点结果 | 自动分片 |
|---|---|---|
| 日常热更（改了几张图） | 几个~几十个 | 1 片 |
| 攒了一批没同步 | 几百个 | 1~2 片 |
| 首次 / `force` 全量 | 数千个 | 6~16 片 |

### 为什么合并成一个 workflow（2026-10-22）

合并前有两个：`sync-assets`（热更新）和 `regen-assets`（全量）。
6 个步骤逐字重复，但更要命的是**语义相反**：

| | 是否 `--seed` | 后果 |
|---|---|---|
| sync-assets | 传了 | 远端有、state 没记 → 跳过，等于断言「远端那份就是最新版」 |
| regen-assets | 刻意不传 | 同上情况 → 重新生成 |

同一个 state 文件在两个入口得出相反结论，是一颗迟早会踩的雷 ——
而 `--seed` 那条断言**无法验证**。这个担心不是理论：2026-10-22 上午游戏热更
`9.7.398 → 9.7.410`，盘点发现 `bisimai` / `shaenhuosite` / `u556` / `yueke_ger`
四张**已入库**的立绘 md5 变了 —— 如果当初用的是 seed，这四张会永远停在旧版。

现在统一为：**默认不用 seed**，需要时 `seed=true` 显式打开，
只该用于「把已由别的途径入库的存量资产接进台账」这一件事。

### 盘点是怎么算的（`azl_assets.py --plan`）

不是另写一套统计，而是**主循环里的一个分支** —— 枚举、分片键、引用集过滤、
`_tex`/`-mesh` 排除，全部与正式运行同一份代码，所以这个数不会和工作量漂移。

判据只有一条：`state 里的 md5 != 当前清单的 md5`。

刻意**不看**「产物是否在磁盘」这一半判据：CI 是干净 checkout，产物本来就不在，
带上它会把已是最新的资源全部误算成待处理。所以这是**下界** —— 远端对象丢失
也会产生真实工作量，但那属于自愈范畴，不该影响分片决策。

盘点不导入 UnityPy / cv2（两者都是函数内导入），所以 `prep` 不装任何依赖，约 20 秒。

### 分片数怎么定

```text
shards = ceil(pending / 400)     上限 16，下限 1
```

400 这个数按 painting 标定：单张约 1.5 MB 下载 + 3.3 MB 产物 ≈ 4.8 MB，
400 个 ≈ 1.9 GB 峰值，安全落在 runner 的 14 GB 磁盘内（预检阈值取 6 GB）。

`shards=0` 表示自动（默认）；填 1..16 可强制覆盖。**盘点为 0 时仍会跑 1 片** ——
那一片会用完整判据（含远端 marker）复核一遍，负责「state 说做过、但远端丢了对象」
的自愈场景。跳过它就等于放弃自愈。

### 为什么全量必须分片

painting 全量约 **2,491 张 / 下载 3.7 GB / 产物 5.6 GB**。托管 runner 的 `/`
只有约 14 GB，单 job 峰值约 9.3 GB 且中途无法回收。所以按
`crc32(名字) % N` 切成 N 片并行，每片跑完立刻上传、立刻丢弃，
磁盘占用与 N 成反比（N=6 时每片约 1.6 GB）。

### 用法

```text
热更新：   gh workflow run update-assets.yml            # 默认参数即可，分片自动
全量重建： gh workflow run update-assets.yml -f force=true
调管路：   gh workflow run update-assets.yml -f items=painting -f limit=3
```

| 输入 | 默认 | 说明 |
|---|---|---|
| `items` | 6 类全导 | 空格分隔；`painting` 会顺带产出 `paintingface` |
| `shards` | `0` | **0 = 按盘点自动定**；1..16 强制指定 |
| `scope` | `used` | 按 `public/data` 的引用集过滤 |
| `limit` | `0` | 每类每片最多几个，调试用（不影响盘点口径）|
| `seed` | `false` | **只在一次性迁移存量时打开** |
| `force` | `false` | 忽略已存在的 md5，连未变的也重做 |
| `dry_run` | `false` | 只生成，不拉远端、不上传、不提交 |

### 前置条件

`public/data` 必须是**当前版本**的数据 —— 它是 `--scope used` 的引用集来源。
数据旧了会漏掉新船（实测旧 16 个月的数据漏 333 张立绘）。
不放心就先 dispatch 一次 `update-data`（`force=true`）把它刷新。

### 为什么它「以后更新时也能用」

关键在 state 的处理，三个细节缺一不可：

1. **每个分片继承基线 state**（`cp .azl/assets-state.json .azl/state-N.json`）。
   一旦各片从空 state 起手，每次 dispatch 都会全量重解一遍 —— 那就退化成
   一次性脚本了。继承之后：首次没记录 → 全量重生；之后只有官方改过的
   （md5 变了）才重做。
2. **配合 `mark_remote`** 把「远端已有对象」翻译成本地 marker，使判据的第二个
   条件（产物在磁盘）在干净 checkout 里也成立。
3. **刻意不加 `--seed`**。seed 会在「远端有、state 没记」时跳过，正好把上面
   那个不可验证的假设写进来。宁可多花几分钟重解，也不要静默停在旧版。

### 合并 state 的规则（容易写错的一处）

每个片段都是从**同一份基线**复制出来的，所以它里面既有本片真正处理过的 key，
也有照抄来的基线值。直接按文件名顺序 union，排在后面的片段会用**旧的基线值**
覆盖前面片段刚算出的新值 —— 表现是「这一轮跑了，下一轮还在跑同一批」。

正确做法是按 key 做差分：只有「与基线不同的那个值」才是本片的新成果。
若同一个 key 冒出两个不同的新值，说明分片键漂了，保守保留基线并告警。

### 成本

| 项 | 数值 |
|---|---|
| 下载 bundle | 3.7 GB（6 片分摊，每片约 0.6 GB） |
| 产物 | 5.6 GB（每片约 0.9 GB） |
| 渲染 | 实测单张约 2.2 s → 6 片并行约 20 分钟（含装依赖） |
| Actions 分钟 | 6 × 约 22 ≈ 130 分钟 / 次全量（free 额度 2000 分钟/月） |

> 全量重生的代价是一次约 130 分钟的 Actions 配额，别频繁跑；
> 日常增量走 `update-assets.yml` 的默认参数（`shards` 留 0 让它自动定）。

## 4.6 两条上传链路：data 归 update-data，图片归 update-assets

R2 桶要能和仓库的 `public/` 对上，前端把 `VITE_CDN` 指过来就能跑：

```text
public/data/ship.json        →  r2:<bucket>/data/ship.json
public/painting/x/x.png      →  r2:<bucket>/painting/x/x.png
public/squareicon/y.png      →  r2:<bucket>/squareicon/y.png
```

两条链路的**分工是「谁产出谁上传」**，不是按文件大小分的：

| 链路 | 产出方 | 上传步骤 | 缓存头 |
|---|---|---|---|
| `data/` | `update-data.yml`（解包 Lua → `build_data.py`） | 「发布 data 到 R2」 | `max-age=300, stale-while-revalidate=60` |
| 图片 / mesh | `update-assets.yml`（`azl_assets.py`） | 各分片的「上传」 | `max-age=86400` |
| 立绘 `*.json` | 同上 | 同一分片里**单独一趟** | `max-age=300, stale-while-revalidate=60` |

**为什么数据必须短缓存**：图片路径里没有内容 hash、但内容几个月才变一次；
数据每天可能变几次，长缓存会让前端在上游更新后仍读到旧值（见 §6）。

**为什么立绘 json 也归短缓存那一档**（2026-10-22 修）：json 是索引 ——
前端靠它决定去取哪些图层。曾经它跟着图片一起标 `max-age=86400`，结果是
删掉 `shop_hx` 图层之后，桶里内容当天就干净了，但已经拿过旧 json 的浏览器
最长要 24h 才换新版，这期间一直在请求已被删除的 `shop_hx.png`（404）。
所以分片上传拆成两趟：`*.json` 走 300s，其余走 86400。

已经上传过的旧对象不会因为改代码就换头。要刷存量，dispatch
`update-assets` 时勾 `refresh_headers=true`：它把桶里所有 `*.json`
拉下来再传回去，只改元数据，不做生成、不动 state，随时可以重跑。

上传步骤刻意放在 `commit` **之前**：上传失败就不提交，`version.json` 不前进，
下一轮自动重试。反过来先提交再上传，会出现「git 说已更新、CDN 还是旧值」
且没有任何东西会去修的状态。

> 注意 `data/`（仓库根目录）里的 `version.json` / `new-ledger.json` 是**内部账本**，
> 不上 CDN。前端读的是 `public/data/` 下那份。

## 4.7 站点不收录的两类资源：`_hx` 与 `_n`

### `_hx`：国服删改版

国服对部分立绘、图标做过删改，游戏里以 `_hx` 结尾另存一份，例如：

```text
painting/2b_hx              painting/2b_bj1_hx_tex
painting/baifeng_2/ 里的 shop_hx 图层
paintingface/bangkeshan_2_hx_0.png
squareicon/xxx_hx.png
```

站点**从不收录**这类资源。依据不是猜的：对照原前端仓库 `AzurLaneTools` 的
`public/painting`（2,159 个目录），含 `_hx` 的是 **0 个**。

所以在两个地方做了硬过滤（`common.is_censored`，正则 `_hx($|_)`）：

| 位置 | 作用 |
|---|---|
| `azl_assets` 的目标枚举 | 跳过所有 `_hx` 资源，放在引用集判断**之前**，`--scope all` 也挡得住 |
| `painting_site._walk` | 拼图 json 里不出现 `_hx` 图层（整棵子树跳过） |

**为什么必须写在代码里而不是靠 `--scope used`**：`paintingface` 不在
`USED_SCOPED` 里（它不按引用集过滤），所以清单里的 `_hx` 表情包会被整包导出；
立绘的 `shop_hx` 图层则来自 layout 里的 GameObject 名，与引用集无关。

**已清理的存量**（2026-10-22）：188 个对象 —— 14 张立绘里的 28 个
`shop_hx.{png,-mesh.obj}`，加 160 个 `paintingface/*_hx_*.png`。
那 14 张立绘的台账条目已删除，重跑后 json 不再含 `shop_hx`。

### `_n`：未收录的差分立绘（独立包）

游戏里还有一批 `_n` 变体，是**独立的资源包**（不是本体包里的节点）：

```text
painting/2b_n          painting/2b_n_hx        painting/yan_n
painting/banjiu_n_ex   painting/wuzang_h_n      painting/a2_n
```

同样一条都不需要。三条独立证据：

| 证据 | 结果 |
|---|---|
| 旧前端仓库 `public/painting`（2,159 个目录） | 含 `_n` 的 **0 个** |
| 当前清单（4,548 个立绘名） | 带 `_n` 的 **1,538 个** |
| `public/data` 引用集（2,925 个名字） | 带 `_n` 的 **0 个** |

过滤点只有一个：`azl_assets` 的目标枚举，位置在引用集判断**之前**
（`common.is_n_variant`，正则 `_n($|_)`），所以 `--scope all` 也不会去下这些包。

**实测效果**：`--plan --scope all` 的待处理从 **1,626 → 261**，
少掉 1,365 个包（`_n` 与 `_hx` 有 173 个交集）。

**这条只管立绘目录，不管包内图层**：`nengdai_idol.json` 里有一个
`nengdai_idol_n` 图层，旧仓库也保留着它，所以 `_walk` 不加这条过滤。
换句话说，`is_n_variant` 只用来筛「要不要导这个包」，不用来筛「包里的节点要不要」。

存量：桶里 2,505 个 painting 目录、state 里 8,068 条，`_n` 均为 **0**
（`--scope used` 一直在挡），所以这次不需要清理，只是把规则写进代码。

### `_asmr`：与本体重合的语音版皮肤（2026-10-23 起不收录）

`hu_2_asmr` / `aimudeng_5_asmr` / `haichou_2_asmr` / `jishang_3_asmr` 这 4 个是
ASMR 语音版皮肤，立绘与本体是**同一张图**，只是挂了语音。收录它，
前端就会列出两条同名皮肤（`hu_2` 与 `hu_2_asmr` 都叫「临水的辰星」）。

过滤规则：`common.is_asmr()`（正则 `_asmr($|_)`），两个落点：

| 链路 | 落点 | 效果 |
|---|---|---|
| 数据 | `build_data_impl.build_skin` | `skin.json` 不再产出 `_asmr` 条目（2,505 → 2,501） |
| 资源 | `azl_assets` 目标枚举（引用集判断之前） | 不下载、不导出这些包，`--scope all` 也挡得住 |

**执行顺序是硬依赖**：数据侧先剔除，资源侧才清得动。
`scripts/prune_assets.py` 会拿 `public/data` 的引用集挡一道 ——
名字还在引用集里就 `die`，防止删出线上缺图。
所以流程是：

```bash
# 1）先让数据不含 _asmr（force=true 强制重解，contract 通过才提交）
gh workflow run update-data.yml -f force=true
# 2）再清资源（脚本会先列一遍对象数与体积）
gh workflow run update-assets.yml -f prune="hu_2_asmr aimudeng_5_asmr haichou_2_asmr jishang_3_asmr"
```

`prune` 的三道安全线：不传 `--apply` 只列不删；名字必须是 `[a-z0-9_]+`；
在引用集里的直接拒绝。

> 计数用 `rclone size`，**不要换回 `lsjson`**：实测 `lsjson <前缀>` 对
> `painting/hu_2_asmr` 只报 1 个对象，而那个目录里实际有 json + png 两个
> （本体 `hu_2` 同样是 2 个）。删除本身是按整个前缀删的，不受这个数影响 ——
> 但预览报 1 个、实际删 2 个，这种数字比没有更糟。

**执行结果（2026-10-23 00:48，run `39817047475`）**：4 个目录全部删除，
json / png / mesh / face 四类 URL 复查全部 404，本体 `hu_2` 仍是 200；
台账清掉 4 条。之后连跑两次同一命令验证幂等：0 个对象、state 无变化、跳过提交。

> `data/new-ledger.json` 里那 4 条历史记录**保留**（它是内部新增台账，不上 CDN，
> 也不影响站点）。删它等于抹掉历史。

## 4.8 R2 与旧仓库的文件树差异（2026-10-22 全量比对）

删掉扁平 `paintingface` 之后，桶里 **28,151 个对象 / 10.01 GB**（旧仓库 19,443 / 6.99 GB）。
逐目录比对：

| 目录 | R2 | 旧仓库 | 差 | 说明 |
|---|---|---|---|---|
| painting | 9,746 | 7,832 | +1,914 | 2,505 vs 2,159 张，且当前版本图层更多 |
| paintingface | 12,626 | 290 | +12,336 | 覆盖扩张：旧仓库只给 42 张做了表情，现在 2,092 张；旧的 42 张全在其中 |
| spine | **0** | 6,363 | −6,363 | **未搬**，脚本无生成器（见 §7） |
| squareicon | 2,507 | 2,164 | +343 | 新船 |
| shipicon | 2,502 | 2,157 | +345 | 新船 |
| equipicon | 496 | 495 | +1 | |
| iconframe | 141 | 9 | +132 | 未按引用集过滤；前端只用到 `<稀有度>.png` / `1<稀有度>.png`，约 12 个 |
| shipbackground / juusui / iconbackground / voteicon / squareback / spinebase | 70/14/9/8/5/5 | 同 | 0 | 完全一致 |
| data | 22 | 21 | +1 | 多 `new-latest.json`（新增台账，正常） |
| _headers | 0 | 1 | −1 | Cloudflare Pages 的响应头文件，R2 用不上 |

**异常扫描**（应为 0）：`painting/` 下扁平文件 **0**、任何位置含 `_hx` **0**、
0 字节对象 **3**（`data/2017-cn.json`、`2019-en.json`、`2019-jp.json` —— 仓库里本来就是空文件）。

**语义抽查**：随机 40 张立绘，取 json 后逐层核对
「每个图层的 `.png`、非 `raw` 图层的 `-mesh.obj`、`face.list` 里每个序号」是否都在桶里 —— **全部齐全**。

唯一的实质缺口是 `spine`。

## 4.9 普通 Transform 会截断图层遍历（已修）

`painting_site._walk` 原来第一句是：

```python
if trans.object_reader.type.name != 'RectTransform':
    return
```

本意是「普通 Transform 没有 size/pivot/anchor，当不了图层」。但它连带把**整棵子树**
丢掉了 —— 而子树里可能有真图层。

`bisimaiz` 就是现场：

```text
bisimaiz (RectTransform, 有 m_Sprite)          ← 导出成基底层
  layers
    bisimaiz_rw
      layers          ← 普通 Transform，在这里 return，下面全丢
        part (m_Sprite)
        part (m_Sprite)
```

结果是那张立绘只有基底层，画面上少一块。对照旧仓库 `public/painting/bisimaiz`
是 7 个文件，我们只有 3 个。

修法：非 RectTransform 不当图层，但**继续遍历子节点**。

影响面：两边都有的 2,157 张立绘里，**39 张**的文件数比旧仓库少（`bisimaiz` 差 4 个最多）。
修完对全部 painting 做了一次 `force=true` 重导。

图层命名：`part` / `part_2`（旧仓库当年是 `bisimaiz_rw0` / `rw1`，因为那时节点名就是那个）。
前端只用 key 拼文件名，功能无差别，不做额外美化。

## 4.10 改造版 `<name>_ex`：独立资源包，产物写进本体目录

游戏里 `painting/<name>_ex` 是**独立资源包**，不是本体包里的一个节点。
旧仓库把它的产物平铺在本体目录里：`public/painting/yan/yan_ex.json` +
`yan_rw_ex.png`。我们照这个布局。

### 三个必须对齐的事实

1. **包内基底层叫 `<name>_ex`，但 json 里的 key 必须写回本体名 `<name>`。**
   前端 `loadPainting(name, json)` 取基底尺寸与枢轴用的是 `json[name]`：

   ```js
   const baseSize = json[name]['size']
   ```

   key 写成 `yan_ex` 会直接读到 `undefined`，整张立绘崩掉。

2. **产物写进本体目录是安全的。** 8 个 `_ex` 包的 externals 全部指向
   本体的 `<name>_tex` / `<name>_front_tex`（外加自己的 `<name>_rw_ex_tex`），
   也就是说它**没有自己的基底贴图**，写出来的 `yan.png` 与本体的逐字节相同，
   覆盖是空操作。实测 `yan.png` 导出前后 md5 一致。

   代码里仍留了一道兜底：写进本体目录前先留底，写完发现基底层字节变了就
   **还原并告警**，绝不静默改掉本体。

3. **`_ex` 不在引用集里，要跟随本体。** `public/data` 引用的是 `yan`，没有
   `yan_ex`。所以引用集判断要先剥掉 `_ex` 后缀：

   ```python
   is_ex = name.endswith('_ex') and name[:-3] in base_names
   scope_name = name[:-3] if is_ex else name
   ```

   `base_names` 是清单里「本身是个立绘」的集合，所以 `yan_n_ex` 会归到
   `yan_n`（清单里确实有），而不是凭空造一个 `yan_n`。

### 前端改动：0 行

现有代码拼的就是 `painting/${name}/${name}${ex.value ? '_ex' : ''}.json`，
和本布局完全一致。图层也用同一个 `name` 目录，不需要额外参数。

### 覆盖范围

清单里 15 个 `_ex` 条目，其中 8 个本体在引用集里：

```text
banjiu  feiniao  gaoxiong_6  xishao  xuebugui  xuequan  yan  zi_shanluan
```

其余 7 个是 `_n_ex`，被 §4.7 的 `_n` 规则直接挡掉（它们在引用集里也是 0）。
每个 `_ex` 只多产 3 个文件（`<name>_ex.json` + `<name>_rw_ex.png` + mesh），
基底与正面贴图复用本体的，不重复占空间。

## 4.11 比对旧仓库时，以 json 的引用为准，不要数文件

旧仓库的管线会把所有 `painting/<x>*` 的包都倒进 `<base>/` 目录，于是留下
一堆**谁都不引用**的 png。按文件数比对，会把这些当成「我们缺东西」。

2026-10-22 那轮就误判了 6 张：

| 立绘 | 旧仓库多出的文件 | json 引用情况 |
|---|---|---|
| `kelong` | `kelong-ui.png` / `-mesh.obj` | 旧 json 只有 `kelong` 一个键，没引用 |
| `kenisibao` | `kenisibao-ui.png` / `-mesh.obj` | 同上 |
| `wuzang` | `wuzang_building.png` / `-mesh.obj` | 旧 json 3 个键，没引用 |
| `siwangzhuzai` | `siwangzhuzai_bj1.png` / `-mesh.obj` | 旧 json 3 个键，没引用 |
| `nengdai_idol` | `nengdai_idol_rw.png` / `-mesh.obj` | 旧 json 2 个键，没引用 |
| `tiancheng_cv` | — | **真差异**：旧版 `_rw1`+`_rw2`，当前包只有一个 `_rw` |

前 5 张我们的键集合与旧 json **完全一致**，不需要动。只有 `tiancheng_cv`
是游戏侧改结构（画布 5550×4290 → 3656×2976，两个 rig 图层合成一个）。

正确做法：取旧 json，把 `key` 展开成需要的文件名
（`<key>.png` + 非 `raw` 的 `<key>-mesh.obj`），再看差集。
未被引用的文件一律不算缺口。

## 4.12 每船小人的 Spine 三件套（2026-10-23）

站点要的 spine 不是 `*_skeletondata`（那是演习台、图标动效，13 个小件），
而是**每船配套的 Q 版小人**，游戏里在：

```text
spinepainting/<皮肤名>_res
```

同一前缀下还有 239 个不带 `_res` 的包，那是场景 prefab（只有
`GameObject` / `SkeletonGraphic` / `CanvasRenderer`，没有 Texture2D 和 TextAsset），
真正的图与骨架全在 `_res` 里。

产物布局（与旧前端仓库 `public/spine` 一致）：

```text
spine/<皮肤名>/<页名>.png      ← 一页图集一张，实测最多 5 张 4096²
spine/<皮肤名>/<皮肤名>.skel
spine/<皮肤名>/<皮肤名>.atlas
```

三个容易踩的点：

1. **目录名与 skel/atlas 名用去掉 `_res` 的包名**（`adaerbote_4_res` → `adaerbote_4`），
   这就是站点拼路径用的皮肤名。
2. **png 用各自的贴图名**。多页图集里 atlas 正文写的页名就是它
   （`aerbien_4.png` / `aerbien_42.png`），改名会让 atlas 指向不存在的文件。
3. **TextAsset 名要剥 `.txt`**。Unity 存的是导出工具的原始文件名，
   Spine 侧常写成 `<name>.atlas.txt`，前端要的是 `<name>.atlas`（有则剥、无则原样）。

现状（CN 9.7.416）：`_res` 包 **224 个**，剔除 3 个 `_asmr` 后 **221 套可用**；
下载量合计 **2.45 GB**（中位数 9.9 MB，最大 39.8 MB），产物量级与下载相当。

> 与旧仓库的差距：旧前端 `public/spine` 有 2,121 个目录 / 6,363 个文件，
> 而当前版本游戏只有 224 个 `_res`。差的是历史皮肤 —— 游戏侧已经不再提供，
> 只能从旧仓库补，不能靠解包追平。

跑法（spine 不在默认 items 里，因为它的 marker 路径要等下载后才知道，
加进默认会让每轮都重做一次）：

```bash
gh workflow run update-assets.yml -f items=spine -f scope=all
```

## 5. 增量同步（CI）

### 5.1 凭据：4 个 secrets

`.github/workflows/update-assets.yml` 已经把 rclone 的 remote 全写成 `RCLONE_CONFIG_R2_*`
环境变量，不落任何配置文件。仓库 Settings → Secrets and variables → Actions 加：

| Secret | 值 | 来源 |
|---|---|---|
| `R2_ACCOUNT_ID` | 账号 ID（32 位 hex） | Dashboard 右侧栏 / R2 Overview |
| `R2_ACCESS_KEY_ID` | token 的 Access Key ID | §4.0 第 3 步 |
| `R2_SECRET_ACCESS_KEY` | token 的 Secret Access Key | §4.0 第 3 步（只显示一次） |
| `R2_BUCKET` | `azurlane-assets` | 自己起的名字 |

workflow 里第一步「检查 R2 凭据」会逐个点名缺失项，不会让 rclone 抛 AWS 的 XML 错误。

本地手动同步时用同一组值：

```bash
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID=...
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=...
export RCLONE_CONFIG_R2_ENDPOINT=https://<account_id>.r2.cloudflarestorage.com
export RCLONE_CONFIG_R2_REGION=auto
```

### 5.2 空工作区陷阱（最容易踩的一处）

`azl_assets.py` 判断"这个资源处理过没有"，要求**两个条件同时成立**：

```python
if prev.get(key) == info['md5'] and marker_file.exists() and not args.force:
    report['skipped'] += 1
    continue
```

- `prev` 来自 `.azl/assets-state.json`（进 git，CI 里有）
- `marker_file` 在 `public/painting/...` 下（**被 .gitignore 挡在 git 之外，CI 里没有**）

所以 CI 每次都是干净 checkout，第二个条件永远为假 —— 即使 state 完整，
每一轮也会把 13,000 个资源全部重下重解（5.9 GB / 2~4 小时），并重复上传同一批对象。

解法不是改代码，而是**把"远端已存在"翻译成本地的 marker 文件**：
`rclone lsf` 拉一份远端 key 列表，`scripts/mark_remote.py` 按同名路径 `touch` 出空文件。

配合 `--seed`，四种 state × 远端组合都能收敛：

| state | 远端 | 行为 |
|---|---|---|
| 有 | 有 | 跳过（正常增量） |
| 无 | 有 | `--seed` 补记录后跳过（手工搬的 spine 不会重下） |
| 有 | 无 | 重新生成 + 上传（远端丢了能自愈） |
| 无 | 无 | 生成 + 上传（新资源） |

`mark_remote.py` 只补两类 marker，与判据一一对应，其余对象（`painting/*.png`、
`*.obj`、`spine/*.skel`、`data/*.json`）全部忽略：

```
painting/<name>/<name>.json      ← painting 的 marker 是 json，不是 png
<item>/<name>.png                ← 其余类别
```

**两个 rclone 参数不能省，都是实测踩出来的（2026-10-21）：**

| 参数 | 省了会怎样 |
|---|---|
| `lsf` 的 `--recursive` | `rclone lsf` 默认**只列一层**。产物全在 `<item>/` 子目录下，清单恒为空 → marker 永远补 0 个 → §5.2 的解法**静默失效**，CI 照旧全量重跑。表现是日志里「远端已有 0 个对象」，而桶里明明有东西。 |
| `copy` 的 `--s3-no-check-bucket` | R2 对**限定单桶的 token** 的 `HeadBucket` 返回 403，rclone 误判「桶不存在」→ 尝试 `CreateBucket` → 同样 403 → 报 `S3: CreateBucket ... AccessDenied`。桶是手工建的，不需要它探测或创建。 |

同一条 token 的权限边界（实测）：`ListObjectsV2` 可以，`ListBuckets`（`rclone lsd r2:`）和
`GetBucketVersioning` 是 403 —— 都是正常的，前者是限定单桶，后者 rclone 只是打个警告。

### 5.3 完整流程（workflow 实际执行的顺序）

```bash
# 0. 拉远端清单（桶为空时是空文件，不算失败）
rclone lsf r2:azurlane-assets --files-only --recursive > .azl/remote.lst

# 1. 补 marker，让增量判据在干净工作区成立
python scripts/mark_remote.py --listing .azl/remote.lst --public public

# 2. 生成（清单里是本次真正写出的文件）
python scripts/azl_assets.py --server CN \
  --public public \
  --cache .azl/bundles \
  --state .azl/assets-state.json \
  --items painting paintingface squareicon shipicon equipicon iconframe \
  --scope used \
  --report-files .azl/written.list \
  --seed

# 3. 清单转 rclone 格式（NUL → 换行，去掉 public/ 前缀）
tr '\0' '\n' < .azl/written.list | sed 's|^public/||' | sed '/^$/d' > .azl/upload.lst

# 4. 只推这次写出的，不做远端比对
rclone copy public r2:azurlane-assets \
  --files-from .azl/upload.lst \
  --ignore-times --s3-no-check-bucket --transfers 16 --checkers 32 \
  --header-upload "Cache-Control: public, max-age=86400"
```

`--ignore-times` 是刻意的：清单已经是精确的变更集，再用 rclone 逐文件比对只会
白白消耗 Class B 操作。

**不要用 `rclone sync`**：它会删除远端多余文件，而 spine 是我们手工搬上去的，
不在任何一次生成的清单里，会被一并删掉。用 `copy`。

### 5.4 state 必须进 git

`.azl/assets-state.json` 是「哪些资源已经出过、md5 是多少」的唯一判据。

| 情况 | 后果 |
|---|---|
| state 在 | 每次只重出 md5 变了的，几十到几百个 |
| state 丢了 | 配合远端 marker 还能靠 `--seed` 补回来，但要重新拉一遍清单 |

**`--seed` 让 state 丢失不再是灾难**（这是加 marker 补齐后的额外收益）：
远端有对象、state 没记录时会补记录而不是重下。

`.gitignore` 已加规则，只放行 state：

```gitignore
.azl/bundles/
.azl/*.list
.azl/*.lst
```

`bundle` 缓存到底要不要走 `actions/cache`，**目前决定不走**：
增量轮只需要下几十个包（每个几 MB），而缓存几个 GB 会挤掉
`lua-CN-<stamp>` 那份 10 GB 配额里的位置。首次全量在本地做，不经过 CI。

### 5.5 触发方式：独立 workflow，不挂 cron

`.github/workflows/update-assets.yml`，只有 `workflow_dispatch`。

**没有嵌进 `update-data.yml`** 的原因：两条链路的失败面完全不同
（UnityPy 解码 / 大文件网络 / R2 凭据），图片跑挂了不该把数据更新一起拖红
——数据 10 分钟能修好，图片可能要几小时，混在一个 job 里会让 cron 反复重跑数据。

> 2026-10-22 更新：它**没有 cron，但也不靠人手点了** —— `update-data.yml`
> 在「版本真的变了 + 契约校验通过 + 不是干跑」时用 `gh workflow run` 自动踢它
> （见 §4.6 的顺序说明）。它自己盘点待处理量、决定分几片。
> 剩下的空档：游戏**连续几周不更新**时它就一直不跑，失败分片和远端丢失没人修 ——
> 这是当前已知的、尚未补的自愈缺口。

### 5.6 `commit.py` 的「推送成功」不可信（加固过两次）

**第一次现场**：run `39641164165` 的 merge job 日志打印了

```
推送失败（1/3）：! [rejected]  HEAD -> main (fetch first)
[azl] 已推送到 main
```

但远端 `main` 上**没有**那次提交。三种方式交叉核验过：

- `git ls-remote --heads origin main` → `25fb6c4`（我手工提交的那条）
- `contents?path=.azl/assets-state.json` → 只有 8 条（全部 squareicon）
- `commits?path=.azl/assets-state.json` → 只有 2 条历史

**根因**：`commit_and_push` 把 `git push` 的返回码 0 直接当成成功。
而 git 在远端已包含 HEAD 时打印 `Everything up-to-date`，**同样返回 0**。

**第一次加固**（不够）：推完回读 `ls-remote`，比对是否等于本地 HEAD。

**第二次现场**（2026-10-22，`update-data` run `39725189756`）：日志又是
`已推送到 main（已回读远端校验）`，但远端仍是上一条提交。原因：
`git pull --rebase` 一旦把提交丢掉，**HEAD 会被挪到远端位置上**，
此时 `HEAD == origin/main` 成立 —— SHA 比对看不出任何异常。

所以 SHA 比对天生有盲区：

| 实际情况 | push rc | HEAD == 远端 |
|---|---|---|
| 我推上去了 | 0 | 是 |
| 提交被 rebase 丢掉 | 0 | **也是** |

**第二次加固**：提交后、推送前，先记下每个目标路径的 tree/blob sha；
推送后回读 `origin/<branch>:<path>` 比对。内容对上才算到了。

```python
snapshot = {p: git("rev-parse", f"HEAD:{p}") for p in paths}
...
if proc.returncode == 0 and landed(cwd, branch, snapshot):
    log(f"已推送到 {branch}（已回读远端内容校验）")
```

本地用裸仓库复现过两种情形：远端内容已变 → `landed()` 返回 False；
正常竞争（远端前进 + 本地提交 → rebase → 推送）→ 通，且远端真的有那个文件。

**遗留**：`--paths-from` 模式没有可比对的目录粒度，会退回 SHA 校验（较弱）。
目前没有任何 workflow 用它，真要启用得补上。

### 5.7 失败项要点名（2026-10-22）

以前分片账目里只有 `failed: N` 一个数字，日志一滚就查不到是哪个资源、为什么
（2026-10-22 那轮「失败 1」，翻了几万行日志才定位到 `iconframe/10003`）。

现在：

- `azl_assets.py --report-failures <file>` 把每个失败资源写成 json
  （`path` / `item` / `size` / `reason`），0 个失败也写文件；
- 分片把它塞进 artifact，merge job 跨片汇总成 `.azl/failures.txt`，
  贴成摘要里的表格，并对前 20 条发 `::warning::`；
- 日志里同样列前 20 条（全量重跑时若上游挂了，失败会是几百个，刷屏会把原因埋掉）。

口径：**失败项刻意不写 state**，所以下一轮 dispatch 会自动重试。
这份名单回答的是"这一轮谁没过"，不是"永久失败清单"。

## 6. 缓存策略：一个必须知道的坑

站点拼 URL 的方式是 `${cdn}/painting/${name}/${name}.png` —— **路径里没有内容 hash**。
立绘更新时是覆盖同名文件。

所以不能无脑设 `immutable`：

| 设法 | 效果 | 评价 |
|---|---|---|
| `max-age=31536000, immutable` | 更新后用户一年看不到新图 | ❌ |
| `max-age=86400` | 最长 1 天生效 | ✅ 简单，推荐先用 |
| URL 加 `?v=<azhash>` + immutable | 立即生效且缓存永久 | ✅ 最佳，但要改前端拼接函数 |

r2.dev 因为没有 Cache 规则，实际上只看对象上的 `Cache-Control` 元数据，
所以 `--header-upload` 设的值就是最终值。

## 7. 缺口处理状态

| 缺口 | 状态 |
|---|---|
| `requirements.txt` 缺 opencv | ✅ 已补 `opencv-python-headless==5.0.0.93`；numpy 锁 `2.4.6`（2.5.x 要 Python ≥3.12，CI 是 3.11） |
| `.azl/` 没被 gitignore | ✅ 已加规则，state 仍进 git |
| CI 空工作区导致全量重跑 | ✅ 用 `scripts/mark_remote.py` 补 marker 解决，见 §5.2 |
| `rclone lsf` 缺 `--recursive` 使上面那条失效 | ✅ 已修（2026-10-21 实测发现），见 §5.2 |
| R2 受限 token 触发 `CreateBucket` 403 | ✅ 已加 `--s3-no-check-bucket` |
| `spine` 无法自动 | ✅ 每船小人（`spinepainting/*_res`）已可导出，见 §4.12；⚠️ 当前版本只有 221 套，历史皮肤仍缺（旧仓库 2,121 套） |
| `HANDOFF.md` 写着"已支持 spine" | ⚠️ 表述有误，实际不支持 |
| 单 job 跑不下 painting 全量 | ✅ crc32 分片并行，见 §4.5 |
| 每个分片从空 state 起手 ⇒ 每轮全量 | ✅ 改为继承基线 state + 差分合并，见 §4.5 |
| 两个入口对 `--seed` 语义相反 | ✅ 合并为 `update-assets.yml`，seed 默认关，见 §4.5 |
| `commit.py` 的「推送成功」不可信 | ⚠️ 已加推后回读校验，见 §5.6 |
| 改造版 `_ex` 完全没导出 | ✅ 已导（8 张），内联进本体目录，前端零改动，见 §4.10 |
| `_n` 差分立绘被当成正常资源 | ✅ 硬过滤（1,538 个包），见 §4.7 |
| 旧仓库里一堆没有 json 引用的孤儿 png | ✅ 不是缺口 —— 它的 json 本来就不引用，按文件数比对会误判，见 §4.11 |
| `tiancheng_cv` 图层与旧仓库不同 | ✅ 游戏侧改动（两个 `_rw` 合成一个，画布 5550×4290 → 3656×2976），非缺陷 |
| 普通 `Transform` 截断图层遍历 | ✅ 已修，见 §4.9 |
| 失败项只有计数、查不到是谁 | ✅ `--report-failures` 点名 + 跨片汇总，见 §5.7 |
| 三个验收用临时 workflow 未清 | ✅ p0-probe / scripts32-probe / stage1-verify 已删（2026-10-22） |
| `seed-legacy.yml` | ⚠️ **保留**：spine（6,363 文件 / 1.10 GB）还没搬，它是搬存量资产的唯一入口，搬完再删 |
| json 与图片共用 86400 缓存头 | ✅ 已按扩展名分开，见 §4.6 |
| `_asmr` 皮肤让前端列出两条同名项 | ✅ 数据与资源两侧都不收录，存量已删，见 §4.7 |
| 不收录的资源不会自动从桶里消失 | ✅ `prune` 入口（先预览再删 + 引用集拦截），见 §4.7 |

## 8. 验收标准

- [ ] R2 桶内对象数 = `19,422(资产) + 21(data) = 19,443`（spine 含在内）
      当前 **2**（联调产物 `squareicon/22.png`、`33.png`），等首次播种
- [ ] `data/` 下 21 个 json 可从 CDN 读到，且 `Cache-Control` 是
      `public, max-age=300, stale-while-revalidate=60`（不能是长缓存）
- [x] 公共读可用：`GET /squareicon/22.png` → `200` `image/png` 27,131 字节，
      md5 `68c03aeb…` 与桶内一致；不存在 key → `404`（2026-10-21）
- [ ] 任意抽 5 张立绘，`curl` 回来的字节数与原文件一致（原样托管，不做转码）
      已验 **1/5**
- [ ] CORS 已配（§3.1）。**不做这条，spine 和 obj 拉不到**，但图片正常，
      所以很容易误判成「CDN 没问题」
- [ ] `azl_assets.py --seed` 第二轮：`new = 0`、`seeded` 不再增长（state 接上了）
- [x] **CI 第二轮：`mark_remote` 补齐数 = 远端 marker 数，`跳过` 数 = 资源总数，
      `新增` = 0**（run `39612271958`：远端 2 → 补齐 2 → 跳过 2 / 新增 0）
- [ ] 增量轮：改一张图后重跑，`written.list` 只有 1~3 行，R2 操作数 < 10
- [ ] 版本没变的轮次：R2 Class A 操作数 = 0
- [ ] 前端 `.env.production` 的 `VITE_CDN` 指向 R2，页面无 404、无图片裂图
- [ ] 无数据引用的图（约 790 张）也在桶里（因为默认不筛选）

## 9. 将来切自定义域

1. 域名加进 Cloudflare 账号（同一账号，否则绑不上 R2）
2. R2 → 桶 → Settings → Custom Domains → Add
3. 改前端 `VITE_CDN=https://cdn.example.com`，重新构建
4. 可选：在这个域上开 Cache 规则、WAF、Bot Fight Mode
5. `r2.dev` 保持开启一段时间便于回滚，确认无问题后再关

## 10. 成本核对

| 项 | 免费额度 | 我们用 | 余量 |
|---|---|---|---|
| 存储 | 10 GB-month | 6.82 GB | 3.18 GB |
| Class A（写） | 100 万/月 | 首次 13,000，之后每次几十~几百 | 极大 |
| Class B（读） | 1000 万/月 | r2.dev 无缓存，日活千人 × 50 图 × 30 天 ≈ 150 万 | 6.6 倍 |
| Egress | 永久免费 | — | — |

超出后：存储 $0.015/GB-month，Class B $0.36/百万次。按每年新增约 0.5~0.6 GB，
3.18 GB 余量大约能撑 5 年；即使超了也是每月几分钱，不是悬崖。
