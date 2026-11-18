# -*- coding: utf-8 -*-
"""指标宽表 + 通用条件筛选。

一只股票一行，把可筛的指标全部算好，界面用「条件行」表达筛选逻辑：

    conds = [{"k": "pe", "min": 0, "max": 15},
             {"k": "drop_pct", "min": 20},          # 只给下限
             {"k": "exclude_st"}]                    # 布尔开关

宽表按需重算并缓存 60 秒（同步数据后由 web 层清空缓存）。
"""

from __future__ import annotations

import sqlite3
import threading
import time

import numpy as np
import pandas as pd

import query

# ---------------------------------------------------------------- 字段登记
PERIODS = [
    {"k": "latest", "label": "最新一期"},
    {"k": "q1", "label": "一季报"},
    {"k": "h1", "label": "半年报"},
    {"k": "q3", "label": "三季报"},
    {"k": "annual", "label": "年报"},
]

PERIOD_MONTH = {"q1": "03-31", "h1": "06-30", "q3": "09-30", "annual": "12-31"}
YEARS: list[int] = []          # 财务里实际存在的年份，metrics() 首次调用后填充
HOLDER_YEARS: list[int] = []   # 股东户数里存在的年份
HOLDER_END: dict[int, str] = {}  # 年份 → 该年最后一个季末
MIN_YEAR = 2016                # 太早的年份不展开成列（节省内存）
MIN_HOLDER_YEAR = 2019

HOLDER_OPTS = [
    {"k": "1", "label": "近 1 季"},
    {"k": "2", "label": "近 2 季"},
    {"k": "3", "label": "近 3 季"},
]

FLAG_POLE_OPTS = [
    {"k": "auto", "label": "旗杆按板块（主板≥6%/20%板≥9.5%）"},
    {"k": "6", "label": "旗杆统一 ≥6%"},
    {"k": "95", "label": "旗杆统一 ≥9.5%"},
]
FLAG_POS_OPTS = [
    {"k": "20", "label": "位置 ≤0.20"},
    {"k": "30", "label": "位置 ≤0.30"},
    {"k": "40", "label": "位置 ≤0.40"},
]
BASE_WIN_OPTS = [
    {"k": "none", "label": "只看形态（不限启动）"},
    {"k": "10", "label": "近 10 日内启动"},
    {"k": "5", "label": "近 5 日内启动"},
    {"k": "20", "label": "近 20 日内启动"},
]
BASE_IGNITE_OPTS = [
    {"k": "std", "label": "标准：涨≥4% + 量 2 倍"},
    {"k": "mild", "label": "温和：涨≥3% + 量 1.5 倍"},
    {"k": "strong", "label": "强势：涨≥6%/9.5% + 量 2 倍"},
]
W_BRK_WIN_OPTS = [
    {"k": "5", "label": "近 5 日内突破"},
    {"k": "1", "label": "今日突破"},
    {"k": "3", "label": "近 3 日内突破"},
    {"k": "10", "label": "近 10 日内突破"},
]
W_BRK_RATE_OPTS = [
    {"k": "3", "label": "站上颈线 3%"},
    {"k": "0", "label": "站上颈线（0%）"},
    {"k": "5", "label": "站上颈线 5%"},
]
W_PEND_WIN_OPTS = [
    {"k": "10", "label": "右底近 10 日形成"},
    {"k": "15", "label": "右底近 15 日形成"},
    {"k": "20", "label": "右底近 20 日形成"},
]
VOLUP_DAY_OPTS = [
    {"k": "3", "label": "连续3日"},
    {"k": "4", "label": "连续4日"},
    {"k": "5", "label": "连续5日"},
]
VOLUP_RULE_OPTS = [
    {"k": "mild", "label": "温和1.2×/1.5×"},
    {"k": "std", "label": "标准1.5×/1.8×"},
    {"k": "strong", "label": "强势1.8×/2.5×"},
]
VOLUP_POS_OPTS = [
    {"k": "30", "label": "分位≤30%"},
    {"k": "40", "label": "分位≤40%"},
    {"k": "50", "label": "分位≤50%"},
]

