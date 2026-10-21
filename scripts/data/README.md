# scripts/data —— 构建期用的固化表（不是站点资源）

| 文件 | 用途 | 谁读 |
|---|---|---|
| `namecode.json` | `{namecode:N}` → 中文显示名 的固化基线 | `build_data_impl.py` 的 `namecode_baseline()` |

`{namecode:N}` 是客户端的全局术语表（257 → 伊490、528 → 圣地亚哥.改），
**不在 sharecfgdata 的 32 张表里**，拿不到原始文件，只能反解。
`build_data_impl.py` 运行时有两路来源：默认皮肤自举（A）+ 这张固化基线（B）。
只靠 A 解不出改型 / META / 台词里提到的别的船，所以需要 B 兜底。

## namecode.json 的来源

旧仓库里这份文件是反解产物，仓库已不可访问。这里按 `build_data_impl.py`
注释里写明的 B 路重新生成（2026-10-21）：

1. 原始表：`AzurLaneLuaScripts` CN/sharecfgdata/`ship_skin_template.lua`（含占位符）
2. 已解析结果：`public/data/skin.json`（迁入的基线，占位符残留 0）
3. 按 painting 对齐两者，把「含 `{namecode:N}` 的原始名」和「解析后中文名」做字符串匹配，
   差值即 N 的中文名；同一 N 多值时取票数最高

验证：用生成的表重新解析全部 2871 条原始皮肤名，与 `public/data/skin.json`
比对 **2279 条一致、0 条占位符残留**；`check_data_contract.py` 在本地产物上
全部通过（此前缺这张表时有 2 处 `{namecode:257}` / `{namecode:528}` 违约）。

剩下 39 条不一致与 namecode 无关，是仓库数据本身的历史偏差
（例如 `beifeng` 仓库记「北风」、游戏现名「苝」）。
