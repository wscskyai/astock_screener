# -*- coding: utf-8 -*-
"""构建股票名称拼音索引：修正多音字与逗号解析问题。

数据源：mozillazg/pinyin-data 的 pinyin.txt。
存两列：py（全拼）、init（首字母），多音字用 | 分隔多组备选，
匹配时用 contains，兼顾「payh / payx」这类差异。
"""

import itertools
import os
import sys
import time
import unicodedata
import urllib.request

sys.path.insert(0, r"D:\工作\A股分析\astock_screener")
import db  # noqa: E402

SRC_URL = "https://raw.githubusercontent.com/mozillazg/pinyin-data/master/pinyin.txt"
SRC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pinyin_src.txt")

# 股票名里常见、且 Unihan 首选读音和习惯读法不一致的字
ALT = {
    "行": ["hang"], "重": ["chong"], "长": ["chang"], "藏": ["cang"],
    "厦": ["sha"], "都": ["du"], "乐": ["le"], "传": ["chuan"],
    "盛": ["sheng"], "参": ["shen"], "石": ["shi"], "会": ["hui"],
}
MAX_VARIANTS = 8


def strip_tone(s: str) -> str:
    s = s.replace("ü", "v").replace("Ü", "v")
    out = unicodedata.normalize("NFKD", s)
    return "".join(c for c in out if not unicodedata.combining(c)).lower()


def load_map() -> dict[str, list[str]]:
    if not os.path.exists(SRC_PATH):
        os.makedirs(os.path.dirname(SRC_PATH), exist_ok=True)
        print(f"下载 {SRC_URL} …", flush=True)
        data = urllib.request.urlopen(SRC_URL, timeout=60).read()
        with open(SRC_PATH, "wb") as f:
            f.write(data)
    m: dict[str, list[str]] = {}
    with open(SRC_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if not line.startswith("U+"):
                continue
            try:
                code, py = line.split(":", 1)
                ch = chr(int(code[2:], 16))
                first = py.strip().split()[0].split(",")[0]   # 关键：先按逗号截断
                m[ch] = [strip_tone(first)]
            except (ValueError, IndexError):
                continue
    return m


def char_options(ch: str, table: dict[str, list[str]]) -> list[str]:
    if "\u4e00" <= ch <= "\u9fff":
        base = list(table.get(ch, [ch]))
        for extra in ALT.get(ch, []):
            if extra not in base:
                base.append(extra)
        return base[:3]
    if ch.isalnum():
        return [ch.lower()]
    return [""]


def variants(name: str, table: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    opts = [char_options(ch, table) for ch in name]
    full: list[str] = []
    inits: list[str] = []
    for combo in itertools.product(*opts):
        if len(full) >= MAX_VARIANTS:
            break
        full.append("".join(combo))
        inits.append("".join(c[0] if c else "" for c in combo))
    return full, inits


def main() -> int:
    t0 = time.time()
    table = load_map()
    print(f"拼音表 {len(table)} 字，用时 {time.time()-t0:.1f}s", flush=True)

    conn = db.connect()
    rows = conn.execute("SELECT symbol, name FROM securities WHERE name IS NOT NULL").fetchall()
    out = []
    for sym, name in rows:
        full, inits = variants(name, table)
        out.append((sym, "|".join(full), "|".join(inits), name))
    conn.execute("DELETE FROM stock_pinyin")
    conn.executemany("INSERT INTO stock_pinyin(symbol, py, init, name) VALUES(?,?,?,?)", out)
    conn.commit()
    print(f"已写入 {len(out)} 只，用时 {time.time()-t0:.1f}s")
    for s in ("600874", "000001", "601988", "600028"):
        r = conn.execute("SELECT name, py, init FROM stock_pinyin WHERE symbol=?", (s,)).fetchone()
        print(f"  {s}: {r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