BOX_WIN_OPTS = [
    {"k": "90", "label": "近 90 天箱体"},
    {"k": "60", "label": "近 60 天箱体"},
    {"k": "120", "label": "近 120 天箱体"},
]
BOX_TOUCH_OPTS = [
    {"k": "3", "label": "上下沿各触碰 ≥3 次"},
    {"k": "2", "label": "上下沿各触碰 ≥2 次"},
]
BOX_POS_OPTS = [
    {"k": "bottom", "label": "箱体底部"},
    {"k": "mid", "label": "箱体中部"},
    {"k": "top", "label": "箱体顶部"},
    {"k": "any", "label": "任意位置"},
]
FLAG_DAY_OPTS = [
    {"k": "2-5", "label": "旗面 2~5 天"},
    {"k": "2-6", "label": "旗面 2~6 天"},
    {"k": "2-8", "label": "旗面 2~8 天"},
    {"k": "3-5", "label": "旗面 3~5 天"},
    {"k": "3-6", "label": "旗面 3~6 天"},
    {"k": "3-8", "label": "旗面 3~8 天"},
]

FIELDS = [
    {"k": "pe",           "label": "市盈率(TTM)", "unit": "倍", "cat": "估值", "dec": 1},
    {"k": "pb",           "label": "市净率",      "unit": "倍", "cat": "估值", "dec": 2},
    {"k": "dv",           "label": "股息率",      "unit": "%",  "cat": "估值", "dec": 2},
    {"k": "pe_pct",       "label": "PE 分位",     "unit": "%",  "cat": "估值", "dec": 0},
    {"k": "pb_pct",       "label": "PB 分位",     "unit": "%",  "cat": "估值", "dec": 0},
    {"k": "dv_pct",       "label": "股息分位",    "unit": "%",  "cat": "估值", "dec": 0},
    {"k": "holder_chg",   "label": "户数变化",    "unit": "%",  "cat": "筹码", "dec": 1,
     "options": HOLDER_OPTS, "yearSource": "holder"},
    {"k": "holder_num",   "label": "股东户数",    "unit": "户", "cat": "筹码", "dec": 0,
     "options": HOLDER_OPTS},
    {"k": "drop_pct",     "label": "户数三季降幅", "unit": "%", "cat": "筹码", "dec": 1},
    {"k": "profit_yi",    "label": "近三季净利",  "unit": "亿", "cat": "财务", "dec": 2},
    {"k": "roe",          "label": "ROE(年化)",   "unit": "%",  "cat": "财务", "dec": 1, "period": True},
    {"k": "yoy",          "label": "净利同比(累计)", "unit": "%", "cat": "财务", "dec": 1, "period": True},
    {"k": "np_yoy",       "label": "净利同比(单季)", "unit": "%", "cat": "财务", "dec": 1, "period": True},
    {"k": "np_qoq",       "label": "净利环比(单季)", "unit": "%", "cat": "财务", "dec": 1, "period": True},
    {"k": "rev_yoy",      "label": "营收同比",    "unit": "%",  "cat": "财务", "dec": 1, "period": True},
    {"k": "gross_margin", "label": "毛利率",      "unit": "%",  "cat": "财务", "dec": 1, "period": True},
    {"k": "net_profit",   "label": "单期净利",    "unit": "亿", "cat": "财务", "dec": 2, "period": True},
    {"k": "mktcap",       "label": "总市值",      "unit": "亿", "cat": "行情", "dec": 1},
    {"k": "float_mktcap", "label": "流通市值",    "unit": "亿", "cat": "行情", "dec": 1},
    {"k": "industry",     "label": "行业",        "unit": "",   "cat": "行情", "choice": True},
    {"k": "years",        "label": "上市年数",    "unit": "年", "cat": "行情", "dec": 1},
    {"k": "base_ok",      "label": "做小做底",    "unit": "",   "cat": "K线形态", "bool": True,
     "options": BASE_WIN_OPTS, "daysOptions": BASE_IGNITE_OPTS, "daysDefault": "std"},
    {"k": "flag_ok",      "label": "旗形整理中",  "unit": "",   "cat": "K线形态", "bool": True,
     "options": FLAG_POLE_OPTS, "daysOptions": FLAG_DAY_OPTS, "daysDefault": "2-8",
     "posOptions": FLAG_POS_OPTS, "posDefault": "20"},
    {"k": "flag_brk",     "label": "旗形突破确认", "unit": "",  "cat": "K线形态", "bool": True,
     "options": FLAG_POLE_OPTS},
    {"k": "w_brk",        "label": "W底突破",    "unit": "",   "cat": "K线形态", "bool": True,
     "options": W_BRK_WIN_OPTS, "daysOptions": W_BRK_RATE_OPTS, "daysDefault": "3"},
    {"k": "w_pend",       "label": "W底右底",    "unit": "",   "cat": "K线形态", "bool": True,
     "options": W_PEND_WIN_OPTS},
    {"k": "volup_ok",     "label": "低位放量",    "unit": "",   "cat": "K线形态", "bool": True,
     "options": VOLUP_DAY_OPTS, "daysOptions": VOLUP_RULE_OPTS, "daysDefault": "std",
     "posOptions": VOLUP_POS_OPTS, "posDefault": "30"},
    {"k": "box_ok",       "label": "箱体形态",    "unit": "",   "cat": "K线形态", "bool": True,
     "options": BOX_WIN_OPTS, "daysOptions": BOX_TOUCH_OPTS, "daysDefault": "3",
     "posOptions": BOX_POS_OPTS, "posDefault": "any"},
    {"k": "limit_days",   "label": "N日内涨跌停/涨跌幅", "unit": "", "cat": "K线形态",
     "special": "limit_days"},
    {"k": "exclude_st",   "label": "排除 ST",     "unit": "",   "cat": "其他", "bool": True},
]
FIELD_MAP = {f["k"]: f for f in FIELDS}

