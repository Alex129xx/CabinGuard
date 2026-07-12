from __future__ import annotations

import aiosqlite


async def setup_preferences(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                profile_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(profile_id, key)
            )
        """)
        await db.commit()


async def load_preferences(db_path: str, profile_id: str | None) -> dict[str, str]:
    if not profile_id:
        return {}
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT key, value FROM user_preferences WHERE profile_id = ?", (profile_id,))
        return {key: value for key, value in await cursor.fetchall()}


async def save_preference(db_path: str, profile_id: str, key: str, value: str, updated_at: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO user_preferences(profile_id, key, value, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(profile_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (profile_id, key, value, updated_at))
        await db.commit()


async def delete_preference(db_path: str, profile_id: str, key: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM user_preferences WHERE profile_id = ? AND key = ?", (profile_id, key))
        await db.commit()
