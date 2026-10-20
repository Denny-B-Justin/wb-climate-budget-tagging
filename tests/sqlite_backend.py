"""A local SQLite stand-in for the Unity Catalog back-end. Used by the tests only; the app itself talks to Unity Catalog."""
import contextlib
import os
import sqlite3

import pandas as pd

from src import constants as C
from src import queries as q

_SQLITE_TYPE = {"STRING": "TEXT", "INT": "INTEGER", "DOUBLE": "REAL", "BOOLEAN": "INTEGER", "TIMESTAMP": "TEXT"}


class SqliteSession(q._Session):
    def execute(self, sql, params=None):
        cur = self.conn.execute(sql, params or {})
        return cur.rowcount if cur.rowcount is not None else 0

    def query(self, sql, params=None):
        cur = self.conn.execute(sql, params or {})
        cols = [d[0] for d in cur.description] if cur.description else []
        return pd.DataFrame(cur.fetchall(), columns=cols)

    def insert(self, kind, records):
        if not records:
            return
        cols = C.column_names(kind)
        stmt = "INSERT INTO %s (%s) VALUES (%s)" % (
            q.table_fqn(kind), ", ".join(q._q(c) for c in cols), ", ".join(":" + c for c in cols))
        self.conn.executemany(stmt, records)

    def ts(self, name):
        return ":" + name


class SqliteBackend(q._Backend):
    name = "sqlite"

    def __init__(self, cfg, path):
        super().__init__(cfg)
        self.path = str(path)

    @contextlib.contextmanager
    def session(self, write=False):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            if write:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("BEGIN IMMEDIATE")
            try:
                yield SqliteSession(self, conn)
                if write:
                    conn.execute("COMMIT")
            except BaseException:
                if write:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def create_table_sql(self, kind):
        cols = ", ".join("%s %s" % (q._q(c.name), _SQLITE_TYPE[c.sql]) for c in C.TABLE_SCHEMAS[kind])
        return "CREATE TABLE IF NOT EXISTS %s (%s, PRIMARY KEY (`record_id`))" % (q.table_fqn(kind), cols)

    def existing_columns(self, s, kind):
        df = s.query("PRAGMA table_info(%s)" % q.table_fqn(kind))
        return df["name"].tolist() if len(df) else []

    def add_column_sql(self, kind, col):
        return "ALTER TABLE %s ADD COLUMN %s %s" % (q.table_fqn(kind), q._q(col.name), _SQLITE_TYPE[col.sql])


def install(path):
    """Point the storage layer at a throw-away SQLite file."""
    q.reset()
    q.use_backend(SqliteBackend(q.get_config(), path))