_CACHE: dict = {"t": 0.0, "df": None}
_LIMIT_CACHE: dict = {"t": 0.0, "key": None, "df": None}
LIMIT_MAX_DAYS = 120                 # 涨跌停/涨跌幅条件最多回看的交易日数
_BUILD_LOCK = threading.Lock()   # 并发请求时只建一次宽表
TTL = 60.0


def invalidate() -> None:
    _CACHE.update(t=0.0, df=None)
    _LIMIT_CACHE.update(t=0.0, key=None, df=None)


# ---------------------------------------------------------------- 宽表
def metrics(conn: sqlite3.Connection, force: bool = False) -> pd.DataFrame:
    now = time.time()
    if not force and _CACHE["df"] is not None and now - _CACHE["t"] < TTL:
        return _CACHE["df"]

    # 两个标签页同时进来时，只让一个真正去建表，另一个等它建完直接用
    with _BUILD_LOCK:
        now = time.time()
        if not force and _CACHE["df"] is not None and now - _CACHE["t"] < TTL:
            return _CACHE["df"]
        return _build(conn, now)


def _build(conn: sqlite3.Connection, now: float) -> pd.DataFrame:
    base = query._q(conn, """
        SELECT s.symbol, s.name, s.board, s.mktcap, s.float_mktcap, s.list_date, COALESCE(s.n_bars, 0) AS n_bars,
               v.pe_ttm, v.pb, v.dv_ttm, v.industry
        FROM securities s LEFT JOIN valuation v ON v.symbol = s.symbol
    """).set_index("symbol")

    # 所有派生列先攒在这里，最后一次拼进宽表（避免逐列插入造成的碎片化）
    add: dict[str, pd.Series] = {}
    wide_parts: list[pd.DataFrame] = []

    # ---------------- 股东户数：最近 4 个连续季末，支持「近 1/2/3 季」变化
    h = query._q(conn, """
        SELECT symbol, end_date, holder_num FROM holders
        WHERE holder_num IS NOT NULL AND holder_num > 0
        ORDER BY symbol, end_date DESC
    """)
    if not h.empty:
        h = h[h["end_date"].str[5:].isin(["03-31", "06-30", "09-30", "12-31"])]
        h["q"] = h["end_date"].str[:4].astype(int) * 4 + h["end_date"].str[5:7].astype(int) // 3
        h["rk"] = h.groupby("symbol").cumcount()
        h_full = h[["symbol", "end_date", "holder_num"]].drop_duplicates(["symbol", "end_date"])
        h = h[h["rk"] < 4]
        hv = h.pivot(index="symbol", columns="rk", values="holder_num")
        hq = h.pivot(index="symbol", columns="rk", values="q")
        if hv.shape[1] == 4:
            seq = pd.Series(True, index=hv.index)
            for n in (1, 2, 3):
                seq &= hq[0] == hq[n] + n
            for n in (1, 2, 3):
                add[f"holder_chg@{n}"] = ((1 - hv[0] / hv[n]) * 100).where(seq).round(2)
                add[f"holder_num@{n}"] = hv[n].where(seq).astype("float64")
            add["holder_latest"] = hv[0].where(seq).astype("float64")
            add["holder_prev2"] = hv[2].where(seq).astype("float64")
            add["drop_pct"] = add["holder_chg@3"]
        elif hv.shape[1] == 3:
            seq = (hq[0] == hq[1] + 1) & (hq[1] == hq[2] + 1)
            add["drop_pct"] = ((1 - hv[0] / hv[2]) * 100).where(seq).round(2)
            add["holder_latest"] = hv[0].where(seq).astype("float64")
            add["holder_prev2"] = hv[2].where(seq).astype("float64")

        # 按年份 × 区间展开：条件里选「2025 年 近 2 季」时直接取列
        global HOLDER_YEARS, HOLDER_END
        hp = h_full.pivot_table(index="symbol", columns="end_date",
                                values="holder_num", aggfunc="last").sort_index(axis=1)
        hdates = [c for c in hp.columns if int(c[:4]) >= MIN_HOLDER_YEAR]
        if hdates:
            hp = hp[hdates]
            by_year: dict[int, str] = {}
            for c in hdates:
                by_year[int(c[:4])] = c          # 升序遍历，最后留下当年最后一个季末
            HOLDER_END = by_year
            HOLDER_YEARS = sorted(by_year, reverse=True)
            for n in (1, 2, 3):
                chg = (1 - hp / hp.shift(axis=1, periods=n)) * 100
                for c in hdates:
                    add[f"holder_chg@{c}@{n}"] = chg[c].round(2)

    # ---------------- 财务：最新一期 + 各报告期类型各取最近一期
    f = query._q(conn, """
        SELECT symbol, report_period, net_profit, net_profit_yoy, revenue_yoy, gross_margin, roe
        FROM financials WHERE net_profit IS NOT NULL ORDER BY symbol, report_period
    """)
    if not f.empty:
        f = f.sort_values(["symbol", "report_period"]).reset_index(drop=True)
        f["year"] = f["report_period"].str[:4]
        f["month"] = f["report_period"].str[5:7]
        f["qidx"] = f["month"].astype(int) // 3
        prev = f.groupby(["symbol", "year"])["net_profit"].shift(1)
        f["single_q"] = f["net_profit"] - prev.fillna(0.0)
        f.loc[f["month"] == "03", "single_q"] = f.loc[f["month"] == "03", "net_profit"]
        f.loc[(f["month"] != "03") & prev.isna(), "single_q"] = np.nan

        # 单季净利的环比 / 同比（分母为正才算，亏损转盈不出数）
        g = f.groupby("symbol")["single_q"]
        f["prev_q"] = g.shift(1)
        f["prev_4"] = g.shift(4)
        f["np_qoq_"] = np.where(f["prev_q"] > 0, (f["single_q"] / f["prev_q"] - 1) * 100, np.nan)
        f["np_yoy_"] = np.where(f["prev_4"] > 0, (f["single_q"] / f["prev_4"] - 1) * 100, np.nan)

        f["rk"] = f.groupby("symbol")["report_period"].rank(ascending=False, method="first")

        def attach(sub: pd.DataFrame, key: str):
            """某报告期类型：取最近一期，算出各项指标。"""
            if sub.empty:
                return
            sub = sub.copy()
            sub["rk2"] = sub.groupby("symbol")["report_period"].rank(ascending=False, method="first")
            latest = sub[sub["rk2"] == 1].set_index("symbol")
            q = latest["qidx"]
            add[f"roe@{key}"] = (latest["roe"] * 4.0 / q).round(2)
            add[f"yoy@{key}"] = latest["net_profit_yoy"]
            add[f"rev_yoy@{key}"] = latest["revenue_yoy"]
            add[f"gross_margin@{key}"] = latest["gross_margin"]
            add[f"net_profit@{key}"] = (latest["single_q"] / 1e8).round(3)
            add[f"np_qoq@{key}"] = latest["np_qoq_"].round(2)
            add[f"np_yoy@{key}"] = latest["np_yoy_"].round(2)
            add[f"period@{key}"] = latest["report_period"]

        attach(f, "latest")
        for code, key in (("03", "q1"), ("06", "h1"), ("09", "q3"), ("12", "annual")):
            attach(f[f["month"] == code], key)

        add["profit_yi"] = (f[f["rk"] <= 3].groupby("symbol")["single_q"].sum() / 1e8).round(3)

        # 具体年份 × 报告期：条件里选「2025 年报」时直接取列
        f["roe_ann"] = (f["roe"] * 4.0 / f["qidx"]).round(2)
        f["np_yi"] = (f["single_q"] / 1e8).round(3)
        global YEARS
        periods = [p for p in sorted(f["report_period"].unique()) if p[:4].isdigit()
                   and int(p[:4]) >= MIN_YEAR]
        YEARS = sorted({int(p[:4]) for p in periods}, reverse=True)
        if periods:
            sub = f[f["report_period"].isin(periods)]
            for col, out in (("roe_ann", "roe"), ("net_profit_yoy", "yoy"),
                             ("revenue_yoy", "rev_yoy"), ("gross_margin", "gross_margin"),
                             ("np_yi", "net_profit"), ("np_qoq_", "np_qoq"), ("np_yoy_", "np_yoy")):
                pv = sub.pivot_table(index="symbol", columns="report_period", values=col, aggfunc="last")
                pv.columns = [f"{out}@{c}" for c in pv.columns]
                wide_parts.append(pv)
            for p in periods:
                add[f"period@{p}"] = pd.Series(p, index=base.index)

    # ---------------- 一次性合并所有派生列
    if add:
        base = pd.concat([base, pd.DataFrame(add, index=base.index)], axis=1)
    if wide_parts:
        base = pd.concat([base] + [w.reindex(base.index) for w in wide_parts], axis=1)
    df = base

    # 默认列指向最新一期，表格直接用
    for name in ("roe", "yoy", "rev_yoy", "gross_margin", "net_profit", "np_yoy", "np_qoq"):
        if f"{name}@latest" in df.columns:
            df[name] = df[f"{name}@latest"]
    if "period@latest" in df.columns:
        df["period"] = df["period@latest"]

    # 估值分位
    try:
        pcts = query.pct_maps(conn)
        df["pe_pct"] = df.index.map(pcts.get("pe_ttm", {}))
        df["pb_pct"] = df.index.map(pcts.get("pb", {}))
        df["dv_pct"] = df.index.map(pcts.get("dv_ttm", {}))
    except Exception:  # noqa: BLE001
        pass

    df["pe"] = pd.to_numeric(df["pe_ttm"], errors="coerce")
    df["dv"] = pd.to_numeric(df["dv_ttm"], errors="coerce")
    df["pb"] = pd.to_numeric(df["pb"], errors="coerce")

    # 上市年数
    ld = pd.to_datetime(df["list_date"], errors="coerce")
    df["years"] = ((pd.Timestamp.today() - ld).dt.days / 365.25).round(1)

    # 形态信号（由 build_patterns.py 预计算，查询时只做一次 join）
    try:
        pat = query._q(conn, "SELECT * FROM pattern_signal").set_index("symbol")
        if not pat.empty:
            cols = [c for c in pat.columns if c not in ("symbol", "date")]
            df = df.join(pat[cols], how="left")
    except Exception:  # noqa: BLE001
        pass
    # 形态信号列统一转成 0/1 整数。
    # 注意：以前是 for 循环逐列赋值，180 多列会把 DataFrame 打成碎片
    # （pandas 会报 PerformanceWarning: DataFrame is highly fragmented），
    # 后面对 df 的每次赋值都要重新拼块，越来越慢。这里改成整块替换。
    meta_cols = {"flag_pole_date", "flag_pole_chg", "flag_vol_mult",
                 "flag_days", "flag_pos"}
    norm_cols = [c for c in df.columns
                 if (c == "base_ok" or c.startswith(("flag_", "brk_")))
                 and c not in meta_cols]
    if norm_cols:
        block = (df[norm_cols].apply(pd.to_numeric, errors="coerce")
                 .fillna(0).astype("int8"))
        df = pd.concat([df.drop(columns=norm_cols), block], axis=1)

    df = df.copy()                     # 合并成连续块，避免后面的碎片化警告
    df["is_st"] = df["name"].fillna("").str.contains("ST")
    df["indu"] = df["industry"].fillna("")

    _CACHE.update(t=now, df=df)
    return df


