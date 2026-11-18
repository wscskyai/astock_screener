# -*- coding: utf-8 -*-
"""本地行情库：SQLite 单文件，所有选股查询都读本地，不依赖接口实时性。

表结构：
    securities  股票基础信息（代码/名称/板块/状态）
    bars        日线（前复权）
    holders     股东户数（季度，含公告日）
    financials  财务（季度，含公告日）
    calendar    交易日历
    meta        构建元信息
"""

from __future__ import annotations

import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根目录
# 容器里用环境变量指定库位置（默认仍是项目下的 data/astock.db）
DB_PATH = os.environ.get("ASTOCK_DB") or os.path.join(BASE_DIR, "data", "astock.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS securities(
    symbol      TEXT PRIMARY KEY,
    name        TEXT,
    market      TEXT,              -- SH / SZ / BJ
    board       TEXT,              -- 沪主板/科创板/深主板/创业板/北交所
    currency    TEXT DEFAULT 'CNY',
    list_date   TEXT,
    mktcap      REAL,              -- 总市值（亿元）
    float_mktcap REAL,             -- 流通市值（亿元）
    mktcap_date TEXT,
    n_bars      INTEGER,           -- 日线根数（冗余字段，加速查询）
    status      TEXT DEFAULT 'L',  -- L 正常 / S 停牌 / D 退市
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS bars(
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,     -- YYYY-MM-DD
    open        REAL, high REAL, low REAL, close REAL,
    volume      REAL,              -- 手
    PRIMARY KEY(symbol, date)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_bars_date ON bars(date);

CREATE TABLE IF NOT EXISTS holders(
    symbol        TEXT NOT NULL,
    end_date      TEXT NOT NULL,   -- 报告期末
    announce_date TEXT,            -- 公告日（做 PIT 用）
    holder_num    INTEGER,
    chg_pct       REAL,            -- 较上期变动 %
    avg_mktcap    REAL,            -- 户均持仓市值
    PRIMARY KEY(symbol, end_date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS financials(
    symbol         TEXT NOT NULL,
    report_period  TEXT NOT NULL,  -- YYYY-MM-DD
    announce_date  TEXT,
    revenue        REAL,           -- 营业总收入
    net_profit     REAL,           -- 归母净利润
    net_profit_yoy REAL,           -- 同比 %
    eps            REAL,
    roe            REAL,
    is_cumulative  INTEGER,        -- 1=年内累计（A股 Q2/Q3/Q4）
    revenue_yoy    REAL,           -- 营收同比 %
    gross_margin   REAL,           -- 毛利率 %
    PRIMARY KEY(symbol, report_period)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS calendar(date TEXT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS bar_meta(
    symbol     TEXT PRIMARY KEY,
    src        TEXT,             -- em/tx=前复权，sina=不复权
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS valuation(
    symbol     TEXT PRIMARY KEY,
    trade_date TEXT,
    pe_ttm     REAL,
    pe_static  REAL,
    pb         REAL,
    ps_ttm     REAL,
    pcf_ttm    REAL,
    dv_ttm     REAL,             -- 股息率 %
    industry   TEXT,
    total_mv   REAL,             -- 总市值（元）
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS valuation_daily(
    symbol  TEXT NOT NULL,
    date    TEXT NOT NULL,
    pe_ttm  REAL,
    pb      REAL,
    dv_ttm  REAL,
    PRIMARY KEY(symbol, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS valuation_pct(
    symbol     TEXT PRIMARY KEY,
    pe_pct     REAL,
    pb_pct     REAL,
    dv_pct     REAL,
    window     INTEGER,
    updated_at TEXT
);

-- 股票名称拼音索引（全拼 + 首字母），供搜索与自动补全
CREATE TABLE IF NOT EXISTS stock_pinyin(
    symbol TEXT PRIMARY KEY,
    py     TEXT,
    init   TEXT,
    name   TEXT
);

-- 形态信号（每次「形态扫描」整体重写）
-- 旗形：(旗杆规则 3 种) × (旗面天数 6 种) × (位置 3 种) = 54 列，参数化查询
CREATE TABLE IF NOT EXISTS pattern_signal(
    symbol         TEXT PRIMARY KEY,
    date           TEXT,
    base_ok        INTEGER,   -- 做小做底
    base_pos       REAL,      -- 现价在近 2 年区间的位置
    base_amp       REAL,      -- 近 2 年振幅
    base_max_run   REAL,      -- 近 2 年最大 20 日涨幅
    base_5_mild    INTEGER,   -- 做小做底 + 近 N 日启动（窗口 × 强度）
    base_5_std     INTEGER,
    base_5_strong  INTEGER,
    base_10_mild   INTEGER,
    base_10_std    INTEGER,
    base_10_strong INTEGER,
    base_20_mild   INTEGER,
    base_20_std    INTEGER,
    base_20_strong INTEGER,
    base_ignite_date TEXT,
    base_ignite_chg  REAL,
    base_ignite_vol  REAL,
    flag_auto_2_5_20 INTEGER,
    flag_auto_2_5_30 INTEGER,
    flag_auto_2_5_40 INTEGER,
    flag_auto_2_6_20 INTEGER,
    flag_auto_2_6_30 INTEGER,
    flag_auto_2_6_40 INTEGER,
    flag_auto_2_8_20 INTEGER,
    flag_auto_2_8_30 INTEGER,
    flag_auto_2_8_40 INTEGER,
    flag_auto_3_5_20 INTEGER,
    flag_auto_3_5_30 INTEGER,
    flag_auto_3_5_40 INTEGER,
    flag_auto_3_6_20 INTEGER,
    flag_auto_3_6_30 INTEGER,
    flag_auto_3_6_40 INTEGER,
    flag_auto_3_8_20 INTEGER,
    flag_auto_3_8_30 INTEGER,
    flag_auto_3_8_40 INTEGER,
    flag_6_2_5_20 INTEGER,
    flag_6_2_5_30 INTEGER,
    flag_6_2_5_40 INTEGER,
    flag_6_2_6_20 INTEGER,
    flag_6_2_6_30 INTEGER,
    flag_6_2_6_40 INTEGER,
    flag_6_2_8_20 INTEGER,
    flag_6_2_8_30 INTEGER,
    flag_6_2_8_40 INTEGER,
    flag_6_3_5_20 INTEGER,
    flag_6_3_5_30 INTEGER,
    flag_6_3_5_40 INTEGER,
    flag_6_3_6_20 INTEGER,
    flag_6_3_6_30 INTEGER,
    flag_6_3_6_40 INTEGER,
    flag_6_3_8_20 INTEGER,
    flag_6_3_8_30 INTEGER,
    flag_6_3_8_40 INTEGER,
    flag_95_2_5_20 INTEGER,
    flag_95_2_5_30 INTEGER,
    flag_95_2_5_40 INTEGER,
    flag_95_2_6_20 INTEGER,
    flag_95_2_6_30 INTEGER,
    flag_95_2_6_40 INTEGER,
    flag_95_2_8_20 INTEGER,
    flag_95_2_8_30 INTEGER,
    flag_95_2_8_40 INTEGER,
    flag_95_3_5_20 INTEGER,
    flag_95_3_5_30 INTEGER,
    flag_95_3_5_40 INTEGER,
    flag_95_3_6_20 INTEGER,
    flag_95_3_6_30 INTEGER,
    flag_95_3_6_40 INTEGER,
    flag_95_3_8_20 INTEGER,
    flag_95_3_8_30 INTEGER,
    flag_95_3_8_40 INTEGER,
    box_60_bottom_2 INTEGER,  -- 箱体：窗口 60/90/120 × 位置 × 触碰次数 2/3
    box_60_bottom_3 INTEGER,
    box_60_mid_2   INTEGER,
    box_60_mid_3   INTEGER,
    box_60_top_2   INTEGER,
    box_60_top_3   INTEGER,
    box_60_any_2   INTEGER,
    box_60_any_3   INTEGER,
    box_90_bottom_2 INTEGER,
    box_90_bottom_3 INTEGER,
    box_90_mid_2   INTEGER,
    box_90_mid_3   INTEGER,
    box_90_top_2   INTEGER,
    box_90_top_3   INTEGER,
    box_90_any_2   INTEGER,
    box_90_any_3   INTEGER,
    box_120_bottom_2 INTEGER,
    box_120_bottom_3 INTEGER,
    box_120_mid_2  INTEGER,
    box_120_mid_3  INTEGER,
    box_120_top_2  INTEGER,
    box_120_top_3  INTEGER,
    box_120_any_2  INTEGER,
    box_120_any_3  INTEGER,
    box_top_touch  INTEGER,
    box_bot_touch  INTEGER,
    box_60_mid     INTEGER,
    box_60_top     INTEGER,
    box_60_any     INTEGER,
    box_90_bottom  INTEGER,
    box_90_mid     INTEGER,
    box_90_top     INTEGER,
    box_90_any     INTEGER,
    box_120_bottom INTEGER,
    box_120_mid    INTEGER,
    box_120_top    INTEGER,
    box_120_any    INTEGER,
    box_ok         INTEGER,   -- 当前组合命中（展示用）
    box_high       REAL,
    box_low        REAL,
    box_days       INTEGER,
    box_pos        REAL,
    box_height     REAL,
    w_brk_5_3 INTEGER,
    w_brk_5_0 INTEGER,
    w_brk_5_5 INTEGER,
    w_brk_1_3 INTEGER,
    w_brk_1_0 INTEGER,
    w_brk_1_5 INTEGER,
    w_brk_3_3 INTEGER,
    w_brk_3_0 INTEGER,
    w_brk_3_5 INTEGER,
    w_brk_10_3 INTEGER,
    w_brk_10_0 INTEGER,
    w_brk_10_5 INTEGER,
    w_pend_10 INTEGER,
    w_pend_15 INTEGER,
    w_pend_20 INTEGER,
    w_l1 REAL, w_l2 REAL, w_neck REAL,
    w_l1_date TEXT, w_l2_date TEXT, w_brk_date TEXT,
    brk_auto       INTEGER,   -- 突破确认（位置 ≤0.20）
    brk_6          INTEGER,
    brk_95         INTEGER,
    flag_ok        INTEGER,   -- 任一区间成立（展示用）
    flag_pole_date TEXT,
    flag_pole_chg  REAL,
    flag_vol_mult  REAL,
    flag_days      INTEGER,
    flag_pos       REAL
);

-- 「一天一行」的完整视图：日K + 当日估值。写入仍分表，读取按行用。
CREATE VIEW IF NOT EXISTS v_daily AS
SELECT b.symbol, b.date, b.open, b.high, b.low, b.close, b.volume,
       vd.pe_ttm, vd.pb, vd.dv_ttm
FROM bars b
LEFT JOIN valuation_daily vd ON vd.symbol = b.symbol AND vd.date = b.date;

CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);

-- 站点运营配置（键值对，见 siteconfig.py）
CREATE TABLE IF NOT EXISTS app_settings(
    k TEXT PRIMARY KEY,
    v TEXT
);

-- 保存的选股条件（查询模板）
CREATE TABLE IF NOT EXISTS screens(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    name       TEXT NOT NULL,
    conds      TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT
);

-- 按日期取全市场（回补/横截面统计）
CREATE INDEX IF NOT EXISTS idx_val_daily_date ON valuation_daily(date);
"""


def connect(path: str = DB_PATH, create: bool = True) -> sqlite3.Connection:
    """打开数据库；遇到跨系统残留的 -shm/-wal 时自愈一次。

    环境变量 ASTOCK_SQLITE_SAFE=1 时用「安全模式」：不用 WAL、不开 mmap，
    适合 Docker / NAS 的 bind mount（9p、virtiofs、NFS 上 WAL+mmap 会报 disk I/O error）。
    """
    try:
        return _connect_once(path, create)
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        shm = path + "-shm"
        if any(k in msg for k in ("disk i/o", "malformed", "unable to open")) and os.path.exists(shm):
            # 典型场景：Windows 上跑过服务端，留下平台相关的 -shm；容器里 SQLite 打不开。
            # 挪走 -shm（不删 -wal，数据还在），SQLite 会自己从 WAL 重建索引文件。
            backup = f"{shm}.stale-{int(time.time())}"
            try:
                os.replace(shm, backup)
            except OSError:
                raise e
            return _connect_once(path, create)
        raise


def _connect_once(path: str, create: bool) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)      # 写锁最多等 30 秒，别直接报 locked
    conn.execute("PRAGMA busy_timeout=30000")

    safe = os.environ.get("ASTOCK_SQLITE_SAFE", "").strip().lower() in ("1", "true", "yes")
    if not safe:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            safe = True                           # 这个文件系统不支持 WAL
    if safe:
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.OperationalError:
            pass

    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    if safe:
        conn.execute("PRAGMA mmap_size=0")
    else:
        try:
            conn.execute("PRAGMA mmap_size=268435456")   # 256MB 内存映射，读大表更快
            # 真正读一页：mmap 在 9p/virtiofs 上会在这里才暴露成 disk I/O error
            conn.execute("SELECT COUNT(1) FROM sqlite_master").fetchone()
        except sqlite3.OperationalError:
            conn.execute("PRAGMA mmap_size=0")
    conn.execute("PRAGMA cache_size=-64000")     # 64MB page cache
    if create:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    return conn


def optimize(conn: sqlite3.Connection) -> None:
    """关闭前调用：让 SQLite 按需重建统计信息（比手动 ANALYZE 温和）。"""
    try:
        conn.execute("PRAGMA optimize")
        conn.commit()
    except sqlite3.Error:
        pass


def _migrate(conn: sqlite3.Connection) -> None:
    """给旧库补列。"""
    # pattern_signal 结构变更过（旗形参数可选），列不对就重建空表
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pattern_signal)")}
        if cols and "flag_auto_3_5_20" not in cols:
            conn.execute("DROP TABLE pattern_signal")
            conn.executescript(SCHEMA)
    except sqlite3.OperationalError:
        pass

    # pattern_signal 逐步加列：缺啥补啥（新列默认 NULL）
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(pattern_signal)")}
    if pcols:
        add0 = []
        for w in ("5", "10", "20"):
            for t in ("mild", "std", "strong"):
                add0.append((f"base_{w}_{t}", "INTEGER"))
        add0 += [("base_ignite_date", "TEXT"), ("base_ignite_chg", "REAL"),
                 ("base_ignite_vol", "REAL")]
        for name, ddl in add0:
            if name not in pcols:
                conn.execute(f"ALTER TABLE pattern_signal ADD COLUMN {name} {ddl}")
        add = []
        for d in ("60", "90", "120"):
            for pos in ("bottom", "mid", "top", "any"):
                for t in ("2", "3"):
                    add.append((f"box_{d}_{pos}_{t}", "INTEGER"))
        for w in ("5", "1", "3", "10"):
            for r in ("3", "0", "5"):
                add.append((f"w_brk_{w}_{r}", "INTEGER"))
        for w in ("10", "15", "20"):
            add.append((f"w_pend_{w}", "INTEGER"))
        add += [("w_l1", "REAL"), ("w_l2", "REAL"), ("w_neck", "REAL"),
                ("w_l1_date", "TEXT"), ("w_l2_date", "TEXT"), ("w_brk_date", "TEXT")]
        for d in ("3", "4", "5"):
            for r in ("mild", "std", "strong"):
                for lp in ("30", "40", "50"):
                    add.append((f"volup_{d}_{r}_{lp}", "INTEGER"))
        add += [("volup_ok", "INTEGER"), ("volup_pos", "REAL"), ("volup_days_run", "INTEGER"),
                ("volup_vr", "REAL"), ("volup_amt", "REAL"), ("volup_turn", "REAL"),
                ("volup_chg", "REAL"), ("volup_up_days", "INTEGER")]
        add += [("box_ok", "INTEGER"), ("box_high", "REAL"), ("box_low", "REAL"),
                ("box_days", "INTEGER"), ("box_pos", "REAL"), ("box_height", "REAL"),
                ("box_top_touch", "INTEGER"), ("box_bot_touch", "INTEGER")]
        for name, ddl in add:
            if name not in pcols:
                conn.execute(f"ALTER TABLE pattern_signal ADD COLUMN {name} {ddl}")

    cols = {r[1] for r in conn.execute("PRAGMA table_info(securities)")}
    for name, ddl in (("mktcap", "REAL"), ("float_mktcap", "REAL"), ("mktcap_date", "TEXT"),
                      ("list_date", "TEXT"), ("n_bars", "INTEGER")):
        if name not in cols:
            conn.execute(f"ALTER TABLE securities ADD COLUMN {name} {ddl}")

    fcols = {r[1] for r in conn.execute("PRAGMA table_info(financials)")}
    for name, ddl in (("revenue_yoy", "REAL"), ("gross_margin", "REAL")):
        if name not in fcols:
            conn.execute(f"ALTER TABLE financials ADD COLUMN {name} {ddl}")


def upsert(conn: sqlite3.Connection, table: str, cols: list[str], rows: list[tuple]) -> int:
    if not rows:
        return 0
    ph = ",".join("?" * len(cols))
    sql = f"INSERT INTO {table}({','.join(cols)}) VALUES({ph}) " \
          f"ON CONFLICT({cols[0]},{cols[1]}) DO UPDATE SET " + \
          ",".join(f"{c}=excluded.{c}" for c in cols[2:])
    conn.executemany(sql, rows)
    return len(rows)


def upsert_pk1(conn: sqlite3.Connection, table: str, cols: list[str], rows: list[tuple]) -> int:
    """单主键表（securities / calendar）用。"""
    if not rows:
        return 0
    ph = ",".join("?" * len(cols))
    sql = f"INSERT INTO {table}({','.join(cols)}) VALUES({ph}) " \
          f"ON CONFLICT({cols[0]}) DO UPDATE SET " + \
          ",".join(f"{c}=excluded.{c}" for c in cols[1:])
    conn.executemany(sql, rows)
    return len(rows)


def set_meta(conn: sqlite3.Connection, k: str, v: str) -> None:
    conn.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def get_meta(conn: sqlite3.Connection, k: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def stats(conn: sqlite3.Connection) -> dict:
    q = lambda s: conn.execute(s).fetchone()[0]  # noqa: E731
    return {
        "securities": q("SELECT COUNT(*) FROM securities"),
        "bars": q("SELECT COUNT(*) FROM bars"),
        "symbols_with_bars": q("SELECT COUNT(DISTINCT symbol) FROM bars"),
        "bar_range": conn.execute("SELECT MIN(date), MAX(date) FROM bars").fetchone(),
        "holders": q("SELECT COUNT(*) FROM holders"),
        "financials": q("SELECT COUNT(*) FROM financials"),
        "calendar": q("SELECT COUNT(*) FROM calendar"),
        "db_mb": round(os.path.getsize(DB_PATH) / 1e6, 1) if os.path.exists(DB_PATH) else 0,
    }


if __name__ == "__main__":
    with connect() as c:
        for k, v in stats(c).items():
            print(f"{k:20} {v}")
