# legacy/ —— 旧链路，仅作回退参考

这里的脚本依赖第三方仓库，**CI 不调用**。

| 脚本 | 原来干什么 | 为什么退役 |
|---|---|---|
| `fetch_lua_tables.py` | `git sparse-checkout` 拉 `AzurLaneTools/AzurLaneLuaScripts` 的 `sharecfgdata/*.lua` | 新鲜度和可用性都由别人决定 |
| `sync_upstream.py` | 同步旧上游仓库 | 同上 |

现在的替代品是 `scripts/unpack_lua.py`（自解包，输出接口完全一致）。

**保留它们的唯一理由**：`unpack_lua.py` 的 P0 风险（`bcdec` / `ljd` 在游戏新版本上
失效）如果短期修不好，可以立刻切回这里，用旧链路顶上，不至于断更。

切换方法：把 `fetch_lua_tables.py` 临时复制回 `scripts/`，
workflow 里把 `unpack_lua.py` 那一步换成它即可，下游 `build_data.py` 完全不用动。