# ---------------------------------------------------------------- 条件
def normalize(conds: list[dict] | None) -> list[dict]:
    out = []
    for c in conds or []:
        k = str(c.get("k") or "").strip()
        if k not in FIELD_MAP:
            continue
        item = {"k": k}
        spec = FIELD_MAP[k]
        if spec.get("special") == "limit_days":
            ev = str(c.get("ev") or "up")
            if ev not in ("up", "down", "any", "chg_up", "chg_down"):
                ev = "up"

            def _int(v, default, lo, hi):
                try:
                    n = int(float(v))
                except (TypeError, ValueError):
                    n = default
                return max(lo, min(hi, n))

            try:
                pct = float(c.get("pct", 5))
            except (TypeError, ValueError):
                pct = 5.0
            pct = max(0.1, min(30.0, round(pct, 1)))
            item.update({
                "ev": ev,
                "days": _int(c.get("days"), 10, 1, LIMIT_MAX_DAYS),
                "cnt": _int(c.get("cnt"), 2, 1, LIMIT_MAX_DAYS),
                "pct": pct,
                "on": bool(c.get("on", True)),
            })
            out.append(item)
            continue
        if spec.get("choice"):
            val = str(c.get("p") or "").strip()
            if not val:
                continue
            item["p"] = val
            item["op"] = "not" if c.get("op") == "not" else "in"
            out.append(item)
            continue
        if spec.get("bool"):
            item["on"] = bool(c.get("on", True))
            if spec.get("options"):
                item["p"] = str(c.get("p") or spec["options"][0]["k"])
            if spec.get("daysOptions"):
                item["d"] = str(c.get("d") or spec.get("daysDefault")
                                or spec["daysOptions"][0]["k"])
            if spec.get("posOptions"):
                item["lp"] = str(c.get("lp") or spec.get("posDefault")
                                 or spec["posOptions"][0]["k"])
        else:
            opts = spec.get("options")
            if opts:
                item["p"] = str(c.get("p") or opts[0]["k"])
                y = c.get("y")
                try:
                    y = int(y) if y not in (None, "", "latest") else None
                except (TypeError, ValueError):
                    y = None
                if y and spec.get("yearSource"):
                    item["y"] = y
            elif spec.get("period"):
                p = str(c.get("p") or "latest")
                if p != "latest":
                    item["p"] = p
                    y = c.get("y")
                    try:
                        y = int(y) if y not in (None, "", "latest") else None
                    except (TypeError, ValueError):
                        y = None
                    if y:
                        item["y"] = y
            for side in ("min", "max"):
                v = c.get(side)
                if v is None or v == "":
                    continue
                try:
                    item[side] = float(v)
                except (TypeError, ValueError):
                    pass
            if "min" not in item and "max" not in item:
                continue
        out.append(item)
    return out


