# -*- coding: utf-8 -*-
"""数据同步任务（供 Web 界面与命令行共用）。

每个 sync_* 函数返回统计字典，并在 meta 表记录完成时间，界面据此显示「上次同步」。
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from . import db, patterns
from . import sources_cn as src

BARS_COLS = ["symbol", "date", "open", "high", "low", "close", "volume"]
HOLDER_COLS = ["symbol", "end_date", "announce_date", "holder_num", "chg_pct", "avg_mktcap"]
FIN_COLS = ["symbol", "report_period", "announce_date", "revenue", "net_profit",
            "net_profit_yoy", "eps", "roe", "is_cumulative", "revenue_yoy", "gross_margin"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _codes(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT symbol FROM securities ORDER BY symbol")]


def _refresh_counts(conn: sqlite3.Connection) -> None:
    conn.execute("""
        UPDATE securities SET n_bars = (
            SELECT COUNT(1) FROM bars b WHERE b.symbol = securities.symbol
        )
    """)


def _drifted(old: dict, rows: list[tuple]) -> bool:
    """复权漂移判断：重叠日期的收盘价差 > 0.3% 视为发生除权。

    最后一根是当天未收盘的（价格随时在变），不参与比较，否则盘中每次同步都会误判。
    """
    for d, _o, _h, _l, c, _v in rows[:-1]:
        if d in old and old[d] and abs(c - old[d]) / old[d] > 0.003:
            return True
    return False


def sync_bars(conn: sqlite3.Connection, progress=None, workers: int = 6) -> dict:
    """增量：补最近 40 天；发现复权漂移（除权除息）的股票整只重建。"""
    codes = _codes(conn)
    total, done = len(codes), 0
    t0 = datetime.now()
    recent: dict[str, list[tuple]] = {}

    def job(code: str):
        try:
            return code, src.fetch_bars_recent(code, days=40)
        except Exception:  # noqa: BLE001
            return code, []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for code, rows in ex.map(job, codes):
            if rows:
                recent[code] = rows
            done += 1
            if done % 300 == 0 and progress:
                progress(done, total, f"日线 {done}/{total}")

    # 复权漂移：新旧重叠日期收盘价相差 > 0.3% 说明发生了除权，前复权价整体变了
    drifted = []
    for code, rows in recent.items():
        cutoff = rows[0][0]
        old = dict(conn.execute("SELECT date, close FROM bars WHERE symbol=? AND date>=?", (code, cutoff)))
        if _drifted(old, rows):
            drifted.append(code)

    fixed = 0
    if drifted:
        if progress:
            progress(done, total, f"复权漂移，重建 {len(drifted)} 只 …")

        def refetch(code: str):
            try:
                return code, src.fetch_bars(code)
            except Exception:  # noqa: BLE001
                return code, []

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for code, rows in ex.map(refetch, drifted):
                if rows:
                    recent[code] = rows
                    fixed += 1

    buf = [(code, *r) for code, rows in recent.items() for r in rows]
    db.upsert(conn, "bars", BARS_COLS, buf)
    _refresh_counts(conn)
    conn.commit()
    db.set_meta(conn, "sync_bars_at", _now())
    conn.commit()
    secs = (datetime.now() - t0).total_seconds()
    return {"updated": done, "fixed": fixed, "seconds": round(secs, 1)}


def sync_bars_symbol(conn: sqlite3.Connection, symbol: str, days: int = 40) -> dict:
    """单只股票的日线同步：补最近 days 天；发现复权漂移就整只重建。

    给「收藏页 / K 线弹窗」的手动同步用，不用跑全市场。
    """
    symbol = str(symbol).zfill(6)
    row = conn.execute("SELECT name FROM securities WHERE symbol=?", (symbol,)).fetchone()
    if not row:
        return {"error": f"没有找到股票 {symbol}"}
    t0 = datetime.now()
    try:
        rows = src.fetch_bars_recent(symbol, days=days)
    except Exception as e:  # noqa: BLE001
        return {"error": f"拉取失败：{type(e).__name__}: {e}"}
    if not rows:
        return {"error": "上游没有返回数据（可能停牌或代码不对）"}

    fixed = False
    cutoff = rows[0][0]
    old = dict(conn.execute("SELECT date, close FROM bars WHERE symbol=? AND date>=?",
                            (symbol, cutoff)))
    if _drifted(old, rows):
        try:                                  # 除权导致前复权价整体变了，整只重拉
            full = src.fetch_bars(symbol)
            if full:
                rows, fixed = full, True
        except Exception:  # noqa: BLE001
            pass

    db.upsert(conn, "bars", BARS_COLS, [(symbol, *r) for r in rows])
    n = conn.execute("SELECT COUNT(1) FROM bars WHERE symbol=?", (symbol,)).fetchone()[0]
    conn.execute("UPDATE securities SET n_bars=? WHERE symbol=?", (n, symbol))
    now = _now()
    conn.execute("INSERT OR IGNORE INTO bar_meta(symbol, src, updated_at) VALUES(?, '', ?)",
                 (symbol, now))
    conn.execute("UPDATE bar_meta SET updated_at=? WHERE symbol=?", (now, symbol))
    conn.commit()
    db.set_meta(conn, "sync_bars_at", now)
    conn.commit()
    latest = conn.execute("SELECT MAX(date) FROM bars WHERE symbol=?", (symbol,)).fetchone()[0]
    return {"ok": True, "symbol": symbol, "name": row[0], "rows": len(rows),
            "latest": latest, "fixed": fixed, "n_bars": n,
            "seconds": round((datetime.now() - t0).total_seconds(), 2)}


def backfill_meta(conn: sqlite3.Connection) -> dict:
    """按首根 K 线日期反推来源：2022-08 之前就有数据的，只能是前复权源。"""
    qfq = [r[0] for r in conn.execute(
        "SELECT symbol FROM (SELECT symbol, MIN(date) m FROM bars GROUP BY symbol) WHERE m < '2022-08-01'")]
    rest = [r[0] for r in conn.execute("SELECT symbol FROM securities")]
    rest = [c for c in rest if c not in set(qfq)]
    _mark(conn, qfq, "em")
    _mark(conn, rest, "sina")
    conn.commit()
    have, total = qfq_progress(conn)
    return {"qfq": have, "sina": len(rest), "total": total}


def qfq_progress(conn: sqlite3.Connection) -> tuple[int, int]:
    """(已是前复权的只数, 总只数)"""
    total = conn.execute("SELECT COUNT(1) FROM securities").fetchone()[0]
    done = conn.execute("SELECT COUNT(1) FROM bar_meta WHERE src IN ('em','tx','txp')").fetchone()[0]
    return done, total


def _mark(conn: sqlite3.Connection, codes: list[str], src: str) -> None:
    if not codes:
        return
    ts = _now()
    conn.executemany(
        "INSERT INTO bar_meta(symbol, src, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(symbol) DO UPDATE SET src=excluded.src, updated_at=excluded.updated_at",
        [(c, src, ts) for c in codes],
    )


def rebuild_bars(conn: sqlite3.Connection, progress=None, workers: int = 6,
                 start: str = "2021-01-01") -> dict:
    """全量重建为前复权（可断点续跑）。

    逐批拉全历史、删旧插新；只接受前复权源（东财/腾讯）。
    某只票两个前复权源都失败时保留原数据，下次再试，不会混入不复权数据。
    进度记在 bar_meta(symbol, src)，中断后重跑只处理未完成的。
    """
    done_set = {r[0] for r in conn.execute("SELECT symbol FROM bar_meta WHERE src IN ('em','tx','txp')")}
    codes = [c for c in _codes(conn) if c not in done_set]
    total_all = conn.execute("SELECT COUNT(1) FROM securities").fetchone()[0]
    total = len(codes)
    ok = fail = rows_n = done = 0
    t0 = datetime.now()
    chunk = 200
    blocked = 0

    for i in range(0, total, chunk):
        part = codes[i:i + chunk]
        got: dict[str, list[tuple]] = {}
        srcs: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(src.fetch_bars_ex, c, start): c for c in part}
            for fut in as_completed(futs):
                code = futs[fut]
                try:
                    rows, s = fut.result()
                except Exception:  # noqa: BLE001
                    rows, s = [], "none"
                if rows and s in ("em", "tx", "txp"):
                    got[code] = rows
                    srcs[code] = s
                else:
                    fail += 1

        if not got:
            blocked += 1
            if progress:
                progress(done, total, f"数据源被限流，已连续 {blocked} 批失败")
            if blocked >= 3:           # 连续三批全失败，判定被限流，停止本轮
                break
            continue
        blocked = 0

        ph = ",".join("?" * len(got))
        conn.execute(f"DELETE FROM bars WHERE symbol IN ({ph})", tuple(got.keys()))
        buf = [(c, *r) for c, rows in got.items() for r in rows]
        db.upsert(conn, "bars", BARS_COLS, buf)
        for c, s in srcs.items():
            conn.execute(
                "INSERT INTO bar_meta(symbol, src, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET src=excluded.src, updated_at=excluded.updated_at",
                (c, s, _now()),
            )
        conn.commit()

        ok += len(got)
        rows_n += len(buf)
        done += len(part)
        if progress:
            have, _ = qfq_progress(conn)
            progress(done, total, f"前复权重建 {have}/{total_all}")

    _refresh_counts(conn)
    conn.commit()
    db.set_meta(conn, "sync_bars_at", _now())
    conn.commit()
    have, allc = qfq_progress(conn)
    return {"updated": ok, "failed": fail, "rows": rows_n, "qfq": have, "total": allc,
            "seconds": round((datetime.now() - t0).total_seconds(), 1)}


def sync_holders(conn: sqlite3.Connection, progress=None, workers: int = 6) -> dict:
    codes = _codes(conn)
    total, done, buf = len(codes), 0, []

    def job(code: str):
        try:
            return src.fetch_holders(code)
        except Exception:  # noqa: BLE001
            return []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(job, codes):
            buf += rows
            done += 1
            if len(buf) >= 20000:
                db.upsert(conn, "holders", HOLDER_COLS, buf)
                conn.commit()
                buf = []
            if done % 300 == 0 and progress:
                progress(done, total, f"股东户数 {done}/{total}")
    db.upsert(conn, "holders", HOLDER_COLS, buf)
    conn.commit()
    db.set_meta(conn, "sync_holders_at", _now())
    conn.commit()
    return {"updated": done}


def sync_financials(conn: sqlite3.Connection, progress=None, workers: int = 6) -> dict:
    codes = _codes(conn)
    total, done, buf = len(codes), 0, []

    def job(code: str):
        try:
            return src.fetch_financials(code)
        except Exception:  # noqa: BLE001
            return []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(job, codes):
            buf += rows
            done += 1
            if len(buf) >= 20000:
                db.upsert(conn, "financials", FIN_COLS, buf)
                conn.commit()
                buf = []
            if done % 300 == 0 and progress:
                progress(done, total, f"财务 {done}/{total}")
    db.upsert(conn, "financials", FIN_COLS, buf)
    conn.commit()
    db.set_meta(conn, "sync_financials_at", _now())
    conn.commit()
    return {"updated": done}


def sync_mktcap(conn: sqlite3.Connection, progress=None) -> dict:
    codes = _codes(conn)
    total, got = len(codes), {}
    for i in range(0, total, 500):
        got.update(src.fetch_mktcap_full_batch(codes[i:i + 500]))
        if progress:
            progress(min(i + 500, total), total, f"市值 {min(i + 500, total)}/{total}")
    today = datetime.now().strftime("%Y-%m-%d")
    conn.executemany(
        "UPDATE securities SET mktcap=?, float_mktcap=?, mktcap_date=? WHERE symbol=?",
        [(v[0], v[1], today, k) for k, v in got.items()],
    )
    conn.commit()
    db.set_meta(conn, "sync_mktcap_at", _now())
    conn.commit()
    return {"updated": len(got)}


def sync_list_date(conn: sqlite3.Connection, progress=None, workers: int = 8) -> dict:
    todo = [r[0] for r in conn.execute(
        "SELECT symbol FROM securities WHERE list_date IS NULL OR list_date=''")]
    total, done, got = len(todo), 0, []

    def job(code: str):
        return code, src.fetch_list_date(code)

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for code, d in ex.map(job, todo):
                if d:
                    got.append((d, code))
                done += 1
                if done % 500 == 0 and progress:
                    progress(done, total, f"上市日期 {done}/{total}")
        conn.executemany("UPDATE securities SET list_date=? WHERE symbol=?", got)
        conn.commit()
    db.set_meta(conn, "sync_list_date_at", _now())
    conn.commit()
    return {"updated": len(got)}


def sync_valuation(conn: sqlite3.Connection, progress=None) -> dict:
    """估值快照 + 近 12 个月股息率。"""
    td = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    if not td:
        return {"error": "日线为空，先同步日线"}
    # 取 3 年分红：近 400 天有记录就用（TTM 口径），没有就回退到最近一次分红
    # （年付息股在「除权满一年、下一次还没派」的空档不会突然变空）。
    since = (datetime.fromisoformat(td) - timedelta(days=1095)).date().isoformat()
    recent_from = (datetime.fromisoformat(td) - timedelta(days=400)).date().isoformat()

    if progress:
        progress(0, 3, "拉取全市场估值 …")
    vals = src.fetch_valuation(td)

    if progress:
        progress(1, 3, "拉取分红方案 …")
    divs = src.fetch_dividends(since)

    # 每股分红 = 每 10 股派息 / 10，只统计已除权且在 td 之前的
    dps: dict[str, float] = {}          # 近 400 天累计（TTM 口径）
    last_dps: dict[str, tuple[str, float]] = {}   # 最近一次分红（日期, 每股）
    for d in divs:
        code = str(d.get("SECURITY_CODE") or "").zfill(6)
        ex = (d.get("EX_DIVIDEND_DATE") or "")[:10]
        amt = d.get("PRETAX_BONUS_RMB")
        if not (len(code) == 6 and ex and ex <= td and amt):
            continue
        try:
            per_share = float(amt) / 10.0
        except (TypeError, ValueError):
            continue
        if ex >= recent_from:
            dps[code] = dps.get(code, 0.0) + per_share
        if code not in last_dps or ex > last_dps[code][0]:
            last_dps[code] = (ex, per_share)
    # 近 400 天没有除权的，回退到最近一次
    for code, (_ex, per_share) in last_dps.items():
        dps.setdefault(code, per_share)

    # 分红记录太少 = 上游接口这次没抓全，此时保留库里原有的股息率，不要把整列冲成空
    keep_old_dv = len(dps) < 500
    old_dv = dict(conn.execute("SELECT symbol, dv_ttm FROM valuation").fetchall()) if keep_old_dv else {}

    # 用「每只股票最近一根日线的收盘价」：当天停牌/还没出数据时也能算出股息率，
    # 否则这些票的 dv 会被写成 None，筛选直接漏掉。
    close: dict[str, float] = {}
    back = (datetime.fromisoformat(td) - timedelta(days=15)).date().isoformat()
    for sym, c_ in conn.execute(
            "SELECT symbol, close FROM bars WHERE date >= ? ORDER BY date", (back,)).fetchall():
        close[sym] = c_
    rows = []
    for v in vals:
        code = str(v.get("SECURITY_CODE") or "").zfill(6)
        if len(code) != 6:
            continue
        c = close.get(code)
        d_share = dps.get(code)
        if keep_old_dv:
            dv = old_dv.get(code)
        else:
            dv = round(d_share / c * 100, 3) if (c and d_share) else None
        rows.append((
            code, td, v.get("PE_TTM"), v.get("PE_LAR"), v.get("PB_MRQ"), v.get("PS_TTM"),
            v.get("PCF_OCF_TTM"), dv, v.get("BOARD_NAME"), v.get("TOTAL_MARKET_CAP"), _now(),
        ))

    if progress:
        progress(2, 3, f"写入 {len(rows)} 条 …")
    conn.executemany("""
        INSERT INTO valuation(symbol, trade_date, pe_ttm, pe_static, pb, ps_ttm, pcf_ttm, dv_ttm,
                              industry, total_mv, updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol) DO UPDATE SET
            trade_date=excluded.trade_date, pe_ttm=excluded.pe_ttm, pe_static=excluded.pe_static,
            pb=excluded.pb, ps_ttm=excluded.ps_ttm, pcf_ttm=excluded.pcf_ttm, dv_ttm=excluded.dv_ttm,
            industry=excluded.industry, total_mv=excluded.total_mv, updated_at=excluded.updated_at
    """, rows)
    # 同时写一份历史序列，便于以后算 PE/PB 分位
    conn.executemany(
        "INSERT INTO valuation_daily(symbol, date, pe_ttm, pb, dv_ttm) VALUES(?,?,?,?,?) "
        "ON CONFLICT(symbol, date) DO UPDATE SET pe_ttm=excluded.pe_ttm, pb=excluded.pb, dv_ttm=excluded.dv_ttm",
        [(r[0], td, r[2], r[4], r[7]) for r in rows],
    )
    conn.commit()
    db.set_meta(conn, "sync_valuation_at", _now())
    conn.commit()
    n_dv = sum(1 for r in rows if r[7] is not None)
    return {"updated": len(rows), "with_dividend": n_dv, "trade_date": td}


def backfill_valuation(conn: sqlite3.Connection, days: int = 250, progress=None) -> dict:
    """回补历史估值（PE/PB），用于算分位数。只补 pe/pb，股息率是当日口径。"""
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT ?", (days,))]
    dates.reverse()
    total, done, rows_n = len(dates), 0, 0
    for i, d in enumerate(dates):
        try:
            vals = src.fetch_valuation(d)
        except Exception:  # noqa: BLE001
            done += 1
            continue
        buf = []
        for v in vals:
            code = str(v.get("SECURITY_CODE") or "").zfill(6)
            if len(code) != 6:
                continue
            buf.append((code, d, v.get("PE_TTM"), v.get("PB_MRQ")))
        if buf:
            conn.executemany(
                "INSERT INTO valuation_daily(symbol, date, pe_ttm, pb) VALUES(?,?,?,?) "
                "ON CONFLICT(symbol, date) DO UPDATE SET pe_ttm=excluded.pe_ttm, pb=excluded.pb",
                buf,
            )
            conn.commit()
            rows_n += len(buf)
        done += 1
        if progress:
            progress(done, total, f"回补估值 {d}（{done}/{total}）")
    db.set_meta(conn, "valuation_daily_days", str(len(dates)))
    conn.commit()
    return {"days": len(dates), "rows": rows_n}


SYNCERS = {
    "bars": ("日线行情", sync_bars),
    "bars_rebuild": ("日线全量重建", rebuild_bars),
    "valuation": ("估值数据", sync_valuation),
    "holders": ("股东户数", sync_holders),
    "financials": ("财务数据", sync_financials),
    "mktcap": ("总市值", sync_mktcap),
    "list_date": ("上市日期", sync_list_date),
    "patterns": ("形态缓存", patterns.clear_cache),
}


def run(kind: str, progress=None) -> dict:
    name, fn = SYNCERS[kind]
    conn = db.connect()
    try:
        return fn(conn, progress=progress)
    finally:
        db.optimize(conn)      # 大批写入后刷新统计信息
        conn.close()
