import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.webhook.async_ import handle_message_parameters

from bot.main import create_bot
from bot.services.memes import MemeService
from bot.services.problems import ServiceError
from bot.ui.memes import MemeLayout


def meme(**changes):
    return {
        "subreddit": "ProgrammerHumor",
        "title": "It works on my machine",
        "url": "https://i.redd.it/test.png",
        "postLink": "https://redd.it/test",
        "nsfw": False,
        "spoiler": False,
        "author": "coder",
        **changes,
    }


class MemeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot = create_bot(database_path=Path(self.tmp.name) / "memes.db")
        self.addAsyncCleanup(self.bot.close)
        self.cog = self.bot.get_cog("MemesCog")
        self.message = SimpleNamespace(
            id=10, jump_url="https://discord.com/channels/1/2/10", add_reaction=AsyncMock()
        )
        self.channel = SimpleNamespace(send=AsyncMock(return_value=self.message))
        self.cog.service.fetch = AsyncMock(return_value=meme())

    async def test_developer_meme_service_rejects_unsuitable_or_unrelated_items(self):
        service = MemeService(SimpleNamespace(request=AsyncMock()), self.bot.storage)
        for item in (
            meme(nsfw=True),
            meme(spoiler=True),
            meme(nsfw=None),
            meme(subreddit="memes"),
            meme(url="https://example.com/tracker"),
            meme(postLink="https://example.com/post"),
        ):
            service.problems.request.return_value = {"memes": [item]}
            with self.assertRaises(ServiceError):
                await service.fetch(1)
        service.problems.request.return_value = {"memes": [meme()]}
        self.assertEqual((await service.fetch(1))["postLink"], "https://redd.it/test")

    async def test_previously_posted_memes_are_skipped(self):
        self.bot.storage.record_meme(1, meme(), 10, int(time.time()))
        problems = SimpleNamespace(
            request=AsyncMock(
                return_value={
                    "memes": [
                        meme(),
                        meme(postLink="https://redd.it/new", url="https://i.redd.it/new.png"),
                    ]
                }
            )
        )
        selected = await MemeService(problems, self.bot.storage).fetch(1)
        self.assertEqual(selected["postLink"], "https://redd.it/new")

    async def test_missing_settings_do_not_send_a_message(self):
        with self.assertRaises(ServiceError):
            await self.cog.post(1)
        self.cog.service.fetch.assert_not_awaited()

    async def test_posts_only_to_configured_channel_and_seeds_reaction(self):
        self.bot.storage.set_meme_settings(1, 2)
        with (
            patch.object(self.bot, "get_channel", return_value=self.channel),
            patch.object(self.cog, "check_channel") as check,
        ):
            message, reacted = await self.cog.post(1)
        check.assert_called_once_with(self.channel, 1)
        self.assertIs(message, self.message)
        self.assertTrue(reacted)
        self.message.add_reaction.assert_awaited_once()
        self.assertTrue(self.channel.send.call_args.kwargs["view"].is_components_v2())
        self.assertTrue(self.bot.storage.meme_seen(1, meme()["postLink"], meme()["url"]))

    async def test_manual_requests_have_server_cooldown(self):
        self.bot.storage.set_meme_settings(1, 2)
        with (
            patch.object(self.bot, "get_channel", return_value=self.channel),
            patch.object(self.cog, "check_channel"),
        ):
            await self.cog.post(1)
            with self.assertRaisesRegex(ServiceError, "30 seconds"):
                await self.cog.post(1)
        self.channel.send.assert_awaited_once()

    async def test_schedule_remembers_delivery_across_restart(self):
        with patch("bot.services.memes.time.time", return_value=1000):
            self.bot.storage.set_meme_settings(1, 2, True, 2)
        due = self.bot.storage.meme_settings(1)["next_post_at"]
        with (
            patch.object(self.bot, "get_channel", return_value=self.channel),
            patch.object(self.cog, "check_channel"),
            patch("bot.cogs.memes.time.time", return_value=due),
        ):
            await self.cog.post(1, scheduled_slot=str(due))
            self.cog.last_post.clear()
            message, _ = await self.cog.post(1, scheduled_slot=str(due))
        self.assertIsNone(message)
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.bot.storage.meme_settings(1)["next_post_at"], due + 7200)

    async def test_interval_waits_full_hours_before_posting(self):
        with patch("bot.services.memes.time.time", return_value=1000):
            self.bot.storage.set_meme_settings(1, 2, True, 3)
        with (
            patch.object(self.bot, "get_channel", return_value=self.channel),
            patch.object(self.cog, "check_channel"),
            patch("bot.cogs.memes.time.time", return_value=11799) as clock,
        ):
            await self.cog.deliver_memes()
            self.channel.send.assert_not_awaited()
            clock.return_value = 11800
            await self.cog.deliver_memes()
            self.channel.send.assert_awaited_once()
            await self.cog.deliver_memes()
            self.channel.send.assert_awaited_once()
        self.assertEqual(self.bot.storage.meme_settings(1)["next_post_at"], 22600)

    async def test_manual_meme_resets_automatic_wait(self):
        self.bot.storage.set_meme_settings(1, 2, True, 4)
        with (
            patch.object(self.bot, "get_channel", return_value=self.channel),
            patch.object(self.cog, "check_channel"),
            patch("bot.cogs.memes.time.time", return_value=10000),
        ):
            await self.cog.post(1)
        self.assertEqual(self.bot.storage.meme_settings(1)["next_post_at"], 24400)

    async def test_old_daily_schedule_migrates_without_losing_channel(self):
        from bot.services.storage import Storage

        legacy = Path(self.tmp.name) / "legacy.db"
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute(
                "CREATE TABLE MemeSettings (guild_id INTEGER PRIMARY KEY, channel_id INTEGER, enabled INTEGER, daily_time TEXT)"
            )
            db.execute("INSERT INTO MemeSettings VALUES (1, 2, 1, '18:00')")
            db.execute(
                "CREATE TABLE MemePosts (guild_id INTEGER, post_link TEXT, image_url TEXT, message_id INTEGER, posted_at INTEGER, scheduled_day TEXT, PRIMARY KEY(guild_id,post_link), UNIQUE(guild_id,scheduled_day))"
            )
            db.execute(
                "INSERT INTO MemePosts VALUES (1, 'https://redd.it/old', 'https://i.redd.it/old.png', 5, 100, '2026-11-20')"
            )
        with patch("bot.services.memes.time.time", return_value=1000):
            migrated = Storage(legacy)
        config = migrated.meme_settings(1)
        self.assertEqual(config["channel_id"], 2)
        self.assertTrue(config["enabled"])
        self.assertEqual(config["interval_hours"], 24)
        self.assertEqual(config["next_post_at"], 87400)
        self.assertTrue(migrated.meme_seen(1, "https://redd.it/old", "https://i.redd.it/old.png"))
        self.assertEqual(Storage(legacy).meme_settings(1), config)

    async def test_invalid_hour_intervals_are_rejected(self):
        for value in (0, -1, 169, "3", True):
            with self.assertRaises(ServiceError):
                self.bot.storage.set_meme_settings(1, 2, True, value)

    async def test_disabled_autopost_does_not_deliver(self):
        self.bot.storage.set_meme_settings(1, 2, False)
        with patch.object(self.cog, "post", new_callable=AsyncMock) as post:
            await self.cog.deliver_memes()
            post.assert_not_awaited()

    async def test_permissions_failure_does_not_post(self):
        self.bot.storage.set_meme_settings(1, 2)
        with (
            patch.object(self.bot, "get_channel", return_value=self.channel),
            patch.object(
                self.cog,
                "check_channel",
                side_effect=ServiceError("Add reactions permission missing"),
            ),
            self.assertRaises(ServiceError),
        ):
            await self.cog.post(1)
        self.channel.send.assert_not_awaited()

    async def test_reaction_failure_does_not_duplicate_already_sent_meme(self):
        self.bot.storage.set_meme_settings(1, 2)
        self.message.add_reaction.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "No reaction permission"
        )
        with (
            patch.object(self.bot, "get_channel", return_value=self.channel),
            patch.object(self.cog, "check_channel"),
            self.assertLogs("bot.cogs.memes", level="ERROR"),
        ):
            message, reacted = await self.cog.post(1)
        self.assertFalse(reacted)
        self.assertIs(message, self.message)
        self.assertTrue(self.bot.storage.meme_seen(1, meme()["postLink"], meme()["url"]))

    async def test_gallery_uses_v2_and_preserves_attribution(self):
        payload = handle_message_parameters(view=MemeLayout(self.bot, meme())).payload
        self.assertTrue(payload["flags"] & 1 << 15)
        self.assertIn("r/ProgrammerHumor", json.dumps(payload))
        self.assertTrue(any(item["type"] == 12 for item in payload["components"][0]["components"]))

    async def test_successful_command_dismisses_loading_without_confirmation(self):
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            defer=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
            interaction=SimpleNamespace(delete_original_response=AsyncMock()),
        )
        with patch.object(
            self.cog, "post", new_callable=AsyncMock, return_value=(self.message, True)
        ):
            await self.cog.meme.callback(self.cog, ctx)
        ctx.interaction.delete_original_response.assert_awaited_once()
        ctx.followup.send.assert_not_awaited()
        self.assertNotIn("Original post", str(MemeLayout(self.bot, meme()).to_components()))

    async def test_admin_configuration_has_permission_check(self):
        self.assertTrue(self.cog.configure.checks)


if __name__ == "__main__":
    unittest.main()
