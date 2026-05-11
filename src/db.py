from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class PendingLink:
    code: str
    tg_chat_id: int
    created_at: float
    expires_at: float


@dataclass
class Bridge:
    tg_chat_id: int
    matrix_room_id: str
    created_at: float


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    @classmethod
    async def connect(cls, path: str) -> Database:
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"Cannot create SQLite directory: {resolved.parent!s} ({e}). "
                "Check directory permissions or DATABASE_PATH."
            ) from e
        self = cls(str(resolved))
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute("PRAGMA secure_delete=ON;")
            await db.commit()
        await self.init_schema()
        return self

    async def init_schema(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS pending_links (
                    code TEXT PRIMARY KEY,
                    tg_chat_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pending_links_tg
                    ON pending_links(tg_chat_id);

                CREATE TABLE IF NOT EXISTS bridges (
                    tg_chat_id INTEGER NOT NULL UNIQUE,
                    matrix_room_id TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bridges_tg ON bridges(tg_chat_id);
                CREATE INDEX IF NOT EXISTS idx_bridges_matrix ON bridges(matrix_room_id);

                CREATE TABLE IF NOT EXISTS matrix_room_meta (
                    room_id TEXT PRIMARY KEY,
                    welcome_sent INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            await db.commit()
        logger.info("SQLite schema ready at %s", self.path)

    async def cleanup_expired_pending(self) -> None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM pending_links WHERE expires_at < ?", (now,))
            await db.commit()

    async def revoke_pending_for_tg(self, tg_chat_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM pending_links WHERE tg_chat_id = ?", (tg_chat_id,))
            await db.commit()

    async def insert_pending(
        self,
        code: str,
        tg_chat_id: int,
        expires_at: float,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO pending_links (code, tg_chat_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (code, tg_chat_id, now, expires_at),
            )
            await db.commit()

    async def get_pending_by_code(self, code: str) -> PendingLink | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT code, tg_chat_id, created_at, expires_at FROM pending_links WHERE code = ?",
                (code,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return PendingLink(
            code=row["code"],
            tg_chat_id=int(row["tg_chat_id"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
        )

    async def delete_pending(self, code: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM pending_links WHERE code = ?", (code,))
            await db.commit()

    async def try_link_atomic(
        self,
        code: str,
        tg_chat_id: int,
        matrix_room_id: str,
    ) -> bool:
        """Atomically consume pending code and create bridge. Returns False on conflict."""
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                cur = await db.execute(
                    "SELECT tg_chat_id FROM pending_links WHERE code = ?", (code,)
                )
                if await cur.fetchone() is None:
                    await db.execute("ROLLBACK")
                    return False
                cur2 = await db.execute(
                    "SELECT 1 FROM bridges WHERE tg_chat_id = ? OR matrix_room_id = ?",
                    (tg_chat_id, matrix_room_id),
                )
                if await cur2.fetchone() is not None:
                    await db.execute("ROLLBACK")
                    return False
                await db.execute("DELETE FROM pending_links WHERE code = ?", (code,))
                await db.execute(
                    "INSERT INTO bridges (tg_chat_id, matrix_room_id, created_at) VALUES (?, ?, ?)",
                    (tg_chat_id, matrix_room_id, now),
                )
                await db.commit()
                return True
            except sqlite3.IntegrityError:
                await db.execute("ROLLBACK")
                return False

    async def get_bridge_by_tg(self, tg_chat_id: int) -> Bridge | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT tg_chat_id, matrix_room_id, created_at FROM bridges WHERE tg_chat_id = ?",
                (tg_chat_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Bridge(
            tg_chat_id=int(row["tg_chat_id"]),
            matrix_room_id=row["matrix_room_id"],
            created_at=float(row["created_at"]),
        )

    async def get_bridge_by_matrix(self, matrix_room_id: str) -> Bridge | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT tg_chat_id, matrix_room_id, created_at FROM bridges WHERE matrix_room_id = ?",
                (matrix_room_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return Bridge(
            tg_chat_id=int(row["tg_chat_id"]),
            matrix_room_id=row["matrix_room_id"],
            created_at=float(row["created_at"]),
        )

    async def update_tg_chat_id(self, old_id: int, new_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE bridges SET tg_chat_id = ? WHERE tg_chat_id = ?", (new_id, old_id)
            )
            await db.execute(
                "UPDATE pending_links SET tg_chat_id = ? WHERE tg_chat_id = ?", (new_id, old_id)
            )
            await db.commit()

    async def delete_bridge_by_tg(self, tg_chat_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM bridges WHERE tg_chat_id = ?", (tg_chat_id,))
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def delete_bridge_by_matrix(self, matrix_room_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "DELETE FROM bridges WHERE matrix_room_id = ?",
                (matrix_room_id,),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def is_welcome_sent(self, room_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT welcome_sent FROM matrix_room_meta WHERE room_id = ?", (room_id,)
            )
            row = await cur.fetchone()
        return bool(row and row[0])

    async def mark_welcome_sent(self, room_id: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO matrix_room_meta (room_id, welcome_sent) VALUES (?, 1)
                ON CONFLICT(room_id) DO UPDATE SET welcome_sent = 1
                """,
                (room_id,),
            )
            await db.commit()
