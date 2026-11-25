"""SQLite storage with a small change log that allows undo."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'aktie',
    shares REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'yahoo',
    isin TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    xpath TEXT NOT NULL DEFAULT '',
    factor REAL NOT NULL DEFAULT 1,
    fallback_url TEXT NOT NULL DEFAULT '',
    fallback_xpath TEXT NOT NULL DEFAULT '',
    price REAL,
    prev_close REAL,
    prev_close_date TEXT,
    change_pct REAL,
    currency TEXT NOT NULL DEFAULT '',
    price_at TEXT,
    price_day TEXT,
    status TEXT NOT NULL DEFAULT '',
    error_since TEXT,
    sold_at TEXT,
    realized REAL,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dividends (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    position TEXT NOT NULL,
    amount REAL NOT NULL,
    tax REAL,
    tx TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    position_id INTEGER,
    position TEXT NOT NULL,
    shares REAL NOT NULL,
    amount REAL NOT NULL,
    gain REAL,
    fee REAL,
    tx TEXT
);
CREATE TABLE IF NOT EXISTS deposits (
    month TEXT PRIMARY KEY,
    gold REAL NOT NULL DEFAULT 0,
    depot REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fixed_costs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    amount REAL,
    interval TEXT NOT NULL DEFAULT 'monatlich',
    account TEXT NOT NULL DEFAULT '',
    due TEXT NOT NULL DEFAULT '',
    grp TEXT NOT NULL DEFAULT 'sonstiges',
    note TEXT NOT NULL DEFAULT '',
    installments INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spending (
    month TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (month, category_id)
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    balance REAL NOT NULL DEFAULT 0,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    month TEXT NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    due TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    paid INTEGER NOT NULL DEFAULT 0,
    reminded TEXT,
    carried INTEGER NOT NULL DEFAULT 0,
    fixed_cost_id INTEGER,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS daily (
    date TEXT PRIMARY KEY,
    total REAL,
    investments REAL,
    stocks_etfs REAL,
    stocks REAL,
    etfs REAL,
    crypto REAL,
    gold REAL
);
CREATE TABLE IF NOT EXISTS tr_transactions (
    tx TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    date TEXT NOT NULL,
    type TEXT NOT NULL,
    isin TEXT NOT NULL,
    name TEXT NOT NULL,
    asset_class TEXT NOT NULL DEFAULT '',
    shares REAL NOT NULL DEFAULT 0,
    price REAL,
    amount REAL NOT NULL DEFAULT 0,
    fee REAL NOT NULL DEFAULT 0,
    tax REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    level TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    text TEXT NOT NULL,
    entries TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0
);
"""

# Primary key columns per table, used by the change log
KEYS = {
    "positions": ("id",),
    "dividends": ("id",),
    "trades": ("id",),
    "deposits": ("month",),
    "fixed_costs": ("id",),
    "categories": ("id",),
    "spending": ("month", "category_id"),
    "accounts": ("id",),
    "payments": ("id",),
    "settings": ("key",),
    "tr_transactions": ("tx",),
}

DATA_TABLES = (
    "positions", "dividends", "trades", "deposits", "fixed_costs", "categories",
    "spending", "accounts", "payments", "daily", "tr_transactions",
)

