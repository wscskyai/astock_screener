# -*- coding: utf-8 -*-
"""回补股息率历史：每日 dv = 过去 12 个月每股派息 / 当日不复权收盘价。

关键点：分母必须是不复权价。库里 bars 是前复权，除权前的价格被缩放过，
直接用会把历史股息率算高。所以这里单独取一份新浪不复权价。
"""

from __future__ import annotations

import bisect
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

sys.path.insert(0, r"D:\工作\A股分析\astock_screener")
import db  # noqa: E402
import sources_cn as src  # noqa: E402


def build_events(conn: sqlite3.Connection, since: str) -> dict[str, tuple[list, list]]:
    """symbol -> (除权日列表, 对应每股派息列表)"""
    rows = src.fetch_dividends(since)
    ev: dict[str, tuple[list, list]] = {}
    for r in rows:
        code = str(r.get("SECURITY_CODE") or "").zfill(6)
        ex = (r.get("EX_DIVIDEND_DATE") or "")[:10]
        amt = r.get("PRETAX_BONUS_RMB")
        if len(code) != 6 or not ex or not amt:
            continue
        try:
            dps = float(amt) / 10.0
        except (TypeError, ValueError):
            continue
        if dps <= 0:
            continue
        d, v = ev.setdefault(code, ([], []))
        d.append(ex)
        v.append(dps)
    for code, (d, v) in ev.items():
        order = sorted(range(len(d)), key=lambda i: d[i])
        ev[code] = ([d[i] for i in order], [v[i] for i in order])
    return ev


def main(days: int = 250) -> int:
    conn = db.connect()
    t0 = time.time()

    oldest = conn.execute(
        "SELECT MIN(date) FROM (SELECT DISTINCT date FROM valuation_daily ORDER BY date DESC LIMIT ?)",
        (days,),
    ).fetchone()[0]
    since = (datetime.fromisoformat(oldest) - timedelta(days=400)).date().isoformat()
    print(f"窗口 {oldest} ~ 今天，分红明细从 {since} 起拉 …", flush=True)
    events = build_events(conn, since)
    print(f"  拿到 {len(events)} 只股票的分红事件，用时 {time.time()-t0:.0f}s", flush=True)

    todo = [r[0] for r in conn.execute("""
        SELECT DISTINCT symbol FROM valuation_daily
        WHERE date >= (SELECT MIN(date) FROM (SELECT DISTINCT date FROM valuation_daily ORDER BY date DESC LIMIT ?))
    """, (days,))]
    total, done, rows_n = len(todo), 0, 0
    buf = []

    def job(sym: str):
        try:
            raw = dict((r[0], r[4]) for r in src._sina_bars(sym, oldest))
        except Exception:  # noqa: BLE001
            return []
        ev = events.get(sym)
        if not ev:
            return []
        ex_dates, dps = ev
        out = []
        for d in dates_by_sym.get(sym, ()):
            c = raw.get(d)
            if not c:
                continue
            lo = (datetime.fromisoformat(d) - timedelta(days=365)).date().isoformat()
            i, j = bisect.bisect_right(ex_dates, lo), bisect.bisect_right(ex_dates, d)
            s = sum(dps[i:j])
            if s > 0:
                out.append((round(s / c * 100, 3), sym, d))
        return out

    dates_by_sym: dict[str, list[str]] = {}
    for sym, d in conn.execute("""
        SELECT symbol, date FROM valuation_daily
        WHERE date >= (SELECT MIN(date) FROM (SELECT DISTINCT date FROM valuation_daily ORDER BY date DESC LIMIT ?))
        ORDER BY symbol, date
    """, (days,)):
        dates_by_sym.setdefault(sym, []).append(d)

    with ThreadPoolExecutor(max_workers=8) as ex:
        for out in ex.map(job, todo):
            buf += out
            rows_n += len(out)
            done += 1
            if len(buf) >= 50000:
                conn.executemany("UPDATE valuation_daily SET dv_ttm=? WHERE symbol=? AND date=?", buf)
                conn.commit()
                buf = []
            if done % 500 == 0:
                print(f"  {done}/{total}  已写 {rows_n} 行  {time.time()-t0:.0f}s", flush=True)

    if buf:
        conn.executemany("UPDATE valuation_daily SET dv_ttm=? WHERE symbol=? AND date=?", buf)
        conn.commit()
    db.set_meta(conn, "dv_backfill_at", datetime.now().isoformat(timespec="seconds"))
    conn.commit()
    n = conn.execute("SELECT COUNT(1) FROM valuation_daily WHERE dv_ttm IS NOT NULL").fetchone()[0]
    print(f"完成：写入 {rows_n} 行，库中非空股息率 {n} 行，用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 250))
