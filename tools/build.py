# -*- coding: utf-8 -*-
"""A 股前置数据构建：首次全量拉取入库，之后每日增量。

    python tools/build.py init                  # 全量建库（约 10 分钟）
    python tools/build.py init --limit 100      # 小样本试跑
    python tools/build.py update                # 每日增量（日线）
    python tools/build.py update --fundamentals # 每周增量（含财务/股东户数）
    python tools/build.py status                # 库状态
    python tools/build.py validate              # 数据体检
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from astock import db
from astock import sources_cn as src
from astock import sync as sync_mod

BARS_COLS = ["symbol", "date", "open", "high", "low", "close", "volume"]
HOLDER_COLS = ["symbol", "end_date", "announce_date", "holder_num", "chg_pct", "avg_mktcap"]
FIN_COLS = ["symbol", "report_period", "announce_date", "revenue", "net_profit",
            "net_profit_yoy", "eps", "roe", "is_cumulative"]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _clean(conn) -> int:
    """清掉停牌日被错误记录成 0 价的占位行，并刷新冗余统计列。"""
    n = conn.execute("DELETE FROM bars WHERE open<=0 OR high<=0 OR low<=0 OR close<=0").rowcount
    conn.execute("""
        UPDATE securities SET n_bars = (
            SELECT COUNT(1) FROM bars b WHERE b.symbol = securities.symbol
        )
    """)
    conn.commit()
    return n


# ------------------------------------------------------------------ 全量
def cmd_init(args) -> int:
    conn = db.connect()
    t0 = time.time()

    log("拉取股票列表 …")
    uni = src.fetch_universe()
    if args.limit:
        uni = uni[: args.limit]
    db.upsert_pk1(conn, "securities",
                  ["symbol", "name", "market", "board", "status", "updated_at"],
                  [(u["symbol"], u["name"], u["market"], u["board"], "L",
                    datetime.now().isoformat(timespec="seconds")) for u in uni])
    conn.commit()
    log(f"股票 {len(uni)} 只")

    log("拉取交易日历 …")
    cal = src.fetch_calendar(start=args.start)
    conn.executemany("INSERT OR IGNORE INTO calendar(date) VALUES(?)", [(d,) for d in cal])
    conn.commit()
    log(f"交易日 {len(cal)} 天（{cal[0]} ~ {cal[-1]}）" if cal else "交易日历为空")

    log(f"开始拉取 {len(uni)} 只的日线/股东户数/财务（{args.workers} 线程）…")
    buf_bars, buf_hold, buf_fin = [], [], []
    done = 0
    t_last = time.time()

    def job(u: dict):
        code = u["symbol"]
        try:
            bars = src.fetch_bars(code, start=args.start)
        except Exception:  # noqa: BLE001
            bars = []
        try:
            hold = src.fetch_holders(code)
        except Exception:  # noqa: BLE001
            hold = []
        try:
            fin = src.fetch_financials(code)
        except Exception:  # noqa: BLE001
            fin = []
        return code, bars, hold, fin

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(job, u) for u in uni]
        for fut in as_completed(futs):
            code, bars, hold, fin = fut.result()
            buf_bars += [(code, b[0], b[1], b[2], b[3], b[4], b[5]) for b in bars]
            buf_hold += hold
            buf_fin += fin
            done += 1
            if done % 300 == 0:
                db.upsert(conn, "bars", BARS_COLS, buf_bars)
                db.upsert(conn, "holders", HOLDER_COLS, buf_hold)
                db.upsert(conn, "financials", FIN_COLS, buf_fin)
                conn.commit()
                buf_bars, buf_hold, buf_fin = [], [], []
                rate = 300 / max(time.time() - t_last, 1e-6)
                t_last = time.time()
                eta = (len(uni) - done) / max(rate, 1e-6) / 60
                log(f"  {done}/{len(uni)}  剩余约 {eta:.0f} 分钟")

    db.upsert(conn, "bars", BARS_COLS, buf_bars)
    db.upsert(conn, "holders", HOLDER_COLS, buf_hold)
    db.upsert(conn, "financials", FIN_COLS, buf_fin)
    conn.commit()

    db.set_meta(conn, "init_at", datetime.now().isoformat(timespec="seconds"))
    db.set_meta(conn, "start", args.start)
    db.set_meta(conn, "limit", str(args.limit or 0))
    conn.commit()
    log(f"清理 0 价占位行 {_clean(conn)} 行")

    st = db.stats(conn)
    log(f"完成，用时 {(time.time()-t0)/60:.1f} 分钟")
    for k, v in st.items():
        log(f"  {k:20} {v}")
    db.optimize(conn)
    return 0


# ------------------------------------------------------------------ 增量
def _last_bar_date(conn, code: str) -> str | None:
    r = conn.execute("SELECT MAX(date) FROM bars WHERE symbol=?", (code,)).fetchone()
    return r[0] if r and r[0] else None


def cmd_update(args) -> int:
    conn = db.connect()
    t0 = time.time()

    log("刷新股票列表 …")
    uni = src.fetch_universe()
    db.upsert_pk1(conn, "securities",
                  ["symbol", "name", "market", "board", "status", "updated_at"],
                  [(u["symbol"], u["name"], u["market"], u["board"], "L",
                    datetime.now().isoformat(timespec="seconds")) for u in uni])
    conn.commit()

    log("增量更新日线（回补 40 天，检测复权漂移）…")
    recent_map: dict[str, list[tuple]] = {}
    done = 0

    def job(code: str):
        try:
            return code, src.fetch_bars_recent(code, days=40)
        except Exception:  # noqa: BLE001
            return code, []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for code, rows in ex.map(job, [u["symbol"] for u in uni]):
            if rows:
                recent_map[code] = rows
            done += 1
            if done % 1000 == 0:
                log(f"  拉取 {done}/{len(uni)}  {(time.time()-t0):.0f}s")

    # 复权漂移：新数据与库里重叠日期的收盘价偏差 > 0.3% => 发生除权，整只重建
    drifted = []
    for code, rows in recent_map.items():
        cutoff = rows[0][0]
        old = dict(conn.execute(
            "SELECT date, close FROM bars WHERE symbol=? AND date>=?", (code, cutoff)
        ).fetchall())
        if any(d in old and old[d] and abs(c - old[d]) / old[d] > 0.003 for d, o, h, l, c, v in rows):
            drifted.append(code)

    log(f"  复权漂移 {len(drifted)} 只，整只重建 …")
    if drifted:
        def refetch(code: str):
            try:
                return code, src.fetch_bars(code, start=args.start)
            except Exception:  # noqa: BLE001
                return code, []

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for code, rows in ex.map(refetch, drifted):
                if rows:
                    recent_map[code] = rows

    buf = [(code, *r) for code, rows in recent_map.items() for r in rows]
    db.upsert(conn, "bars", BARS_COLS, buf)
    conn.commit()
    log(f"  写入 {len(buf)} 行")

    if args.fundamentals:
        log("刷新股东户数 / 财务 …")
        hb, fb, n = [], [], 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(src.fetch_holders, u["symbol"]): u["symbol"] for u in uni}
            for fut in as_completed(futs):
                try:
                    hb += fut.result()
                except Exception:  # noqa: BLE001
                    pass
                n += 1
                if n % 500 == 0:
                    db.upsert(conn, "holders", HOLDER_COLS, hb)
                    conn.commit()
                    hb = []
        db.upsert(conn, "holders", HOLDER_COLS, hb)

        n = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(src.fetch_financials, u["symbol"]): u["symbol"] for u in uni}
            for fut in as_completed(futs):
                try:
                    fb += fut.result()
                except Exception:  # noqa: BLE001
                    pass
                n += 1
                if n % 500 == 0:
                    db.upsert(conn, "financials", FIN_COLS, fb)
                    conn.commit()
                    fb = []
        db.upsert(conn, "financials", FIN_COLS, fb)
        conn.commit()

        log("刷新前十大股东占比 …")
        try:
            res = sync_mod.sync_top10(conn)
            log(f"  前十大股东占比：{res}")
        except Exception as e:  # noqa: BLE001
            log(f"  前十大股东占比失败：{type(e).__name__}: {e}")

    db.set_meta(conn, "update_at", datetime.now().isoformat(timespec="seconds"))
    conn.commit()
    log(f"清理 0 价占位行 {_clean(conn)} 行")
    log(f"增量完成，用时 {(time.time()-t0)/60:.1f} 分钟")
    st = db.stats(conn)
    for k, v in st.items():
        log(f"  {k:20} {v}")
    return 0


# ------------------------------------------------------------------ 补上市日期
def cmd_backfill(args) -> int:
    conn = db.connect()
    todo = [r[0] for r in conn.execute("SELECT symbol FROM securities WHERE list_date IS NULL OR list_date=''")]
    if args.limit:
        todo = todo[: args.limit]
    log(f"待补上市日期 {len(todo)} 只 …")
    t0, done, ok = time.time(), 0, 0
    buf = []

    def job(code: str):
        return code, src.fetch_list_date(code)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for code, d in ex.map(job, todo):
            done += 1
            if d:
                buf.append((d, code))
                ok += 1
            if len(buf) >= 500:
                conn.executemany("UPDATE securities SET list_date=? WHERE symbol=?", buf)
                conn.commit()
                buf = []
            if done % 1000 == 0:
                log(f"  {done}/{len(todo)}  成功 {ok}  {(time.time()-t0):.0f}s")
    if buf:
        conn.executemany("UPDATE securities SET list_date=? WHERE symbol=?", buf)
    conn.commit()
    log(f"完成：成功 {ok}/{len(todo)}，用时 {(time.time()-t0)/60:.1f} 分钟")
    return 0


# ------------------------------------------------------------------ 补市值
def cmd_mktcap(args) -> int:
    conn = db.connect()
    codes = [r[0] for r in conn.execute("SELECT symbol FROM securities ORDER BY symbol")]
    log(f"更新总市值 {len(codes)} 只（腾讯批量，50 只/次）…")
    t0 = time.time()
    got: dict[str, float] = {}
    for i in range(0, len(codes), 500):
        try:
            got.update(src.fetch_mktcap_batch(codes[i:i + 500]))
        except Exception as e:  # noqa: BLE001
            log(f"  批次 {i} 失败：{type(e).__name__}")
        if (i // 500) % 2 == 0:
            log(f"  {min(i + 500, len(codes))}/{len(codes)}  已获取 {len(got)}  {(time.time()-t0):.0f}s")
    today = datetime.now().strftime("%Y-%m-%d")
    conn.executemany(
        "UPDATE securities SET mktcap=?, mktcap_date=? WHERE symbol=?",
        [(v, today, k) for k, v in got.items()],
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(1) FROM securities WHERE mktcap IS NOT NULL").fetchone()[0]
    log(f"完成：{len(got)} 只，库中有市值 {n} 只，用时 {time.time()-t0:.0f}s")
    return 0


# ------------------------------------------------------------------ 状态
def cmd_checkpoint(args) -> int:
    """把 WAL 合并回主库；-shm 是平台相关的，跨系统切换前删掉可避免 disk I/O error。"""
    path = db.DB_PATH
    for suf in ("-wal", "-shm"):
        f = path + suf
        if os.path.exists(f):
            print(f"  {suf}: {os.path.getsize(f)/1e6:.1f} MB")
    conn = db.connect()
    try:
        print("  journal:", conn.execute("PRAGMA journal_mode").fetchone()[0])
        busy, log, done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        print(f"  checkpoint: busy={busy} log={log} checkpointed={done}")
    finally:
        conn.close()
    if args.drop_shm:
        f = path + "-shm"
        if os.path.exists(f):
            try:
                os.remove(f)
                print("  已删除 -shm")
            except OSError as e:
                print(f"  删不掉 -shm（{e.strerror}）：可能有同步/杀毒软件正占用，"
                      f"停掉它再试，或忽略——主库本身不受影响")
    print(f"  主库 {os.path.getsize(path)/1e6:.0f} MB")
    return 0


def cmd_status(args) -> int:
    conn = db.connect()
    st = db.stats(conn)
    print("=" * 60)
    print(f"数据库 {db.DB_PATH}")
    print("=" * 60)
    for k, v in st.items():
        print(f"  {k:22} {v}")
    print(f"  {'init_at':22} {db.get_meta(conn, 'init_at', '-')}")
    print(f"  {'update_at':22} {db.get_meta(conn, 'update_at', '-')}")
    return 0


# ------------------------------------------------------------------ 体检
def cmd_validate(args) -> int:
    conn = db.connect()
    print("=" * 72)
    print("数据体检")
    print("=" * 72)

    total_sec = conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    with_bars = conn.execute("SELECT COUNT(DISTINCT symbol) FROM bars").fetchone()[0]
    thin = conn.execute(
        "SELECT COUNT(*) FROM (SELECT symbol, COUNT(*) n FROM bars GROUP BY symbol HAVING n < 200)"
    ).fetchone()[0]
    print(f"股票 {total_sec} 只，有日线 {with_bars} 只，日线不足 200 根 {thin} 只")

    last = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    stale = conn.execute(
        "SELECT COUNT(*) FROM (SELECT symbol, MAX(date) m FROM bars GROUP BY symbol) WHERE m < ?", (last,)
    ).fetchone()[0]
    print(f"最新交易日 {last}，未更新到该日（停牌/退市）{stale} 只")

    bad = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE high < low OR close > high*1.001 OR close < low*0.999 OR open <= 0"
    ).fetchone()[0]
    print(f"价格异常行（high<low 或 close 越界）{bad} 行")

    nh = conn.execute("SELECT COUNT(DISTINCT symbol) FROM holders").fetchone()[0]
    nf = conn.execute("SELECT COUNT(DISTINCT symbol) FROM financials").fetchone()[0]
    print(f"有股东户数 {nh} 只，有财务 {nf} 只")

    miss_ann = conn.execute("SELECT COUNT(*) FROM financials WHERE announce_date IS NULL").fetchone()[0]
    print(f"财务缺公告日 {miss_ann} 行（做 PIT 时需剔除）")

    q = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT symbol, COUNT(*) c FROM holders
            WHERE end_date >= '2024-01-01' GROUP BY symbol HAVING c >= 3
        )""").fetchone()[0]
    print(f"近两年有 ≥3 期股东户数的股票 {q} 只")

    print("\n最近 5 个交易日覆盖：")
    rows = conn.execute("""
        SELECT date, COUNT(*) FROM bars
        WHERE date >= (SELECT MIN(date) FROM (SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT 5))
        GROUP BY date ORDER BY date DESC
    """).fetchall()
    for d, c in rows:
        print(f"  {d}  {c} 只")

    # 成交量单位体检：不同数据源的 volume 单位不一致（新浪=股、东财/腾讯=手），
    # 一旦混用，全市场平均量会在某一天出现 5 倍以上的跳变。
    print("\n成交量量级体检（相邻交易日全市场中位倍数，超过 5 倍说明混入了错误单位）：")
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT 21").fetchall()][::-1]
    bad_days = []
    for prev, cur in zip(dates, dates[1:]):
        rows = conn.execute("""
            SELECT a.volume, b.volume FROM bars a JOIN bars b ON b.symbol = a.symbol
            WHERE a.date = ? AND b.date = ? AND a.volume > 0 AND b.volume > 0
        """, (prev, cur)).fetchall()
        if len(rows) < 100:
            continue
        ratios = sorted(y / x for x, y in rows)
        med = ratios[len(ratios) // 2]
        flag = "  ⚠ 单位可疑" if med > 5 or med < 0.2 else ""
        if flag:
            bad_days.append(cur)
        print(f"  {prev} → {cur}  中位 {med:6.2f}x  （{len(rows)} 只）{flag}")
    if bad_days:
        print(f"  ⚠ 疑似成交量单位错乱：{', '.join(bad_days)}")
        print("    修复：python repair_bars.py 90")
    else:
        print("  正常：各日量级连续，没有单位跳变")

    print("=" * 72)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=6)
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("update")
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--fundamentals", action="store_true")
    p.set_defaults(fn=cmd_update)

    p = sub.add_parser("checkpoint")
    p.add_argument("--drop-shm", action="store_true",
                   help="顺手删掉 -shm（在 Windows 与 Docker 之间切换时用）")
    p.set_defaults(fn=cmd_checkpoint)

    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("validate")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("backfill")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(fn=cmd_backfill)

    p = sub.add_parser("mktcap")
    p.set_defaults(fn=cmd_mktcap)

    p = sub.add_parser("clean")
    p.set_defaults(fn=lambda a: (lambda c: print("清理", _clean(c), "行"))(db.connect()))

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
