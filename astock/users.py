# -*- coding: utf-8 -*-
"""用户 / 会话 / 收藏（含笔记）。

密码用 PBKDF2-HMAC-SHA256 加盐存储；会话用随机 token 存库，走 HttpOnly Cookie。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

ITERATIONS = 200_000
SESSION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT DEFAULT 'user',
    nickname      TEXT,
    created_at    TEXT,
    last_login    TEXT
);

CREATE TABLE IF NOT EXISTS sessions(
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS favorites(
    user_id    INTEGER NOT NULL,
    symbol     TEXT NOT NULL,
    note       TEXT DEFAULT '',
    tags       TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY(user_id, symbol)
);

CREATE TABLE IF NOT EXISTS fav_groups(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS fav_notes(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    symbol     TEXT NOT NULL,
    note       TEXT NOT NULL,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fav_notes ON fav_notes(user_id, symbol, id);
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(favorites)")}
    if "group_id" not in cols:
        conn.execute("ALTER TABLE favorites ADD COLUMN group_id INTEGER")
    conn.commit()
    _migrate_role(conn)
    _migrate_nickname(conn)
    _migrate_notes(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires "
                 "ON sessions(expires_at)")
    conn.commit()


def _migrate_nickname(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "nickname" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT")
        conn.commit()


def _migrate_role(conn: sqlite3.Connection) -> None:
    """旧库补 role 列；把最早创建的用户设为系统管理员。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "role" in cols:
        return
    conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
    conn.commit()
    row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if row:
        conn.execute("UPDATE users SET role='admin' WHERE id=?", (row[0],))
        conn.commit()


def _migrate_notes(conn: sqlite3.Connection) -> None:
    """把旧版 favorites.note 里的一次性笔记搬到时间线（只搬一次）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(favorites)")}
    if "note" not in cols:
        return
    rows = conn.execute(
        "SELECT f.user_id, f.symbol, f.note FROM favorites f "
        "WHERE f.note IS NOT NULL AND f.note <> '' "
        "AND NOT EXISTS (SELECT 1 FROM fav_notes n WHERE n.user_id=f.user_id AND n.symbol=f.symbol)"
    ).fetchall()
    for uid, sym, note in rows:
        conn.execute(
            "INSERT INTO fav_notes(user_id, symbol, note, created_at) VALUES(?,?,?,?)",
            (uid, sym, note, _now()),
        )
    if rows:
        conn.commit()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 密码
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        it, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(it))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 用户
def create_user(conn: sqlite3.Connection, username: str, password: str,
                role: str = "user") -> dict:
    username = (username or "").strip()
    if not (2 <= len(username) <= 20):
        return {"error": "用户名 2~20 个字符"}
    if len(password or "") < 6:
        return {"error": "密码至少 6 位"}
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        return {"error": "用户名已存在"}
    if role not in ("user", "admin"):
        role = "user"
    # 空库注册的第一个用户自动成为系统管理员
    if conn.execute("SELECT COUNT(1) FROM users").fetchone()[0] == 0:
        role = "admin"
    cur = conn.execute(
        "INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)",
        (username, hash_password(password), role, _now()),
    )
    conn.commit()
    return {"id": cur.lastrowid, "username": username, "nickname": username, "role": role}


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    row = conn.execute(
        "SELECT id, username, password_hash, COALESCE(role, 'user'), created_at, last_login, nickname "
        "FROM users WHERE username=?", ((username or "").strip(),)
    ).fetchone()
    if not row or not verify_password(password or "", row[2]):
        return None
    conn.execute("UPDATE users SET last_login=? WHERE id=?", (_now(), row[0]))
    conn.commit()
    return {"id": row[0], "username": row[1], "role": row[3],
            "created_at": row[4], "last_login": row[5],
            "nickname": row[6] or row[1]}


def change_password(conn: sqlite3.Connection, user_id: int, old: str, new: str) -> dict:
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not verify_password(old, row[0]):
        return {"error": "原密码不正确"}
    if len(new or "") < 6:
        return {"error": "新密码至少 6 位"}
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), user_id))
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 用户管理（管理员）
def _admin_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(1) FROM users WHERE COALESCE(role, 'user')='admin'"
    ).fetchone()[0]


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT u.id, u.username, COALESCE(u.role, 'user'), u.created_at, u.last_login,
               (SELECT COUNT(1) FROM favorites f WHERE f.user_id=u.id),
               (SELECT COUNT(1) FROM fav_notes n WHERE n.user_id=u.id),
               u.nickname
        FROM users u ORDER BY u.id
    """).fetchall()
    return [{
        "id": r[0], "username": r[1], "role": r[2],
        "created_at": r[3], "last_login": r[4],
        "favorites": r[5] or 0, "notes": r[6] or 0,
        "nickname": r[7] or r[1],
    } for r in rows]


