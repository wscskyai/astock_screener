# -*- coding: utf-8 -*-
"""基于本地库的选股查询（示例：股东户数连降 + 近三季净利润）。

    python -m astock.query holders --min-drop 5          # 股东户数三季连降
    python -m astock.query profit --min 10000000         # 近三季单季净利之和 > 1000 万
    python -m astock.query combo --min-drop 5 --min-profit 10000000
    python -m astock.query bars 600000                   # 看单只票的日线

所有查询只读本地 astock.db，不联网。
"""

from __future__ import annotations

import argparse
import sqlite3

import pandas as pd

from . import db

pd.set_option("display.unicode.east_asian_width", True)
pd.set_option("display.width", 200)


def _q(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


# ------------------------------------------------------------------ 筹码
def holders_drop(conn: sqlite3.Connection, n: int = 3, min_drop: float = 0.0,
                 max_age_days: int = 400) -> pd.DataFrame:
    """最近 n 个**连续季末**股东户数严格递减，且首末累计降幅 ≥ min_drop%。

    只认 03-31 / 06-30 / 09-30 / 12-31 四个季末，临时公告的户数（如 10 日/20 日）会打乱节奏，剔除。
    最近一期必须在 max_age_days 内，避免用两年前的旧数据。
    """
    df = _q(conn, """
        SELECT symbol, end_date, holder_num
        FROM holders
        WHERE holder_num IS NOT NULL AND holder_num > 0
        ORDER BY symbol, end_date DESC
    """)
    if df.empty:
        return pd.DataFrame()
    df = df[df["end_date"].str[5:].isin(["03-31", "06-30", "09-30", "12-31"])]
    if df.empty:
        return pd.DataFrame()
    df["q"] = df["end_date"].str[:4].astype(int) * 4 + df["end_date"].str[5:7].astype(int) // 3
    df["rk"] = df.groupby("symbol").cumcount()

    h = df[df["rk"] < n].pivot(index="symbol", columns="rk", values="holder_num")
    q = df[df["rk"] < n].pivot(index="symbol", columns="rk", values="q")
    d = df[df["rk"] < n].pivot(index="symbol", columns="rk", values="end_date")
    if h.shape[1] < n:
        return pd.DataFrame()

    # 连续三个季度：q0 = q1+1 = q2+2
    seq = (q[0] == q[1] + 1) & (q[1] == q[2] + 1)
    drop = (h[0] / h[n - 1] - 1.0) * 100
    recent = pd.to_datetime(d[0]) >= (pd.Timestamp.today() - pd.Timedelta(days=max_age_days))

    out = pd.DataFrame({
        "latest": h[0], "prev1": h[1], "prev2": h[n - 1],
        "drop_pct": drop.round(2),
        "d0": d[0], "d1": d[1], "d2": d[n - 1],
    })[seq & recent & (drop <= -min_drop)].dropna()
    out[["latest", "prev1", "prev2"]] = out[["latest", "prev1", "prev2"]].astype("int64")
    return out.sort_values("drop_pct")


# ------------------------------------------------------------------ 财务
def profit_ok(conn: sqlite3.Connection, quarters: int = 3, min_profit: float = 1e7,
              max_age_days: int = 400) -> pd.DataFrame:
    """最近 quarters 个「单季」归母净利润之和 ≥ min_profit（元）。

    A 股财报是年内累计，先折算单季：Q1=cum，Q2=cum-Q1cum，Q3=cum-Q1cum-Q2cum，Q4=cum-Q1..Q3。
    """
    df = _q(conn, """
        SELECT symbol, report_period, net_profit, is_cumulative, announce_date,
               roe, net_profit_yoy
        FROM financials WHERE net_profit IS NOT NULL
        ORDER BY symbol, report_period
    """)
    if df.empty:
        return df
    df["year"] = df["report_period"].str[:4]
    df["month"] = df["report_period"].str[5:7]
    prev = df.groupby(["symbol", "year"])["net_profit"].shift(1)
    df["single_q"] = df["net_profit"] - prev.fillna(0.0)
    df.loc[df["month"] == "03", "single_q"] = df.loc[df["month"] == "03", "net_profit"]
    # 缺上一期累计时，无法拆出单季
    df.loc[(df["month"] != "03") & prev.isna(), "single_q"] = float("nan")

    cut = pd.Timestamp.today().normalize() - pd.Timedelta(days=max_age_days)
    recent = df[pd.to_datetime(df["report_period"]) >= cut].copy()
    if recent.empty:
        return pd.DataFrame()
    recent["rk"] = recent.groupby("symbol")["report_period"].rank(ascending=False, method="first")
    last = recent[recent["rk"] <= quarters]

    agg = last.groupby("symbol").agg(
        n_q=("single_q", "count"),
        sum_profit=("single_q", "sum"),
        last_period=("report_period", "max"),
        announced=("announce_date", "max"),
    )
    agg = agg[(agg["n_q"] >= quarters) & (agg["sum_profit"] >= min_profit)]

    # 最新一期的 ROE（年化）与净利同比
    latest = recent[recent["rk"] == 1].set_index("symbol")
    qidx = latest["report_period"].str[5:7].astype(int) // 3
    agg["roe_annual"] = (latest["roe"] * 4.0 / qidx).round(2)
    agg["yoy"] = latest["net_profit_yoy"].round(2)
    return agg.drop(columns="n_q").sort_values("sum_profit", ascending=False)


def fundamental_ok(conn: sqlite3.Connection, min_roe: float = 0.0,
                   min_yoy: float | None = None, max_age_days: int = 400) -> pd.DataFrame:
    """最新报告期的 ROE（年化）与净利同比。

    A 股 ROE 是年内累计数：Q1 的 3% 不能和年报的 12% 直接比，
    这里按 4/季度数 年化（Q1×4，半年×2，三季×4/3，年报×1）。
    """
    df = _q(conn, """
        SELECT symbol, report_period, roe, net_profit_yoy, announce_date
        FROM financials WHERE roe IS NOT NULL
        ORDER BY symbol, report_period
    """)
    if df.empty:
        return df
    cut = pd.Timestamp.today().normalize() - pd.Timedelta(days=max_age_days)
    df = df[pd.to_datetime(df["report_period"]) >= cut]
    if df.empty:
        return df
    df["rk"] = df.groupby("symbol")["report_period"].rank(ascending=False, method="first")
    latest = df[df["rk"] == 1].set_index("symbol")
    qidx = latest["report_period"].str[5:7].astype(int) // 3
    out = pd.DataFrame({
        "roe": latest["roe"].round(2),
        "roe_annual": (latest["roe"] * 4.0 / qidx).round(2),
        "yoy": latest["net_profit_yoy"].round(2),
        "period": latest["report_period"],
    })
    if min_roe:
        out = out[out["roe_annual"] >= min_roe]
    if min_yoy is not None:
        out = out[out["yoy"].fillna(-999) >= min_yoy]
    return out


# ------------------------------------------------------------------ 组合
_PCT_CACHE: dict[str, dict] = {}


def _compute_pct(conn: sqlite3.Connection, window: int) -> dict[str, dict[str, float]]:
    cutoff = conn.execute(
        "SELECT MIN(date) FROM (SELECT DISTINCT date FROM valuation_daily ORDER BY date DESC LIMIT ?)",
        (window,),
    ).fetchone()[0]
    maps: dict[str, dict[str, float]] = {}
    for f in ("pe_ttm", "pb", "dv_ttm"):
        sql = f"""
            SELECT symbol,
                   ROUND(SUM(CASE WHEN v <= cur THEN 1 ELSE 0 END) * 100.0 / COUNT(1), 1)
            FROM (
                SELECT symbol, {f} AS v,
                       LAST_VALUE({f}) OVER (
                           PARTITION BY symbol ORDER BY date
                           ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
                       ) AS cur
                FROM valuation_daily
                WHERE {f} > 0 AND date >= ?
            )
            GROUP BY symbol
        """
        try:
            maps[f] = {k: float(v) for k, v in conn.execute(sql, (cutoff,))}
        except sqlite3.OperationalError:
            maps[f] = {}
    return maps


def pct_stale(conn: sqlite3.Connection) -> bool:
    """分位表是不是过期了：已算过的日期和最新交易日不同就需要重算。"""
    day = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    ts = conn.execute("SELECT MAX(updated_at) FROM valuation_pct").fetchone()[0]
    if not day:
        return False
    return not (ts and str(ts)[:10] == str(day)[:10])


def refresh_pct(conn: sqlite3.Connection, window: int = 1200) -> int:
    """重算分位并落表，查询时直接读表（毫秒级）。"""
    import time as _t

    t0 = _t.time()
    maps = _compute_pct(conn, window)
    syms = set().union(*(m.keys() for m in maps.values())) if maps else set()
    ts = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [(s, maps["pe_ttm"].get(s), maps["pb"].get(s), maps["dv_ttm"].get(s), window, ts) for s in syms]
    conn.executemany(
        "INSERT INTO valuation_pct(symbol, pe_pct, pb_pct, dv_pct, window, updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(symbol) DO UPDATE SET pe_pct=excluded.pe_pct, pb_pct=excluded.pb_pct, "
        "dv_pct=excluded.dv_pct, window=excluded.window, updated_at=excluded.updated_at",
        rows,
    )
    conn.commit()
    _PCT_CACHE["all"] = {"t": _t.time(), "maps": maps, "window": window}
    print(f"分位已更新：{len(rows)} 只 / {window} 日窗口 / {_t.time()-t0:.1f}s", flush=True)
    return len(rows)


def pct_maps(conn: sqlite3.Connection, fields: tuple[str, ...] = ("pe_ttm", "pb", "dv_ttm"),
             window: int = 1200) -> dict[str, dict[str, float]]:
    """优先读预计算表；表为空时现场算一次。"""
    import time as _t

    hit = _PCT_CACHE.get("all")
    if hit and _t.time() - hit["t"] < 600 and hit["window"] == window:
        return {f: hit["maps"].get(f, {}) for f in fields}

    rows = conn.execute("SELECT symbol, pe_pct, pb_pct, dv_pct FROM valuation_pct").fetchall()
    if not rows:
        refresh_pct(conn, window)
        rows = conn.execute("SELECT symbol, pe_pct, pb_pct, dv_pct FROM valuation_pct").fetchall()

    maps = {"pe_ttm": {}, "pb": {}, "dv_ttm": {}}
    for s, pe, pb, dv in rows:
        if pe is not None:
            maps["pe_ttm"][s] = pe
        if pb is not None:
            maps["pb"][s] = pb
        if dv is not None:
            maps["dv_ttm"][s] = dv
    _PCT_CACHE["all"] = {"t": _t.time(), "maps": maps, "window": window}
    return {f: maps.get(f, {}) for f in fields}


def pct_map(conn: sqlite3.Connection, field: str = "pe_ttm", window: int = 1200) -> dict[str, float]:
    return pct_maps(conn, (field,), window)[field]


def pe_percentile(conn: sqlite3.Connection, window: int = 250) -> dict[str, float]:
    return pct_map(conn, "pe_ttm", window)


def combo(conn: sqlite3.Connection, min_drop: float, min_profit: float,
          exclude_st: bool = False, min_bars: int = 250,
          min_roe: float = 0.0, min_yoy: float | None = None,
          max_pe: float | None = None, min_dv: float | None = None,
          max_pe_pct: float | None = None, max_pb_pct: float | None = None,
          min_dv_pct: float | None = None) -> pd.DataFrame:
    h = holders_drop(conn, 3, min_drop)
    p = profit_ok(conn, 3, min_profit)
    if h.empty or p.empty:
        return pd.DataFrame()
    m = h.join(p, how="inner")

    if min_roe:
        m = m[m["roe_annual"].fillna(-999) >= min_roe]
    if min_yoy is not None:
        m = m[m["yoy"].fillna(-999) >= min_yoy]

    meta = _q(conn, """
        SELECT s.symbol, s.name, s.board, s.mktcap, COALESCE(s.n_bars, 0) AS n_bars,
               v.pe_ttm, v.pb, v.dv_ttm, v.industry
        FROM securities s LEFT JOIN valuation v ON v.symbol = s.symbol
    """).set_index("symbol")
    m = m.join(meta, how="left")
    if exclude_st:
        m = m[~m["name"].fillna("").str.contains("ST")]
    m = m[m["n_bars"] >= min_bars]          # 剔除次新
    if max_pe:
        # 亏损股（PE≤0）不算便宜，直接排除
        m = m[m["pe_ttm"].notna() & (m["pe_ttm"] > 0) & (m["pe_ttm"] <= max_pe)]
    if min_dv:
        m = m[m["dv_ttm"].notna() & (m["dv_ttm"] >= min_dv)]
    if max_pe_pct is not None:
        m = m.copy()
        m["pe_pct"] = m.index.map(pct_map(conn, "pe_ttm"))
        m = m[m["pe_pct"].notna() & (m["pe_pct"] <= max_pe_pct)]
    if max_pb_pct is not None:
        m = m.copy()
        m["pb_pct"] = m.index.map(pct_map(conn, "pb"))
        m = m[m["pb_pct"].notna() & (m["pb_pct"] <= max_pb_pct)]
    if min_dv_pct is not None:
        m = m.copy()
        m["dv_pct"] = m.index.map(pct_map(conn, "dv_ttm"))
        m = m[m["dv_pct"].notna() & (m["dv_pct"] >= min_dv_pct)]
    return m.sort_values(["drop_pct", "sum_profit"])


def filter_universe(conn: sqlite3.Connection, df: pd.DataFrame, years: float = 0,
                    mktcap: tuple[float, float] | None = None, exclude_st: bool = False) -> pd.DataFrame:
    """按上市年数 / 总市值（亿）/ 是否剔除 ST 过滤。

    - years>0：上市满 years 年（list_date 缺失的剔除）
    - mktcap=(lo, hi)：总市值区间，单位亿元
    - exclude_st：剔除名称含 ST 的股票
    """
    if df.empty:
        return df
    conds, params = [], []
    if years and years > 0:
        cutoff = (pd.Timestamp.today() - pd.Timedelta(days=int(round(years * 365.25)))).strftime("%Y-%m-%d")
        conds.append("list_date IS NOT NULL AND list_date <> '' AND list_date <= ?")
        params.append(cutoff)
    if mktcap:
        lo, hi = mktcap
        conds.append("mktcap IS NOT NULL AND mktcap >= ? AND mktcap <= ?")
        params += [lo, hi]
    if exclude_st:
        conds.append("name NOT LIKE '%ST%'")
    if not conds:
        return df
    sql = "SELECT symbol FROM securities WHERE " + " AND ".join(conds)
    ok = {r[0] for r in conn.execute(sql, params)}
    return df[df.index.isin(ok)]


def filter_listed(conn: sqlite3.Connection, df: pd.DataFrame, years: float) -> pd.DataFrame:
    """向后兼容：只按上市年数过滤。"""
    return filter_universe(conn, df, years=years)


def _profit(a) -> float:
    """净利下限（元）：--profit-yi 优先于 --min-profit / --min。"""
    if getattr(a, "profit_yi", None) is not None:
        return float(a.profit_yi) * 1e8
    return float(getattr(a, "min_profit", None) or getattr(a, "min", 1e7))


def parse_mktcap(vals: list[float] | None) -> tuple[float, float] | None:
    """--mktcap 50 → (50, +inf)；--mktcap 50 200 → (50, 200)。"""
    if not vals:
        return None
    if len(vals) == 1:
        return (vals[0], float("inf"))
    lo, hi = min(vals[0], vals[1]), max(vals[0], vals[1])
    return (lo, hi)


def show(df: pd.DataFrame, title: str, limit: int = 40) -> None:
    print("\n" + "=" * 100)
    print(f"{title}  命中 {len(df)} 只")
    print("=" * 100)
    if df.empty:
        print("（无）")
        return
    d = df.head(limit).copy()
    if "sum_profit" in d:
        d["sum_profit"] = (d["sum_profit"] / 1e8).round(2)
    print(d.to_string())
    if len(df) > limit:
        print(f"… 其余 {len(df) - limit} 只略")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--min-years", type=float, default=0, help="上市年数下限，如 5")
        p.add_argument("--mktcap", nargs="+", type=float, metavar="亿",
                       help="总市值（亿元）：一个值=下限，两个值=区间，如 --mktcap 50 200")
        p.add_argument("--exclude-st", action="store_true", help="剔除 ST / *ST")
        return p

    def apply(c, a, df):
        return filter_universe(c, df, a.min_years, parse_mktcap(a.mktcap), a.exclude_st)

    def title(base: str, a) -> str:
        bits = []
        if a.min_years:
            bits.append(f"上市≥{a.min_years:g}年")
        mc = parse_mktcap(a.mktcap)
        if mc:
            hi = "∞" if mc[1] == float("inf") else f"{mc[1]:g}"
            bits.append(f"市值{mc[0]:g}~{hi}亿")
        if a.exclude_st:
            bits.append("非ST")
        return base + ("（" + "，".join(bits) + "）" if bits else "")

    p = common(sub.add_parser("holders"))
    p.add_argument("--min-drop", type=float, default=0.0, help="三季累计降幅下限（%%）")
    p.set_defaults(fn=lambda c, a: show(apply(c, a, holders_drop(c, 3, a.min_drop)),
                                        title("股东户数三季连降", a)))

    p = common(sub.add_parser("profit"))
    p.add_argument("--min", type=float, default=1e7, help="净利下限（元）")
    p.add_argument("--profit-yi", type=float, default=None, help="净利下限（亿元），优先于 --min，如 0.1")
    p.set_defaults(fn=lambda c, a: show(
        apply(c, a, profit_ok(c, 3, _profit(a))),
        title(f"近三季单季净利之和 ≥ {_profit(a)/1e8:.2f} 亿", a)))

    p = common(sub.add_parser("combo"))
    p.add_argument("--min-drop", type=float, default=5.0, help="三季累计降幅下限（%%）")
    p.add_argument("--min-profit", type=float, default=1e7, help="净利下限（元）")
    p.add_argument("--profit-yi", type=float, default=None, help="净利下限（亿元），优先于 --min-profit，如 0.1")
    p.add_argument("--min-roe", type=float, default=0.0, help="最新一期 ROE（年化）下限，如 10")
    p.add_argument("--min-yoy", type=float, default=None, help="净利润同比下限（%%），如 0 表示同比为正")
    p.add_argument("--min-bars", type=int, default=250, help="上市不足该交易日的次新剔除")
    p.set_defaults(fn=lambda c, a: show(
        apply(c, a, combo(c, a.min_drop, _profit(a), a.exclude_st, a.min_bars, a.min_roe, a.min_yoy)),
        title("筹码集中 + 盈利", a) + (f" ROE≥{a.min_roe:g}%" if a.min_roe else "")))

    p = common(sub.add_parser("quality"))
    p.add_argument("--min-roe", type=float, default=10.0, help="最新一期 ROE（年化）下限，如 10")
    p.add_argument("--min-yoy", type=float, default=None, help="净利润同比下限（%%）")
    p.set_defaults(fn=lambda c, a: show(
        apply(c, a, fundamental_ok(c, a.min_roe, a.min_yoy)),
        title(f"ROE≥{a.min_roe:g}%（年化）", a)))

    p = sub.add_parser("bars")
    p.add_argument("symbol")
    p.set_defaults(fn=lambda c, a: print(_q(c, "SELECT * FROM bars WHERE symbol=? ORDER BY date DESC LIMIT 20",
                                           (a.symbol,)).to_string()))

    args = ap.parse_args(argv)
    conn = db.connect()
    args.fn(conn, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
