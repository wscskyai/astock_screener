# -*- coding: utf-8 -*-
"""形态选股：做小做底 / 旗形整理。

设计原则（基于 2021-2026 全市场实测）：
  - 做小做底单独用**没有超额**（120 日 −3.5%），定位是「股票池过滤器」，
    要叠加估值/股息/筹码等基本面条件使用。
  - 旗形要严格版才有微弱正超额：极低位 + 旗面不回撤 + 突破确认（5 日 +0.72% vs 基准 +0.54%）。

信号写入 pattern_signal 表，metrics 宽表直接 join，查询时零成本。
"""

from __future__ import annotations

import sqlite3
import threading
import time

import numpy as np
import pandas as pd

import query

# ---------------------------------------------------------------- 参数
BASE = {
    "min_bars": 480,          # 至少 2 年数据（"盘整 2 年以上"）
    "off_low_max": 0.45,      # 现价距「历史最低」≤45%（历史低位，不是区间分位）
    "amp_max": 0.45,          # 近 2 年振幅 ≤45%（窄幅横盘）
    "max_run": 0.28,          # 近 2 年任意 20 日涨幅上限（中间没有大行情）
    "low_age_min": 60,        # 2 年最低点至少在 60 个交易日前（底部守住，不是还在创新低）
    "above_low_min": 0.03,    # 现价高于 2 年最低 3%（排除破位）
    "min_amt": 2e7,           # 20 日均成交额 ≥2000 万（放宽，小盘筑底股普遍不活跃）
}

# 启动信号：底部横盘后放量大阳线（异动）
IGNITE = {
    "mild":   {"chg": 0.03, "vol": 1.5},   # 温和：涨 3% + 量 1.5 倍
    "std":    {"chg": 0.04, "vol": 2.0},   # 标准：涨 4% + 量 2 倍
    "strong": {"chg": None, "vol": 2.0},   # 强势：按板块（主板 6% / 20%板 9.5%）+ 量 2 倍
}
IGNITE_WINDOWS = ["10", "5", "20"]
IGNITE_OPTS = ["std", "mild", "strong"]

# ---------------------------------------------------------------- W 底
W = {
    "pivot": 5,          # 局部低点：前后 5 根 K 线里最低
    "min_gap": 20,       # 两底间隔（交易日）
    "max_gap": 45,
    "tol": 0.04,         # 两底价差容错 |L2-L1|/L1
    "downtrend": 0.15,   # L1 之前 40 根内至少跌 15%
    "neck_min": 0.05,    # 颈线至少比两底高 5%
    "vol_shrink": 0.80,  # 右底 5 日均量 ≤ 左底 ×0.8
    "brk_vol": 1.30,     # 突破日量 ≥ MA10 ×1.3
    "min_bars": 120,
}
W_BRK_WIN = ["5", "1", "3", "10"]    # 突破发生在近 N 日内
W_BRK_RATE = ["3", "0", "5"]         # 收盘需站上颈线 +N%
W_PEND_WIN = ["10", "15", "20"]      # 右底形成于近 N 日内

FLAG = {
    "vol_mult": 2.0,      # 旗杆量 ≥ 前 20 日均量 ×2
    "low_pos": 0.20,      # 旗杆所在位置的区间分位
    "run_up_max": 0.05,   # 旗面最高不超过旗杆收盘 +5%
    "shrink_max": 1.2,    # 旗面量不超过旗杆量 ×1.2
    "drawdown_max": 0.05, # 旗面最大回撤
    "brk_vol": 1.5,       # 突破日量能 ≥ 旗面均量 ×1.5
    "brk_window": 5,      # 突破确认的有效天数（信号日往前）
}

# 旗杆涨幅规则：10% 涨跌停板给 6%，20% 涨跌停板给 9.5%
POLE_RULES = {
    "auto": "按板块（主板 ≥6%、20% 板 ≥9.5%）",
    "6": "统一 ≥6%",
    "95": "统一 ≥9.5%",
}

# 旗面天数区间（可选）
FLAG_RANGES = ["2-5", "2-6", "2-8", "3-5", "3-6", "3-8"]

# 旗杆所在位置（近 250 日区间分位）上限，可选
LOW_POS_OPTS = ["20", "30", "40"]
LOW_POS_LABEL = {"20": "位置 ≤0.20", "30": "位置 ≤0.30", "40": "位置 ≤0.40"}

# ---------------------------------------------------------------- 箱体
BOX = {
    "max_height": 0.25,     # 箱体高度 (高-低)/低 ≤25%
    "touch": 0.03,          # 距上下沿 3% 内算"触碰"
    "touch_gap": 8,         # 两次触碰至少间隔 8 个交易日（连续贴边只算一次）
    "min_cross": 2,         # 至少穿越中轴 2 次（来回震荡）
    "cross_band": 0.30,     # 越过中轴上下 30% 才计一次穿越
    "trend_max": 0.10,      # 后半段均价比前半段偏移 ≤10%（横向而非趋势）
    "min_bars": 60,
}
BOX_WINDOWS = ["60", "90", "120"]
BOX_TOUCH_OPTS = ["2", "3"]
BOX_POS = ["bottom", "mid", "top", "any"]
BOX_POS_LABEL = {"bottom": "箱体底部", "mid": "箱体中部", "top": "箱体顶部",
                 "any": "箱体任意位置"}
BOX_POS_RANGE = {"bottom": (0.0, 0.30), "mid": (0.30, 0.70), "top": (0.70, 1.0),
                 "any": (0.0, 1.0)}


