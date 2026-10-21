# tools/ 里的二进制

这两个是**从游戏资源包里还原 Lua 的工具**，来自
[`AzurLaneTools/AzurLaneExtractor`](https://github.com/AzurLaneTools/AzurLaneExtractor)
（Apache-2.0，commit `ca7af9d`，2023-04-23）。

| 文件 | 作用 | 输入 → 输出 |
|---|---|---|
| `bcdec` | 还原被魔改的 LuaJIT 字节码 | stdin（补过 LJ 头的 BC）→ stdout（标准 BC） |
| `uabdec` | 解密整包加密的 UnityFS | `uabdec <in> <out>` |

两者都是 **32 位 x86 ELF**（`ELF 32-bit LSB, machine=3`），而且是**静态链接**
（`file` 输出里带 `statically linked`，程序头里没有 `PT_INTERP`）。

**实测（2026-10-21，ubuntu-24.04 runner）**：不需要装任何 i386 运行库，
`chmod +x` 之后直接就能跑，`bcdec` 对空输入返回 0、对 `sharecfgdata` 分段
2422/2422 成功。所以下面这套 `libc6:i386` 的说明是旧版本的遗留，留作参考：

```
bash: ./tools/bcdec: No such file or directory     # 文件明明存在
```

只有**动态链接**的 32 位 ELF 才会因为缺 `/lib/ld-linux.so.2` 报这个迷惑性错误。
真遇到了再装：

```bash
sudo dpkg --add-architecture i386
sudo apt-get update && sudo apt-get install -y libc6:i386
```

## 为什么不是调用官方 Docker 镜像

`ghcr.io/azurlanetools/azex/azex` 是**私有**的（匿名获取 token 返回 401，
需要 `GHCR_TOKEN`），所以在 CI 里用不了。只能本地带二进制。

## 版本风险

这两个二进制是 2023 年构建的。已实测 `sharecfgdata` 的**容器格式**至今没变
（uleb128 分段，219 段正好铺满文件），但 `bcdec` 要还原的**字节码混淆方案**
是否变了，只能跑一次才知道。

失败的典型表现：`bcdec` 返回非 0，或返回的字节无法被 `ljd` 反编译。
真遇到时的出路：

1. 从 `AzurLaneDecTools` 的 `src/` 用 `gcc -m32` 重新编译（需要 `gcc-multilib`）
2. 从 `AzurLaneExtractor` 的新 commit 里取新的 `tools/`

## 重新获取

```bash
git clone --depth=1 https://github.com/AzurLaneTools/AzurLaneExtractor.git /tmp/azex
cp /tmp/azex/tools/bcdec /tmp/azex/tools/uabdec tools/
chmod +x tools/bcdec tools/uabdec
```
