# -*- coding: utf-8 -*-
"""回补历史估值序列（用于 PE/PB 分位）。多线程按日期并行，跳过已回补的日期。

    python backfill_valuation.py 1200
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, r"D:\工作\A股分析\astock_screener")
import db  # noqa: E402
import sources_cn as src  # noqa: E402

WORKERS = 4


def existing_dates(conn) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT date FROM valuation_daily GROUP BY date HAVING COUNT(1) > 4000")}


def fetch_day(d: str) -> tuple[str, list]:
    try:
        return d, src.fetch_valuation(d)
    except Exception:  # noqa: BLE001
        return d, []


def main(days: int = 250) -> int:
    conn = db.connect()
    t0 = time.time()

    all_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT ?", (days,))]
    all_dates.reverse()
    have = existing_dates(conn)
    todo = [d for d in all_dates if d not in have]
    print(f"目标 {len(all_dates)} 个交易日，已有 {len(have)}，待补 {len(todo)}", flush=True)
    if not todo:
        print("无需回补")
        return 0

    total, done, rows_n = len(todo), 0, 0
    buf = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for d, vals in ex.map(fetch_day, todo):
            done += 1
            for v in vals:
                code = str(v.get("SECURITY_CODE") or "").zfill(6)
                if len(code) != 6:
                    continue
                buf.append((code, d, v.get("PE_TTM"), v.get("PB_MRQ")))
            if len(buf) >= 60000:
                conn.executemany(
                    "INSERT INTO valuation_daily(symbol, date, pe_ttm, pb) VALUES(?,?,?,?) "
                    "ON CONFLICT(symbol, date) DO UPDATE SET pe_ttm=excluded.pe_ttm, pb=excluded.pb",
                    buf,
                )
                conn.commit()
                rows_n += len(buf)
                buf = []
            if done % 50 == 0 or done == total:
                rate = done / max(time.time() - t0, 1e-6)
                eta = (total - done) / max(rate, 1e-6) / 60
                print(f"  {done}/{total}  已写 {rows_n//1000} 千行  "
                      f"{time.time()-t0:.0f}s  剩余约 {eta:.0f} 分钟", flush=True)

    if buf:
        conn.executemany(
            "INSERT INTO valuation_daily(symbol, date, pe_ttm, pb) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol, date) DO UPDATE SET pe_ttm=excluded.pe_ttm, pb=excluded.pb",
            buf,
        )
        conn.commit()
        rows_n += len(buf)

    db.set_meta(conn, "valuation_daily_days", str(len(all_dates)))
    conn.commit()
    n = conn.execute("SELECT COUNT(1) FROM valuation_daily").fetchone()[0]
    d = conn.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM valuation_daily").fetchone()
    print(f"完成：写入 {rows_n} 行，库中 {n} 行 / {d[2]} 个交易日 / {d[0]} ~ {d[1]}，"
          f"用时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 250))
