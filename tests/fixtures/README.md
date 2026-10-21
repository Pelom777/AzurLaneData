# tests/fixtures — 判定"等价"的基线

`weapon_name.json` 是**旧链路**产出的权威结果，用来给新链路做逐字段比对
（HANDOFF §4.1：不比 lua 文本，只比 json）。

| 项 | 值 |
|---|---|
| 来源 | `scripts/legacy/fetch_lua_tables.py --server CN --only weapon_name` |
| 上游仓库 | `AzurLaneTools/AzurLaneLuaScripts` |
| 上游 commit | `5822b089`（2026-10-17T02:42:55Z） |
| 游戏版本 | CN `$azhash$9$7$398$def4c26318af25f3` |
| 条目数 | 2422 |
| 大小 | 85,011 字节 |

生成命令（需要 `PYTHONPATH=scripts`，因为 legacy 脚本假定自己被放在 `scripts/` 下）：

```bash
PYTHONPATH=scripts python scripts/legacy/fetch_lua_tables.py \
    --server CN --dst /tmp/lua --out /tmp/raw_old --only weapon_name
cp /tmp/raw_old/weapon_name.json tests/fixtures/weapon_name.json
```

**什么时候该更新这个文件**：只有当游戏侧 `weapon_name` 表真的改了、
且新链路与旧链路在**同一版本**下都跑了、确认差异是游戏改动而不是链路差异时。
否则它一变，验收就失去意义。
