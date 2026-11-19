# -*- coding: utf-8 -*-
"""A 股选股系统 · Web 界面（标准库 http.server，无第三方依赖）

    python web/app.py            # 默认 http://127.0.0.1:5001
    python web/app.py --port 8080

路由（Flask 风格，迁移时一一对应）：
    GET /                       首页
    GET /api/screen             选股（返回 JSON）
    GET /api/kline/<code>       单只 K 线
    GET /static/*               静态资源
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
import time
import uuid
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from astock import db, metrics, patterns, query   # noqa: E402
from astock import siteconfig, sync, users        # noqa: E402
from astock import sources_cn as src              # noqa: E402

STATIC = os.path.join(HERE, "static")
TEMPLATES = os.path.join(HERE, "templates")
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 120.0        # 结果缓存有效期（秒）
_CACHE_MAX = 16           # 最多留几套条件的结果，防止内存无限增长
JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()

# 单只股票同步的新鲜度判断：60 秒内已经同步过就直接复用，避免多人重复打上游
_BAR_FRESH_SECONDS = 60.0
_BAR_SYNC_LOCKS: dict[str, threading.Lock] = {}
_BAR_SYNC_LOCKS_GUARD = threading.Lock()


def _bar_sync_lock(symbol: str) -> threading.Lock:
    with _BAR_SYNC_LOCKS_GUARD:
        return _BAR_SYNC_LOCKS.setdefault(symbol, threading.Lock())


def _bar_is_fresh(conn, symbol: str) -> bool:
    row = conn.execute("SELECT updated_at FROM bar_meta WHERE symbol=?", (symbol,)).fetchone()
    if not row or not row[0]:
        return False
    try:
        t = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return False
    return (datetime.now() - t).total_seconds() < _BAR_FRESH_SECONDS


def _running_job() -> str | None:
    for jid, j in JOBS.items():
        if j["status"] == "running":
            return jid
    return None


def start_sync(kind: str) -> dict:
    if kind not in sync.SYNCERS:
        return {"error": f"未知数据类型：{kind}"}
    with _JOB_LOCK:
        busy = _running_job()
        if busy:
            return {"error": "已有同步任务在运行", "job": busy}
        jid = uuid.uuid4().hex[:8]
        JOBS[jid] = {"id": jid, "kind": kind, "name": sync.SYNCERS[kind][0],
                     "status": "running", "done": 0, "total": 0,
                     "msg": "启动中…", "started": time.time(), "result": None}

    def run():
        def prog(done, total, msg):
            JOBS[jid].update(done=done, total=total, msg=msg)
        log(f"同步开始：{sync.SYNCERS[kind][0]}（job {jid}）")
        try:
            res = sync.run(kind, progress=prog)
            JOBS[jid].update(status="done", result=res, msg="完成", done=1, total=1)
            log(f"同步完成：{sync.SYNCERS[kind][0]}  {res}")
        except Exception as e:  # noqa: BLE001
            JOBS[jid].update(status="error", msg=f"{type(e).__name__}: {e}")
            log(f"同步失败：{sync.SYNCERS[kind][0]}  {type(e).__name__}: {e}")
        finally:
            _CACHE.clear()
            _clear_fav_fields()

    threading.Thread(target=run, daemon=True).start()
    return {"job": jid}


# ---------------------------------------------------------------- 自动同步
AUTO_SYNC_KINDS = ("bars", "valuation", "mktcap", "holders", "top10", "financials")


def start_auto_sync() -> str | None:
    """依次自动更新主要数据集；已有任务时返回 None。"""
    with _JOB_LOCK:
        if _running_job():
            return None
        jid = uuid.uuid4().hex[:8]
        JOBS[jid] = {"id": jid, "kind": "auto", "name": "自动同步", "status": "running",
                     "done": 0, "total": 0, "msg": "准备自动同步…",
                     "started": time.time(), "result": None}

    def run():
        results = {}
        for kind in AUTO_SYNC_KINDS:
            name = sync.SYNCERS[kind][0]
            JOBS[jid].update(kind=kind, name=name, msg=f"自动同步：{name}…", done=0, total=0)
            log(f"自动同步开始：{name}")
            try:
                res = sync.run(kind, progress=lambda d, t, m: JOBS[jid].update(done=d, total=t, msg=m))
                results[kind] = res
                log(f"自动同步完成：{name}  {res}")
            except Exception as e:  # noqa: BLE001
                results[kind] = {"error": f"{type(e).__name__}: {e}"}
                log(f"自动同步失败：{name}  {type(e).__name__}: {e}")
        JOBS[jid].update(kind="auto", name="自动同步", status="done",
                         result=results, msg="完成", done=1, total=1)
        _CACHE.clear()

    threading.Thread(target=run, daemon=True).start()
    return jid


def auto_sync_loop() -> None:
    """每分钟检查一次；到点且今天没跑过就启动自动同步。"""
    while True:
        try:
            conn = db.connect()
            try:
                cfg = siteconfig.get(conn)
                last = db.get_meta(conn, "auto_sync_last") or ""
            finally:
                conn.close()
            now = datetime.now()
            # 只要「已过配置时间」且今天还没跑过就跑：服务重启/关机错过时间点也能补上
            due = bool(cfg["sync_time"]) and now.strftime("%H:%M") >= cfg["sync_time"]
            if cfg["auto_sync"] == "1" and due:
                stamp = now.strftime("%Y-%m-%dT%H:%M")
                if (last or "")[:10] != now.strftime("%Y-%m-%d"):
                    jid = start_auto_sync()
                    if jid:
                        conn = db.connect()
                        try:
                            db.set_meta(conn, "auto_sync_last", stamp)
                            conn.commit()
                        finally:
                            conn.close()
                        log(f"自动同步任务已启动（{stamp}）")
        except Exception as e:  # noqa: BLE001
            log(f"自动同步检查失败：{type(e).__name__}: {e}")
        time.sleep(25)


# ---------------------------------------------------------------- 保存的查询
DEFAULT_SCREENS = [
    ("形态·做小做底", [
        {"k": "base_ok", "on": True},
        {"k": "float_mktcap", "min": 20, "max": 80},
        {"k": "dv", "min": 2},
        {"k": "exclude_st", "on": True},
    ]),
    ("形态·低位放量", [
        {"k": "volup_ok", "on": True, "p": "3", "d": "std", "lp": "30"},
        {"k": "exclude_st", "on": True},
    ]),
    ("形态·W底突破", [
        {"k": "w_brk", "on": True, "p": "10", "d": "3"},
        {"k": "exclude_st", "on": True},
    ]),
    ("形态·W底右底", [
        {"k": "w_pend", "on": True, "p": "10"},
        {"k": "exclude_st", "on": True},
    ]),
    ("形态·底部启动", [
        {"k": "base_ok", "on": True, "p": "20", "d": "std"},
        {"k": "float_mktcap", "min": 20, "max": 300},
        {"k": "exclude_st", "on": True},
    ]),
    ("形态·旗形整理", [
        {"k": "flag_ok", "on": True, "p": "auto", "d": "2-8"},
        {"k": "exclude_st", "on": True},
    ]),
    ("形态·旗形突破", [
        {"k": "flag_brk", "on": True},
        {"k": "exclude_st", "on": True},
    ]),
    ("形态·低位+筹码集中", [
        {"k": "base_ok", "on": True},
        {"k": "holder_chg", "p": "1", "min": 5},
        {"k": "holder_chg", "p": "3", "min": 10},
        {"k": "exclude_st", "on": True},
    ]),
    ("筹码集中", [
        {"k": "holder_chg", "p": "1", "min": 5},
        {"k": "holder_chg", "p": "3", "min": 15},
        {"k": "profit_yi", "min": 0.1},
        {"k": "exclude_st", "on": True},
    ]),
    ("红利", [
        {"k": "dv", "min": 4},
        {"k": "pb_pct", "max": 40},
        {"k": "roe", "min": 8},
        {"k": "exclude_st", "on": True},
    ]),
    ("高股息", [
        {"k": "dv", "min": 6},
        {"k": "dv_pct", "min": 70},
        {"k": "exclude_st", "on": True},
    ]),
    ("高成长", [
        {"k": "rev_yoy", "min": 30},
        {"k": "np_yoy", "min": 30},
        {"k": "roe", "min": 12},
        {"k": "exclude_st", "on": True},
    ]),
]


def seed_screens(conn) -> int:
    """写入默认选股方案。按名字逐个判断：新加的默认方案会自动补上，
    用户手动删掉的不会在重启时复活（删掉的同时会把 seed 标记一起清掉）。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for name, conds in DEFAULT_SCREENS:
        mark = "seed:" + name
        if db.get_meta(conn, mark) == "1":
            continue
        hit = conn.execute("SELECT 1 FROM screens WHERE name=? AND user_id IS NULL", (name,)).fetchone()
        if not hit:
            conn.execute(
                "INSERT INTO screens(user_id, name, conds, created_at, updated_at) VALUES(NULL,?,?,?,?)",
                (name, json.dumps(conds, ensure_ascii=False), ts, ts))
            n += 1
        db.set_meta(conn, mark, "1")
    db.set_meta(conn, "screens_seeded", "1")
    conn.commit()
    return n


