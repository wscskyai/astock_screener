# -*- coding: utf-8 -*-
"""按名单强制重拉：删掉旧行，从腾讯前复权源重建。

用于那些历史遗留下来、混着「不复权价 + 股单位」的股票。

    python repair_symbols.py D:\\tmp\\suspect_symbols.txt
"""

import sys
import time

sys.path.insert(0, r"D:\工作\A股分析\astock_screener")
import db  # noqa: E402
import sources_cn as src  # noqa: E402

START = "2021-01-01"


def main(path: str) -> int:
    symbols = [s.strip() for s in open(path, encoding="utf-8") if s.strip()]
    conn = db.connect()
    t0 = time.time()
    ok = fail = rows_n = 0
    for i, sym in enumerate(symbols, 1):
        try:
            bars = src._txp_bars(sym, START)
        except Exception:  # noqa: BLE001
            bars = []
        if not bars:
            fail += 1
            print(f"  {sym} 拉取失败，跳过", flush=True)
            continue
        conn.execute("DELETE FROM bars WHERE symbol=?", (sym,))
        conn.executemany(
            "INSERT INTO bars(symbol, date, open, high, low, close, volume) VALUES(?,?,?,?,?,?,?)",
            [(sym, b[0], b[1], b[2], b[3], b[4], b[5]) for b in bars],
        )
        conn.execute("INSERT OR REPLACE INTO bar_meta(symbol, src, updated_at) VALUES(?,?,?)",
                     (sym, "txp", time.strftime("%Y-%m-%dT%H:%M:%S")))
        conn.execute("UPDATE securities SET n_bars=? WHERE symbol=?", (len(bars), sym))
        conn.commit()
        ok += 1
        rows_n += len(bars)
        if i % 10 == 0:
            print(f"  {i}/{len(symbols)}  已重建 {ok} 只 / {rows_n} 行  "
                  f"{time.time()-t0:.0f}s", flush=True)
    print(f"完成：重建 {ok} 只（失败 {fail}），{rows_n} 行，用时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
