# -*- coding: utf-8 -*-
"""数据层自检：python tools/test_data.py

校验表结构、行数、OHLC 合法性、股东户数连续性、财务单季折算。
建库完成后跑一遍，可放进每日任务里做健康检查。
"""

from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from astock import db
from astock import query


class TestDataLayer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = db.connect()

    def test_tables(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("securities", "bars", "holders", "financials", "calendar"):
            self.assertIn(t, names)

    def test_coverage(self):
        sec = self.conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
        sym = self.conn.execute("SELECT COUNT(DISTINCT symbol) FROM bars").fetchone()[0]
        bars = self.conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        self.assertGreater(sec, 5000)
        self.assertGreater(sym, sec * 0.95)
        self.assertGreater(bars, 4_000_000)

    def test_no_duplicate(self):
        n = self.conn.execute("""
            SELECT COUNT(*) FROM (SELECT symbol, date, COUNT(*) c FROM bars GROUP BY symbol, date HAVING c > 1)
        """).fetchone()[0]
        self.assertEqual(n, 0)

    def test_ohlc_sane(self):
        bad = self.conn.execute("""
            SELECT COUNT(*) FROM bars WHERE high < low OR close > high OR close < low OR open > high OR open < low
        """).fetchone()[0]
        self.assertEqual(bad, 0)

    def test_holder_quarters(self):
        rows = self.conn.execute("""
            SELECT end_date, holder_num FROM holders WHERE symbol='600000'
            ORDER BY end_date DESC LIMIT 3
        """).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0][0].endswith("09-30"))
        self.assertTrue(rows[1][0].endswith("06-30"))

    def test_single_quarter_profit(self):
        """600000：2026Q3 累计 405.72 亿，Q2 累计 309.51 亿 -> 单季 ≈ 96.21 亿。"""
        rows = dict(self.conn.execute("""
            SELECT report_period, net_profit FROM financials
            WHERE symbol='600000' AND report_period IN ('2026-09-30','2026-06-30')
        """).fetchall())
        q3 = rows["2026-09-30"]
        q2 = rows["2026-06-30"]
        self.assertAlmostEqual((q3 - q2) / 1e8, 96.2, delta=1.0)

    def test_query_holders_runs(self):
        df = query.holders_drop(self.conn, 3, 5.0)
        self.assertFalse(df.empty)
        self.assertTrue((df["drop_pct"] <= -5).all())
        # 三期为连续季末
        self.assertTrue(df["d0"].str[5:7].isin(["03", "06", "09", "12"]).all())

    def test_query_profit_runs(self):
        df = query.profit_ok(self.conn, 3, 1e7)
        self.assertFalse(df.empty)
        self.assertTrue((df["sum_profit"] >= 1e7).all())

    def test_list_date_backfilled(self):
        n = self.conn.execute(
            "SELECT COUNT(1) FROM securities WHERE list_date IS NOT NULL AND list_date <> ''"
        ).fetchone()[0]
        self.assertGreater(n, 5000)

    def test_filter_listed(self):
        df = query.holders_drop(self.conn, 3, 5.0)
        f5 = query.filter_listed(self.conn, df, 5)
        f0 = query.filter_listed(self.conn, df, 0)
        self.assertLessEqual(len(f5), len(f0))
        self.assertTrue(set(f5.index).issubset(set(f0.index)))
        cutoff = (pd.Timestamp.today() - pd.Timedelta(days=int(5 * 365.25))).strftime("%Y-%m-%d")
        if len(f5):
            dates = dict(self.conn.execute("SELECT symbol, list_date FROM securities").fetchall())
            self.assertTrue(all(dates[s] <= cutoff for s in f5.index))

    def test_parse_mktcap(self):
        self.assertIsNone(query.parse_mktcap(None))
        self.assertEqual(query.parse_mktcap([50]), (50, float("inf")))
        self.assertEqual(query.parse_mktcap([50, 200]), (50, 200))
        self.assertEqual(query.parse_mktcap([200, 50]), (50, 200))

    def test_mktcap_filter(self):
        df = query.combo(self.conn, 8, 1e7)
        band = query.filter_universe(self.conn, df, mktcap=(50, 200))
        self.assertTrue((band["mktcap"] >= 50).all())
        self.assertTrue((band["mktcap"] <= 200).all())
        big = query.filter_universe(self.conn, df, mktcap=(100, float("inf")))
        self.assertTrue((big["mktcap"] >= 100).all())

    def test_exclude_st(self):
        df = query.combo(self.conn, 8, 1e7)
        f = query.filter_universe(self.conn, df, exclude_st=True)
        self.assertFalse(f["name"].fillna("").str.contains("ST").any())
        self.assertLessEqual(len(f), len(df))


if __name__ == "__main__":
    unittest.main(verbosity=2)