def list_screens(conn, user_id: int | None) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, conds, created_at FROM screens "
        "WHERE user_id IS NULL OR user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
    out = []
    for sid, name, conds, created in rows:
        try:
            parsed = json.loads(conds)
        except json.JSONDecodeError:
            parsed = []
        out.append({"id": sid, "name": name, "conds": parsed, "created_at": created})
    return out


def save_screen(conn, user_id: int | None, name: str, conds) -> dict:
    name = (name or "").strip()[:40]
    if not name:
        return {"error": "请填写名称"}
    if not isinstance(conds, list) or not conds:
        return {"error": "当前没有可保存的条件"}
    payload = json.dumps(conds, ensure_ascii=False)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    old = conn.execute(
        "SELECT id FROM screens WHERE name = ? AND (user_id IS ? OR user_id = ?)",
        (name, user_id, user_id)).fetchone()
    if old:
        conn.execute("UPDATE screens SET conds = ?, updated_at = ? WHERE id = ?",
                     (payload, now, old[0]))
        sid = old[0]
        log(f"更新查询「{name}」")
    else:
        cur = conn.execute(
            "INSERT INTO screens(user_id, name, conds, created_at, updated_at) "
            "VALUES(?,?,?,?,?)", (user_id, name, payload, now, now))
        sid = cur.lastrowid
        log(f"保存查询「{name}」")
    conn.commit()
    return {"ok": True, "id": sid, "name": name,
            "screens": list_screens(conn, user_id)}


def delete_screen(conn, sid, user_id: int | None) -> int:
    if not sid:
        return 0
    cur = conn.execute("DELETE FROM screens WHERE id = ? AND (user_id IS NULL OR user_id = ?)",
                       (int(sid), user_id))
    conn.commit()
    return cur.rowcount


_FAV_FIELDS_CACHE: dict[str, tuple[float, dict]] = {}
_FAV_FIELDS_TTL = 60.0
_FAV_FIELDS_LOCK = threading.Lock()


def _clear_fav_fields() -> None:
    with _FAV_FIELDS_LOCK:
        _FAV_FIELDS_CACHE.clear()