def _col(k: str, p: str | None, y: int | None = None, d: str | None = None,
         lp: str | None = None) -> str:
    """条件 → 宽表列名。指定年份时取该年该期，否则取该类型最近一期。"""
    if k == "volup_ok":
        if p and p != "none":
            return f"volup_{p}_{d or 'std'}_{lp or '30'}"
        return "volup_ok"
    if k == "flag_ok":
        return f"flag_{p or 'auto'}_{(d or '2-8').replace('-', '_')}_{lp or '20'}"
    if k == "flag_brk":
        return f"brk_{p or 'auto'}"
    if k == "box_ok":
        return f"box_{p or '90'}_{lp or 'any'}_{d or '3'}"
    if k == "w_brk":
        return f"w_brk_{p or '5'}_{d or '3'}"
    if k == "w_pend":
        return f"w_pend_{p or '5'}"
    if k == "base_ok":
        if p and p != "none":
            return f"base_{p}_{d or 'std'}"
        return "base_ok"
    if p and p != "latest":
        if y:
            if k.startswith("holder_") and p in ("1", "2", "3"):
                end = HOLDER_END.get(y)
                if end:
                    return f"{k}@{end}@{p}"
            elif p in PERIOD_MONTH:
                return f"{k}@{y}-{PERIOD_MONTH[p]}"
        return f"{k}@{p}"
    return k


