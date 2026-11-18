# -*- coding: utf-8 -*-
"""界面自检：确认 HTML 里的元素与 JS 引用的 id 对得上，接口能返回数据。

    python web/check_ui.py                       # 默认 5001
    python web/check_ui.py http://127.0.0.1:5002 # 指定地址
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5001"


def check_static() -> bool:
    js = open(os.path.join(HERE, "static", "app.js"), encoding="utf-8").read()
    html = open(os.path.join(HERE, "templates", "index.html"), encoding="utf-8").read()
    ids = set(re.findall(r'\$\("([^"]+)"\)', js))
    missing = [i for i in sorted(ids) if f'id="{i}"' not in html]
    print(f"  JS 引用 {len(ids)} 个元素，HTML 缺失：{missing or '无'}")
    keys = sorted(set(re.findall(r'data-key="(\w+)"', html)))
    print(f"  可排序字段：{keys}")

    # 收藏独立页
    fjs = open(os.path.join(HERE, "static", "fav.js"), encoding="utf-8").read()
    fhtml = open(os.path.join(HERE, "templates", "fav.html"), encoding="utf-8").read()
    fids = set(re.findall(r'\$\("([^"]+)"\)', fjs))
    fmissing = [i for i in sorted(fids) if f'id="{i}"' not in fhtml]
    print(f"  收藏页 JS 引用 {len(fids)} 个元素，HTML 缺失：{fmissing or '无'}")

    css = open(os.path.join(HERE, "static", "style.css"), encoding="utf-8").read()
    hidden_ok = re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css) is not None
    print(f"  [hidden] 强制隐藏规则：{'有' if hidden_ok else '缺失（会导致隐藏字段仍显示）'}")
    return not missing and not fmissing and hidden_ok


def check_api() -> bool:
    ok = True
    for path, expect in (
        ("/", "HOME"),
        ("/screen", "HTML"),
        ("/fav", "HTML2"),
        ("/api/screen?conds=" + urllib.parse.quote(
            '[{"k":"drop_pct","min":20},{"k":"pb","min":0,"max":5},{"k":"exclude_st","on":true}]'), "screen"),
        ("/api/fields", "fields"),
        ("/api/kline/600310?n=60", "kline"),
        ("/api/datasets", "datasets"),
        ("/api/suggest?q=cyhb", "suggest"),
        ("/api/industries", "industries"),
        ("/api/me", "me"),
    ):
        try:
            body = urllib.request.urlopen(BASE + path, timeout=60).read()
        except Exception as e:  # noqa: BLE001
            print(f"  ❌ {path}  {type(e).__name__}: {e}")
            ok = False
            continue
        if expect == "HOME":
            print(f"  ✅ /  指数首页 {len(body)} 字节")
        elif expect == "HTML":
            print(f"  ✅ /screen  选股页 {len(body)} 字节")
        elif expect == "HTML2":
            print(f"  ✅ /fav  收藏页 {len(body)} 字节")
        else:
            d = json.loads(body)
            if "error" in d:
                print(f"  ❌ {path} {d['error']}")
                ok = False
            elif expect == "screen":
                r0 = d["rows"][0] if d["rows"] else {}
                print(f"  ✅ screen 命中 {d['count']} 只，首行 {r0.get('name')} "
                      f"ROE={r0.get('roe_annual')} 市值={r0.get('mktcap')}")
            elif expect == "datasets":
                print(f"  ✅ datasets {len(d['datasets'])} 项，库 {d['db_mb']} MB")
            elif expect == "fields":
                print(f"  ✅ fields {len(d['fields'])} 个可筛指标")
            elif expect == "industries":
                print(f"  ✅ industries {len(d['industries'])} 个行业")
            elif expect == "suggest":
                hit = d["items"][0] if d["items"] else {}
                pi = "拼音补全 " + ("正常" if hit.get("symbol") else "❌ 无结果")
                print(f"  ✅ suggest {pi}（cyhb → {hit.get('symbol', '')} {hit.get('name', '')}）")
            elif expect == "me":
                u = d.get("user")
                print(f"  ✅ me 当前{'已登录 ' + u['username'] if u else '未登录'}")
            else:
                print(f"  ✅ kline {d['name']} {len(d['bars'])} 根")
    return ok


if __name__ == "__main__":
    print("静态自检：")
    a = check_static()
    print("接口自检：")
    b = check_api()
    print("结果：", "通过" if (a and b) else "有问题")
    sys.exit(0 if (a and b) else 1)
