"""解析 AzurLane 的 sharecfgdata / sharecfg .lua 表。

为什么要自己写解析器：这批文件是机械导出的纯表字面量，形如

    _G.pg.base.ship_data_template[100001] = {
        oil_at_start = 1,
        equip_1 = { 1 },
        name = "泛用型",
    }

没有 require、没有循环，绝大多数也没有函数调用，因此不需要 Lua 运行时（不需要 lupa / lua5.1），
纯 Python 递归下降就能读，托管 runner 上零依赖。

两种目录的格式差异（都已支持）：
  - `sharecfgdata/`：每行 `_G.pg.base.X[id] = {...}`
  - `sharecfg/`：每行 `pg.base.X[id] = {...}`，且文件头有
    `pg = pg or {}` / `setmetatable(...)` / `pg.X.all = {...}` 这类非数据语句

真实数据里踩到的形态（都已在下面处理）：
  1. `_G.pg.base.X[100001] = {...}`      —— 数字键，最常见
  2. `_G.pg.base.X.ad_0 = {...}`         —— 点号字符串键（gametip）
  3. `_G.pg.base.X = {...}`              —— 整表赋值（少数小表）
  4. 文件里夹着非数据语句（sharecfg 文件头、`X.all` 汇总表）
                                          —— 跳过它，接着找下一条数据行
  5. 文件尾部附着反编译代码（weapon_property 结尾有 `if rawget(...) then`）
                                          —— 后面再也找不到数据行，判为截断，保留已解析的部分
另外值里会出现 `[[长字符串]]`（游戏内文案），以及上游自己的拼写错误（`ture`）。

产出结构：
    {"100001": {...}, "100011": {...}}      每个表 -> 一个 dict，键是原表的 id
"""

from __future__ import annotations

import re

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")
# 数据行的起点。sharecfgdata 里是 `_G.pg.base.X[...]`，sharecfg 里是 `pg.base.X[...]`。
# 用 regex 定位起点、而不是逐个 IDENT 往下走，是因为 sharecfg 文件头有
# `pg = pg or {}` / `setmetatable(...)` / `confNEO` 这类非数据语句，
# 逐个走会在第一个语句就判定「不是数据」而整张表作废。
ANCHOR = re.compile(r"(?:_G\.)?pg\.base\.")
ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
    "f": "\f", "v": "\v", "0": "\0", "\\": "\\", '"': '"', "'": "'", "/": "/",
}


class LuaParseError(ValueError):
    pass


