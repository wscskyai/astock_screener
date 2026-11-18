# -*- coding: utf-8 -*-
"""形态扫描：全市场算一遍「做小做底 / 旗形整理」信号，写入 pattern_signal 表。

    python tools/build_patterns.py

耗时约 60~90 秒（5578 只 × 1420 根日线）。日线更新后跑一次即可，
界面上「形态扫描」也可以单独点同步。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from astock import db
from astock import patterns


def main() -> int:
    conn = db.connect()

    def progress(done, total, msg):
        print(f"  {msg}", flush=True)

    st = patterns.build_all(conn, progress=progress)
    print(f"完成：{st['symbols']} 只  "
          f"做小做底 {st['base_ok']} 只 · 旗面整理中 {st['flag_ok']} 只 · "
          f"突破确认 {st['flag_brk']} 只 · 用时 {st['elapsed']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
