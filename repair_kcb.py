# -*- coding: utf-8 -*-
"""修复科创板（688/689）日线：用腾讯前复权源重拉全历史覆盖。

腾讯对科创板返回的成交量单位是「股」，之前没换算，导致这 618 只股票的成交量
全部放大 100 倍（成交额中位数算出来 123 亿/天）。sources_cn 已修正单位，
这里把库里已污染的整段历史重写一遍。

    python repair_kcb.py
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, r"D:\工作\A股分析\astock_screener")
import db  # noqa: E402
import sources_cn as src  # noqa: E402

WORKERS = 6
START = "2021-01-01"


def main() -> int:
    conn = db.connect()
    t0 = time.time()
    symbols = [r[0] for r in conn.execute(
        "SELECT symbol FROM securities WHERE symbol LIKE '688%' OR symbol LIKE '689%' "
        "ORDER BY symbol")]
    print(f"科创板 {len(symbols)} 只，重拉 {START} 起全历史", flush=True)

    done = rows_n = failed = 0
    buf: list[tuple] = []

    def fetch(sym: str):
        try:
            return sym, src._txp_bars(sym, START)
        except Exception:  # noqa: BLE001
            return sym, None

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sym, bars in ex.map(fetch, symbols):
            done += 1
            if not bars:
                failed += 1
                continue
            for b in bars:
                buf.append((b[1], b[2], b[3], b[4], b[5], sym, b[0]))
            if len(buf) >= 40000:
                rows_n += _flush(conn, buf)
                buf = []
            if done % 100 == 0:
                print(f"  {done}/{len(symbols)}  已写 {rows_n} 行  {time.time()-t0:.0f}s", flush=True)

    if buf:
        rows_n += _flush(conn, buf)
    conn.commit()
    print(f"完成：{done} 只（失败 {failed}），覆盖 {rows_n} 行，"
          f"用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


def _flush(conn, rows: list[tuple]) -> int:
    conn.executemany(
        "UPDATE bars SET open=?, high=?, low=?, close=?, volume=? WHERE symbol=? AND date=?", rows)
    conn.commit()
    return len(rows)


if __name__ == "__main__":
    raise SystemExit(main())