def _touch_count(mask, gap: int) -> int:
    """同一段贴边只算一次：两次触碰间隔 ≥ gap 个交易日。"""
    n, last = 0, -10 ** 9
    for i in np.where(mask)[0]:
        if i - last >= gap:
            n += 1
        last = i
    return n


def _crossings(c, lo: float, hi: float, band: float) -> int:
    """穿越中轴的次数（带迟滞，避免噪音）。"""
    mid = (hi + lo) / 2
    up = mid + band * (hi - mid)
    dn = mid - band * (mid - lo)
    state, n = 0, 0
    for x in c:
        if state != 1 and x >= up:
            state, n = 1, n + 1
        elif state != -1 and x <= dn:
            state, n = -1, n + 1
    return n


def box_signal(code: str, c, h, l, days: int, pos_rule: str,
               min_touch: int = 3) -> dict | None:
    """箱体形态：横向震荡 + 上下沿各被独立触碰 min_touch 次 + 现价在指定档位。"""
    n = len(c)
    if n < max(BOX["min_bars"], days) + 5:
        return None
    wc, wh, wl = c[-days:], h[-days:], l[-days:]
    hi, lo = float(wh.max()), float(wl.min())
    if lo <= 0 or hi <= lo:
        return None
    height = hi / lo - 1
    if height > BOX["max_height"]:
        return None

    # 上下沿各至少 min_touch 次「独立」触碰（连续贴边只算一次）
    top_touch = _touch_count(wh >= hi * (1 - BOX["touch"]), BOX["touch_gap"])
    bot_touch = _touch_count(wl <= lo * (1 + BOX["touch"]), BOX["touch_gap"])
    if top_touch < min_touch or bot_touch < min_touch:
        return None

    # 来回穿越中轴，才算箱体（单边爬升/下跌不算）
    if _crossings(wc, lo, hi, BOX["cross_band"]) < BOX["min_cross"]:
        return None

    # 横向而非趋势
    half = days // 2
    m1, m2 = float(wc[:half].mean()), float(wc[half:].mean())
    if m1 <= 0 or abs(m2 / m1 - 1) > BOX["trend_max"]:
        return None

    pos = (c[-1] - lo) / (hi - lo)
    lo_r, hi_r = BOX_POS_RANGE.get(pos_rule, (0.0, 1.0))
    if lo_r > 0 and not (lo_r < pos <= hi_r):
        return None
    if lo_r == 0 and pos > hi_r:
        return None

    return {"box_high": round(hi, 2), "box_low": round(lo, 2),
            "box_days": days, "box_pos": round(float(pos), 3),
            "box_height": round(float(height), 3),
            "box_top_touch": top_touch, "box_bot_touch": bot_touch}


def box_col(days: str, pos: str, touch: str = "3") -> str:
    return f"box_{days}_{pos}_{touch}"


def box_columns() -> list[str]:
    return [box_col(d, p, t) for d in BOX_WINDOWS for p in BOX_POS for t in BOX_TOUCH_OPTS]


def pole_min_chg(code: str, rule: str) -> float:
    """旗杆最小涨幅。主板（10% 板）6%，创业板/科创板/北交所（20%+ 板）9.5%。"""
    if rule == "6":
        return 0.06
    if rule == "95":
        return 0.095
    c = str(code).zfill(6)
    if c.startswith(("60", "000", "001", "002", "003")):
        return 0.06
    return 0.095