def period_label(k: str, p: str, y: int | None, d: str | None = None,
                 lp: str | None = None) -> str:
    spec = FIELD_MAP.get(k, {})
    if y and k.startswith("holder_"):
        n = next((o["label"] for o in (spec.get("options") or []) if o["k"] == p), p)
        return f"{y}年{n}"
    if y and p in PERIOD_MONTH:
        return f"{y}年" + next((o["label"] for o in PERIODS if o["k"] == p), p)
    if k == "flag_ok":
        day = next((o["label"] for o in FLAG_DAY_OPTS if o["k"] == (d or "2-8")), "")
        pole = next((o["label"] for o in FLAG_POLE_OPTS if o["k"] == (p or "auto")), p)
        pos = next((o["label"] for o in FLAG_POS_OPTS if o["k"] == (lp or "20")), "")
        return f"{pole} · {day} · {pos}"
    if k == "flag_brk":
        return next((o["label"] for o in FLAG_POLE_OPTS if o["k"] == (p or "auto")), p)
    if k == "w_brk":
        win = next((o["label"] for o in W_BRK_WIN_OPTS if o["k"] == (p or "5")), p)
        rate = next((o["label"] for o in W_BRK_RATE_OPTS if o["k"] == (d or "3")), "")
        return f"{win} · {rate}"
    if k == "w_pend":
        return next((o["label"] for o in W_PEND_WIN_OPTS if o["k"] == (p or "5")), p)
    if k == "volup_ok":
        if not p or p == "none":
            return "低位放量"
        days = next((o["label"] for o in VOLUP_DAY_OPTS if o["k"] == p), p)
        rule = next((o["label"] for o in VOLUP_RULE_OPTS if o["k"] == (d or "std")), "")
        pos = next((o["label"] for o in VOLUP_POS_OPTS if o["k"] == (lp or "30")), "")
        return f"{days}·{rule}·{pos}"
    if k == "industry":
        return ("不属于 " if lp == "not" else "") + str(p)
    if k == "base_ok":
        if not p or p == "none":
            return "只看形态"
        win = next((o["label"] for o in BASE_WIN_OPTS if o["k"] == p), p)
        st = next((o["label"] for o in BASE_IGNITE_OPTS if o["k"] == (d or "std")), "")
        return f"{win} · {st}"
    if k == "box_ok":
        win = next((o["label"] for o in BOX_WIN_OPTS if o["k"] == (p or "90")), p)
        pos = next((o["label"] for o in BOX_POS_OPTS if o["k"] == (lp or "any")), "")
        touch = next((o["label"] for o in BOX_TOUCH_OPTS if o["k"] == (d or "3")), "")
        return f"{win} · {touch} · {pos}"
    opts = spec.get("options") or PERIODS
    return next((o["label"] for o in opts if o["k"] == p), p)


