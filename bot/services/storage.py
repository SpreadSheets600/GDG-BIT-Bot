import sqlite3
from contextlib import contextmanager
from pathlib import Path

from bot.services.community_storage import CommunityStore
from bot.services.memes import MemeStore
from bot.services.platforms import platform_name
from bot.services.problems import ServiceError
from bot.services.server_tools import ServerToolsStore


class Storage(CommunityStore, MemeStore, ServerToolsStore):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            self.init_community(db)
            self.init_memes(db)
            self.init_server_tools(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS POTDSettings (\n                guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,\n                enabled INTEGER NOT NULL DEFAULT 1)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS Users (user_id INTEGER PRIMARY KEY)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS Profiles (\n                user_id INTEGER PRIMARY KEY, leetcode_id TEXT, gfg_id TEXT, cf_id TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS DailyPosts (\n                channel_id INTEGER, provider TEXT, date TEXT, message_id INTEGER,\n                PRIMARY KEY (channel_id, provider, date))"
            )

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def register(self, user_id: int) -> bool:
        with self.connect() as db:
            created = (
                db.execute("INSERT OR IGNORE INTO Users (user_id) VALUES (?)", (user_id,)).rowcount
                > 0
            )
            db.execute("INSERT OR IGNORE INTO Profiles (user_id) VALUES (?)", (user_id,))
        return created

    def set_profile(self, user_id: int, platform: str, username: str):
        account = (
            self.connected_account(user_id, platform) if platform in {"leetcode", "gfg"} else None
        )
        if account and account["username"].casefold() != username.casefold():
            raise ServiceError(
                f"Your verified {platform_name(platform)} account is already connected. /profile with platform:{platform} shows it."
            )
        column = {"leetcode": "leetcode_id", "gfg": "gfg_id", "cf": "cf_id"}[platform]
        self.register(user_id)
        with self.connect() as db:
            db.execute(f"UPDATE Profiles SET {column} = ? WHERE user_id = ?", (username, user_id))

    def get_profile(self, user_id: int) -> dict:
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM Profiles WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else {}

    def was_posted(self, channel_id: int, provider: str, day: str) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM DailyPosts WHERE channel_id=? AND provider=? AND date=?",
                    (channel_id, provider, day),
                ).fetchone()
                is not None
            )

    def mark_posted(self, channel_id: int, provider: str, day: str, message_id: int):
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO DailyPosts VALUES (?, ?, ?, ?)",
                (channel_id, provider, day, message_id),
            )

    def potd_settings(self, guild_id):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM POTDSettings WHERE guild_id=?", (guild_id,)).fetchone()
        return dict(row) if row else None

    def set_potd_settings(self, guild_id, channel_id, enabled=True):
        with self.connect() as db:
            db.execute(
                "INSERT INTO POTDSettings VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, enabled=excluded.enabled",
                (guild_id, channel_id, int(enabled)),
            )

    def configured_potd_channels(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM POTDSettings")]
