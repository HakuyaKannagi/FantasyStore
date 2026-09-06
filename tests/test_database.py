from pathlib import Path
import sqlite3

import pytest

from fantasy_store.domain.errors import DatabaseSchemaCorruptError, DatabaseVersionUnsupportedError
from fantasy_store.persistence.connection import connect
from fantasy_store.persistence.migration import (
    PACK_DB_NAME,
    USER_DB_NAME,
    get_schema_version,
    initialize_database,
    sqlite_supports_strict_tables,
)


def test_sqlite_supports_strict_tables_in_current_environment():
    assert sqlite_supports_strict_tables() is True


@pytest.mark.parametrize("schema_name,filename", [
    (USER_DB_NAME, "user_data.db"),
    (PACK_DB_NAME, "pack_manifest.db"),
])
def test_new_database_ddl_and_schema_version(tmp_path: Path, schema_name: str, filename: str):
    db = tmp_path / filename
    assert initialize_database(db, schema_name) == 1
    assert get_schema_version(db, schema_name) == 1
    conn = connect(db)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()


def test_existing_incomplete_database_is_corrupt_not_silently_recreated(tmp_path: Path):
    db = tmp_path / "user_data.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE unrelated(id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseSchemaCorruptError):
        initialize_database(db, USER_DB_NAME)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='unrelated'").fetchone() is not None
    finally:
        conn.close()


def test_future_schema_version_is_rejected(tmp_path: Path):
    db = tmp_path / "user_data.db"
    initialize_database(db, USER_DB_NAME)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseVersionUnsupportedError):
        initialize_database(db, USER_DB_NAME)


def test_price_sort_derived_keys_order_correctly_in_sqlite():
    from fantasy_store.domain.money import MoneyLiteral, price_sort_key

    values = [
        ("a", MoneyLiteral("385", "8")),
        ("b", MoneyLiteral("39", "9")),
        ("c", MoneyLiteral("499", "8")),
        ("d", MoneyLiteral("5", "10")),
        ("z", MoneyLiteral("0", "0")),
    ]
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE prices(id TEXT, magnitude INTEGER, digits TEXT)")
    for item_id, literal in values:
        magnitude, digits = price_sort_key(literal)
        conn.execute("INSERT INTO prices VALUES(?,?,?)", (item_id, magnitude, digits))
    ordered = [row[0] for row in conn.execute("SELECT id FROM prices ORDER BY magnitude, digits")]
    conn.close()
    assert ordered == ["z", "a", "b", "c", "d"]


def test_future_schema_rejection_does_not_change_journal_mode(tmp_path: Path):
    db = tmp_path / "user_data.db"
    initialize_database(db, USER_DB_NAME)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("UPDATE schema_meta SET value='2' WHERE key='schema_version'")
    conn.execute("PRAGMA user_version=2")
    conn.commit()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    conn.close()

    with pytest.raises(DatabaseVersionUnsupportedError):
        initialize_database(db, USER_DB_NAME)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    finally:
        conn.close()