def _rolling_mean(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if len(a) > n:
        cs = np.cumsum(np.insert(a, 0, 0.0))
        out[n:] = (cs[n:] - cs[:-n])[: len(a) - n] / n
    return out


def base_signal(c, h, l, v, hist_low: float | None = None) -> dict:
    """做小做底：长期下跌后在历史低位窄幅横盘、期间没有大行情。

    旧版用「现价在 3 年区间的分位 ≤0.2」判断低位，会把一路阴跌、不断创
    新低的股票也算进来（它们看着"在底部"，其实还在下跌）。现在改成
    三条更贴近形态的规则：
      1. 现价距**历史最低**不超过 45%
      2. 近 **2 年**振幅不超过 45%（真正的横盘）
      3. 近 2 年没有 20 日涨幅超过 28% 的行情，且底部不是最近才创的
    """
    p = BASE
    n = len(c)
    empty = {"base_ok": 0, "base_pos": None, "base_amp": None, "base_max_run": None}
    if n < p["min_bars"]:
        return empty

    low_all = float(hist_low) if hist_low else float(l.min())   # 全历史最低（窗口外也能用）
    off_low = float(c[-1] / low_all - 1)
    w = c[-480:]
    lo, hi = float(w.min()), float(w.max())
    if hi <= lo:
        return empty
    pos2y = float((c[-1] - lo) / (hi - lo))
    amp = float((h[-480:].max() - l[-480:].min()) / l[-480:].min())
    max_run = float((w[20:] / w[:-20] - 1).max())
    low_age = 479 - int(np.argmin(l[-480:]))          # 2 年最低点距今多少根
    above_low = float(c[-1] / lo - 1)
    amt20 = float(np.mean(c[-20:] * v[-20:] * 100))

    ok = (off_low <= p["off_low_max"]
          and amp <= p["amp_max"]
          and max_run <= p["max_run"]
          and low_age >= p["low_age_min"]
          and above_low >= p["above_low_min"]
          and amt20 >= p["min_amt"])
    return {"base_ok": int(ok), "base_pos": round(pos2y, 3),
            "base_amp": round(amp, 3), "base_max_run": round(max_run, 3)}


def _flag_for_pole(c, o, h, l, v, i, ma20v, rule_chg, days_now):
    """检查某个旗杆之后到今天是否构成有效旗面，返回 (有效最大天数, 明细)。"""
    p = FLAG
    if i < 30 or ma20v[i] != ma20v[i]:      # nan 检查
        return 0, None
    if c[i] / c[i - 1] - 1 < rule_chg:
        return 0, None
    if v[i] < p["vol_mult"] * ma20v[i]:
        return 0, None
    w = c[max(0, i - 250):i + 1]
    lo, hi = float(w.min()), float(w.max())
    if hi <= lo:
        return 0, None
    pos = (c[i] - lo) / (hi - lo)

    top = c[i]
    floor_ = min((o[i] + c[i]) / 2, o[i])
    max_days = min(8, days_now)
    valid = 0
    for t in range(1, max_days + 1):
        j = i + t
        if h[j] > top * (1 + p["run_up_max"]):
            break
        if v[j] > v[i] * p["shrink_max"]:
            break
        if c[j] < floor_:
            break
        dd = (top - float(c[i + 1:j + 1].min())) / top
        if dd > p["drawdown_max"]:
            break
        valid = t
    info = {"pole_date": None, "pole_chg": round(float(c[i] / c[i - 1] - 1) * 100, 2),
            "vol_mult": round(float(v[i] / ma20v[i]), 2), "pos": round(pos, 3),
            "days": days_now, "max_valid": valid, "idx": i}
    return valid, info


def _pivots(low, k: int = 5):
    """局部低点（前后 k 根里最低）。"""
    win = np.lib.stride_tricks.sliding_window_view(low, 2 * k + 1)
    return np.where(win[:, k] == win.min(axis=1))[0] + k


def w_signal(code: str, o, h, l, c, v, mode: str, recent: int,
             rate: float = 0.03) -> dict | None:
    """W 底。mode="brk" 突破确认；mode="pend" 右底埋伏。

    只检查最近一段的局部低点，配对数量有限（pivots 可能上百个，
    全量两两配对会慢到卡住请求）。
    """
    n = len(c)
    if n < W["min_bars"]:
        return None
    piv = _pivots(l, W["pivot"])
    if len(piv) < 2:
        return None
    ma10 = _rolling_mean(v, 10)
    last = n - 1

    # 右底必须够新：突破模式要留出「突破日 + recent」的余量
    span = W["max_gap"] + max(recent, 10)
    cand2 = piv[piv >= last - span]
    for i2 in cand2[::-1]:
        if mode == "pend" and last - i2 > recent:
            continue
        left = piv[(piv >= i2 - W["max_gap"]) & (piv <= i2 - W["min_gap"])]
        for i1 in left[::-1]:
            l1, l2 = float(l[i1]), float(l[i2])
            if l1 <= 0 or abs(l2 - l1) / l1 > W["tol"]:
                continue
            seg_h = h[i1:i2 + 1]
            H = float(seg_h.max())
            if H <= 0 or H / l1 - 1 < W["neck_min"] or H / l2 - 1 < W["neck_min"]:
                continue
            if i1 < 40:
                continue
            pre_high = float(h[i1 - 40:i1].max())
            if pre_high <= 0 or (pre_high - l1) / pre_high < W["downtrend"]:
                continue
            v1 = float(v[max(0, i1 - 2):i1 + 3].mean())
            v2 = float(v[max(0, i2 - 2):i2 + 3].mean())
            if v1 <= 0 or v2 > v1 * W["vol_shrink"]:
                continue

            base = {"l1": round(l1, 2), "l2": round(l2, 2), "neck": round(H, 2),
                    "i1": int(i1), "i2": int(i2), "gap": int(i2 - i1)}

            if mode == "pend":
                if c[-1] > H * (1 + rate):
                    continue
                return base

            if i2 + 1 >= n:
                continue
            seg_c, seg_v, seg_ma = c[i2 + 1:], v[i2 + 1:], ma10[i2 + 1:]
            cond = (seg_c > H * (1 + rate)) & (seg_v > seg_ma * W["brk_vol"]) \
                & np.isfinite(seg_ma)
            if not cond.any():
                continue
            brk = i2 + 1 + int(np.argmax(cond))
            if last - brk > recent or c[-1] < H:
                continue
            base.update({"brk_idx": int(brk),
                         "brk_chg": round(float(c[brk] / H - 1) * 100, 2)})
            return base
    return None


def w_columns() -> list[str]:
    return ([f"w_brk_{w}_{r}" for w in W_BRK_WIN for r in W_BRK_RATE]
            + [f"w_pend_{w}" for w in W_PEND_WIN])


def _ignition(code: str, c, o, h, v, ma20v, days: int, strength: str):
    """近 days 日内是否出现放量大阳线（从最近往前找，返回最近一次）。"""
    conf = IGNITE.get(strength, IGNITE["std"])
    thr = conf["chg"] if conf["chg"] is not None else pole_min_chg(code, "auto")
    n = len(c)
    best = None
    for t in range(max(21, n - days), n):
        if ma20v[t] != ma20v[t]:
            continue
        chg = c[t] / c[t - 1] - 1
        if chg < thr:
            continue
        if v[t] < conf["vol"] * ma20v[t]:
            continue
        best = {"chg": round(float(chg) * 100, 2),
                "vol_mult": round(float(v[t] / ma20v[t]), 2), "idx": t}
    return best


def base_variant(code: str, c, o, h, l, v, window: str, strength: str,
                 hist_low: float | None = None) -> dict | None:
    """做小做底 + 近 window 日内出现启动（放量大阳线）。"""
    b = base_signal(c, h, l, v, hist_low)
    if not b["base_ok"]:
        return None
    ma20v = _rolling_mean(v, 20)
    ign = _ignition(code, c, o, h, v, ma20v, int(window), strength)
    if not ign:
        return None
    return {"base_pos": b["base_pos"], "base_amp": b["base_amp"],
            "base_max_run": b["base_max_run"],
            "ign_chg": ign["chg"], "ign_vol": ign["vol_mult"], "ign_idx": ign["idx"]}


def base_col(window: str, strength: str) -> str:
    return f"base_{window}_{strength}"


def base_columns() -> list[str]:
    return [base_col(w, t) for w in IGNITE_WINDOWS for t in IGNITE_OPTS]


def flag_signal(code: str, c, o, h, l, v) -> dict:
    """返回各规则/各天数区间下的旗形结果。

    结构：
      {"flag": {"auto|2-5": 1, ...},        # 18 个组合
       "brk":  {"auto": 0, "6": 0, "95": 0},  # 突破确认（旗面 2~8 天）
       "info": {...}}                        # 最好那个旗杆的明细
    """
    p = FLAG
    n = len(c)
    out = {"flag": {}, "brk": {}, "info": {}}
    if n < 70:
        return out

    ma20v = _rolling_mean(v, 20)
    best = None
    for rule in POLE_RULES:
        thr = pole_min_chg(code, rule)
        for age in range(2, 9):
            i = n - 1 - age
            if i < 30:
                continue
            valid, info = _flag_for_pole(c, o, h, l, v, i, ma20v, thr, age)
            if not valid:
                continue
            for lp in LOW_POS_OPTS:
                if info["pos"] > int(lp) / 100.0:
                    continue
                for rng in FLAG_RANGES:
                    lo_d, hi_d = (int(x) for x in rng.split("-"))
                    if lo_d <= age <= hi_d and age <= valid:
                        out["flag"][f"{rule}|{rng}|{lp}"] = 1
            if best is None or info["max_valid"] > best["max_valid"]:
                best = info

    if best:
        out["info"] = {"flag_ok": 1, "flag_pole_date": None, "flag_pole_chg": best["pole_chg"],
                       "flag_vol_mult": best["vol_mult"], "flag_days": best["days"],
                       "flag_pos": best["pos"], "_pole_idx": best["idx"]}

    # 突破确认：旗面走完（2~8 天）后，最近 5 个交易日内放量突破旗面高点
    for rule in POLE_RULES:
        thr = pole_min_chg(code, rule)
        for i in range(max(30, n - 20), n - 2):
            if ma20v[i] != ma20v[i] or c[i] / c[i - 1] - 1 < thr:
                continue
            if v[i] < p["vol_mult"] * ma20v[i]:
                continue
            w = c[max(0, i - 250):i + 1]
            lo, hi = float(w.min()), float(w.max())
            if hi <= lo or (c[i] - lo) / (hi - lo) > 0.20:
                continue
            top = c[i]
            floor_ = min((o[i] + c[i]) / 2, o[i])
            flag_high, vols = -np.inf, []
            for t in range(i + 1, min(i + 1 + 8, n)):
                if h[t] > top * (1 + p["run_up_max"]) or v[t] > v[i] * p["shrink_max"] \
                        or c[t] < floor_:
                    break
                flag_high = max(flag_high, float(h[t]))
                vols.append(float(v[t]))
            if len(vols) < 2:
                continue
            ave = float(np.mean(vols))
            for t in range(i + 1 + len(vols), n):
                if c[t] > flag_high and v[t] > p["brk_vol"] * ave and n - 1 - t <= p["brk_window"]:
                    out["brk"][rule] = 1
                    if not out["info"]:
                        out["info"] = {"flag_ok": 0, "flag_pole_date": None,
                                       "flag_pole_chg": round(float(c[i] / c[i - 1] - 1) * 100, 2),
                                       "flag_vol_mult": round(float(v[i] / ma20v[i]), 2),
                                       "flag_days": len(vols),
                                       "flag_pos": round(float((c[i] - lo) / (hi - lo)), 3),
                                       "_pole_idx": i}
                    break
    return out


def flag_columns() -> list[str]:
    cols = []
    for rule in POLE_RULES:
        for rng in FLAG_RANGES:
            for lp in LOW_POS_OPTS:
                cols.append(f"flag_{rule}_{rng.replace('-', '_')}_{lp}")
    cols += [f"brk_{rule}" for rule in POLE_RULES]
    return cols


def build_all(conn: sqlite3.Connection, progress=None) -> dict:
    """全市场扫一遍，写 pattern_signal 表。"""
    t0 = time.time()
    df = query._q(conn, "SELECT symbol, date, open, high, low, close, volume FROM bars "
                        "ORDER BY symbol, date")
    rows = []
    total = df["symbol"].nunique()
    for i, (sym, g) in enumerate(df.groupby("symbol", sort=False), 1):
        c = g["close"].to_numpy(float)
        o = g["open"].to_numpy(float)
        h = g["high"].to_numpy(float)
        l = g["low"].to_numpy(float)
        v = g["volume"].to_numpy(float)
        rec = {"symbol": sym, "date": str(g["date"].iloc[-1])}
        rec.update(base_signal(c, h, l, v))

        f = flag_signal(sym, c, o, h, l, v)
        for rule in POLE_RULES:
            for rng in FLAG_RANGES:
                for lp in LOW_POS_OPTS:
                    key = f"{rule}|{rng}|{lp}"
                    rec[f"flag_{rule}_{rng.replace('-', '_')}_{lp}"] = f["flag"].get(key, 0)
            rec[f"brk_{rule}"] = f["brk"].get(rule, 0)
        info = dict(f["info"])
        pole = info.pop("_pole_idx", None)
        info["flag_pole_date"] = str(g["date"].iloc[pole]) if pole is not None else None
        rec.update({k: info.get(k) for k in ("flag_ok", "flag_pole_date", "flag_pole_chg",
                                             "flag_vol_mult", "flag_days", "flag_pos")})
        rows.append(rec)
        if progress and i % 200 == 0:
            progress(i, total, f"形态扫描 {i}/{total}")

    out = pd.DataFrame(rows)
    cols = (["symbol", "date", "base_ok", "base_pos", "base_amp", "base_max_run"]
            + flag_columns()
            + ["flag_ok", "flag_pole_date", "flag_pole_chg", "flag_vol_mult", "flag_days", "flag_pos"])
    out = out.reindex(columns=cols)
    for col in cols:
        if col not in ("symbol", "date", "flag_pole_date"):
            out[col] = out[col].fillna(0)

    conn.execute("DELETE FROM pattern_signal")
    conn.executemany(
        f"INSERT INTO pattern_signal({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        out.astype(object).where(pd.notna(out), None).values.tolist(),
    )
    conn.commit()
    import db as _db
    _db.set_meta(conn, "sync_patterns_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    conn.commit()
    stats = {
        "symbols": int(len(out)),
        "base_ok": int(out["base_ok"].sum()),
        "flag_ok": int((out["flag_auto_3_5_20"] == 1).sum()),
        "flag_brk": int(out["brk_auto"].sum()),
        "elapsed": round(time.time() - t0, 1),
    }
    return stats


# ---------------------------------------------------------------- 按需计算
# ---------------------------------------------------------------- 低位放量
VOLUP = {
    "pos_q": 0.30,        # 250 日价格分位上限（参数 lp）
    "drawdown": -0.25,    # 距 250 日最高收盘的回撤
    "sustain": 1.2,       # vol_ma5 > vol_ma20 × 1.2
    "chg_min": -0.03,     # 放量段累计涨幅下限
    "chg_max": 0.15,      # 上限（低位放量不该已经大涨）
    "amt_min": 5e7,       # 近 20 日日均成交额 ≥ 5000 万
    "turn_min": 1.0,      # 放量段日均换手率下限 %
    "turn_max": 10.0,     # 上限 %
    "max_spike": 5.0,     # 单日量 > 5×均量 视为异常（对倒/一日游）
    "min_bars": 260,
    "scan": 5,            # 只在最近 5 个交易日里找连续放量段
}
VOLUP_DAYS = ["3", "4", "5"]
VOLUP_DAYS_LABEL = {"3": "连续3日", "4": "连续4日", "5": "连续5日"}
VOLUP_RULES = {"mild": (1.2, 1.5), "std": (1.5, 1.8), "strong": (1.8, 2.5)}
VOLUP_RULE_OPTS = [
    {"k": "mild", "label": "温和 1.2×/1.5×"},
    {"k": "std", "label": "标准 1.5×/1.8×"},
    {"k": "strong", "label": "强势 1.8×/2.5×"},
]
VOLUP_POS = ["30", "40", "50"]
VOLUP_POS_OPTS = [
    {"k": "30", "label": "分位≤30%"},
    {"k": "40", "label": "分位≤40%"},
    {"k": "50", "label": "分位≤50%"},
]

_META = {"date": None, "data": None}


def _meta(conn):
    """证券静态信息（名称/上市天数/流通市值），按最新交易日缓存。"""
    day = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    if _META["date"] == day and _META["data"] is not None:
        return _META["data"]
    rows = conn.execute(
        "SELECT symbol, name, list_date, float_mktcap FROM securities").fetchall()
    today = pd.Timestamp(day)
    out = {}
    for sym, name, ld, fmv in rows:
        listed = None
        if ld:
            try:
                listed = int((today - pd.Timestamp(str(ld))).days)
            except (ValueError, TypeError):
                listed = None
        out[sym] = {"name": name or "", "list_days": listed, "float_mv": fmv}
    _META.update(date=day, data=out)
    return out


def _prev_mean(a, n: int):
    """前 n 日均值（不含当日）：当日量对比「之前」的均量。"""
    cs = np.concatenate([[0.0], np.cumsum(a, dtype="float64")])
    out = np.full(a.size, np.nan, dtype="float64")
    idx = np.arange(a.size)
    ok = idx >= n
    out[ok] = (cs[idx[ok]] - cs[idx[ok] - n]) / n
    return out


def limit_ratio(code: str) -> float:
    """涨停幅度：主板 10%，创业板/科创板 20%，北交所 30%。"""
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("43", "83", "87", "92")):
        return 0.30
    return 0.10


def volup_signal(code: str, o, h, l, c, v, days: int = 3, rule: str = "std",
                 pos_q: float = 0.30, meta: dict | None = None) -> dict | None:
    """低位放量：低位 + 连续 days 日放量 + 量价配合 + 过滤无效放量。"""
    n = c.size
    if n < VOLUP["min_bars"]:
        return None
    if meta:
        if "ST" in (meta.get("name") or "").upper():
            return None
        ld = meta.get("list_days")
        if ld is not None and ld < 120:
            return None

    base, strong = VOLUP_RULES.get(rule, VOLUP_RULES["std"])
    win = 250
    hi = float(np.max(h[-win:]))
    lo = float(np.min(l[-win:]))
    if hi <= lo:
        return None
    price = float(c[-1])
    bound = (price - lo) / (hi - lo)
    if bound > pos_q:
        return None
    if price / float(np.max(c[-win:])) - 1 > VOLUP["drawdown"]:
        return None

    vma20 = _prev_mean(v, 20)
    run = None
    for end in range(n - 1, max(n - 1 - VOLUP["scan"], 0) - 1, -1):
        start = end - days + 1
        if start < 1:
            break
        seg = slice(start, end + 1)
        ratios = v[seg] / vma20[seg]
        if np.any(~np.isfinite(ratios)):
            continue
        if not np.all(ratios > base):
            continue
        if np.any(ratios > VOLUP["max_spike"]):
            continue
        if int(np.sum(ratios > strong)) < 2:
            continue
        if np.any(h[seg] <= l[seg]):
            continue
        run = (start, end)
        break
    if run is None:
        return None

    start, end = run
    seg = slice(start, end + 1)
    up_days = int(np.sum(c[seg] > o[seg]))
    if up_days < 2:
        return None
    prev_close = float(c[start - 1])
    chg_sum = price / prev_close - 1
    if chg_sum < VOLUP["chg_min"] or chg_sum > VOLUP["chg_max"]:
        return None

    m20_now = float(vma20[-1]) if np.isfinite(vma20[-1]) else float(np.mean(v[-21:-1]))
    m5_now = float(np.mean(v[-5:]))
    m5_prev = float(np.mean(v[-10:-5]))
    if m20_now <= 0 or m5_now < VOLUP["sustain"] * m20_now:
        return None
    if m5_now < m5_prev:
        return None

    amt20 = float(np.mean(c[-20:] * v[-20:] * 100.0))
    if amt20 < VOLUP["amt_min"]:
        return None

    turn = None
    if meta and meta.get("float_mv"):
        float_shares = float(meta["float_mv"]) * 1e8 / price
        if float_shares > 0:
            turn = float(np.mean(v[seg])) * 100.0 / float_shares * 100.0
            if turn < VOLUP["turn_min"] or turn > VOLUP["turn_max"]:
                return None

    limit_up = round(prev_close * (1 + limit_ratio(code)), 2)
    if price >= limit_up * 0.995:
        return None

    return {
        "volup_pos": round(bound, 4),
        "volup_days_run": days,
        "volup_vr": round(m5_now / m20_now, 2),
        "volup_amt": round(amt20 / 1e8, 2),
        "volup_turn": round(turn, 2) if turn is not None else None,
        "volup_chg": round(chg_sum * 100, 2),
        "volup_up_days": up_days,
        "volup_fresh": int(n - 1 - end),
        "run_start": start,
        "run_end": end,
    }


def volup_col(days: str, rule: str, pos: str) -> str:
    return f"volup_{days}_{rule}_{pos}"


def volup_columns() -> list[str]:
    return [volup_col(d, r, lp) for d in VOLUP_DAYS for r in VOLUP_RULES for lp in VOLUP_POS]


_BARS = {"date": None, "data": None}


BARS_WINDOW = 520          # 形态最多用到 260 根，留一倍余量
_BARS_LOCK = threading.Lock()
_ONE = {}                  # 单只票缓存：{(symbol, day): arrays}
_ONE_MAX = 600


_HIST = {"date": None, "low": {}}


def _hist_low(conn):
    """每只票的全历史最低价（一个 SQL，几百万行的 MIN，约 1 秒）。"""
    day = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    if _HIST["date"] == day and _HIST["low"]:
        return _HIST["low"]
    rows = conn.execute("SELECT symbol, MIN(low) FROM bars GROUP BY symbol").fetchall()
    _HIST.update(date=day, low={s: v for s, v in rows if v is not None})
    return _HIST["low"]


def _bars(conn):
    """把最近 BARS_WINDOW 个交易日的日线读进内存（按最新交易日缓存）。"""
    day = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    if _BARS["date"] == day and _BARS["data"] is not None:
        return day, _BARS["data"]
    with _BARS_LOCK:                        # 并发请求时只读一遍
        if _BARS["date"] == day and _BARS["data"] is not None:
            return day, _BARS["data"]
        cutoff = conn.execute(
            "SELECT MIN(date) FROM (SELECT DISTINCT date FROM bars "
            "ORDER BY date DESC LIMIT ?)", (BARS_WINDOW,)).fetchone()[0]
        df = query._q(conn, "SELECT symbol, date, open, high, low, close, volume FROM bars "
                            "WHERE date >= ? ORDER BY symbol, date", (cutoff,))
        data = {}
        for sym, g in df.groupby("symbol", sort=False):
            data[sym] = (g["open"].to_numpy("float32"), g["high"].to_numpy("float32"),
                         g["low"].to_numpy("float32"), g["close"].to_numpy("float32"),
                         g["volume"].to_numpy("float32"),
                         g["date"].to_numpy("datetime64[D]"))
        _BARS.update(date=day, data=data)
        _ONE.clear()
        return day, data


def _bars_one(conn, symbol: str):
    """只读一只票的日线（K 线弹窗画标记用，别再拖全市场进来）。"""
    day = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    key = (symbol, day)
    hit = _ONE.get(key)
    if hit is not None:
        return hit
    df = query._q(conn, "SELECT date, open, high, low, close, volume FROM bars "
                        "WHERE symbol=? ORDER BY date", (symbol,))
    if df.empty:
        return None
    arr = (df["open"].to_numpy("float32"), df["high"].to_numpy("float32"),
           df["low"].to_numpy("float32"), df["close"].to_numpy("float32"),
           df["volume"].to_numpy("float32"), df["date"].to_numpy("datetime64[D]"))
    if len(_ONE) > _ONE_MAX:
        _ONE.clear()
    _ONE[key] = arr
    return arr


def refresh_symbol(conn, symbol: str) -> bool:
    """单只票同步后，只更新这只票的内存 K 线；日期没变就不整份重读。"""
    day = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    if _BARS["date"] != day or _BARS["data"] is None:
        _bars(conn)                     # 换交易日了，本来就要整份重读
        return True
    data = _BARS["data"]
    df = query._q(conn, "SELECT date, open, high, low, close, volume FROM bars "
                        "WHERE symbol=? ORDER BY date", (symbol,))
    if df.empty:
        data.pop(symbol, None)
        return False
    data[symbol] = (df["open"].to_numpy("float32"), df["high"].to_numpy("float32"),
                    df["low"].to_numpy("float32"), df["close"].to_numpy("float32"),
                    df["volume"].to_numpy("float32"),
                    pd.to_datetime(df["date"]).to_numpy("datetime64[D]"))
    return True


def variant_col(kind: str, p: str | None = None, d: str | None = None,
                lp: str | None = None) -> str:
    if kind == "flag":
        return f"flag_{p or 'auto'}_{(d or '2-8').replace('-', '_')}_{lp or '20'}"
    if kind == "brk":
        return f"brk_{p or 'auto'}"
    if kind == "volup":
        return volup_col(p or "3", d or "std", lp or "30")
    if kind == "base":
        return base_col(p, d) if p else "base_ok"
    return "base_ok"


def _spec_of(c: dict) -> tuple[str, str, str | None, str | None, str | None]:
    """条件 → (kind, 列名, 旗杆规则, 天数区间, 位置)"""
    if c["k"] == "flag_ok":
        p, d, lp = c.get("p") or "auto", c.get("d") or "2-8", c.get("lp") or "20"
        return "flag", variant_col("flag", p, d, lp), p, d, lp
    if c["k"] == "flag_brk":
        p = c.get("p") or "auto"
        return "brk", variant_col("brk", p), p, None, None
    if c["k"] == "box_ok":
        days, pos = c.get("p") or "90", c.get("lp") or "any"
        touch = c.get("d") or "3"
        return "box", box_col(days, pos, touch), days, touch, pos
    if c["k"] == "w_brk":
        win, rate = c.get("p") or "5", c.get("d") or "3"
        return "wbrk", f"w_brk_{win}_{rate}", win, rate, None
    if c["k"] == "w_pend":
        win = c.get("p") or "5"
        return "wpend", f"w_pend_{win}", win, None, None
    if c["k"] == "volup_ok":
        days, rule, pos = c.get("p") or "3", c.get("d") or "std", c.get("lp") or "30"
        return "volup", volup_col(days, rule, pos), days, rule, pos
    w, t = c.get("p") or "none", c.get("d") or "std"
    if w in IGNITE_WINDOWS:
        return "base", base_col(w, t), w, t, None
    return "base", "base_ok", None, None, None


def _computing(conn, kind, bars, p, d, lp):
    """算一个组合，返回 {symbol: info}"""
    hit = {}
    if kind == "base":
        if p in IGNITE_WINDOWS:
            hlow = _hist_low(conn)
            for sym, (o, h, l, c, v, dt) in bars.items():
                r = base_variant(sym, c, o, h, l, v, p, d or "std", hlow.get(sym))
                if r:
                    r["ign_date"] = str(dt[r.pop("ign_idx")]) if r.get("ign_idx") is not None else None
                    hit[sym] = r
            return hit
        hlow = _hist_low(conn)
        for sym, (o, h, l, c, v, _dt) in bars.items():
            r = base_signal(c, h, l, v, hlow.get(sym))
            if r["base_ok"]:
                hit[sym] = r
        return hit

    if kind in ("wbrk", "wpend"):
        mode = "brk" if kind == "wbrk" else "pend"
        rate = (float(d) / 100.0) if d else 0.03
        for sym, (o, h, l, c, v, dt) in bars.items():
            r = w_signal(sym, o, h, l, c, v, mode, int(p), rate)
            if r:
                r["l1_date"] = str(dt[r["i1"]])
                r["l2_date"] = str(dt[r["i2"]])
                if "brk_idx" in r:
                    r["brk_date"] = str(dt[r.pop("brk_idx")])
                hit[sym] = r
        return hit

    if kind == "flag":
        for sym, (o, h, l, c, v, dt) in bars.items():
            res = flag_signal(sym, c, o, h, l, v)
            if res["flag"].get(f"{p}|{d}|{lp}"):
                info = dict(res.get("info") or {})
                idx = info.pop("_pole_idx", None)
                if idx is not None and idx < len(dt):
                    info["pole_date"] = str(dt[idx])
                hit[sym] = info
        return hit

    if kind == "volup":
        meta = _meta(conn)
        for sym, (o, h, l, c, v, dt) in bars.items():
            r = volup_signal(sym, o, h, l, c, v, int(p or 3), d or "std",
                             int(lp or 30) / 100.0, meta.get(sym))
            if r:
                r["run_end_date"] = str(dt[r["run_end"]])
                hit[sym] = r
        return hit

    if kind == "box":
        for sym, (o, h, l, c, v, dt) in bars.items():
            r = box_signal(sym, c, h, l, int(p), lp, int(d))
            if r:
                hit[sym] = r
        return hit

    # brk
    for sym, (o, h, l, c, v, dt) in bars.items():
        res = flag_signal(sym, c, o, h, l, v)
        if res["brk"].get(p):
            hit[sym] = res.get("info") or {}
    return hit


def ensure_variants(conn, conds, progress=None) -> list[str]:
    """只补算条件里用到的组合；当天算过就直接用缓存。"""
    wanted = []
    for c in conds or []:
        if c.get("k") in ("flag_ok", "flag_brk", "base_ok", "box_ok",
                          "w_brk", "w_pend", "volup_ok") and c.get("on"):
            kind, col, p, d, lp = _spec_of(c)
            wanted.append((kind, col, p, d, lp))
    if not wanted:
        return []

    day, bars = _bars(conn)
    done = {r[0] for r in conn.execute(
        "SELECT k FROM meta WHERE k LIKE 'pat:%'").fetchall()}
    built = []
    for kind, col, p, d, lp in wanted:
        mark = f"pat:{day}:{col}"
        if mark in done:
            continue
        if progress:
            progress(f"计算形态：{col}")
        hit = _computing(conn, kind, bars, p, d, lp)
        _write_variant(conn, day, kind, col, hit)
        import db as _db
        _db.set_meta(conn, mark, "1")
        conn.commit()
        built.append(col)
    return built


def _write_variant(conn, day: str, kind: str, col: str, hit: dict) -> None:
    """把结果写进 pattern_signal（先清零该列，再标记命中的）。"""
    if kind == "base":
        if col == "base_ok":
            conn.execute("UPDATE pattern_signal SET base_ok=0")
            conn.executemany(
                "UPDATE pattern_signal SET base_ok=1, base_pos=?, base_amp=?, base_max_run=? "
                "WHERE symbol=?",
                [(h["base_pos"], h["base_amp"], h["base_max_run"], s) for s, h in hit.items()])
            return
        # 变体只写自己的列：base_ok 是「只看形态」用的，清零会让它被收窄成
        # 这次变体的命中集（变体 ⊂ 形态，所以只做 OR 置位是安全的）。
        conn.execute(f"UPDATE pattern_signal SET {col}=0")
        conn.executemany(
            f"UPDATE pattern_signal SET {col}=1, base_ok=1, base_pos=?, base_amp=?, "
            "base_max_run=?, base_ignite_date=?, base_ignite_chg=?, base_ignite_vol=? "
            "WHERE symbol=?",
            [(h["base_pos"], h["base_amp"], h["base_max_run"], h.get("ign_date"),
              h.get("ign_chg"), h.get("ign_vol"), s) for s, h in hit.items()])
        return
    conn.execute(f"UPDATE pattern_signal SET {col}=0")
    if not hit:
        return
    if kind in ("wbrk", "wpend"):
        conn.execute(f"UPDATE pattern_signal SET {col}=0")
        conn.executemany(
            f"UPDATE pattern_signal SET {col}=1, w_l1=?, w_l2=?, w_neck=?, "
            "w_l1_date=?, w_l2_date=?, w_brk_date=? WHERE symbol=?",
            [(h["l1"], h["l2"], h["neck"], h.get("l1_date"), h.get("l2_date"),
              h.get("brk_date"), s) for s, h in hit.items()])
        return

    if kind == "volup":
        conn.execute(f"UPDATE pattern_signal SET {col}=0")   # 只清自己的变体列
        conn.executemany(
            f"UPDATE pattern_signal SET {col}=1, volup_ok=1, volup_pos=?, volup_days_run=?, "
            "volup_vr=?, volup_amt=?, volup_turn=?, volup_chg=?, volup_up_days=? "
            "WHERE symbol=?",
            [(h["volup_pos"], h["volup_days_run"], h["volup_vr"], h["volup_amt"],
              h.get("volup_turn"), h["volup_chg"], h.get("volup_up_days"), s)
             for s, h in hit.items()])
        conn.commit()
        return

    if kind == "box":
        conn.execute("UPDATE pattern_signal SET box_ok=0")
        conn.executemany(
            f"UPDATE pattern_signal SET {col}=1, box_ok=1, box_high=?, box_low=?, "
            "box_days=?, box_pos=?, box_height=?, box_top_touch=?, box_bot_touch=? "
            "WHERE symbol=?",
            [(h["box_high"], h["box_low"], h["box_days"], h["box_pos"], h["box_height"],
              h.get("box_top_touch"), h.get("box_bot_touch"), s) for s, h in hit.items()])
        conn.commit()
        return

    if kind == "flag":
        conn.execute("UPDATE pattern_signal SET flag_ok=0")
        conn.executemany(
            f"UPDATE pattern_signal SET {col}=1, flag_ok=1, flag_days=?, flag_pole_chg=?, "
            "flag_vol_mult=?, flag_pos=?, flag_pole_date=? WHERE symbol=?",
            [(h.get("flag_days") or h.get("days"),
              h.get("flag_pole_chg") or h.get("pole_chg"),
              h.get("flag_vol_mult") or h.get("vol_mult"),
              h.get("flag_pos") or h.get("pos"),
              h.get("pole_date") or h.get("flag_pole_date"), s)
             for s, h in hit.items()])
    else:
        conn.executemany(f"UPDATE pattern_signal SET {col}=1 WHERE symbol=?",
                         [(s,) for s in hit])
    conn.commit()


def variant_status(conn) -> dict:
    """当天已算出哪些组合（面板展示用）。"""
    day = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    rows = conn.execute("SELECT k FROM meta WHERE k LIKE ?", (f"pat:{day}:%",)).fetchall()
    return {"date": day, "cached": len(rows),
            "total": len(POLE_RULES) * len(FLAG_RANGES) * len(LOW_POS_OPTS)
                     + len(POLE_RULES)
                     + len(BOX_WINDOWS) * len(BOX_POS) * len(BOX_TOUCH_OPTS)
                     + len(IGNITE_WINDOWS) * len(IGNITE_OPTS)
                     + len(W_BRK_WIN) * len(W_BRK_RATE) + len(W_PEND_WIN)
                     + len(VOLUP_DAYS) * len(VOLUP_RULES) * len(VOLUP_POS) + 1}


def clear_cache(conn) -> int:
    """清掉当天的组合缓存，下次查询会重新算。"""
    n = conn.execute("DELETE FROM meta WHERE k LIKE 'pat:%'").rowcount
    conn.commit()
    return n