def set_nickname(conn: sqlite3.Connection, user_id: int, nickname: str) -> dict:
    nickname = (nickname or "").strip()
    if not (1 <= len(nickname) <= 20):
        return {"error": "昵称 1~20 个字符"}
    conn.execute("UPDATE users SET nickname=? WHERE id=?", (nickname, int(user_id)))
    conn.commit()
    return {"ok": True, "nickname": nickname}


def set_role(conn: sqlite3.Connection, target_id: int, role: str, actor_id: int) -> dict:
    role = (role or "").strip()
    if role not in ("admin", "user"):
        return {"error": "角色不正确"}
    if int(target_id or 0) == int(actor_id or 0):
        return {"error": "不能修改自己的角色"}
    row = conn.execute(
        "SELECT COALESCE(role, 'user') FROM users WHERE id=?", (int(target_id),)
    ).fetchone()
    if not row:
        return {"error": "用户不存在"}
    if row[0] == "admin" and role == "user" and _admin_count(conn) <= 1:
        return {"error": "至少要保留一个管理员"}
    conn.execute("UPDATE users SET role=? WHERE id=?", (role, int(target_id)))
    conn.commit()
    return {"ok": True, "role": role}


def reset_password(conn: sqlite3.Connection, target_id: int, new: str,
                   actor_id: int) -> dict:
    if int(target_id or 0) == int(actor_id or 0):
        return {"error": "自己的密码请用「修改密码」"}
    if len(new or "") < 6:
        return {"error": "新密码至少 6 位"}
    if not conn.execute("SELECT 1 FROM users WHERE id=?", (int(target_id),)).fetchone():
        return {"error": "用户不存在"}
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (hash_password(new), int(target_id)))
    # 被重置密码的用户强制重新登录
    conn.execute("DELETE FROM sessions WHERE user_id=?", (int(target_id),))
    conn.commit()
    return {"ok": True}


def delete_user(conn: sqlite3.Connection, target_id: int, actor_id: int) -> dict:
    target_id = int(target_id or 0)
    if target_id == int(actor_id or 0):
        return {"error": "不能删除当前登录的账号"}
    row = conn.execute(
        "SELECT username, COALESCE(role, 'user') FROM users WHERE id=?", (target_id,)
    ).fetchone()
    if not row:
        return {"error": "用户不存在"}
    if row[1] == "admin" and _admin_count(conn) <= 1:
        return {"error": "至少要保留一个管理员"}
    try:
        conn.execute("DELETE FROM screens WHERE user_id=?", (target_id,))
    except sqlite3.OperationalError:
        pass
    conn.execute("DELETE FROM sessions WHERE user_id=?", (target_id,))
    conn.execute("DELETE FROM fav_notes WHERE user_id=?", (target_id,))
    conn.execute("DELETE FROM favorites WHERE user_id=?", (target_id,))
    conn.execute("DELETE FROM fav_groups WHERE user_id=?", (target_id,))
    conn.execute("DELETE FROM users WHERE id=?", (target_id,))
    conn.commit()
    return {"ok": True, "username": row[0]}


# ---------------------------------------------------------------- 会话
def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO sessions(token, user_id, expires_at, created_at) VALUES(?,?,?,?)",
        (token, user_id, exp, _now()),
    )
    conn.commit()
    return token


