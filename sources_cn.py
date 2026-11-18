# -*- coding: utf-8 -*-
"""A 股数据源适配层。

行情：腾讯（主，支持前复权与分页）→ 东财（备）
股东户数 / 财务：东方财富数据中心
交易日历：以上证指数 sh000001 的交易日为准
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
TX_HDR = {"User-Agent": UA, "Referer": "https://gu.qq.com/"}
DC_HDR = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}
LIST_HDR = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}

TX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,{start},{end},{n},qfq"
EM_LIST = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
           "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f13,f14")
EM_HOLDER = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_HOLDERNUM_DET"
             "&columns=ALL&filter=(SECURITY_CODE%3D%22{code}%22)&pageNumber=1&pageSize=16"
             "&sortColumns=END_DATE&sortTypes=-1")
EM_FIN = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD"
          "&columns=ALL&filter=(SECURITY_CODE%3D%22{code}%22)&pageNumber=1&pageSize=16"
          "&sortColumns=REPORTDATE&sortTypes=-1")


# ---------------------------------------------------------------- 基础设施
def get(url: str, headers: dict, timeout: int = 20, retries: int = 3) -> str:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4 * (2 ** i))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def tx_symbol(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "90", "51", "58")):
        return "sh" + c
    if c.startswith(("00", "30", "20", "15", "16")):
        return "sz" + c
    if c.startswith(("8", "4", "9")):
        return "bj" + c
    return "sz" + c


def board_of(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith("68"):
        return "科创板"
    if c.startswith("30"):
        return "创业板"
    if c.startswith("60"):
        return "沪主板"
    if c.startswith("00"):
        return "深主板"
    if c.startswith(("8", "4", "92")):
        return "北交所"
    return "其他"


def _d(s: str) -> str:
    return str(s)[:10]


# ---------------------------------------------------------------- 股票列表
SINA_LIST = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page={pn}&num=100&sort=symbol&asc=1&node=hs_a")
CACHE_UNIVERSE = ("../vol_shrink_screener/data/universe_20500101.pkl.gz")


def _mk(code: str, name: str, market: str) -> dict:
    return {"symbol": code, "name": name.strip(), "market": market, "board": board_of(code)}


def _universe_em() -> list[dict]:
    first = json.loads(get(EM_LIST.format(pn=1), LIST_HDR, timeout=8, retries=1))
    total = first["data"]["total"]
    rows = list(first["data"]["diff"])
    for pn in range(2, (total + 99) // 100 + 1):
        d = json.loads(get(EM_LIST.format(pn=pn), LIST_HDR, timeout=8, retries=1))["data"]
        if d and d.get("diff"):
            rows.extend(d["diff"])
    out, seen = [], set()
    for r in rows:
        code = str(r["f12"]).zfill(6)
        if code in seen:
            continue
        seen.add(code)
        out.append(_mk(code, str(r["f14"]), "SH" if int(r["f13"]) == 1 else "SZ"))
    return out


def _universe_sina() -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor

    def page(pn: int) -> list[dict]:
        try:
            txt = get(SINA_LIST.format(pn=pn), {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"},
                      timeout=10, retries=2)
            arr = json.loads(txt) if txt.strip().startswith("[") else []
        except Exception:  # noqa: BLE001
            return []
        out = []
        for r in arr:
            sym = str(r.get("symbol", ""))
            code = str(r.get("code", "")).zfill(6)
            if len(code) != 6:
                continue
            mkt = "SH" if sym.startswith("sh") else ("SZ" if sym.startswith("sz") else "BJ")
            out.append(_mk(code, str(r.get("name", "")), mkt))
        return out

    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=6) as ex:
        for chunk in ex.map(page, range(1, 70)):
            for u in chunk:
                if u["symbol"] not in seen:
                    seen.add(u["symbol"])
                    out.append(u)
    return out


def _universe_cache() -> list[dict]:
    import os

    import pandas as pd

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CACHE_UNIVERSE)
    if not os.path.exists(path):
        raise RuntimeError("所有列表源均不可用，且无本地缓存")
    df = pd.read_pickle(path)
    return [_mk(str(r["code"]).zfill(6), str(r["name"]), "SH" if int(r["market"]) == 1 else "SZ")
            for _, r in df.iterrows()]


def fetch_universe() -> list[dict]:
    """东财 → 新浪 → 本地缓存，三级降级。"""
    for name, fn in (("东财", _universe_em), ("新浪", _universe_sina), ("本地缓存", _universe_cache)):
        try:
            out = fn()
            if len(out) > 3000:
                print(f"  股票列表来源：{name}（{len(out)} 只）")
                return out
        except Exception as e:  # noqa: BLE001
            print(f"  列表源 {name} 失败：{type(e).__name__}")
    raise RuntimeError("股票列表获取失败")


# ---------------------------------------------------------------- 日线
def _parse_tx_row(row: list) -> tuple:
    # [date, open, close, high, low, volume, ...]
    d, o, c, h, l, v = row[0], row[1], row[2], row[3], row[4], row[5]
    return _d(d), float(o), float(h), float(l), float(c), float(v)


def _tx_page(sym: str, start: str, end: str, n: int = 800) -> list[list]:
    url = TX_KLINE.format(sym=sym, start=start, end=end, n=n)
    j = json.loads(get(url, TX_HDR))
    data = j.get("data") or {}
    node = data.get(sym)
    if not node:
        return []
    key = next((k for k in node if k.endswith("day")), None)
    return node.get(key) or [] if key else []


EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?fields1=f1,f2,f3,f4,f5,f6"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&klt=101&fqt=1&beg={beg}&end=20500101&secid={secid}")

# 腾讯 proxy 域名：与 web.ifzq 数据一致（前复权），但走不同主机，主域被限流时可作备用
TXP_KLINE = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
             "?param={sym},day,{start},{end},{n},qfq")


def _txp_page(sym: str, start: str, end: str, n: int = 640) -> list[list]:
    url = TXP_KLINE.format(sym=sym, start=start, end=end, n=n)
    j = json.loads(get(url, TX_HDR, timeout=15, retries=2))
    node = (j.get("data") or {}).get(sym)
    if not node:
        return []
    key = next((k for k in node if k.endswith("day")), None)
    return (node.get(key) or []) if key else []


def _txp_bars(code: str, start: str, max_pages: int = 8) -> list[tuple]:
    sym = tx_symbol(code)
    got: dict[str, tuple] = {}
    start_arg, end_arg = "", ""
    for _ in range(max_pages):
        rows = _txp_page(sym, start_arg, end_arg)
        if not rows:
            break
        for r in rows:
            if len(r) < 6:
                continue
            try:
                got[r[0]] = _parse_tx_row(r)
            except (ValueError, TypeError):
                continue
        earliest = min(got)
        if earliest <= start:
            break
        end_arg = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
        start_arg = start
    rows = [got[k] for k in sorted(got) if k >= start]
    # 实测：腾讯对科创板返回的成交量单位是「股」，其他板块是「手」。
    # 不换算的话科创板成交额会放大 100 倍（688001 会算出 550 亿/天）。
    if str(code).zfill(6).startswith(("688", "689")):
        rows = [(d, o, h, lo, c, v / 100.0) for d, o, h, lo, c, v in rows]
    return rows


def em_secid(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "90", "51", "58", "11")):
        return "1." + c
    return "0." + c


def _em_klines(secid: str, start: str) -> list[tuple]:
    url = EM_KLINE.format(beg=start.replace("-", ""), secid=secid)
    j = json.loads(get(url, LIST_HDR, timeout=12, retries=2))
    data = j.get("data")
    if not data or not data.get("klines"):
        return []
    out = []
    for k in data["klines"]:
        p = k.split(",")
        out.append((p[0], float(p[1]), float(p[3]), float(p[4]), float(p[2]), float(p[5])))
    return sorted(out)


def _em_bars(code: str, start: str) -> list[tuple]:
    """东财一次请求返回全部历史，比腾讯翻页快得多。"""
    return _em_klines(em_secid(code), start)


SINA_KLINE = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
              "?symbol={sym}&scale=240&ma=no&datalen=1023")
SINA_HDR = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}


def _sina_sym(sym: str, start: str) -> list[tuple]:
    """新浪日线。注意：新浪的 volume 单位是「股」，东财/腾讯是「手」，
    这里统一除以 100 换成「手」，否则混用会让成交量差 100 倍。"""
    txt = get(SINA_KLINE.format(sym=sym), SINA_HDR, timeout=15, retries=2)
    arr = json.loads(txt) if txt.strip().startswith("[") else []
    out = []
    for r in arr:
        try:
            out.append((r["day"], float(r["open"]), float(r["high"]), float(r["low"]),
                        float(r["close"]), float(r["volume"]) / 100.0))
        except (KeyError, ValueError, TypeError):
            continue
    return sorted([r for r in out if r[0] >= start])


def _sina_bars(code: str, start: str) -> list[tuple]:
    """新浪日线（不复权，约 1023 根）。东财/腾讯都被限流时的兜底。"""
    return _sina_sym(tx_symbol(code), start)


_STATE = {"source": None, "fails": 0}
_STATE_LOCK = __import__("threading").Lock()
_ORDER = ("em", "tx", "sina")


def _call(src: str, code: str, start: str, max_pages: int = 8) -> list[tuple]:
    if src == "em":
        return _em_bars(code, start)
    if src == "tx":
        return _tx_bars(code, start, max_pages)
    if src == "txp":
        return _txp_bars(code, start, max_pages)
    return _sina_bars(code, start)


def pick_source(force: bool = False) -> str:
    """启动时探测一次可用源（持锁，避免多线程重复探测）。"""
    with _STATE_LOCK:
        if _STATE["source"] and not force:
            return _STATE["source"]
        for s in _ORDER:
            try:
                if _call(s, "600000", "2026-10-01"):
                    _STATE["source"] = s
                    print(f"  行情源：{ {'em': '东财', 'tx': '腾讯', 'sina': '新浪'}[s] }")
                    return s
            except Exception:  # noqa: BLE001
                continue
        _STATE["source"] = "sina"
        return "sina"


def fetch_bars_ex(code: str, start: str = "2021-01-01", max_pages: int = 8) -> tuple[list[tuple], str]:
    """返回 (日线, 来源)。em/tx 为前复权，sina 为不复权。

    先试探测到的可用源，再按 腾讯 → 东财 → 新浪 降级。
    """
    primary = pick_source()
    order = [primary] if primary in ("em", "tx", "txp") else []
    for s in ("txp", "em", "tx"):
        if s not in order:
            order.append(s)
    order.append("sina")          # 不复权只能兜底，永远排最后
    for src in order:
        try:
            rows = _call(src, code, start, max_pages)
            if rows:
                return rows, src
        except Exception:  # noqa: BLE001
            continue
    return [], "none"


def fetch_bars(code: str, start: str = "2021-01-01", max_pages: int = 8) -> list[tuple]:
    """前复权日线：东财 → 腾讯 → 新浪（不复权兜底）。"""
    return fetch_bars_ex(code, start, max_pages)[0]


def _tx_bars(code: str, start: str, max_pages: int = 8) -> list[tuple]:
    sym = tx_symbol(code)
    got: dict[str, tuple] = {}
    start_arg, end_arg = "", ""
    for page in range(max_pages):
        rows = _tx_page(sym, start_arg, end_arg)
        if not rows:
            break
        for r in rows:
            if len(r) < 6:
                continue
            try:
                got[r[0]] = _parse_tx_row(r)
            except (ValueError, TypeError):
                continue
        earliest = min(got)
        if earliest <= start:
            break
        end_arg = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
        start_arg = start
    rows = [got[k] for k in sorted(got) if k >= start]
    # 实测：腾讯对科创板返回的成交量单位是「股」，其他板块是「手」。
    # 不换算的话科创板成交额会放大 100 倍（688001 会算出 550 亿/天）。
    if str(code).zfill(6).startswith(("688", "689")):
        rows = [(d, o, h, lo, c, v / 100.0) for d, o, h, lo, c, v in rows]
    return rows


def fetch_bars_recent(code: str, days: int = 40) -> list[tuple]:
    """只取最近 days 天，用于每日增量。

    只接受前复权源（txp / em / tx）：新浪是不复权价，写进前复权表会破坏价格口径，
    而且它的 volume 单位是「股」。都拿不到时返回空，宁可这次不更新，
    等下一轮同步再补，也不要把口径不一致的数据写进库。
    """
    start = (date.today() - timedelta(days=days)).isoformat()
    primary = pick_source()
    order = [s for s in ([primary] if primary in ("txp", "em", "tx") else [])
             + ["txp", "em", "tx"]]
    seen: set[str] = set()
    order = [s for s in order if not (s in seen or seen.add(s))]
    for name in order:
        try:
            rows = _call(name, code, start)
            if rows:
                return rows
        except Exception:  # noqa: BLE001
            continue
    return []


# ---------------------------------------------------------------- 上市日期
EM_F10 = ("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO"
          "&columns=SECUCODE,SECURITY_CODE,LISTING_DATE&filter=(SECUCODE%3D%22{secucode}%22)"
          "&pageNumber=1&pageSize=1&source=HSF10&client=PC")
F10_HDR = {"User-Agent": UA, "Referer": "https://emweb.securities.eastmoney.com/"}


def secucode(code: str) -> str:
    c = str(code).zfill(6)
    if c.startswith(("60", "68", "90")):
        return f"{c}.SH"
    if c.startswith(("8", "4", "92")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def fetch_list_date(code: str) -> str | None:
    """上市日期（东财 F10）。"""
    try:
        j = json.loads(get(EM_F10.format(secucode=secucode(code)), F10_HDR, timeout=10, retries=2))
        d = (j.get("result") or {}).get("data") or []
        if d and d[0].get("LISTING_DATE"):
            return _d(d[0]["LISTING_DATE"])
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------- 市值
TX_QUOTE = "https://qt.gtimg.cn/q={symbols}"
TX_QUOTE_HDR = {"User-Agent": UA, "Referer": "https://gu.qq.com/"}


def fetch_mktcap_full_batch(codes: list[str]) -> dict[str, tuple[float, float]]:
    """腾讯批量行情：字段 45 = 总市值（亿元）、字段 44 = 流通市值（亿元）。"""
    out: dict[str, tuple[float, float]] = {}
    syms = [tx_symbol(c) for c in codes]
    for i in range(0, len(syms), 50):
        chunk = syms[i:i + 50]
        try:
            txt = get(TX_QUOTE.format(symbols=",".join(chunk)), TX_QUOTE_HDR, timeout=10, retries=2)
        except Exception:  # noqa: BLE001
            continue
        for line in txt.split(";"):
            line = line.strip()
            if not line.startswith("v_") or "=" not in line:
                continue
            sym = line.split("=")[0][2:]
            parts = line.split("~")
            if len(parts) < 46:
                continue
            try:
                total = float(parts[45])
                floating = float(parts[44])
            except (ValueError, IndexError):
                continue
            code = sym[-6:]
            if total > 0:
                out[code] = (total, floating if floating > 0 else 0.0)
    return out


def fetch_mktcap_batch(codes: list[str]) -> dict[str, float]:
    """腾讯批量行情：字段 45 = 总市值（亿元）。一次最多约 50 只。"""
    out: dict[str, float] = {}
    syms = [tx_symbol(c) for c in codes]
    for i in range(0, len(syms), 50):
        chunk = syms[i:i + 50]
        txt = get(TX_QUOTE.format(symbols=",".join(chunk)), TX_QUOTE_HDR, timeout=10, retries=2)
        for line in txt.split(";"):
            line = line.strip()
            if not line.startswith("v_") or "=" not in line:
                continue
            sym = line.split("=")[0][2:]
            parts = line.split("~")
            if len(parts) < 46:
                continue
            try:
                mc = float(parts[45])
            except (ValueError, IndexError):
                continue
            code = sym[-6:]
            if mc > 0:
                out[code] = mc
    return out


# ---------------------------------------------------------------- 估值
EM_VAL = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_VALUEANALYSIS_DET"
          "&columns=ALL&pageNumber={pn}&pageSize=500&sortColumns=SECURITY_CODE&sortTypes=1"
          "&filter=(TRADE_DATE%3D%27{date}%27)")
EM_DIV = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_SHAREBONUS_DET"
          "&columns=ALL&pageNumber={pn}&pageSize=500&sortColumns=EX_DIVIDEND_DATE&sortTypes=-1"
          "&filter=(EX_DIVIDEND_DATE%3E%3D%27{since}%27)")


def _pages(url_tpl: str, **kw) -> list[dict]:
    out, pn = [], 1
    while pn <= 60:
        j = json.loads(get(url_tpl.format(pn=pn, **kw), DC_HDR, timeout=25, retries=2))
        r = j.get("result")
        if not r or not r.get("data"):
            break
        out.extend(r["data"])
        if pn >= (r.get("pages") or 1):
            break
        pn += 1
    return out


def fetch_valuation(trade_date: str) -> list[dict]:
    """全市场估值快照（PE/PB/PS/PCF/行业）。"""
    return _pages(EM_VAL, date=trade_date)


def fetch_dividends(since: str) -> list[dict]:
    """近一年除权的分红方案（每 10 股派息）。"""
    return _pages(EM_DIV, since=since)


# ---------------------------------------------------------------- 股东户数
def fetch_holders(code: str) -> list[tuple]:
    """[(symbol, end_date, announce_date, holder_num, chg_pct, avg_mktcap)]"""
    j = json.loads(get(EM_HOLDER.format(code=code), DC_HDR))
    rows = ((j.get("result") or {}).get("data")) or []
    out = []
    for r in rows:
        end = r.get("END_DATE")
        if not end:
            continue
        out.append((
            code, _d(end), _d(r.get("HOLD_NOTICE_DATE") or "") or None,
            int(r["HOLDER_NUM"]) if r.get("HOLDER_NUM") is not None else None,
            float(r["HOLDER_NUM_RATIO"]) if r.get("HOLDER_NUM_RATIO") is not None else None,
            float(r["AVG_MARKET_CAP"]) if r.get("AVG_MARKET_CAP") is not None else None,
        ))
    return out


# ---------------------------------------------------------------- 财务
def fetch_financials(code: str) -> list[tuple]:
    """[(symbol, report_period, announce_date, revenue, net_profit, yoy, eps, roe,
         is_cumulative, revenue_yoy, gross_margin)]"""
    j = json.loads(get(EM_FIN.format(code=code), DC_HDR))
    rows = ((j.get("result") or {}).get("data")) or []
    out = []
    for r in rows:
        rp = r.get("REPORTDATE")
        if not rp:
            continue
        month = int(_d(rp)[5:7])
        out.append((
            code, _d(rp), _d(r.get("NOTICE_DATE") or "") or None,
            _f(r.get("TOTAL_OPERATE_INCOME")), _f(r.get("PARENT_NETPROFIT")),
            _f(r.get("SJLTZ")), _f(r.get("BASIC_EPS")), _f(r.get("WEIGHTAVG_ROE")),
            0 if month == 3 else 1,
            _f(r.get("YSTZ")), _f(r.get("XSMLL")),
        ))
    return out


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 交易日历
def fetch_calendar(start: str = "2020-01-01") -> list[str]:
    """以上证指数（secid 1.000001）的交易日为准，东财 → 腾讯 → 新浪。"""
    try:
        rows = _em_klines("1.000001", start)
        if rows:
            return [r[0] for r in rows if r[0] >= start]
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = _sina_sym("sh000001", start)
        if rows:
            return [r[0] for r in rows if r[0] >= start]
    except Exception:  # noqa: BLE001
        pass
    sym = "sh000001"
    got: dict[str, None] = {}
    start_arg, end_arg = "", ""
    for _ in range(8):
        rows = _tx_page(sym, start_arg, end_arg)
        if not rows:
            break
        for r in rows:
            got[_d(r[0])] = None
        earliest = min(got)
        if earliest <= start:
            break
        end_arg = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
        start_arg = start
    return [d for d in sorted(got) if d >= start]


if __name__ == "__main__":
    print("universe:", len(fetch_universe()))
    b = fetch_bars("600000")
    print("bars:", len(b), b[0], b[-1])
    print("holders:", fetch_holders("600000")[:2])
    print("financials:", fetch_financials("600000")[:2])
    print("calendar:", len(fetch_calendar()))

# ---------------------------------------------------------------- 指数（不进库）
INDEX_LIST = [
    {"code": "000001", "secid": "1.000001", "name": "上证指数", "ths": "1A0001"},
    {"code": "399001", "secid": "0.399001", "name": "深证成指", "ths": "399001"},
    {"code": "399006", "secid": "0.399006", "name": "创业板指", "ths": "399006"},
    {"code": "000688", "secid": "1.000688", "name": "科创50", "ths": "1B0688"},
    {"code": "899050", "secid": "0.899050", "name": "北证50", "ths": "899050"},
    {"code": "000680", "secid": "1.000680", "name": "科创综指", "ths": "1B0680"},
    {"code": "000300", "secid": "1.000300", "name": "沪深300", "ths": "399300"},
    {"code": "000905", "secid": "1.000905", "name": "中证500", "ths": "1B0905"},
    {"code": "000852", "secid": "1.000852", "name": "中证1000", "ths": "1B0852"},
    {"code": "000016", "secid": "1.000016", "name": "上证50", "ths": "1B0016"},
    {"code": "000985", "secid": "1.000985", "name": "中证全指", "ths": None,
     "em": "zs000985"},
    {"code": "HSI", "secid": "100.HSI", "name": "恒生指数", "ths": "HSI"},
]


def ths_url(code: str) -> str:
    """指数对应的行情页：优先同花顺，没有同花顺页面的走东财。"""
    hit = next((x for x in INDEX_LIST if x["code"] == code), INDEX_LIST[0])
    if hit.get("ths"):
        return f"https://stockpage.10jqka.com.cn/{hit['ths']}/"
    if hit.get("em"):
        return f"https://quote.eastmoney.com/{hit['em']}.html"
    return "https://quote.eastmoney.com/"

EM_ULIST = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&secids={secids}"
            "&fields=f1,f2,f3,f4,f6,f12,f14,f15,f16,f17,f18")


def fetch_index_quotes() -> list[dict]:
    """一次拿所有指数的当日行情（涨跌幅、最高最低、成交额）。"""
    import time as _t

    now = _t.time()
    with _IDX_LOCK:
        hit = _IDX_CACHE.get("quotes")
        if hit and now - hit[0] < 10:
            return hit[1]

    secids = ",".join(x["secid"] for x in INDEX_LIST)
    j = json.loads(get(EM_ULIST.format(secids=secids), LIST_HDR, timeout=12, retries=2))
    diff = (j.get("data") or {}).get("diff") or []
    by_code = {}
    for d in diff:
        raw = str(d.get("f12") or "")
        if not raw:
            continue
        by_code[raw] = d
        by_code[raw.zfill(6)] = d          # 支持 HSI 这种非 6 位代码
    out = []
    for meta in INDEX_LIST:
        d = by_code.get(meta["code"]) or {}
        def num(key):
            v = d.get(key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        price, pct = num("f2"), num("f3")
        amount = num("f6")
        out.append({
            "code": meta["code"], "name": meta["name"],
            "price": price, "pct": pct,
            "high": num("f15"), "low": num("f16"),
            "open": num("f17"), "preClose": num("f18"),
            "change": round(price - num("f18"), 2) if price is not None and num("f18") else None,
            "amount": round(amount / 1e8, 1) if amount else None,
        })
    with _IDX_LOCK:
        _IDX_CACHE["quotes"] = (now, out)
    return out

EM_TRENDS = ("https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid={secid}"
             "&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
             "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays={days}")

_IDX_CACHE: dict = {}
_IDX_LOCK = __import__("threading").Lock()
_IDX_TTL = 20.0          # 秒：分时只做短缓存，避免高频打接口


def fetch_index_minute(secid: str, days: int = 1) -> dict:
    """指数分时：返回 {name, preClose, points: [[时间, 价格, 均价], ...], quote}。"""
    import time as _t

    key = f"{secid}|{days}"
    now = _t.time()
    with _IDX_LOCK:
        hit = _IDX_CACHE.get(key)
        if hit and now - hit[0] < _IDX_TTL:
            return hit[1]

    j = json.loads(get(EM_TRENDS.format(secid=secid, days=days), LIST_HDR, timeout=12, retries=2))
    d = (j.get("data") or {})
    trends = d.get("trends") or []
    pre = float(d.get("preClose") or 0) or None
    points = []
    total_amt = 0.0
    total_vol = 0.0
    for row in trends:
        parts = row.split(",")
        if len(parts) < 8:
            continue
        t = parts[0][-5:] if len(parts[0]) > 5 else parts[0]
        if days > 1 and len(parts[0]) >= 16:
            t = parts[0][5:16]          # "MM-DD HH:MM"
        try:
            price = float(parts[2])
            vol = float(parts[5])
            amt = float(parts[6])
        except ValueError:
            continue
        points.append([t, round(price, 3), round(vol, 0)])
        total_vol += vol
        total_amt += amt

    last = points[-1][1] if points else None
    quote = {
        "name": d.get("name") or "",
        "price": last,
        "preClose": pre,
        "change": round(last - pre, 2) if (last is not None and pre) else None,
        "pct": round((last / pre - 1) * 100, 2) if (last is not None and pre) else None,
        "amount": round(total_amt / 1e8, 1) if total_amt else None,   # 亿元
        "volume": round(total_vol / 1e4, 1) if total_vol else None,   # 万手
    }
    if points:
        prices = [p[1] for p in points]
        quote["high"] = max(prices)
        quote["low"] = min(prices)
        quote["open"] = points[0][1]
    data = {"name": quote["name"], "preClose": pre, "points": points, "quote": quote}
    with _IDX_LOCK:
        _IDX_CACHE[key] = (now, data)
    return data

IDX_KLT = {"day": 101, "week": 102, "month": 103}
EM_IDX_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
                "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
                "&klt={klt}&fqt=1&beg=0&end=20500101&lmt={n}")

_KLINE_CACHE: dict = {}
_KLINE_TTL = 300.0       # K 线一天基本不变，5 分钟缓存足够


def fetch_index_kline(secid: str, period: str = "day", n: int = 250) -> dict:
    """指数 K 线：period = day / week / month。返回 [date, open, high, low, close, volume]。"""
    import time as _t

    klt = IDX_KLT.get(period, 101)
    key = f"{secid}|{period}|{n}"
    now = _t.time()
    with _IDX_LOCK:
        hit = _KLINE_CACHE.get(key)
        if hit and now - hit[0] < _KLINE_TTL:
            return hit[1]

    url = EM_IDX_KLINE.format(secid=secid, klt=klt, n=n)
    j = json.loads(get(url, LIST_HDR, timeout=12, retries=2))
    d = j.get("data") or {}
    bars = []
    for k in (d.get("klines") or [])[-n:]:
        p = k.split(",")
        if len(p) < 6:
            continue
        try:
            bars.append([p[0], float(p[1]), float(p[3]), float(p[4]), float(p[2]),
                         float(p[5]), float(p[6]) if len(p) > 6 else 0.0])
        except ValueError:
            continue
    data = {"name": d.get("name") or "", "period": period, "bars": bars}
    with _IDX_LOCK:
        _KLINE_CACHE[key] = (now, data)
    return data
