# AzurLaneData 项目长期约定

## 不可违反的规则
- `build_data.py` / `build_data_impl.py` / `check_data_contract.py` 的**语义不改**，
  它们是前端契约的来源。要改先说清理由。
- **图片资产不进 git**（6.82 GB）。走 Cloudflare R2，见 `docs/CDN.md`。
- 每小时 cron 只做版本检查，`azhash` 没变直接结束，不产生空提交。
- 任何"看起来成功"的结论都要给可复查证据（文件大小 / 条目数 / diff / md5）。

## 环境事实
- CI 统一 **Python 3.11**（`update-data` / `sync-assets` / 各探针 workflow）。
  本地是 3.13，锁版本时必须查 PyPI 的 `requires_python`，不能只看本机装了什么。
- `tools/bcdec` `tools/uabdec` 是**静态链接**的 32 位 i386 ELF（无 PT_INTERP），
  ubuntu-24.04 直接可跑，**不需要 `libc6:i386`**。
- 本机是纯 Windows：WSL 被安全策略拉黑，无 docker/gcc → 本地跑不了 ELF。
  所有需要 bcdec/uabdec 的验证都走 GitHub Actions。
- 图片链路（`azl_assets.py`）不需要 ELF，本地可直接跑。

## 数据链路关键事实
- 解包：`sharecfgdata` = uleb128 分段容器；每段是**独立** BC dump →
  `bcdec`（输出完整 dump，**不要剥头尾**）→ 逐段 `ljd` → 拼 lua 文本 → `luatable`。
- `scripts32` = uabdec → UnityPy → `m_Script.encode('utf-8','surrogateescape')`（1.10+ 无 `.script`）。
- `scripts/data/namecode.json` 是契约校验的必要输入，缺失会导致 `{namecode:NNN}` 残留。

## 资产链路关键事实
- 跳过判据 = state 有 md5 **且** 产物文件在磁盘。CI 空工作区必须先用
  `scripts/mark_remote.py` 按远端清单 touch marker，否则每轮全量重跑。
- 上传用 `rclone copy --files-from --ignore-times`，**绝不用 `sync`**（会删掉手工搬的 spine）。
- 路径里没有内容 hash，Cache-Control 用 `max-age=86400`，不要 `immutable`。
- spine（1.10 GB）无法自动生成，只能从旧仓库搬一次，前端需 fallback 到静态立绘。

## 用户偏好
- 遇到需要决策的岔路要停下来问，不要自己拍。
- 偏好真实、克制的表达，反对过度包装；用表格做对照。
