# cache.py
import sqlite3, time, json, os
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "cache.db")
Path(DB_PATH).touch(exist_ok=True)

EXPECTED_COLS = {"k", "v", "ts"}

def _current_cols(con: sqlite3.Connection):
    try:
        rows = con.execute("PRAGMA table_info(cache)").fetchall()
        return {row[1] for row in rows}  # row[1] is column name
    except sqlite3.OperationalError:
        return set()

def _recreate_cache_table(con: sqlite3.Connection):
    con.execute("DROP TABLE IF EXISTS cache")
    con.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            k  TEXT PRIMARY KEY,
            v  TEXT NOT NULL,
            ts INTEGER NOT NULL
        )
    """)
    con.commit()

def _ensure():
    with sqlite3.connect(DB_PATH) as con:
        cols = _current_cols(con)
        if cols != EXPECTED_COLS:
            _recreate_cache_table(con)

_ensure()

def cache_get(key: str, ttl_seconds: int):
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute("SELECT v, ts FROM cache WHERE k=?", (key,)).fetchone()
        if not row:
            return None
        v, ts = row
        if time.time() - ts > ttl_seconds:
            con.execute("DELETE FROM cache WHERE k=?", (key,))
            con.commit()
            return None
        try:
            return json.loads(v)
        except Exception:
            return None

def cache_set(key: str, value):
    s = json.dumps(value)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT OR REPLACE INTO cache (k, v, ts) VALUES (?, ?, ?)",
            (key, s, int(time.time()))
        )
        con.commit()