def _limit_frame(conn: sqlite3.Connection):
    """最近 121 个交易日的涨跌幅 / 涨跌停标记（按需加载并缓存 60 秒）。"""
    now = time.time()
    key = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    if (_LIMIT_CACHE["df"] is not None and _LIMIT_CACHE["key"] == key
            and now - _LIMIT_CACHE["t"] < TTL):
        return _LIMIT_CACHE["df"]
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT ?", (LIMIT_MAX_DAYS + 1,))]
    if len(dates) < 2:
        return None
    cutoff = dates[-1]
    b = query._q(conn, "SELECT symbol, date, close FROM bars WHERE date >= ? "
                       "ORDER BY symbol, date", (cutoff,))
    if b.empty:
        return None
    b["pct"] = b.groupby("symbol")["close"].pct_change() * 100.0
    meta = query._q(conn, "SELECT symbol, board, name FROM securities").set_index("symbol")
    b = b.join(meta, on="symbol")
    board = b["board"].fillna("")
    name = b["name"].fillna("")
    limit = np.where(board.isin(["创业板", "科创板"]), 19.8,
                     np.where(board.eq("北交所"), 29.8,
                              np.where(name.str.contains("ST", case=False), 4.8, 9.8)))
    b["lim"] = limit
    b["up"] = b["pct"] >= (b["lim"] - 0.2)
    b["down"] = b["pct"] <= -(b["lim"] - 0.2)
    b["rn"] = b.groupby("symbol").cumcount(ascending=False)   # 0 = 最新一根
    _LIMIT_CACHE.update(t=now, key=key, df=b)
    return b