def _num(v, dec: int = 2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return round(f, dec)


def _favorite_stock_fields(conn, symbols: list[str]) -> dict[str, dict]:
    """只为收藏涉及的股票算 PE/PB/财务/户数等字段，不再构建全市场宽表。

    按股票代码缓存 60 秒：同一只票被多个用户收藏时，只算一次。
    """
    symbols = [s for s in dict.fromkeys(symbols) if s]
    if not symbols:
        return {}
    now = time.time()
    out: dict[str, dict] = {}
    missing: list[str] = []
    with _FAV_FIELDS_LOCK:
        for s in symbols:
            hit = _FAV_FIELDS_CACHE.get(s)
            if hit and now - hit[0] < _FAV_FIELDS_TTL:
                out[s] = dict(hit[1])
            else:
                missing.append(s)
        if not missing:
            return out

        ph = ",".join("?" * len(missing))
        params = tuple(missing)
        base = query._q(conn, f"""
            SELECT s.symbol, s.mktcap,
                   v.pe_ttm AS pe, v.pb, v.dv_ttm AS dv,
                   COALESCE(v.industry, '') AS industry
            FROM securities s LEFT JOIN valuation v ON v.symbol = s.symbol
            WHERE s.symbol IN ({ph})
        """, params).set_index("symbol")

        # 最新一期财报（与 metrics 的口径一致：net_profit 非空、按报告期最新）
        fin_latest: dict[str, dict] = {}
        fin = query._q(conn, f"""
            SELECT symbol, report_period, net_profit_yoy, revenue_yoy, gross_margin, roe
            FROM financials
            WHERE symbol IN ({ph}) AND net_profit IS NOT NULL
            ORDER BY symbol, report_period
        """, params)
        if not fin.empty:
            fin = fin.dropna(subset=["report_period"]).sort_values(["symbol", "report_period"])
            for sym, r in fin.groupby("symbol").tail(1).set_index("symbol").iterrows():
                per = str(r["report_period"] or "")
                q = int(per[5:7]) // 3 if len(per) >= 7 else 0
                fin_latest[sym] = {
                    "period": per,
                    "roe": _num(float(r["roe"]) * 4.0 / q) if q and r["roe"] is not None and r["roe"] == r["roe"] else None,
                    "yoy": _num(r["net_profit_yoy"]),
                    "rev_yoy": _num(r["revenue_yoy"]),
                    "gross_margin": _num(r["gross_margin"]),
                }

        # 最近 4 个季末户数：与 metrics 一样，要求季末连续
        drop_by: dict[str, float | None] = {}
        hold = query._q(conn, f"""
            SELECT symbol, end_date, holder_num
            FROM holders
            WHERE symbol IN ({ph}) AND holder_num IS NOT NULL AND holder_num > 0
            ORDER BY symbol, end_date DESC
        """, params)
        if not hold.empty:
            hold = hold[hold["end_date"].str[5:].isin(["03-31", "06-30", "09-30", "12-31"])]
            for sym, g in hold.groupby("symbol"):
                g = g.head(4)
                if len(g) < 3:
                    drop_by[sym] = None
                    continue
                vals = [float(x) for x in g["holder_num"].tolist()]
                ends = g["end_date"].tolist()
                qs = [int(e[:4]) * 4 + int(e[5:7]) // 3 for e in ends]
                drop = None
                if len(g) == 4:
                    if qs[0] == qs[3] + 3:
                        drop = (1 - vals[0] / vals[3]) * 100
                elif qs[0] == qs[1] + 1 and qs[1] == qs[2] + 1:
                    drop = (1 - vals[0] / vals[2]) * 100
                drop_by[sym] = _num(drop)

        pcts = query.pct_maps(conn)
        pe_pct_map = pcts.get("pe_ttm", {})
        pb_pct_map = pcts.get("pb", {})
        dv_pct_map = pcts.get("dv_ttm", {})

        for sym in missing:
            row = base.loc[sym] if sym in base.index else None
            fin_row = fin_latest.get(sym, {})
            fields = {
                "industry": str(row["industry"]) if row is not None else "",
                "mktcap": _num(row["mktcap"], 1) if row is not None else None,
                "pe": _num(row["pe"]) if row is not None else None,
                "pe_pct": _num(pe_pct_map.get(sym), 1),
                "pb": _num(row["pb"]) if row is not None else None,
                "pb_pct": _num(pb_pct_map.get(sym), 1),
                "dv": _num(row["dv"]) if row is not None else None,
                "dv_pct": _num(dv_pct_map.get(sym), 1),
                "roe": fin_row.get("roe"),
                "yoy": fin_row.get("yoy"),
                "rev_yoy": fin_row.get("rev_yoy"),
                "gross_margin": fin_row.get("gross_margin"),
                "drop_pct": drop_by.get(sym),
                "period": fin_row.get("period") or "",
            }
            _FAV_FIELDS_CACHE[sym] = (now, dict(fields))
            out[sym] = fields
    return out


def enrich_favorites(conn, user_id: int) -> list[dict]:
    """收藏列表补上估值 / 财务指标，供收藏页展示。"""
    favs = users.list_favorites(conn, user_id)
    if not favs:
        return favs
    fields = _favorite_stock_fields(conn, [f["symbol"] for f in favs])
    for f in favs:
        f.update(fields.get(f["symbol"], {}))
    return favs


def _py_match(kw: str, limit: int = 300) -> list[dict]:
    """按「代码 / 名称 / 全拼 / 首字母」匹配股票，返回排好序的结果。"""
    q = (kw or "").strip()
    if not q:
        return []
    ql = q.lower()
    conn = db.connect()
    try:
        rows = conn.execute("""
            SELECT s.symbol, s.name, s.board, COALESCE(p.py, ''), COALESCE(p.init, '')
            FROM securities s LEFT JOIN stock_pinyin p ON p.symbol = s.symbol
            WHERE s.symbol LIKE ? OR s.name LIKE ? OR p.py LIKE ? OR p.init LIKE ?
            LIMIT ?
        """, (q + "%", "%" + q + "%", "%" + ql + "%", "%" + ql + "%", limit)).fetchall()
    finally:
        conn.close()

    def score(sym: str, name: str, py: str, init: str) -> int:
        if sym == q:
            return 0
        if sym.startswith(q):
            return 1
        if name.startswith(q):
            return 2
        if any(v.startswith(ql) for v in init.split("|") if v):
            return 3
        if any(v.startswith(ql) for v in py.split("|") if v):
            return 4
        if ql and ql in name.lower():
            return 5
        return 6

    out = [{"symbol": sym, "name": name, "board": board or "",
            "score": score(sym, name, py, init)}
           for sym, name, board, py, init in rows]
    out.sort(key=lambda r: (r["score"], len(r["name"]), r["symbol"]))
    return out


def suggest(kw: str, limit: int = 12) -> dict:
    """输入补全：返回最相关的 limit 只股票。"""
    hits = _py_match(kw)[:limit]
    return {"q": kw, "items": hits}


def list_industries() -> list[dict]:
    """行业列表（按成分股数量降序），供筛选下拉。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT v.industry, COUNT(1) n FROM valuation v "
            "WHERE v.industry IS NOT NULL AND v.industry <> '' "
            "GROUP BY v.industry ORDER BY n DESC").fetchall()
    finally:
        conn.close()
    return [{"k": name, "label": name, "n": n} for name, n in rows]


def market_dates(conn, n: int = 2) -> list[str]:
    """最近 n 个「覆盖足够多股票」的交易日。

    盘中若只有少数股票被单独同步，最新日期可能只有几只；不能拿这个日期
    做全市场 INNER JOIN，否则个股列表会只剩这几只。这里跳过覆盖率过低
    的日期，回退到上一个完整交易日。
    """
    recent = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT 16")]
    if not recent:
        return []
    active = conn.execute(
        "SELECT COUNT(1) FROM securities WHERE COALESCE(status, 'L') = 'L'").fetchone()[0] or 1
    threshold = max(1000, int(active * 0.5))
    good: list[str] = []
    for d in recent:
        cnt = conn.execute("SELECT COUNT(1) FROM bars WHERE date = ?", (d,)).fetchone()[0]
        if cnt >= threshold:
            good.append(d)
            if len(good) >= n:
                return good
    return good if len(good) >= n else recent[:n]


def stock_list(industry: str = "", sort: str = "pct", direction: str = "desc",
               limit: int = 50, offset: int = 0) -> dict:
    """全部个股快照（最新完整交易日收盘）：按涨幅/成交量/换手率/市值等排行。"""
    t0 = time.time()
    conn = db.connect()
    try:
        dates = market_dates(conn, 2)
        if len(dates) < 2:
            return {"rows": [], "count": 0, "offset": max(0, int(offset)),
                    "date": None, "elapsed": round(time.time() - t0, 2)}
        latest, prev = dates[0], dates[1]
        df = query._q(conn, """
            SELECT s.symbol, s.name, COALESCE(v.industry, '') AS industry,
                   s.mktcap, s.float_mktcap,
                   v.pe_ttm AS pe, v.pb, v.dv_ttm AS dv,
                   b.close, b.volume, p.close AS prev_close
            FROM securities s
            JOIN bars b ON b.symbol = s.symbol AND b.date = ?
            LEFT JOIN bars p ON p.symbol = s.symbol AND p.date = ?
            LEFT JOIN valuation v ON v.symbol = s.symbol
        """, (latest, prev))
    finally:
        conn.close()

    if df.empty:
        return {"rows": [], "count": 0, "offset": max(0, int(offset)),
                "date": latest, "elapsed": round(time.time() - t0, 2)}

    df["pct"] = ((df["close"] / df["prev_close"] - 1) * 100).where(df["prev_close"] > 0)
    share_base = (df["float_mktcap"] * 1e8 / df["close"]).where(df["close"] > 0)
    df["turnover"] = ((df["volume"] * 100 / share_base) * 100).where(share_base > 0)
    if industry:
        df = df[df["industry"] == industry]

    sort_map = {
        "symbol": "symbol", "name": "name", "industry": "industry",
        "close": "close", "pct": "pct", "volume": "volume",
        "turnover": "turnover", "mktcap": "mktcap",
        "float_mktcap": "float_mktcap", "pe": "pe", "pb": "pb", "dv": "dv",
    }
    key = sort_map.get(sort, "pct")
    df = df.sort_values(key, ascending=direction.lower() == "asc",
                        na_position="last", kind="mergesort")

    total = int(len(df))
    offset = max(0, int(offset))
    limit = max(0, int(limit))
    page = df.iloc[offset: offset + limit] if limit else df.iloc[offset:]
    cols = ["symbol", "name", "industry", "close", "pct", "volume",
            "turnover", "mktcap", "float_mktcap", "pe", "pb", "dv"]
    page = page[cols].copy()
    page["volume"] = page["volume"] / 1e4          # 手 → 万手
    for col, dec in (("close", 2), ("pct", 2), ("volume", 2), ("turnover", 2),
                     ("mktcap", 1), ("float_mktcap", 1), ("pe", 2),
                     ("pb", 2), ("dv", 2)):
        page[col] = pd.to_numeric(page[col], errors="coerce").round(dec)
    rows = json.loads(page.to_json(orient="records", force_ascii=False))
    return {"rows": rows, "count": total, "offset": offset,
            "date": latest, "elapsed": round(time.time() - t0, 2)}



def _has_table(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def datasets() -> dict:
    conn = db.connect()
    q = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731
    meta = lambda k: db.get_meta(conn, k)  # noqa: E731
    init_at = meta("init_at")

    def item(key, name, coverage, latest, synced, note="", hint=""):
        return {
            "key": key, "name": name, "coverage": coverage, "latest": latest,
            "synced_at": synced or init_at, "note": note, "synced_hint": hint,
            "running": any(j["kind"] == key and j["status"] == "running" for j in JOBS.values()),
        }

    done, allc = sync.qfq_progress(conn)
    if done >= allc:
        adj_note = "前复权"
    elif done > 0:
        adj_note = f"前复权 {done}/{allc}（重建中）"
    else:
        adj_note = "不复权"
    out = [
        item("bars", "日线行情", f"{q('SELECT COUNT(DISTINCT symbol) FROM bars')} 只",
             q("SELECT MAX(date) FROM bars"), meta("sync_bars_at"),
             f"{q('SELECT COUNT(1) FROM bars')/1e4:.0f} 万行 · {adj_note}",
             f"整库同步时间 {meta('sync_bars_at') or '—'}（单只/收藏页同步不更新这里）"),
        item("holders", "股东户数", f"{q('SELECT COUNT(DISTINCT symbol) FROM holders')} 只",
             q("SELECT MAX(end_date) FROM holders"), meta("sync_holders_at"),
             f"{q('SELECT COUNT(1) FROM holders')/1e4:.1f} 万条"),
        item("top10", "前十大股东占比", f"{q('SELECT COUNT(DISTINCT symbol) FROM top10_holders')} 只",
             q("SELECT MAX(end_date) FROM top10_holders"), meta("sync_top10_at"),
             f"{q('SELECT COUNT(1) FROM top10_holders')/1e4:.1f} 万条"),
        item("financials", "财务数据", f"{q('SELECT COUNT(DISTINCT symbol) FROM financials')} 只",
             q("SELECT MAX(report_period) FROM financials"), meta("sync_financials_at"),
             f"{q('SELECT COUNT(1) FROM financials')/1e4:.1f} 万条"),
        item("valuation", "估值数据", f"{q('SELECT COUNT(1) FROM valuation')} 只",
             q("SELECT MAX(trade_date) FROM valuation"), meta("sync_valuation_at"),
             f"PE/PB/股息率 · {q('SELECT COUNT(1) FROM valuation WHERE dv_ttm IS NOT NULL')} 只有分红"),
        item("mktcap", "总市值", f"{q('SELECT COUNT(1) FROM securities WHERE mktcap IS NOT NULL')} 只",
             q("SELECT MAX(mktcap_date) FROM securities"), meta("sync_mktcap_at"), "快照"),
        item("list_date", "上市日期", f"{q('SELECT COUNT(1) FROM securities WHERE list_date IS NOT NULL')} 只",
             "-", meta("sync_list_date_at"), ""),
        item("patterns", "形态信号",
             (lambda st: f"{st['cached']}/{st['total']} 组合")(patterns.variant_status(conn)),
             patterns.variant_status(conn)["date"],
             meta("sync_patterns_at"),
             (f"做小做底 {q('SELECT COALESCE(SUM(base_ok),0) FROM pattern_signal') if _has_table(conn, 'pattern_signal') else 0}"
              f" · 按需计算")),
    ]
    size_mb = round(os.path.getsize(db.DB_PATH) / 1e6, 0) if os.path.exists(db.DB_PATH) else 0
    return {"datasets": out, "db_mb": size_mb, "now": time.strftime("%Y-%m-%dT%H:%M:%S")}



def _f(v, default):
    try:
        x = float(v)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def parse_params(qs: dict) -> dict:
    """界面走条件行：conds=[{k,min,max}]；兼容旧参数（老链接/命令行）。"""
    raw = qs.get("conds", [""])[0]
    conds = []
    if raw:
        try:
            conds = json.loads(raw)
        except json.JSONDecodeError:
            conds = []
    if not conds:
        # 旧参数兜底
        def one(k, d=""):
            return qs.get(k, [d])[0]

        def add(k, lo=None, hi=None):
            c = {"k": k}
            if lo is not None:
                c["min"] = lo
            if hi is not None:
                c["max"] = hi
            if len(c) > 1:
                conds.append(c)

        add("drop_pct", lo=_f(one("min_drop", ""), None))
        add("profit_yi", lo=(_f(one("min_profit_yi", ""), 0.0) or None))
        add("roe", lo=_f(one("min_roe", ""), None) or None)
        add("yoy", lo=_f(one("min_yoy", ""), None))
        add("mktcap", lo=_f(one("mktcap_lo", ""), None), hi=_f(one("mktcap_hi", ""), None))
        add("years", lo=_f(one("min_years", ""), None) or None)
        add("pe", hi=_f(one("max_pe", ""), None))
        add("dv", lo=_f(one("min_dv", ""), None))
        add("pe_pct", hi=_f(one("max_pe_pct", ""), None))
        add("pb_pct", hi=_f(one("max_pb_pct", ""), None))
        add("dv_pct", lo=_f(one("min_dv_pct", ""), None))
        if one("exclude_st", "1") in ("1", "true", "on"):
            conds.append({"k": "exclude_st", "on": True})

    return {"conds": conds, "keyword": qs.get("keyword", [""])[0].strip()}


def _sort_rows(rows: list[dict], key: str, asc: bool, cols: dict | None) -> list[dict]:
    """按表格列排序（和前端 effVal 口径一致：yoy 可能切到 np_yoy / np_qoq）。"""
    alt = (cols or {}).get(key) or key

    def kf(r: dict):
        v = r.get(alt)
        if isinstance(v, bool):
            v = int(v)
        if isinstance(v, (int, float)):
            return (0, float(v), "")
        if v is None:
            return (0, -1e9, "")      # 缺失值按前端习惯当很小
        return (1, 0.0, str(v))

    out = sorted(rows, key=kf)
    return out if asc else list(reversed(out))


def _paginate(payload: dict, limit: int, offset: int) -> dict:
    """只返回一页；count 仍是命中总数。limit<=0 表示不分页。"""
    rows = payload.get("rows") or []
    total = len(rows)
    if limit > 0:
        page = rows[offset:offset + limit]
    else:
        page = rows[offset:] if offset else rows
    out = dict(payload)
    out["rows"] = page
    out["count"] = total
    out["offset"] = offset
    out["limit"] = limit
    return out


def favorites_list(user_id: int, sort: str = "created", direction: str = "desc") -> dict:
    """我的收藏（列表视图）：和「个股列表」同样的字段，另外带分组/笔记数。"""
    t0 = time.time()
    conn = db.connect()
    try:
        dates = market_dates(conn, 2)
        if len(dates) < 2:
            return {"rows": [], "count": 0, "date": None, "elapsed": 0}
        latest, prev = dates[0], dates[1]
        df = query._q(conn, """
            SELECT f.symbol, s.name, COALESCE(v.industry, '') AS industry,
                   s.mktcap, s.float_mktcap,
                   v.pe_ttm AS pe, v.pb, v.dv_ttm AS dv,
                   b.close, b.volume, p.close AS prev_close,
                   f.group_id, g.name AS group_name, f.created_at,
                   (SELECT COUNT(1) FROM fav_notes n
                     WHERE n.user_id = f.user_id AND n.symbol = f.symbol) AS notes
            FROM favorites f
            JOIN securities s ON s.symbol = f.symbol
            LEFT JOIN bars b ON b.symbol = f.symbol AND b.date = ?
            LEFT JOIN bars p ON p.symbol = f.symbol AND p.date = ?
            LEFT JOIN valuation v ON v.symbol = f.symbol
            LEFT JOIN fav_groups g ON g.id = f.group_id
            WHERE f.user_id = ?
        """, (latest, prev, int(user_id)))
    finally:
        conn.close()

    if df.empty:
        return {"rows": [], "count": 0, "date": latest, "elapsed": round(time.time() - t0, 2)}

    df["pct"] = ((df["close"] / df["prev_close"] - 1) * 100).where(df["prev_close"] > 0)
    share_base = (df["float_mktcap"] * 1e8 / df["close"]).where(df["close"] > 0)
    df["turnover"] = ((df["volume"] * 100 / share_base) * 100).where(share_base > 0)

    sort_map = {"symbol": "symbol", "name": "name", "industry": "industry",
                "close": "close", "pct": "pct", "volume": "volume",
                "turnover": "turnover", "mktcap": "mktcap",
                "float_mktcap": "float_mktcap", "pe": "pe", "pb": "pb",
                "dv": "dv", "created": "created_at", "group_name": "group_name",
                "notes": "notes"}
    key = sort_map.get(sort, "created_at")
    df = df.sort_values(key, ascending=(direction or "desc").lower() == "asc",
                        na_position="last", kind="mergesort")

    df["volume"] = df["volume"] / 1e4            # 手 → 万手
    cols = ["symbol", "name", "industry", "close", "pct", "volume", "turnover",
            "mktcap", "float_mktcap", "pe", "pb", "dv",
            "group_name", "notes", "created_at"]
    page = df[cols].copy()
    for col, dec in (("close", 2), ("pct", 2), ("volume", 2), ("turnover", 2),
                     ("mktcap", 1), ("float_mktcap", 1), ("pe", 2), ("pb", 2), ("dv", 2)):
        page[col] = pd.to_numeric(page[col], errors="coerce").round(dec)
    page["group_name"] = page["group_name"].fillna("未分组")
    page["notes"] = pd.to_numeric(page["notes"], errors="coerce").fillna(0).astype(int)
    rows = json.loads(page.to_json(orient="records", force_ascii=False))
    return {"rows": rows, "count": len(rows), "date": latest,
            "elapsed": round(time.time() - t0, 2)}


def run_screen(p: dict) -> dict:
    key = json.dumps(p, sort_keys=True, default=str)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    if hit:
        _CACHE.pop(key, None)

    t0 = time.time()
    conn = db.connect()
    conds = metrics.normalize(p["conds"])
    # 形态信号按需计算：只算这次查询用到的组合，同一天算过就复用缓存
    try:
        built = patterns.ensure_variants(conn, conds)
        if built:
            log(f"形态按需计算：{', '.join(built)}")
    except Exception as e:  # noqa: BLE001
        log(f"形态计算失败：{e}")

    df = metrics.metrics(conn, force=bool(conds and any(
        c["k"] in ("flag_ok", "flag_brk", "base_ok", "box_ok",
                   "w_brk", "w_pend", "volup_ok") for c in conds)))
    out_df = metrics.apply(df, conds, conn)

    if p["keyword"]:
        kw = p["keyword"].upper()
        mask = (out_df["name"].fillna("").str.upper().str.contains(kw, regex=False)
                | out_df.index.str.contains(kw, regex=False))
        py_hits = [r["symbol"] for r in _py_match(p["keyword"], limit=3000)]
        if py_hits:
            mask = mask | out_df.index.isin(py_hits)
        out_df = out_df[mask]

    def num(v, d=2):
        return round(float(v), d) if v is not None and v == v else None

    # 条件里指定的报告期：表格对应列显示该期数值，避免"筛选用年报、显示用最新"的错位。
    # 同一指标可以按不同报告期/年份出现多次，这里保留全部，展示列取第一条。
    period_list = [(c["k"], c["p"], c.get("y"), c.get("d"),
                    c.get("op") if c["k"] == "industry" else c.get("lp"))
                   for c in conds if c.get("p")]
    period_of = {}
    for k, p2, y2, _d2, _lp2 in period_list:
        period_of.setdefault(k, (p2, y2))
    col_map = {"roe": "roe_annual", "holder_chg": "drop_pct", "holder_num": "holder_latest"}

    rows = []
    for sym, r in out_df.iterrows():
        item = {
            "symbol": sym,
            "name": r.get("name") or "",
            "board": r.get("board") or "",
            "industry": r.get("indu") or "",
            "drop_pct": num(r.get("drop_pct")),
            "profit_yi": num(r.get("profit_yi"), 3),
            "mktcap": num(r.get("mktcap"), 1),
            "float_mktcap": num(r.get("float_mktcap"), 1),
            "roe_annual": num(r.get("roe")),
            "yoy": num(r.get("yoy")),
            "np_yoy": num(r.get("np_yoy")),
            "np_qoq": num(r.get("np_qoq")),
            "rev_yoy": num(r.get("rev_yoy")),
            "gross_margin": num(r.get("gross_margin")),
            "pe": num(r.get("pe")),
            "pe_pct": num(r.get("pe_pct"), 1),
            "pb": num(r.get("pb")),
            "pb_pct": num(r.get("pb_pct"), 1),
            "dv": num(r.get("dv")),
            "dv_pct": num(r.get("dv_pct"), 1),
            "period": str(r.get("period") or ""),
        }
        for k, (p2, y2) in period_of.items():
            src = metrics._col(k, p2, y2)
            if src in out_df.columns:
                item[col_map.get(k, k)] = num(out_df.at[sym, src])
        rows.append(item)

    out = {"count": len(rows), "elapsed": round(time.time() - t0, 2), "rows": rows,
           "periods": period_info(period_list), "cols": column_switch(conds)}
    now = time.time()
    _CACHE[key] = (now, out)
    for k in [k for k, v in _CACHE.items() if now - v[0] > _CACHE_TTL]:
        _CACHE.pop(k, None)
    while len(_CACHE) > _CACHE_MAX:
        oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
        _CACHE.pop(oldest, None)
    return out


def column_switch(conds: list[dict]) -> dict:
    """某个条件用了不同口径时，告诉界面哪一列要换数据。"""
    keys = {c["k"] for c in conds}
    cols = {}
    if "np_yoy" in keys:
        cols["yoy"] = "np_yoy"
    elif "np_qoq" in keys:
        cols["yoy"] = "np_qoq"
    if "holder_chg" in keys:
        cols["drop_pct"] = "drop_pct"
    if "top10_ratio" in keys:
        cols["drop_pct"] = "top10_ratio"
    return cols


def period_info(period_list: list[tuple]) -> list[dict]:
    """把「字段=报告期(年份)」翻译成人话，供界面标注口径。"""
    out, seen = [], set()
    for k, p, y, d, lp in period_list:
        if (k, p, y, d, lp) in seen:
            continue
        seen.add((k, p, y, d, lp))
        spec = metrics.FIELD_MAP.get(k, {})
        out.append({"label": spec.get("label", k),
                    "ptext": metrics.period_label(k, p, y, d, lp)})
    return out


def get_kline(code: str, n: int = 150, before: str | None = None) -> dict:
    """取最近 n 根日线；before 指定时只取该日期之前的（用于向左翻页加载更早数据）。"""
    conn = db.connect()
    info = conn.execute("SELECT name, board, mktcap, list_date, float_mktcap "
                        "FROM securities WHERE symbol=?", (code,)).fetchone()
    sql = ("SELECT date, open, high, low, close, volume, pe_ttm, pb, dv_ttm FROM v_daily "
           "WHERE symbol=?")
    params: list = [code]
    if before:
        sql += " AND date < ?"
        params.append(before)
    sql += " ORDER BY date DESC LIMIT ?"
    params.append(n + 1)          # 多取一根用来判断还有没有更早的数据
    fetched = conn.execute(sql, params).fetchall()
    more = len(fetched) > n
    rows = fetched[:n]
    rows.reverse()
    # 估值（valuation_daily）通常比日线晚一天：最新那根的 PE/PB/股息是 NULL。
    # 按时间前向填充，避免弹窗顶部显示「PE —」；同时记录估值实际到哪一天。
    val_date = None
    filled = []
    prev_val = [None, None, None]
    for r in rows:
        cur = list(r)
        if cur[6] is not None:
            val_date = cur[0]
        for j, idx in enumerate((6, 7, 8)):
            if cur[idx] is not None:
                prev_val[j] = cur[idx]
            else:
                cur[idx] = prev_val[j]
        filled.append(cur)
    rows = filled
    last = rows[-1] if rows else (None,) * 9
    return {
        "symbol": code,
        "name": info[0] if info else "",
        "board": info[1] if info else "",
        "mktcap": round(info[2], 1) if info and info[2] else None,
        "float_mktcap": round(info[4], 1) if info and len(info) > 4 and info[4] else None,
        "list_date": info[3] if info else "",
        "pe": round(last[6], 2) if len(last) > 6 and last[6] else None,
        "pb": round(last[7], 2) if len(last) > 7 and last[7] else None,
        "dv": round(last[8], 2) if len(last) > 8 and last[8] else None,
        "val_date": val_date,
        "bars": [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]] for r in rows],
        "more": more,          # 是否还有更早的日线
        "pattern": _pattern_of(conn, code),
    }


def _pattern_of(conn, code: str) -> dict | None:
    """形态标记：旗杆日 / 旗面天数 / 做小做底，供 K 线画阴影用。"""
    try:
        row = conn.execute(
            "SELECT base_ok, flag_ok, brk_auto, flag_pole_date, flag_days, flag_pole_chg, "
            "base_amp, base_pos, box_ok, box_high, box_low, box_days, box_pos, "
            "base_ignite_date, base_ignite_chg, base_ignite_vol "
            "FROM pattern_signal WHERE symbol=?", (code,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    box = _box_mark(conn, code)
    base = _base_mark(conn, code) or {}
    w = _w_mark(conn, code)
    if not row:
        if box or base.get("base_ok") or w:
            return {"box": box, "base_ok": bool(base.get("base_ok")),
                    "ignite": base.get("ignite"), "w": w}
        return None
    (base_ok, flag_ok, flag_brk, pole, days, chg, bamp, bpos,
     box_ok, box_high, box_low, box_days, box_pos,
     ign_date, ign_chg, ign_vol) = row
    if not (flag_ok or flag_brk) and not box and not base.get("base_ok") and not w:
        return None
    return {"base_ok": bool(base.get("base_ok", base_ok)), "w": w,
            "flag_ok": bool(flag_ok), "flag_brk": bool(flag_brk),
            "pole_date": pole, "flag_days": days, "pole_chg": chg,
            "base_amp": base.get("amp", bamp), "base_pos": base.get("pos", bpos),
            "box": box, "ignite": base.get("ignite")}


def _w_mark(conn, code: str) -> dict | None:
    """W 底：先看突破确认（近 10 日），没有就看右底埋伏。"""
    try:
        row = patterns._bars_one(conn, code)
        if not row:
            return None
        o, h, l, c, v, dt = row
        r = patterns.w_signal(code, o, h, l, c, v, "brk", 10, 0.03)
        kind = "brk"
        if not r:
            r = patterns.w_signal(code, o, h, l, c, v, "pend", 10, 0.0)
            kind = "pend"
        if not r:
            return None
        out = {"kind": kind, "neck": r["neck"], "l1": r["l1"], "l2": r["l2"],
               "l1_date": str(dt[r["i1"]]), "l2_date": str(dt[r["i2"]])}
        if r.get("brk_idx") is not None:
            out["brk_date"] = str(dt[r["brk_idx"]])
        return out
    except Exception:  # noqa: BLE001
        return None


def _base_mark(conn, code: str) -> dict | None:
    """做小做底 + 近 20 日标准启动：现算，口径固定，图上一眼能对。"""
    try:
        row = patterns._bars_one(conn, code)
        if not row:
            return None
        o, h, l, c, v, dt = row
        hl = conn.execute("SELECT MIN(low) FROM bars WHERE symbol=?", (code,)).fetchone()[0]
        b = patterns.base_signal(c, h, l, v, hl)
        if not b["base_ok"]:
            return {"base_ok": False}
        out = {"base_ok": True, "amp": b["base_amp"], "pos": b["base_pos"]}
        r = patterns.base_variant(code, c, o, h, l, v, "20", "std", hl)
        if r:
            out["ignite"] = {"date": str(dt[r["ign_idx"]]), "chg": r["ign_chg"],
                             "vol": r["ign_vol"]}
        return out
    except Exception:  # noqa: BLE001
        return None


def _box_mark(conn, code: str) -> dict | None:
    """箱体区间：用 90 天窗口现算（日线已在内存，几乎零成本）。"""
    try:
        row = patterns._bars_one(conn, code)
        if not row:
            return None
        _o, h, l, c, _v, _dt = row
        r = patterns.box_signal(code, c, h, l, 90, "any", 3)
        if not r:
            return None
        return {"high": r["box_high"], "low": r["box_low"],
                "days": r["box_days"], "pos": r["box_pos"]}
    except Exception:  # noqa: BLE001
        return None


def log(msg: str) -> None:
    """命令窗口的状态日志，带时间戳。"""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "AScreener/1.1"

    def log_message(self, fmt, *args):  # 关掉默认的 stderr 访问日志
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> bool:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in getattr(self, "_extra_headers", []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self._broken = True   # 浏览器取消请求（切换筛选/关页面），正常现象
            return False
        except OSError:
            self._broken = True
            return False

    def _json(self, obj, code: int = 200) -> bool:
        return self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8")

    def _redirect(self, url: str) -> bool:
        try:
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return True
        except OSError:
            self._broken = True
            return False

    def _file(self, path: str, ctype: str) -> bool:
        # 只允许读 STATIC 目录里的文件：否则 /static/../../data/astock.db
        # 能把整个数据库（含用户密码哈希、会话 token）下载走
        real = os.path.realpath(path)
        base = os.path.realpath(STATIC)
        if not (real == base or real.startswith(base + os.sep)):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        if not os.path.isfile(real):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        with open(path, "rb") as f:
            return self._send(200, f.read(), ctype)

    def _page(self, name: str) -> bool:
        """渲染页面：注入网站标题 / 备案号 / 统计代码。"""
        path = os.path.join(TEMPLATES, name)
        if not os.path.isfile(path):
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        with open(path, encoding="utf-8") as f:
            page = f.read()
        try:
            conn = db.connect()
            try:
                cfg = siteconfig.get(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001
            cfg = dict(siteconfig.DEFAULTS)
        title = escape(cfg.get("site_title") or "A 股选股系统")
        icp = (cfg.get("icp") or "").strip()
        icp_html = ""
        if icp:
            icp_html = (' · <a class="icp" href="https://beian.miit.gov.cn/" '
                        'target="_blank" rel="noopener">' + escape(icp) + "</a>")
        page = page.replace("{{SITE_TITLE}}", title).replace("{{ICP}}", icp_html)
        analytics = (cfg.get("analytics") or "").strip()
        if analytics:
            page = page.replace("</body>", analytics + "\n</body>", 1)
        return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _finish_log(self, status: str) -> None:
        cost = (time.perf_counter() - self._t0) * 1000
        path = self.path.split("?")[0]
        if path.startswith("/static/") or path == "/api/sync/status":
            return          # 静态资源与同步轮询不刷屏
        if getattr(self, "_broken", False):
            status = "断开"
        log(f"{self.command:<4} {path:<28} {status:<8} {cost:6.0f}ms")

    # ---------- 登录态 ----------
    def _cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _user(self) -> dict | None:
        if not hasattr(self, "_u"):
            conn = db.connect()
            try:
                self._u = users.user_by_token(conn, self._cookie("sid"))
            finally:
                conn.close()
        return self._u

    def _set_cookie(self, token: str | None) -> None:
        if not hasattr(self, "_extra_headers"):
            self._extra_headers = []
        if token:
            self._extra_headers.append(
                ("Set-Cookie", f"sid={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={users.SESSION_DAYS*86400}")
            )
        else:
            self._extra_headers.append(("Set-Cookie", "sid=; Path=/; HttpOnly; Max-Age=0"))

    def _handle(self, fn) -> None:
        self._t0 = time.perf_counter()
        self._extra_headers = []
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log(f"{self.command:<4} {self.path.split('?')[0]:<28} 断开 客户端提前关闭连接")
        except Exception as e:  # noqa: BLE001
            log(f"!! {self.command} {self.path} -> {type(e).__name__}: {e}")
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_GET(self):  # noqa: N802
        self._handle(self._get)

    def do_POST(self):  # noqa: N802
        self._handle(self._post)

    def _get(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        status = "200"
        if path in ("/", "/home", "/index.html"):
            self._page("index_home.html")
        elif path in ("/screen", "/screen/"):
            self._page("index.html")
        elif path in ("/fav", "/fav/"):
            self._page("fav.html")
        elif path in ("/account", "/account/"):
            if not self._user():
                self._redirect("/?login=1")
                status = "302/未登录"
            else:
                self._page("account.html")
        elif path == "/api/screen":
            d = run_screen(parse_params(qs))
            limit = max(0, int(_f(qs.get("limit", ["0"])[0], 0)))
            offset = max(0, int(_f(qs.get("offset", ["0"])[0], 0)))
            sort = (qs.get("sort", [""])[0] or "").strip()
            asc = (qs.get("dir", ["desc"])[0] or "desc").lower() == "asc"
            if sort or limit or offset:
                d = dict(d)                       # 别改缓存里的那份
            if sort:
                d["rows"] = _sort_rows(d.get("rows") or [], sort, asc, d.get("cols"))
            if limit or offset:
                d = _paginate(d, limit, offset)
            self._json(d)
            status = f"200/{d['count']}"
        elif path.startswith("/api/kline/"):
            code = unquote(path.rsplit("/", 1)[-1])
            n = int(_f(qs.get("n", ["150"])[0], 150))
            before = qs.get("before", [""])[0] or None
            self._json(get_kline(code, max(30, min(2000, n)), before))
        elif path == "/api/index/list":
            self._json({"indexes": src.INDEX_LIST})
            status = "200"
        elif path == "/api/index/minute":
            code = qs.get("code", ["000001"])[0]
            days = int(_f(qs.get("days", ["1"])[0], 1))
            secid = next((x["secid"] for x in src.INDEX_LIST if x["code"] == code),
                         "1.000001")
            try:
                self._json(src.fetch_index_minute(secid, max(1, min(5, days))))
            except Exception as e:  # noqa: BLE001
                log(f"分时获取失败：{e} | {traceback.format_exc().splitlines()[-3].strip()}")
                self._json({"error": f"分时数据获取失败：{e}"}, 502)
            status = "200"
        elif path == "/api/index/quotes":
            try:
                self._json({"quotes": src.fetch_index_quotes(),
                            "ths": {x["code"]: src.ths_url(x["code"])
                                    for x in src.INDEX_LIST}})
            except Exception as e:  # noqa: BLE001
                log(f"指数行情失败：{e} | {traceback.format_exc().splitlines()[-3].strip()}")
                self._json({"error": f"指数行情获取失败：{e}"}, 502)
            status = "200"
        elif path == "/api/index/kline":
            code = qs.get("code", ["000001"])[0]
            period = qs.get("period", ["day"])[0]
            n = int(_f(qs.get("n", ["250"])[0], 250))
            secid = next((x["secid"] for x in src.INDEX_LIST if x["code"] == code),
                         "1.000001")
            try:
                self._json(src.fetch_index_kline(secid, period, max(60, min(600, n))))
            except Exception as e:  # noqa: BLE001
                log(f"K线获取失败：{e} | {traceback.format_exc().splitlines()[-3].strip()}")
                self._json({"error": f"K线获取失败：{e}"}, 502)
            status = "200"
        elif path == "/api/favorites_list":
            me = self._user()
            if not me:
                self._json({"error": "未登录", "rows": [], "count": 0}, 401)
            else:
                self._json(favorites_list(
                    me["id"],
                    qs.get("sort", ["created"])[0] or "created",
                    qs.get("dir", ["desc"])[0] or "desc"))
            status = "200"
        elif path == "/api/stock_list":
            self._json(stock_list(
                industry=qs.get("industry", [""])[0],
                sort=qs.get("sort", ["pct"])[0],
                direction=qs.get("dir", ["desc"])[0],
                limit=int(_f(qs.get("limit", ["50"])[0], 50)),
                offset=int(_f(qs.get("offset", ["0"])[0], 0))))
            status = "200"
        elif path == "/api/industries":
            self._json({"industries": list_industries()})
            status = "200"
        elif path == "/api/suggest":
            self._json(suggest(qs.get("q", [""])[0]))
            status = "200"
        elif path == "/api/datasets":
            self._json(datasets())
            status = "200"
        elif path == "/api/fields":
            if not metrics.YEARS:          # 首次访问时建一次宽表，拿到可用年份
                conn = db.connect()
                try:
                    metrics.metrics(conn)
                finally:
                    conn.close()
            if not metrics.TOP10_YEARS:    # 同步完前十大股东后无需强建宽表，直接补年份
                conn = db.connect()
                try:
                    years = [int(r[0]) for r in conn.execute(
                        "SELECT DISTINCT substr(end_date, 1, 4) FROM top10_holders "
                        "WHERE end_date IS NOT NULL")]
                    metrics.TOP10_YEARS = sorted(years, reverse=True)
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    conn.close()
            self._json({"fields": metrics.FIELDS, "periods": metrics.PERIODS,
                        "years": metrics.YEARS, "holderYears": metrics.HOLDER_YEARS,
                        "top10Years": metrics.TOP10_YEARS})
            status = "200"
        elif path == "/api/screens":
            me = self._user()
            conn = db.connect()
            try:
                self._json({"screens": list_screens(conn, me["id"] if me else None)})
            finally:
                conn.close()
            status = "200"
        elif path == "/api/me":
            me = self._user()
            if not me:
                self._json({"user": None})
            else:
                conn = db.connect()
                try:
                    self._json({
                        "user": me,
                        "favorites": users.list_favorites(conn, me["id"]),
                        "symbols": users.favorite_symbols(conn, me["id"]),
                        "groups": users.list_groups(conn, me["id"]),
                    })
                finally:
                    conn.close()
            status = "200"
        elif path == "/api/settings":
            me = self._user()
            conn = db.connect()
            try:
                cfg = siteconfig.get(conn)
                admin = bool(me and me.get("role") == "admin")
                data = siteconfig.as_admin(conn, cfg) if admin else siteconfig.as_public(cfg)
                data["is_admin"] = admin
                self._json(data)
            finally:
                conn.close()
            status = "200"
        elif path == "/api/users":
            me = self._user()
            if not me:
                self._json({"error": "未登录"}, 401)
                status = "401"
            elif me.get("role") != "admin":
                self._json({"error": "需要管理员权限"}, 403)
                status = "403"
            else:
                conn = db.connect()
                try:
                    self._json({"users": users.list_users(conn)})
                finally:
                    conn.close()
                status = "200"
        elif path == "/api/notes":
            me = self._user()
            sym = qs.get("symbol", [""])[0]
            if not me:
                self._json({"error": "未登录"}, 401)
            else:
                conn = db.connect()
                try:
                    self._json({"notes": users.list_notes(conn, me["id"], sym)})
                finally:
                    conn.close()
        elif path == "/api/favs":
            me = self._user()
            if not me:
                self._json({"error": "未登录"}, 401)
                status = "401"
            else:
                conn = db.connect()
                try:
                    self._json({"favorites": enrich_favorites(conn, me["id"])})
                finally:
                    conn.close()
        elif path == "/api/sync/status":
            jid = qs.get("id", [""])[0]
            job = JOBS.get(jid) or JOBS.get(_running_job() or "")
            self._json(job or {"status": "idle"})
            status = f"200/{job['status']}" if job else "200/idle"
        elif path.startswith("/static/"):
            name = path[len("/static/"):]
            ext = os.path.splitext(name)[1]
            ctype = {".js": "application/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8",
                     ".svg": "image/svg+xml",
                     ".ico": "image/x-icon",
                     ".html": "text/html; charset=utf-8"}.get(ext, "application/octet-stream")
            self._file(os.path.join(STATIC, name), ctype)
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            status = "404"
        self._finish_log(status)

    def _post(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except Exception:  # noqa: BLE001
            body = {}
        if u.path == "/api/bars/sync":
            symbol = str(body.get("symbol") or "").strip()
            if not symbol:
                self._json({"error": "缺少 symbol"}, 400)
                self._finish_log("400/缺参数")
                return
            if symbol.isdigit():
                symbol = symbol.zfill(6)
            lock = _bar_sync_lock(symbol)
            with lock:
                conn = db.connect()
                try:
                    if _bar_is_fresh(conn, symbol):
                        row = conn.execute(
                            "SELECT name FROM securities WHERE symbol=?", (symbol,)).fetchone()
                        latest = conn.execute(
                            "SELECT MAX(date) FROM bars WHERE symbol=?", (symbol,)).fetchone()[0]
                        self._json({
                            "ok": True, "symbol": symbol,
                            "name": row[0] if row else "",
                            "rows": 0, "latest": latest, "skipped": True,
                        })
                        self._finish_log("200/fresh")
                        return
                    r = sync.sync_bars_symbol(conn, symbol)
                except Exception as e:  # noqa: BLE001
                    self._json({"error": f"同步失败：{type(e).__name__}: {e}"}, 500)
                    self._finish_log("500/同步失败")
                    return
                finally:
                    conn.close()
            if r.get("error"):
                self._json(r, 400)
                self._finish_log(f"400/{str(r['error'])[:40]}")
                return
            # 数据变了：清掉筛选缓存、宽表缓存、收藏字段缓存、形态缓存
            _CACHE.clear()
            metrics._CACHE.update(t=0.0, df=None)
            _clear_fav_fields()
            try:
                conn2 = db.connect()
                try:
                    patterns.refresh_symbol(conn2, r["symbol"])   # 只更新这一只
                    patterns.clear_cache(conn2)                   # 形态当天重算
                finally:
                    conn2.close()
            except Exception:  # noqa: BLE001
                pass
            self._json(r)
            status = f"200/{r['symbol']}"
            self._finish_log(status)
            return

        if u.path == "/api/sync":
            me = self._user()
            if not me:
                self._json({"error": "请先登录再同步数据"}, 401)
                self._finish_log("401/未登录")
                return
            kind = qs.get("kind", [""])[0] or body.get("kind", "")
            res = start_sync(kind)
            log(f"同步 {kind}（{me['username']}）")
            self._json(res, 200 if "job" in res else 409)
            self._finish_log(f"200/{kind}")
        elif u.path in ("/api/register", "/api/login", "/api/logout", "/api/fav", "/api/fav/delete",
                        "/api/password", "/api/profile", "/api/note", "/api/note/delete", "/api/group",
                        "/api/screens", "/api/screens/delete", "/api/settings",
                        "/api/users/create", "/api/users/role",
                        "/api/users/reset_password", "/api/users/delete"):
            self._auth_route(u.path, body)
            self._finish_log("200")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            self._finish_log("404")

    def _auth_route(self, path: str, body: dict) -> None:
        conn = db.connect()
        try:
            if path == "/api/register":
                cfg = siteconfig.get(conn)
                if cfg["allow_register"] != "1":
                    self._json({"error": "管理员已关闭注册"}, 403)
                    return
                r = users.create_user(conn, body.get("username", ""), body.get("password", ""))
                if "error" in r:
                    self._json(r, 400)
                else:
                    self._set_cookie(users.create_session(conn, r["id"]))
                    log(f"新用户注册：{r['username']}")
                    self._json({"user": {"id": r["id"], "username": r["username"],
                                         "nickname": r.get("nickname", r["username"]),
                                         "role": r.get("role", "user")}})

            elif path == "/api/login":
                u = users.authenticate(conn, body.get("username", ""), body.get("password", ""))
                if not u:
                    self._json({"error": "用户名或密码错误"}, 401)
                else:
                    self._set_cookie(users.create_session(conn, u["id"]))
                    log(f"登录：{u['username']}")
                    self._json({"user": u})

            elif path == "/api/logout":
                users.drop_session(conn, self._cookie("sid"))
                self._set_cookie(None)
                self._json({"ok": True})

            elif path == "/api/password":
                me = self._user()
                if not me:
                    self._json({"error": "未登录"}, 401)
                    return
                r = users.change_password(conn, me["id"], body.get("old", ""), body.get("new", ""))
                self._json(r, 400 if "error" in r else 200)

            elif path == "/api/profile":
                me = self._user()
                if not me:
                    self._json({"error": "未登录"}, 401)
                    return
                r = users.set_nickname(conn, me["id"], body.get("nickname", ""))
                self._json(r, 400 if "error" in r else 200)

            elif path == "/api/settings":
                me = self._user()
                if not me:
                    self._json({"error": "未登录"}, 401)
                    return
                if me.get("role") != "admin":
                    self._json({"error": "需要管理员权限"}, 403)
                    return
                r = siteconfig.save(conn, body)
                if "error" not in r:
                    log(f"管理员 {me['username']} 更新系统配置")
                self._json(r, 400 if "error" in r else 200)

            elif path in ("/api/users/create", "/api/users/role",
                          "/api/users/reset_password", "/api/users/delete"):
                me = self._user()
                if not me:
                    self._json({"error": "未登录"}, 401)
                    return
                if me.get("role") != "admin":
                    self._json({"error": "需要管理员权限"}, 403)
                    return
                if path == "/api/users/create":
                    r = users.create_user(conn, body.get("username", ""),
                                          body.get("password", ""), body.get("role", "user"))
                    if "error" not in r:
                        log(f"管理员 {me['username']} 新建用户：{r['username']}（{r['role']}）")
                elif path == "/api/users/role":
                    r = users.set_role(conn, int(body.get("id") or 0),
                                       body.get("role", ""), me["id"])
                elif path == "/api/users/reset_password":
                    r = users.reset_password(conn, int(body.get("id") or 0),
                                             body.get("password", ""), me["id"])
                else:
                    r = users.delete_user(conn, int(body.get("id") or 0), me["id"])
                    if "ok" in r:
                        log(f"管理员 {me['username']} 删除用户：{r['username']}")
                self._json(r, 400 if "error" in r else 200)

            elif path == "/api/screens":
                me = self._user()
                self._json(save_screen(conn, me["id"] if me else None,
                                       body.get("name", ""), body.get("conds")),
                           400 if not body.get("name") else 200)

            elif path == "/api/screens/delete":
                me = self._user()
                self._json({"deleted": delete_screen(conn, body.get("id"),
                                                     me["id"] if me else None)})

            elif path == "/api/fav":
                me = self._user()
                if not me:
                    self._json({"error": "请先登录"}, 401)
                    return
                sym = body.get("symbol", "")
                r = users.add_favorite(conn, me["id"], sym, body.get("group_id") or None)
                if "ok" in r and body.get("group_id") is not None:
                    users.set_group(conn, me["id"], sym, body.get("group_id") or None)
                self._json(r, 400 if "error" in r else 200)

            elif path == "/api/fav/delete":
                me = self._user()
                if not me:
                    self._json({"error": "请先登录"}, 401)
                    return
                self._json(users.remove_favorite(
                    conn, me["id"], body.get("symbol", ""),
                    delete_notes=bool(body.get("delete_notes"))))

            elif path == "/api/note":
                me = self._user()
                if not me:
                    self._json({"error": "请先登录"}, 401)
                    return
                r = users.add_note(conn, me["id"], body.get("symbol", ""), body.get("note", ""))
                self._json(r, 400 if "error" in r else 200)

            elif path == "/api/note/delete":
                me = self._user()
                if not me:
                    self._json({"error": "请先登录"}, 401)
                    return
                self._json(users.delete_note(conn, me["id"], int(body.get("id") or 0)))

            elif path == "/api/group":
                me = self._user()
                if not me:
                    self._json({"error": "请先登录"}, 401)
                    return
                action = body.get("action", "create")
                gid = int(body.get("id") or 0)
                if action == "create":
                    r = users.create_group(conn, me["id"], body.get("name", ""))
                elif action == "rename":
                    r = users.rename_group(conn, me["id"], gid, body.get("name", ""))
                elif action == "delete":
                    r = users.delete_group(conn, me["id"], gid)
                elif action == "assign":
                    r = users.set_group(conn, me["id"], body.get("symbol", ""), gid or None)
                else:
                    r = {"error": "未知操作"}
                self._json(r, 400 if "error" in r else 200)
        finally:
            conn.close()


class Server(ThreadingHTTPServer):
    # Windows 下 SO_REUSEADDR 会允许「绑定已被占用的端口」，造成假启动，这里禁用
    allow_reuse_address = False
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5001)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = ap.parse_args()

    srv = None
    for p in range(args.port, args.port + 10):
        try:
            srv = Server((args.host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        print(f"端口 {args.port}~{args.port + 9} 都被占用")
        return 1

    url = f"http://{args.host}:{port}"
    print("=" * 62)
    print(f"  A 股选股系统  v1.1")
    print(f"  地址   {url}")
    if port != args.port:
        print(f"  端口   {args.port} 被占用，已自动改用 {port}")
    try:
        conn = db.connect()
        st = db.stats(conn)
        have, allc = sync.qfq_progress(conn)
        print(f"  数据库 {db.DB_PATH}")
        print(f"  数据   {st['securities']} 只 / 日线 {st['bars']/1e4:.0f} 万行 "
              f"({st['bar_range'][0]} ~ {st['bar_range'][1]})")
        print(f"  复权   前复权 {have}/{allc}　库 {st['db_mb']} MB")
        users.ensure(conn)
        seeded = seed_screens(conn)
        n_user = conn.execute("SELECT COUNT(1) FROM users").fetchone()[0]
        n_fav = conn.execute("SELECT COUNT(1) FROM favorites").fetchone()[0]
        print(f"  用户   {n_user} 人 / 收藏 {n_fav} 条" + (f" / 新增默认方案 {seeded} 个" if seeded else ""))
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"  数据库读取失败：{e}")
    print("=" * 62)
    print("  Ctrl+C 停止\n")

    def warm():
        """只有分位过期（数据日期变了）才重算，平时启动直接跳过这 40 秒。"""
        try:
            conn = db.connect()
            try:
                if not query.pct_stale(conn):
                    query.pct_maps(conn)          # 只是读表预热一下内存
                    log("估值分位已是最新，跳过重算")
                    return
            finally:
                conn.close()
            t0 = time.time()
            conn = db.connect()
            n = query.refresh_pct(conn)
            conn.close()
            log(f"估值分位预热完成：{n} 只，{time.time()-t0:.1f}s")
        except Exception as e:  # noqa: BLE001
            log(f"预热失败：{e}")

    threading.Thread(target=warm, daemon=True).start()

    def warm_bars():
        """把日线读进内存，第一次形态查询就不用等。"""
        try:
            t0 = time.time()
            conn = db.connect()
            day, bars = patterns._bars(conn)
            patterns._hist_low(conn)          # 顺带算全历史最低（做小做底要用）
            conn.close()
            log(f"日线预热完成：{len(bars)} 只 / {day}，{time.time()-t0:.1f}s")
        except Exception as e:  # noqa: BLE001
            log(f"日线预热失败：{e}")

    # 延迟 2 秒再预热：先让首屏的几个接口跑完（K 线画标记已改成只读单只票）
    def _later(delay, fn):
        t = threading.Timer(delay, fn)
        t.daemon = True
        t.start()

    _later(2.0, warm_bars)
    _later(3.0, warm)
    threading.Thread(target=auto_sync_loop, daemon=True).start()
    if not args.no_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
