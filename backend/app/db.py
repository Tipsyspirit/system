"""SQLite storage. One row of JSON state per user; ml model stored beside it."""
import json
import sqlite3
import threading
from pathlib import Path

_lock = threading.RLock()


class DB:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with _lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users(
              id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, salt TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tokens(
              token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS players(
              user_id INTEGER PRIMARY KEY, state TEXT NOT NULL, model TEXT NOT NULL DEFAULT '{}');
            """)
            self.conn.commit()

    def q(self, sql, args=()):
        with _lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with _lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    def get_player(self, uid):
        r = self.one("SELECT state, model FROM players WHERE user_id=?", (uid,))
        if not r:
            return None, None
        return json.loads(r["state"]), json.loads(r["model"])

    def put_player(self, uid, state, model):
        self.x("INSERT INTO players(user_id,state,model) VALUES(?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, model=excluded.model",
               (uid, json.dumps(state), json.dumps(model)))
