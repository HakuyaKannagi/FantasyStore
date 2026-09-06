from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

from fantasy_store.domain.errors import ValidationError
from fantasy_store.pack.validator import PreparedItemRecord
from .connection import begin_immediate, connect


@dataclass(frozen=True, slots=True)
class InstalledPack:
    pack_id: str
    pack_name: str
    pack_version: str
    author: str
    description: str
    schema_version: int
    is_enabled: bool
    content_digest: str
    install_dir: str
    installed_at: str
    updated_at: str


FailureHook = Callable[[str], None]


def _pack_from_row(row: sqlite3.Row) -> InstalledPack:
    return InstalledPack(
        pack_id=row["pack_id"],
        pack_name=row["pack_name"],
        pack_version=row["pack_version"],
        author=row["author"],
        description=row["description"],
        schema_version=int(row["schema_version"]),
        is_enabled=bool(row["is_enabled"]),
        content_digest=row["content_digest"],
        install_dir=row["install_dir"],
        installed_at=row["installed_at"],
        updated_at=row["updated_at"],
    )


class PackRepository:
    """SQLite repository for pack_manifest.db only.

    File-system switching, Pack locks, validation, and Recovery decisions remain
    outside this repository. Transaction helpers use BEGIN IMMEDIATE per the
    frozen detailed design.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def get_installed_pack(self, pack_id: str) -> InstalledPack | None:
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT * FROM installed_packs WHERE pack_id=?", (pack_id,)).fetchone()
            return _pack_from_row(row) if row else None
        finally:
            conn.close()

    def list_installed_packs(self) -> list[InstalledPack]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute("SELECT * FROM installed_packs ORDER BY pack_id").fetchall()
            return [_pack_from_row(row) for row in rows]
        finally:
            conn.close()

    def get_digest(self, pack_id: str) -> str | None:
        conn = connect(self.db_path)
        try:
            row = conn.execute("SELECT content_digest FROM installed_packs WHERE pack_id=?", (pack_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def list_items_for_pack(self, pack_id: str) -> list[sqlite3.Row]:
        conn = connect(self.db_path)
        try:
            return conn.execute(
                "SELECT * FROM items_master WHERE pack_id=? ORDER BY item_id",
                (pack_id,),
            ).fetchall()
        finally:
            conn.close()

    def count_items_for_pack(self, pack_id: str) -> int:
        conn = connect(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM items_master WHERE pack_id=?", (pack_id,)).fetchone()[0])
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.db_path)
        try:
            begin_immediate(conn)
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    @staticmethod
    def _insert_pack(conn: sqlite3.Connection, pack: InstalledPack) -> None:
        conn.execute(
            """
            INSERT INTO installed_packs(
                pack_id,pack_name,pack_version,author,description,schema_version,
                is_enabled,content_digest,install_dir,installed_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pack.pack_id,
                pack.pack_name,
                pack.pack_version,
                pack.author,
                pack.description,
                pack.schema_version,
                1 if pack.is_enabled else 0,
                pack.content_digest,
                pack.install_dir,
                pack.installed_at,
                pack.updated_at,
            ),
        )

    @staticmethod
    def _insert_items(conn: sqlite3.Connection, items: Iterable[PreparedItemRecord]) -> None:
        rows = [
            (
                item.pack_id,
                item.item_id,
                item.item_name,
                item.price_significand,
                item.price_exponent,
                item.price_magnitude,
                item.price_sort_digits,
                item.category,
                item.description,
                item.attributes_json,
                item.images_json,
                item.search_text,
            )
            for item in items
        ]
        conn.executemany(
            """
            INSERT INTO items_master(
                pack_id,item_id,item_name,price_significand,price_exponent,
                price_magnitude,price_sort_digits,category,description,
                attributes_json,images_json,search_text
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )

    def insert_pack_with_items(
        self,
        pack: InstalledPack,
        items: Iterable[PreparedItemRecord],
        *,
        failure_hook: FailureHook | None = None,
    ) -> None:
        with self.transaction() as conn:
            if failure_hook: failure_hook("after_begin")
            self._insert_pack(conn, pack)
            if failure_hook: failure_hook("after_pack_insert")
            self._insert_items(conn, items)
            if failure_hook: failure_hook("after_items_insert")

    def replace_pack_with_items(
        self,
        pack_id: str,
        *,
        pack_name: str,
        pack_version: str,
        author: str,
        description: str,
        schema_version: int,
        content_digest: str,
        install_dir: str,
        updated_at: str,
        items: Iterable[PreparedItemRecord],
        failure_hook: FailureHook | None = None,
    ) -> None:
        with self.transaction() as conn:
            existing = conn.execute("SELECT pack_id FROM installed_packs WHERE pack_id=?", (pack_id,)).fetchone()
            if existing is None:
                raise ValidationError("cannot update a pack that is not installed")
            if failure_hook: failure_hook("after_begin")
            conn.execute("DELETE FROM items_master WHERE pack_id=?", (pack_id,))
            if failure_hook: failure_hook("after_items_delete")
            self._insert_items(conn, items)
            if failure_hook: failure_hook("after_items_insert")
            cur = conn.execute(
                """
                UPDATE installed_packs
                SET pack_name=?, pack_version=?, author=?, description=?, schema_version=?,
                    content_digest=?, install_dir=?, updated_at=?
                WHERE pack_id=?
                """,
                (
                    pack_name,
                    pack_version,
                    author,
                    description,
                    schema_version,
                    content_digest,
                    install_dir,
                    updated_at,
                    pack_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValidationError("pack metadata update did not affect exactly one row")
            if failure_hook: failure_hook("after_metadata_update")

    def uninstall_pack(self, pack_id: str, *, failure_hook: FailureHook | None = None) -> bool:
        """Remove derived catalog rows and installed-pack metadata atomically.

        Filesystem movement is intentionally outside this repository. History and
        user_data.db are not touched.
        """
        with self.transaction() as conn:
            if failure_hook:
                failure_hook("after_begin")
            row = conn.execute("SELECT 1 FROM installed_packs WHERE pack_id=?", (pack_id,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM items_master WHERE pack_id=?", (pack_id,))
            if failure_hook:
                failure_hook("after_items_delete")
            cur = conn.execute("DELETE FROM installed_packs WHERE pack_id=?", (pack_id,))
            if failure_hook:
                failure_hook("after_pack_delete")
            return cur.rowcount == 1

    def set_enabled(self, pack_id: str, enabled: bool) -> bool:
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE installed_packs SET is_enabled=? WHERE pack_id=?",
                (1 if enabled else 0, pack_id),
            )
            return cur.rowcount == 1

    def delete_items(self, pack_id: str, *, conn: sqlite3.Connection | None = None) -> int:
        if conn is not None:
            return conn.execute("DELETE FROM items_master WHERE pack_id=?", (pack_id,)).rowcount
        with self.transaction() as tx:
            return tx.execute("DELETE FROM items_master WHERE pack_id=?", (pack_id,)).rowcount

    def bulk_insert_items(self, items: Iterable[PreparedItemRecord], *, conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            self._insert_items(conn, items)
            return
        with self.transaction() as tx:
            self._insert_items(tx, items)


    def replace_items_only(self, pack_id: str, items: Iterable[PreparedItemRecord]) -> None:
        with self.transaction() as conn:
            if conn.execute("SELECT 1 FROM installed_packs WHERE pack_id=?", (pack_id,)).fetchone() is None:
                raise ValidationError("cannot rebuild items for a pack that is not installed")
            conn.execute("DELETE FROM items_master WHERE pack_id=?", (pack_id,))
            self._insert_items(conn, items)


    # ---- Phase 5 application read queries ---------------------------
    def count_installed_packs(self) -> int:
        conn = connect(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM installed_packs").fetchone()[0])
        finally:
            conn.close()

    def count_enabled_packs(self) -> int:
        conn = connect(self.db_path)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM installed_packs WHERE is_enabled=1").fetchone()[0])
        finally:
            conn.close()

    def get_item(self, pack_id: str, item_id: str) -> sqlite3.Row | None:
        conn = connect(self.db_path)
        try:
            return conn.execute(
                "SELECT * FROM items_master WHERE pack_id=? AND item_id=?",
                (pack_id, item_id),
            ).fetchone()
        finally:
            conn.close()

    def list_enabled_categories(self) -> list[str]:
        conn = connect(self.db_path)
        try:
            rows = conn.execute(
                """SELECT DISTINCT i.category
                   FROM items_master i
                   JOIN installed_packs p ON p.pack_id=i.pack_id
                   WHERE p.is_enabled=1
                   ORDER BY i.category COLLATE BINARY"""
            ).fetchall()
            return [str(row[0]) for row in rows]
        finally:
            conn.close()

    def query_enabled_items(
        self,
        *,
        query_pattern: str | None,
        category: str | None,
        min_key: tuple[int, str] | None,
        max_key: tuple[int, str] | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> tuple[list[sqlite3.Row], int]:
        order_sql = {
            "name_asc": "i.item_name COLLATE BINARY ASC, i.pack_id ASC, i.item_id ASC",
            "price_asc": "i.price_magnitude ASC, i.price_sort_digits ASC, i.pack_id ASC, i.item_id ASC",
            "price_desc": "i.price_magnitude DESC, i.price_sort_digits DESC, i.pack_id ASC, i.item_id ASC",
        }.get(sort)
        if order_sql is None:
            raise ValidationError("invalid catalog sort")
        where = ["p.is_enabled=1"]
        params: list[object] = []
        if query_pattern is not None:
            where.append("i.search_text LIKE ? ESCAPE '\\'")
            params.append(query_pattern)
        if category is not None:
            where.append("i.category=?")
            params.append(category)
        if min_key is not None:
            mag, digits = min_key
            where.append("(i.price_magnitude > ? OR (i.price_magnitude = ? AND i.price_sort_digits >= ?))")
            params.extend((mag, mag, digits))
        if max_key is not None:
            mag, digits = max_key
            where.append("(i.price_magnitude < ? OR (i.price_magnitude = ? AND i.price_sort_digits <= ?))")
            params.extend((mag, mag, digits))
        where_sql = " AND ".join(where)
        conn = connect(self.db_path)
        try:
            total = int(conn.execute(
                f"""SELECT COUNT(*) FROM items_master i
                    JOIN installed_packs p ON p.pack_id=i.pack_id
                    WHERE {where_sql}""",
                tuple(params),
            ).fetchone()[0])
            rows = conn.execute(
                f"""SELECT i.* FROM items_master i
                    JOIN installed_packs p ON p.pack_id=i.pack_id
                    WHERE {where_sql}
                    ORDER BY {order_sql}
                    LIMIT ? OFFSET ?""",
                tuple(params) + (limit, offset),
            ).fetchall()
            return list(rows), total
        finally:
            conn.close()

    def rebuild_all(self, packs: Iterable[tuple[InstalledPack, Iterable[PreparedItemRecord]]]) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM items_master")
            conn.execute("DELETE FROM installed_packs")
            for pack, items in packs:
                self._insert_pack(conn, pack)
                self._insert_items(conn, items)