def user_by_token(conn: sqlite3.Connection, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT s.user_id, u.username, s.expires_at, COALESCE(u.role, 'user'), "
        "u.created_at, u.last_login, u.nickname "
        "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,),
    ).fetchone()
    if not row:
        return None
    if row[2] < _now():            # 过期
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        return None
    return {"id": row[0], "username": row[1], "role": row[3],
            "created_at": row[4], "last_login": row[5],
            "nickname": row[6] or row[1]}


def drop_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()


# ---------------------------------------------------------------- 分组
def list_groups(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute("""
        SELECT g.id, g.name,
               (SELECT COUNT(1) FROM favorites f WHERE f.user_id=g.user_id AND f.group_id=g.id) AS n
        FROM fav_groups g WHERE g.user_id=? ORDER BY g.id
    """, (user_id,)).fetchall()
    groups = [{"id": r[0], "name": r[1], "count": r[2]} for r in rows]
    un = conn.execute(
        "SELECT COUNT(1) FROM favorites WHERE user_id=? AND group_id IS NULL", (user_id,)
    ).fetchone()[0]
    return [{"id": 0, "name": "未分组", "count": un}] + groups


RESERVED_GROUP_NAMES = {"未分组", "全部"}


def create_group(conn: sqlite3.Connection, user_id: int, name: str) -> dict:
    name = (name or "").strip()
    if not (1 <= len(name) <= 12):
        return {"error": "分组名 1~12 个字符"}
    if name in RESERVED_GROUP_NAMES:
        return {"error": f"「{name}」是保留名称，换一个" }
    if conn.execute("SELECT 1 FROM fav_groups WHERE user_id=? AND name=?", (user_id, name)).fetchone():
        return {"error": "分组已存在"}
    cur = conn.execute(
        "INSERT INTO fav_groups(user_id, name, created_at) VALUES(?,?,?)", (user_id, name, _now())
    )
    conn.commit()
    return {"ok": True, "id": cur.lastrowid, "name": name}


def rename_group(conn: sqlite3.Connection, user_id: int, gid: int, name: str) -> dict:
    name = (name or "").strip()
    if not (1 <= len(name) <= 12):
        return {"error": "分组名 1~12 个字符"}
    conn.execute("UPDATE fav_groups SET name=? WHERE id=? AND user_id=?", (name, gid, user_id))
    conn.commit()
    return {"ok": True}


def delete_group(conn: sqlite3.Connection, user_id: int, gid: int) -> dict:
    """删分组，组内股票回到「未分组」。"""
    conn.execute("UPDATE favorites SET group_id=NULL WHERE user_id=? AND group_id=?", (user_id, gid))
    conn.execute("DELETE FROM fav_groups WHERE id=? AND user_id=?", (gid, user_id))
    conn.commit()
    return {"ok": True}


def set_group(conn: sqlite3.Connection, user_id: int, symbol: str, gid: int | None) -> dict:
    symbol = str(symbol).zfill(6)
    if gid:
        if not conn.execute("SELECT 1 FROM fav_groups WHERE id=? AND user_id=?", (gid, user_id)).fetchone():
            return {"error": "分组不存在"}
    conn.execute(
        "UPDATE favorites SET group_id=?, updated_at=? WHERE user_id=? AND symbol=?",
        (gid or None, _now(), user_id, symbol),
    )
    conn.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 收藏
def add_favorite(conn: sqlite3.Connection, user_id: int, symbol: str,
                 group_id: int | None = None) -> dict:
    symbol = str(symbol).zfill(6)
    if not conn.execute("SELECT 1 FROM securities WHERE symbol=?", (symbol,)).fetchone():
        return {"error": f"没有找到股票 {symbol}"}
    ts = _now()
    conn.execute(
        "INSERT INTO favorites(user_id, symbol, group_id, created_at, updated_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(user_id, symbol) DO UPDATE SET "
        "group_id=COALESCE(excluded.group_id, favorites.group_id), updated_at=excluded.updated_at",
        (user_id, symbol, group_id, ts, ts),
    )
    conn.commit()
    return {"ok": True, "symbol": symbol}


def remove_favorite(conn: sqlite3.Connection, user_id: int, symbol: str,
                    delete_notes: bool = False) -> dict:
    symbol = str(symbol).zfill(6)
    removed = 0
    if delete_notes:
        cur = conn.execute(
            "DELETE FROM fav_notes WHERE user_id=? AND symbol=?", (user_id, symbol))
        removed = cur.rowcount
    conn.execute("DELETE FROM favorites WHERE user_id=? AND symbol=?", (user_id, symbol))
    conn.commit()
    return {"ok": True, "removed_notes": removed}


def add_note(conn: sqlite3.Connection, user_id: int, symbol: str, note: str) -> dict:
    note = (note or "").strip()
    if not note:
        return {"error": "笔记内容为空"}
    if len(note) > 2000:
        return {"error": "笔记过长（上限 2000 字）"}
    symbol = str(symbol).zfill(6)
    if not conn.execute("SELECT 1 FROM favorites WHERE user_id=? AND symbol=?",
                        (user_id, symbol)).fetchone():
        return {"error": "请先收藏该股票"}
    cur = conn.execute(
        "INSERT INTO fav_notes(user_id, symbol, note, created_at) VALUES(?,?,?,?)",
        (user_id, symbol, note, _now()),
    )
    conn.execute("UPDATE favorites SET updated_at=? WHERE user_id=? AND symbol=?",
                 (_now(), user_id, symbol))
    conn.commit()
    return {"ok": True, "id": cur.lastrowid}


def list_notes(conn: sqlite3.Connection, user_id: int, symbol: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, note, created_at FROM fav_notes WHERE user_id=? AND symbol=? ORDER BY id DESC",
        (user_id, str(symbol).zfill(6)),
    ).fetchall()
    return [{"id": r[0], "note": r[1], "created_at": r[2]} for r in rows]


def delete_note(conn: sqlite3.Connection, user_id: int, note_id: int) -> dict:
    conn.execute("DELETE FROM fav_notes WHERE id=? AND user_id=?", (note_id, user_id))
    conn.commit()
    return {"ok": True}


def list_favorites(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute("""
        SELECT f.symbol, s.name, s.board, s.mktcap, f.group_id, g.name, f.created_at, f.updated_at,
               (SELECT COUNT(1) FROM fav_notes n WHERE n.user_id=f.user_id AND n.symbol=f.symbol),
               (SELECT note FROM fav_notes n WHERE n.user_id=f.user_id AND n.symbol=f.symbol
                 ORDER BY n.id DESC LIMIT 1),
               (SELECT created_at FROM fav_notes n WHERE n.user_id=f.user_id AND n.symbol=f.symbol
                 ORDER BY n.id DESC LIMIT 1)
        FROM favorites f
        LEFT JOIN securities s ON s.symbol = f.symbol
        LEFT JOIN fav_groups g ON g.id = f.group_id
        WHERE f.user_id=? ORDER BY f.updated_at DESC
    """, (user_id,)).fetchall()
    out = []
    for sym, name, board, mktcap, gid, gname, created, updated, n_notes, latest, latest_at in rows:
        bars = conn.execute(
            "SELECT date, close FROM bars WHERE symbol=? ORDER BY date DESC LIMIT 2", (sym,)
        ).fetchall()
        close = bars[0][1] if bars else None
        chg = (bars[0][1] / bars[1][1] - 1) * 100 if len(bars) == 2 and bars[1][1] else None
        out.append({
            "symbol": sym, "name": name or "", "board": board or "",
            "mktcap": round(mktcap, 1) if mktcap else None,
            "close": round(close, 2) if close else None,
            "chg": round(chg, 2) if chg is not None else None,
            "date": bars[0][0] if bars else None,
            "group_id": gid or 0, "group_name": gname or "未分组",
            "notes": n_notes or 0,
            "latest_note": latest or "", "latest_at": latest_at,
            "created_at": created, "updated_at": updated,
        })
    return out


def favorite_symbols(conn: sqlite3.Connection, user_id: int) -> list[str]:
    return [r[0] for r in conn.execute("SELECT symbol FROM favorites WHERE user_id=?", (user_id,))]
