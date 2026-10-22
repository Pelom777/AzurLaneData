# 新任务提示词

直接把下面 `---` 之间的内容整段粘到新会话里。它是自包含的，不依赖之前任何对话。

---

我在给碧蓝航线数据站做仓库拆分，目前**解包链路已经端到端跑通**，剩下的是发布与拆分。

**工作目录**：`D:\Projects\AzurLaneData`（远端 `Pelom777/AzurLaneData`）

**先读这三份，按顺序**：

1. `docs/HANDOFF.md` —— 架构、技术链路、容量算账、分阶段步骤
2. `docs/VERIFICATION.md` —— 阶段 0–2 的验收数字（已通过，别重做）
3. `docs/CDN.md` —— R2 资产分发的全部细节（已落地）

**当前架构，一句话**：三个角色各干一件事，别混。

| 角色 | 干什么 |
|---|---|
| `Pelom777/AzurLaneTools` | **前端仓库**，Cloudflare Pages 的唯一构建源。`public/` 会被清空 |
| `Pelom777/AzurLaneData`（= 当前目录） | **自动构建仓库**，跑 Actions 解包+构建+发布，**不对外提供任何内容** |
| R2 bucket | **唯一内容源**，存 19,422 个图片资产 + `data/` 下 21 个 json |

所以 `AzurLaneData` 公开还是私有，跟站点能不能跑没有关系 —— 没有任何人从 GitHub 读它。

**你的任务，按顺序**：

1. **验证 data 发布到 R2**（唯一剩下的 P0）。我给 `update-data.yml` 加了三步
   （装 rclone / 查凭据 / `rclone copy public/data`），但**还没跑过**。
   跑一次 `force=true, dry_run=false`，确认：
   - R2 的 `data/` 出现 21 个对象
   - `curl -I` 回来 `Cache-Control: public, max-age=300`
   - 内容与本地逐字节一致

2. **前端拆分**：`AzurLaneTools` 删 `public/`，`vite.config.ts` 加
   `copyPublicDir: false`，`.env.development` 的 `VITE_CDN` 改成 R2 域名。
   然后全量回归：图片全来自 CDN，数据面板无 NaN / 白屏。

3. **停掉旧链路**：`D:\Files\WorkBuddy\2026-10-20-16-11-15\azurlane-automation`
   里的 workflow 停用。它只作参考，不再演进。

**几条硬约束**：

- `build_data.py` / `build_data_impl.py` / `check_data_contract.py` 的**语义不要改**，
  它们是前端契约的来源。要改先问我为什么。
- 图片资产 6.82 GB **绝不进 git**，只走 R2（`rclone`）。
- 每小时 cron 只做版本检查，`azhash` 没变就直接结束，不要无条件 commit。
- `data/` 的缓存头必须是**短缓存**（`max-age=300`），长了前端会在游戏更新后读到旧值。
  图片相反，要长缓存（`immutable`）。
- 任何"看起来成功了"的结论，都要给一条可复查的证据（对象数、字节数、diff 行数）。

**环境限制**：本地是 Windows，`tools/bcdec` 和 `tools/uabdec` 是 **Linux 静态 32 位 ELF**，
本地跑不了。验证解包请用 CI（`p0-probe.yml`），不要试图在本地装 WSL 绕。

**我没有**：GHCR 的 token（官方 Docker 镜像 `ghcr.io/azurlanetools/azex/azex` 是私有的，
用不了，只能靠 `tools/` 里的二进制）。

遇到需要我拍板的岔路，停下来问。

---

## 附：这份提示词对应的上下文

| 项 | 值 |
|---|---|
| 本地路径 | `D:\Projects\AzurLaneData` |
| 前端仓库 | `Pelom777/AzurLaneTools`（Pages 源，public 待清空） |
| 旧自动化仓库 | `D:\Files\WorkBuddy\2026-10-20-16-11-15\azurlane-automation`（只作回退参考） |
| CDN | R2 bucket `azurlane-assets`，经 `r2.dev` 或 `cdn.al.pelom.cn` |
| 游戏服务器 | CN 为主，`az` 清单 64,649 条 |
| 已验收版本 | 9.7.25 / azhash `9.7.398` / 36 张表 236,150 条 |
| 已完成阶段 | 0（解包）、0.5（基线）、1（全量）、2（CI） |
| 待办阶段 | 2.5（data 发布）、3（资产与前端拆分）、4（切换） |