class Parser:
    __slots__ = ("s", "i", "n", "trailing")

    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.n = len(text)
        self.trailing = False   # 是否因为遇到非数据语句而截断

    # ---------------- 基础
    def skip(self) -> None:
        s, n = self.s, self.n
        while self.i < n:
            ch = s[self.i]
            if ch in " \t\r\n\ufeff":
                self.i += 1
            elif ch == "-" and s.startswith("--", self.i):
                end = s.find("\n", self.i)
                self.i = n if end < 0 else end + 1
            else:
                return

    def fail(self, msg: str):
        line = self.s.count("\n", 0, self.i) + 1
        raise LuaParseError(f"{msg}（第 {line} 行，偏移 {self.i}）")

    def expect(self, ch: str) -> None:
        self.skip()
        if self.i >= self.n or self.s[self.i] != ch:
            self.fail(f"期望 {ch!r}，实际 {self.s[self.i:self.i + 20]!r}")
        self.i += 1

    # ---------------- 值
    def value(self):
        self.skip()
        if self.i >= self.n:
            self.fail("意外结束")
        ch = self.s[self.i]
        if ch == "{":
            return self.table()
        if ch in "\"'":
            return self.string()
        if ch == "[" and self._is_long_bracket(self.i):
            return self.long_string()
        m = NUMBER.match(self.s, self.i)
        if m:
            self.i = m.end()
            raw = m.group(0)
            if raw.lower().lstrip("-").startswith("0x"):
                return int(raw, 16)
            return float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
        m = IDENT.match(self.s, self.i)
        if m:
            self.i = m.end()
            word = m.group(0)
            if word == "true":
                return True
            if word == "false":
                return False
            # nil，以及游戏自己表里拼错的字面量（实测 barrage_template.lua 里有 `ture`）。
            # Lua 语义下读未定义全局就是 nil，所以统一降级成 None，不因此中断整张表。
            return None
        self.fail(f"无法识别的值 {self.s[self.i:self.i + 20]!r}")

    def _is_long_bracket(self, pos: int) -> bool:
        j = pos + 1
        while j < self.n and self.s[j] == "=":
            j += 1
        return j < self.n and self.s[j] == "["

    def long_string(self) -> str:
        j = self.i + 1
        level = 0
        while self.s[j] == "=":
            level += 1
            j += 1
        j += 1                                  # 跳过第二个 '['
        close = "]" + "=" * level + "]"
        end = self.s.find(close, j)
        if end < 0:
            self.fail("长字符串未闭合")
        body = self.s[j:end]
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        self.i = end + len(close)
        return body

    def string(self) -> str:
        quote = self.s[self.i]
        self.i += 1
        out: list[str] = []
        s, n = self.s, self.n
        while self.i < n:
            ch = s[self.i]
            if ch == quote:
                self.i += 1
                return "".join(out)
            if ch == "\\":
                self.i += 1
                if self.i >= n:
                    break
                esc = s[self.i]
                if esc in ESCAPES:
                    out.append(ESCAPES[esc])
                    self.i += 1
                elif esc == "x":
                    out.append(chr(int(s[self.i + 1:self.i + 3], 16)))
                    self.i += 3
                elif esc.isdigit():
                    digits = s[self.i:self.i + 3]
                    j = 0
                    while j < len(digits) and digits[j].isdigit():
                        j += 1
                    out.append(chr(int(digits[:j])))
                    self.i += j
                else:
                    out.append(esc)
                    self.i += 1
                continue
            out.append(ch)
            self.i += 1
        self.fail("字符串未闭合")

    def table(self):
        self.expect("{")
        items: list = []
        keyed: dict = {}
        has_key = False
        while True:
            self.skip()
            if self.i >= self.n:
                self.fail("表未闭合")
            ch = self.s[self.i]
            if ch == "}":
                self.i += 1
                break
            # `[[` 在键位置不可能是键，只能是位置值（长字符串）
            if ch == "[" and not self._is_long_bracket(self.i):
                self.i += 1
                k = self.value()
                self.expect("]")
                self.expect("=")
                keyed[k] = self.value()
                has_key = True
            else:
                m = IDENT.match(self.s, self.i)
                if m and self._is_key_assign(m.end()):
                    key = m.group(0)
                    self.i = m.end()
                    self.expect("=")
                    keyed[key] = self.value()
                    has_key = True
                else:
                    items.append(self.value())
            self.skip()
            if self.i < self.n and self.s[self.i] in ",;":
                self.i += 1
        if not has_key:
            return items
        for idx, val in enumerate(items, start=1):
            keyed.setdefault(idx, val)
        # 同一个表里可能既有 name = ... 又有 [3] = ...，键类型混用会让 JSON 的
        # sort_keys 直接抛 TypeError（int 和 str 不可比）。JSON 的键本来就只能是字符串，
        # 所以统一转成 str，既忠于 Lua 语义也不丢信息。
        return {k if isinstance(k, str) else str(k): v for k, v in keyed.items()}

    def _is_key_assign(self, pos: int) -> bool:
        """区分 `name = v` 与 `name`（后者在我们的数据里不会出现，保守判 False）。"""
        j = pos
        while j < self.n and self.s[j] in " \t\r\n":
            j += 1
        return j < self.n and self.s[j] == "="

    # ---------------- 顶层：pg.base.NAME[...] = {...}
    def assignments(self):
        while True:
            self.skip()
            if self.i >= self.n:
                return
            anchor = ANCHOR.match(self.s, self.i)
            if not anchor:
                # 当前不是数据行。可能是 sharecfg 的文件头、`X.all = {...}` 汇总表，
                # 也可能是文件尾部附着的反编译代码。先往后找下一条数据行：
                # 找得到就接着解析，找不到才算真的截断。
                nxt = ANCHOR.search(self.s, self.i + 1)
                if not nxt:
                    self.trailing = True
                    return
                self.i = nxt.start()
                continue

            self.i = anchor.end()
            m = IDENT.match(self.s, self.i)
            if not m:
                self.i = anchor.start()
                self.trailing = True
                return
            name = m.group(0)
            self.i = m.end()
            self.skip()
            key = None
            if self.i < self.n and self.s[self.i] == "[":
                self.i += 1
                key = self.value()
                self.expect("]")
            elif self.i < self.n and self.s[self.i] == ".":
                # `pg.base.X.ad_0 = {...}`，点号字符串键
                self.i += 1
                self.skip()
                km = IDENT.match(self.s, self.i)
                if not km:
                    self.fail(f"无法识别的赋值目标 {self.s[anchor.start():self.i + 10]!r}")
                key = km.group(0)
                self.i = km.end()
            elif not (self.i < self.n and self.s[self.i] == "="):
                self.fail(f"无法识别的赋值目标 {self.s[anchor.start():self.i + 10]!r}")
            self.expect("=")
            table = self.value()
            yield name, key, table


def parse_text(text: str) -> tuple[dict[str, dict], bool]:
    """返回 (表字典, 是否在文件尾部截断过)。"""
    parser = Parser(text)
    out: dict[str, dict] = {}
    for name, key, table in parser.assignments():
        bucket = out.setdefault(name, {})
        if key is None:
            if isinstance(table, dict):
                bucket.update(table)
            else:
                bucket["__value__"] = table
        else:
            bucket[str(key)] = table
    return out, parser.trailing


def parse_file(path) -> tuple[dict[str, dict], bool]:
    with open(path, "r", encoding="utf-8", errors="strict") as fh:
        text = fh.read()
    return parse_text(text)
