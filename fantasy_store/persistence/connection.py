from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fantasy_store.config import SQLITE_BUSY_TIMEOUT_MS
from fantasy_store.domain.errors import DatabaseOpenError


def connect(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise DatabaseOpenError("failed to enable SQLite foreign_keys")
        return conn
    except sqlite3.Error as exc:
        raise DatabaseOpenError(str(exc)) from exc




def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing database without changing journal mode or file content.

    Used for schema compatibility gating so an unknown future schema can be
    rejected before any write-capable connection applies WAL mode.
    """
    try:
        uri = f"{Path(path).resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        return conn
    except sqlite3.Error as exc:
        raise DatabaseOpenError(str(exc)) from exc


@contextmanager
def connection(path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def begin_immediate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")

def connect_readonly_immutable(path: Path) -> sqlite3.Connection:
    """Open a closed SQLite snapshot without creating WAL/SHM sidecars.

    This is only for immutable backup/restore-temp validation. It must not be
    used for the live current DB because immutable mode ignores live WAL state.
    """
    try:
        uri = f"{Path(path).resolve().as_uri()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        return conn
    except sqlite3.Error as exc:
        raise DatabaseOpenError(str(exc)) from exc