def limit_mask(conn: sqlite3.Connection, c: dict):
    """返回满足「近 N 日内 ≥ K 次涨停/跌停/涨跌幅」的股票集合；数据不足时返回 None。"""
    b = _limit_frame(conn)
    if b is None:
        return None
    days = int(c.get("days", 10))
    cnt = int(c.get("cnt", 2))
    ev = c.get("ev", "up")
    pct = abs(float(c.get("pct", 5)))
    sub = b[b["rn"] < days]
    if ev == "up":
        hit = sub["up"]
    elif ev == "down":
        hit = sub["down"]
    elif ev == "any":
        hit = sub["up"] | sub["down"]
    elif ev == "chg_up":
        hit = sub["pct"] >= pct
    elif ev == "chg_down":
        hit = sub["pct"] <= -pct
    else:
        hit = sub["up"]
    counts = hit.groupby(sub["symbol"]).sum()
    return set(counts[counts >= cnt].index)


def apply(df: pd.DataFrame, conds: list[dict],
          conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    out = df
    # 行业条件单独处理：多条之间是「或」，且支持「不属于」
    inds = [c for c in conds if c["k"] == "industry"]
    if inds and "industry" in out.columns:
        mask = None
        for c in inds:
            hit = out["industry"].fillna("") == c["p"]
            if c.get("op") == "not":
                hit = ~hit
            mask = hit if mask is None else (mask | hit)
        out = out[mask]
    for c in conds:
        if c["k"] == "industry":
            continue
        k = c["k"]
        if k == "exclude_st":
            if c.get("on"):
                out = out[~out["is_st"]]
            continue
        if k == "limit_days":
            if c.get("on", True) and conn is not None:
                hit = limit_mask(conn, c)
                if hit is not None:
                    out = out[out.index.isin(hit)]
            continue
        if k == "base_ok":
            col_b = _col("base_ok", c.get("p"), None, c.get("d"))
            if c.get("on") and col_b in out.columns:
                out = out[out[col_b] == 1]
            continue
        if k in ("flag_ok", "flag_brk", "box_ok", "w_brk", "w_pend", "volup_ok"):
            col_name = _col(k, c.get("p"), None, c.get("d"), c.get("lp"))
            if c.get("on") and col_name in out.columns:
                out = out[out[col_name] == 1]
            continue
        col_name = _col(k, c.get("p"), c.get("y"))
        if col_name not in out.columns:
            continue
        col = out[col_name]
        if "min" in c:
            out = out[col.notna() & (col >= c["min"])]
            col = out[col_name]
        if "max" in c:
            out = out[col.notna() & (col <= c["max"])]
    return out
