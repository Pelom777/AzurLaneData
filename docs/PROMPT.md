# 新任务提示词

直接把下面 `---` 之间的内容整段粘到新会话里。它是自包含的，不依赖之前任何对话。

---

我在给碧蓝航线数据站做自动化仓库拆分。已经有一个**采集并交接完成**的新仓库骨架在
`D:\Projects\AzurLaneData`（远端计划叫 `Pelom777/AzurLaneData`），你的任务是把它从骨架
跑成真正能用的流水线。

**第一步**：读 `D:\Projects\AzurLaneData\docs\HANDOFF.md`。里面有目标的完整描述、
已验证的技术事实、未验证的风险、分阶段实施步骤和验收标准。按它走，不要自己重新设计。

背景一句话：过去数据靠 `git sparse-checkout` 拉第三方仓库
`AzurLaneTools/AzurLaneLuaScripts`，现在要改成**自己从游戏 CDN 解包 Lua**。
解包链路已经摸清（两条：`sharecfgdata` 走 uleb128 分段 + bcdec + ljd；
`scripts32` 走 uabdec + UnityPy + bcdec + ljd），工具二进制已放在 `tools/`。

**请按这个顺序做**：

1. **先打通阶段 0**（HANDOFF §7 阶段 0）。只解一张 `weapon_name` 表，
   证明 `bcdec` → `ljd` → `luatable` 这条链在 9.7 版本上还能走通。
   这是最大的风险点，P0。**跑不通就不要往下做**，把失败点报给我。

2. 通过后做阶段 1：全量解包 + `build_data` + `check_data_contract`，
   **并且用旧链路（`scripts/legacy/fetch_lua_tables.py`）在同一个版本上跑一遍，
   diff 两份 json**。这是判断"自己解包出来的和 `AzurLaneLuaScripts` 是否等价"的
   唯一可靠办法 —— 不要比 lua 文本，ljd 输出本来就可能因为版本差异而不同，
   但 json 里的数据必须一样（HANDOFF §4.1）。

3. 再做阶段 2：让 `.github/workflows/update-data.yml` 在 GitHub 上真正跑起来，
   包括 `actions/cache` 命中验证、`check_version` 跳过逻辑验证。

4. 阶段 3、4（资产搬迁、切换）我确认过前两步之后再动。

**几条硬约束**：

- 现有 `build_data.py` / `build_data_impl.py` / `check_data_contract.py` 的
  **语义不要改**。它们是前端契约的来源。要改先告诉我为什么。
- 不要往 git 里提交图片资产（6.82 GB）。原因见 HANDOFF §6。
- 每小时 cron 只做版本检查，`azhash` 没变就直接结束，不要无条件 commit。
- 改动前先跑 `python -m py_compile scripts/*.py` 和 `python scripts/check_data_contract.py`。
- 任何"看起来成功了"的结论，都要给一条可复查的证据（文件大小、条目数、diff 行数）。

**我手上有**：游戏的 CN 服可访问、GitHub 推权限、`D:\Projects\AzurLaneData` 的写权限。
**我没有**：GHCR 的 token（所以官方 Docker 镜像用不了，必须用 `tools/` 里的二进制）。

遇到需要我决策的岔路，停下来问，不要自己拍。

---

## 附：这份提示词对应的上下文

| 项 | 值 |
|---|---|
| 本地路径 | `D:\Projects\AzurLaneData` |
| 上游前端仓库 | `Pelom777/AzurLaneTools` |
| 旧自动化仓库 | `D:\Files\WorkBuddy\2026-10-20-16-11-15\azurlane-automation`（只作参考，不再演进） |
| 目标 CDN | `https://cdn.al.pelom.cn`（自建服务器） |
| 游戏服务器 | CN 为主，`az` 清单 64,649 条 |
| 当前实测版本 | 9.7.25 / azhash 9.7.398 |
