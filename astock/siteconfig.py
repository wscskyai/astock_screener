# -*- coding: utf-8 -*-
"""站点运营配置：自动同步、备案号、网站标题、统计代码、注册开关。

全部存在 app_settings(k, v) 里；没写过的键用 DEFAULTS 兜底。
"""

from __future__ import annotations

import re
import sqlite3

DEFAULTS = {
    "site_title": "A 股选股系统",
    "icp": "",
    "analytics": "",
    "allow_register": "1",
    "auto_sync": "0",
    "sync_time": "17:30",
}

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def get(conn: sqlite3.Connection) -> dict:
    rows = dict(conn.execute("SELECT k, v FROM app_settings").fetchall())
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in rows and rows[k] is not None:
            out[k] = str(rows[k])
    return out


def as_public(cfg: dict) -> dict:
    """给未登录用户 / 普通用户看的字段。"""
    return {
        "site_title": cfg["site_title"],
        "icp": cfg["icp"],
        "allow_register": cfg["allow_register"] == "1",
    }


def as_admin(conn: sqlite3.Connection, cfg: dict) -> dict:
    out = as_public(cfg)
    out.update({
        "analytics": cfg["analytics"],
        "auto_sync": cfg["auto_sync"] == "1",
        "sync_time": cfg["sync_time"],
        "auto_sync_last": db_get_meta(conn, "auto_sync_last"),
    })
    return out


def db_get_meta(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row[0] if row else ""


def save(conn: sqlite3.Connection, data: dict) -> dict:
    site_title = (data.get("site_title") or "").strip() or DEFAULTS["site_title"]
    site_title = site_title[:60]
    icp = (data.get("icp") or "").strip()[:100]
    analytics = (data.get("analytics") or "").strip()[:5000]
    sync_time = (data.get("sync_time") or DEFAULTS["sync_time"]).strip()
    if not _TIME_RE.match(sync_time):
        return {"error": "同步时间格式应为 HH:MM"}
    values = {
        "site_title": site_title,
        "icp": icp,
        "analytics": analytics,
        "allow_register": "1" if data.get("allow_register") else "0",
        "auto_sync": "1" if data.get("auto_sync") else "0",
        "sync_time": sync_time,
    }
    for k, v in values.items():
        conn.execute(
            "INSERT INTO app_settings(k, v) VALUES(?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )
    conn.commit()
    return {"ok": True, **as_public(get(conn)),
            **{"analytics": analytics, "auto_sync": values["auto_sync"] == "1",
               "sync_time": sync_time,
               "auto_sync_last": db_get_meta(conn, "auto_sync_last")}}