# Columns added after the first release: table -> {column: definition}
ADDED_COLUMNS = {
    "positions": {"isin": "TEXT NOT NULL DEFAULT ''"},
    "dividends": {"tax": "REAL", "tx": "TEXT"},
    "trades": {"fee": "REAL", "tx": "TEXT"},
}


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("PRAGMA journal_mode = DELETE")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self._pending: list[dict] | None = None

    def _migrate(self) -> None:
        """Add columns of newer versions to an existing database."""
        for table, added in ADDED_COLUMNS.items():
            columns = {row["name"] for row in self.q(f"PRAGMA table_info({table})")}
            for column, definition in added.items():
                if column not in columns:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.set_setting("schema_version", SCHEMA_VERSION)

    # ------------------------------------------------------------------ basics
    def q(self, sql: str, params=()) -> list[dict]:
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def scalar(self, sql: str, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def execute(self, sql: str, params=()):
        return self.conn.execute(sql, params)

    @contextmanager
    def transaction(self):
        self.conn.execute("BEGIN")
        try:
            yield
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # --------------------------------------------------------------- settings
    def get_setting(self, key: str, default=None):
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # ------------------------------------------------------------ change log
    def _key_where(self, table: str, key: dict) -> tuple[str, tuple]:
        cols = KEYS[table]
        return " AND ".join(f"{c} = ?" for c in cols), tuple(key[c] for c in cols)

    def _row(self, table: str, key: dict) -> dict | None:
        where, params = self._key_where(table, key)
        return self.one(f"SELECT * FROM {table} WHERE {where}", params)

    @contextmanager
    def change(self, text: str):
        """Group writes into one undoable change. Use the tracked write helpers inside."""
        if self._pending is not None:
            yield
            return
        self._pending = []
        try:
            with self.transaction():
                yield
                if self._pending:
                    self.conn.execute(
                        "INSERT INTO changes (at, text, entries) VALUES (?, ?, ?)",
                        (now_iso(), text, json.dumps(self._pending)),
                    )
                    self.conn.execute(
                        "DELETE FROM changes WHERE id NOT IN (SELECT id FROM changes ORDER BY id DESC LIMIT 200)"
                    )
        finally:
            self._pending = None

    def _track(self, table: str, key: dict) -> None:
        if self._pending is None or table not in KEYS:
            return
        for entry in self._pending:
            if entry["table"] == table and entry["key"] == key:
                return
        self._pending.append({"table": table, "key": key, "old": self._row(table, key)})

    def insert(self, table: str, data: dict) -> int:
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(data.values()))
        if table in KEYS and KEYS[table] == ("id",):
            key = {"id": cur.lastrowid}
            if self._pending is not None:
                self._pending.append({"table": table, "key": key, "old": None})
        elif table in KEYS and self._pending is not None:
            key = {c: data[c] for c in KEYS[table]}
            self._pending.append({"table": table, "key": key, "old": None})
        return cur.lastrowid

    def upsert(self, table: str, data: dict) -> None:
        key = {c: data[c] for c in KEYS[table]}
        self._track(table, key)
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        updates = ", ".join(f"{c} = excluded.{c}" for c in data if c not in KEYS[table])
        conflict = ", ".join(KEYS[table])
        sql = f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT({conflict}) DO "
        sql += f"UPDATE SET {updates}" if updates else "NOTHING"
        self.conn.execute(sql, tuple(data.values()))

    def update(self, table: str, key: dict, data: dict) -> None:
        if not data:
            return
        self._track(table, key)
        where, params = self._key_where(table, key)
        sets = ", ".join(f"{c} = ?" for c in data)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", tuple(data.values()) + params)

    def delete(self, table: str, key: dict) -> None:
        self._track(table, key)
        where, params = self._key_where(table, key)
        self.conn.execute(f"DELETE FROM {table} WHERE {where}", params)

    def undo(self, change_id: int) -> str:
        change = self.one("SELECT * FROM changes WHERE id = ?", (change_id,))
        if not change:
            raise KeyError("Änderung nicht gefunden")
        if change["undone"]:
            raise ValueError("Diese Änderung wurde schon zurückgenommen")
        entries = json.loads(change["entries"])
        with self.transaction():
            for entry in reversed(entries):
                table, key, old = entry["table"], entry["key"], entry["old"]
                where, params = self._key_where(table, key)
                self.conn.execute(f"DELETE FROM {table} WHERE {where}", params)
                if old is not None:
                    cols = ", ".join(old)
                    marks = ", ".join("?" for _ in old)
                    self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(old.values()))
            self.conn.execute("UPDATE changes SET undone = 1 WHERE id = ?", (change_id,))
        return change["text"]

    # --------------------------------------------------------------- activity
    def log(self, text: str, level: str = "info") -> None:
        self.conn.execute("INSERT INTO activity (at, level, text) VALUES (?, ?, ?)", (now_iso(), level, text))
        self.conn.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 500)")

    # ---------------------------------------------------------------- backups
    def backup_to(self, target: Path) -> None:
        target = Path(target)
        dest = sqlite3.connect(str(target))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()

    def restore_from(self, source: Path) -> None:
        src = sqlite3.connect(str(source))
        try:
            src.backup(self.conn)
        finally:
            src.close()

    def clear_data(self) -> None:
        for table in DATA_TABLES:
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("DELETE FROM changes")

    def close(self) -> None:
        self.conn.close()


def copy_file(src: Path, dst: Path) -> None:
    shutil.copyfile(src, dst)
