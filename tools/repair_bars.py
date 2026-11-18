# -*- coding: utf-8 -*-
"""修复日线数据：用「前复权」权威源（腾讯 / 东财）重拉最近 N 天并覆盖。

背景：新浪源的 volume 单位是「股」，东财/腾讯是「手」。增量同步一旦回退到新浪，
最近 40 天的成交量就会被写成 100 倍；而且新浪是不复权价。这个脚本只认 txp / em
两个前复权源，避免再引入价格口径问题。

    python tools/repair_bars.py          # 默认最近 90 个交易日
    python tools/repair_bars.py 120
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from astock import db
from astock import sources_cn as src

WORKERS = 6


def fetch_recent(sym: str, since: str):
    """只从前复权源取；两个都失败就跳过这只。"""
    for fn in (src._txp_bars, src._em_bars):
        try:
            bars = fn(sym, since)
            if bars:
                return bars
        except Exception:  # noqa: BLE001
            continue
    return None


def main(days: int = 90) -> int:
    conn = db.connect()
    t0 = time.time()

    since = conn.execute(
        "SELECT MIN(date) FROM (SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT ?)",
        (days,),
    ).fetchone()[0]
    symbols = [r[0] for r in conn.execute("SELECT symbol FROM securities ORDER BY symbol")]
    print(f"修复区间 {since} ~ 今天，共 {len(symbols)} 只", flush=True)

    done = updated = price_mismatch = failed = 0
    buf: list[tuple] = []
    mismatch_samples: list[str] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sym, bars in ex.map(lambda s: (s, fetch_recent(s, since)), symbols):
            done += 1
            if bars is None:
                failed += 1
                continue
            old = dict(conn.execute(
                "SELECT date, close FROM bars WHERE symbol=? AND date>=?", (sym, since)).fetchall())
            for b in bars:
                if b[0] < since:
                    continue
                prev = old.get(b[0])
                if prev and prev > 0 and abs(b[4] / prev - 1) > 0.05:
                    price_mismatch += 1
                    if len(mismatch_samples) < 5:
                        mismatch_samples.append(f"{sym} {b[0]} 库内={prev:.2f} 新={b[4]:.2f}")
                buf.append((b[1], b[2], b[3], b[4], b[5], sym, b[0]))
            if len(buf) >= 40000:
                updated += _flush(conn, buf)
                buf = []
            if done % 400 == 0:
                print(f"  {done}/{len(symbols)}  已覆盖 {updated} 行  "
                      f"{time.time()-t0:.0f}s", flush=True)

    if buf:
        updated += _flush(conn, buf)
    conn.commit()

    print(f"完成：{done} 只（取不到 {failed}），覆盖 {updated} 行，"
          f"收盘价偏差>5% 的 {price_mismatch} 行", flush=True)
    if mismatch_samples:
        print("  抽样：", "; ".join(mismatch_samples), flush=True)
    print(f"用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


def _flush(conn, rows: list[tuple]) -> int:
    conn.executemany(
        "UPDATE bars SET open=?, high=?, low=?, close=?, volume=? "
        "WHERE symbol=? AND date=?", rows)
    conn.commit()
    return len(rows)


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 90))
