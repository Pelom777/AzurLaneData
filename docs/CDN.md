# CDN 与资产分发

> 状态：**方案已落地为 `.github/workflows/sync-assets.yml`，尚未跑过真实 R2**
> （缺 4 个 secrets，见 §5.1）。首次播种仍需本地做，见 §4。
> 最后核对：2026-10-21，依据 `scripts/azl_assets.py` 源码 + 旧仓库实测数据。

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
| `data` | 21 | 0.7 MB | — | 走 git，不走 CDN |
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
| **C. 数据** | 21 | 0.7 MB | `build_data.py` | 走 git，不进 R2 |

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

耗时参考（6.8 GB）：

| 上行带宽 | 耗时 |
|---|---|
| 10 Mbps | 约 93 分钟 |
| 20 Mbps | 约 47 分钟 |
| 50 Mbps | 约 19 分钟 |
| 100 Mbps | 约 9 分钟 |

## 4.5 可生成类别：走 CI 全量重生（`.github/workflows/regen-assets.yml`）

这是现在的**主路径**。和上面几节的区别：**一切从当前 CDN 现取**，
不依赖任何历史快照，产出的每张图都是官方当前版本。

### 为什么必须有这个 workflow，而不能直接扩 sync-assets

painting 全量约 **2,491 张 / 下载 3.7 GB / 产物 5.6 GB**。托管 runner 的 `/`
只有约 14 GB，单 job 峰值约 9.3 GB 且中途无法回收。所以按
`crc32(名字) % N` 切成 N 片并行，每片跑完立刻上传、立刻丢弃，
磁盘占用与 N 成反比（N=6 时每片约 1.6 GB）。

### 用法

```bash
# GitHub → Actions → regen-assets → Run workflow
items  = painting          # 空格分隔；painting 会顺带产出 paintingface
shards = 6                 # 1..16，越大越省磁盘
scope  = used              # 按 public/data 的引用集过滤
limit  = 0                 # 调试时给个小值（如 3）把成本压到最小
force  = false             # true = 忽略已存在的 md5，连未变的也重做
dry_run= false             # true = 只生成，不拉远端、不上传、不提交
```

或用命令行触发：

```bash
gh workflow run regen-assets.yml \
  -f items=painting -f shards=6 -f scope=used -f limit=0
```

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
> 日常增量走 `sync-assets.yml`。

## 5. 增量同步（CI）

### 5.1 凭据：4 个 secrets

`.github/workflows/sync-assets.yml` 已经把 rclone 的 remote 全写成 `RCLONE_CONFIG_R2_*`
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

`.github/workflows/sync-assets.yml`，只有 `workflow_dispatch`。

**没有嵌进 `update-data.yml`** 的原因：两条链路的失败面完全不同
（UnityPy 解码 / 大文件网络 / R2 凭据），图片跑挂了不该把数据更新一起拖红
——数据 10 分钟能修好，图片可能要几小时，混在一个 job 里会让 cron 反复重跑数据。

版本没变时不会有人 dispatch，所以 R2 操作数是 0。想接成自动的话，在
`update-data.yml` 末尾加一个 `workflow_dispatch` 调用即可，但建议先手动跑几轮确认增量正确。

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
| `spine` 无法自动 | ⚠️ 无生成器，只能从旧仓库搬一次；前端需 fallback |
| `HANDOFF.md` 写着"已支持 spine" | ⚠️ 表述有误，实际不支持 |
| 单 job 跑不下 painting 全量 | ✅ 新增 `regen-assets.yml`，crc32 分片并行，见 §4.5 |
| 每个分片从空 state 起手 ⇒ 每轮全量 | ✅ 改为继承基线 state + 差分合并，见 §4.5 |

## 8. 验收标准

- [ ] R2 桶内对象数 = `19,443 - 21(data) = 19,422`（spine 含在内）
      当前 **2**（联调产物 `squareicon/22.png`、`33.png`），等首次播种
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
