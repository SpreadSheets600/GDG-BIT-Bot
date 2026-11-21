import asyncio
import sqlite3
import time
from urllib.parse import urlparse

from bot.services.problems import ServiceError


class MemeStore:
    @staticmethod
    def init_memes(db):
        columns = {row[1] for row in db.execute("PRAGMA table_info(MemeSettings)")}
        if columns and "interval_hours" not in columns:
            if not db.in_transaction:
                db.execute("BEGIN")
            db.execute("ALTER TABLE MemeSettings RENAME TO LegacyMemeSettings")
        db.execute(
            "CREATE TABLE IF NOT EXISTS MemeSettings (\n            guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,\n            enabled INTEGER NOT NULL DEFAULT 0, interval_hours INTEGER NOT NULL DEFAULT 24,\n            next_post_at INTEGER NOT NULL)"
        )
        if columns and "interval_hours" not in columns:
            db.execute(
                "INSERT INTO MemeSettings SELECT guild_id, channel_id, enabled, 24, ? FROM LegacyMemeSettings",
                (int(time.time()) + 86400,),
            )
            db.execute("DROP TABLE LegacyMemeSettings")
        db.execute(
            "CREATE TABLE IF NOT EXISTS MemePosts (\n            guild_id INTEGER NOT NULL, post_link TEXT NOT NULL, image_url TEXT NOT NULL,\n            message_id INTEGER NOT NULL, posted_at INTEGER NOT NULL, schedule_key TEXT,\n            PRIMARY KEY(guild_id, post_link), UNIQUE(guild_id, schedule_key))"
        )
        post_columns = {row[1] for row in db.execute("PRAGMA table_info(MemePosts)")}
        if "scheduled_day" in post_columns:
            db.execute("ALTER TABLE MemePosts RENAME COLUMN scheduled_day TO schedule_key")

    def meme_settings(self, guild_id):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM MemeSettings WHERE guild_id=?", (guild_id,)).fetchone()
        return dict(row) if row else None

    def set_meme_settings(self, guild_id, channel_id, enabled=False, interval_hours=24):
        if (
            not isinstance(interval_hours, int)
            or isinstance(interval_hours, bool)
            or (not 1 <= interval_hours <= 168)
        ):
            raise ServiceError("Choose an interval from 1 to 168 hours.")
        with self.connect() as db:
            db.execute(
                "INSERT INTO MemeSettings VALUES (?, ?, ?, ?, ?)\n                ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,\n                enabled=excluded.enabled, interval_hours=excluded.interval_hours,\n                next_post_at=excluded.next_post_at",
                (
                    guild_id,
                    channel_id,
                    int(enabled),
                    interval_hours,
                    int(time.time()) + interval_hours * 3600,
                ),
            )

    def enabled_meme_settings(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM MemeSettings WHERE enabled=1")]

    def meme_seen(self, guild_id, post_link, image_url):
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM MemePosts WHERE guild_id=? AND (post_link=? OR image_url=?)",
                    (guild_id, post_link, image_url),
                ).fetchone()
                is not None
            )

    def meme_scheduled_posted(self, guild_id, slot):
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM MemePosts WHERE guild_id=? AND schedule_key=?",
                    (guild_id, str(slot)),
                ).fetchone()
                is not None
            )

    def record_meme(self, guild_id, meme, message_id, posted_at, scheduled_slot=None):
        with self.connect() as db:
            db.execute(
                "INSERT INTO MemePosts VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, meme["postLink"], meme["url"], message_id, posted_at, scheduled_slot),
            )
            if scheduled_slot is not None:
                db.execute(
                    "UPDATE MemeSettings SET next_post_at=? + interval_hours * 3600 WHERE guild_id=?",
                    (posted_at, guild_id),
                )


def valid_meme(item):
    if (
        not isinstance(item, dict)
        or item.get("nsfw") is not False
        or item.get("spoiler") is not False
    ):
        return False
    if str(item.get("subreddit", "")).casefold() != "programmerhumor":
        return False
    image = urlparse(str(item.get("url", "")))
    source = urlparse(str(item.get("postLink", "")))
    return (
        image.scheme == "https"
        and image.hostname in {"i.redd.it", "preview.redd.it"}
        and image.path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
        and (source.scheme == "https")
        and (source.hostname in {"redd.it", "www.reddit.com", "reddit.com"})
        and isinstance(item.get("title"), str)
        and bool(item["title"].strip())
    )


class MemeService:
    def __init__(self, problems, storage):
        self.problems = problems
        self.storage = storage

    async def fetch(self, guild_id):
        for _ in range(2):
            payload = await self.problems.request(
                "GET", "https://meme-api.com/gimme/ProgrammerHumor/5"
            )
            items = payload.get("memes")
            if not isinstance(items, list):
                raise ServiceError("The developer meme source is unavailable. Try again shortly.")
            for item in items:
                if valid_meme(item) and (
                    not await asyncio.to_thread(
                        self.storage.meme_seen, guild_id, item["postLink"], item["url"]
                    )
                ):
                    return item
        raise ServiceError("No new developer meme is available right now. Try again later.")
