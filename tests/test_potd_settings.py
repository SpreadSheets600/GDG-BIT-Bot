import inspect
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord.ext import commands
from test_bot import gfg_payload, leetcode_payload

from bot.main import create_bot
from bot.services.problems import ServiceError, normalize_daily
from bot.services.storage import Storage


class POTDConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot = create_bot(database_path=Path(self.tmp.name) / "potd.db")
        self.addAsyncCleanup(self.bot.close)
        self.settings = self.bot.get_cog("POTDSettingsCog")
        self.daily = self.bot.get_cog("PlatformsCog")
        self.ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1, me=object()),
            author=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True)),
            defer=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        self.channel = SimpleNamespace(
            id=2,
            guild=self.ctx.guild,
            mention="<#2>",
            permissions_for=lambda member: SimpleNamespace(
                view_channel=True, send_messages=True, embed_links=True
            ),
            send=AsyncMock(return_value=SimpleNamespace(id=100)),
        )

    async def test_configuration_requires_administrator_not_just_manage_server(self):
        self.ctx.author.guild_permissions = SimpleNamespace(administrator=False, manage_guild=True)
        for command in (self.settings.configure, self.settings.status, self.settings.repost):
            self.assertTrue(command.checks)
            with self.assertRaises(commands.MissingPermissions):
                result = command.checks[0](self.ctx)
                if inspect.isawaitable(result):
                    await result
        self.assertIsNone(self.bot.storage.potd_settings(1))
        group = next(
            command for command in self.bot.pending_application_commands if command.name == "potd"
        )
        self.assertEqual(int(group.to_dict()["default_member_permissions"]), 8)

    async def test_repost_sends_both_current_problems_to_the_configured_channel(self):
        self.bot.storage.set_potd_settings(1, 2, False)

        async def daily(provider):
            return normalize_daily(
                provider,
                leetcode_payload() if provider == "leetcode" else gfg_payload(),
                datetime.now(UTC),
            )

        self.bot.problems.daily = AsyncMock(side_effect=daily)
        with patch.object(self.bot, "get_channel", return_value=self.channel):
            await self.settings.repost.callback(self.settings, self.ctx)
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertEqual(self.bot.problems.daily.await_args_list[0].args, ("leetcode",))
        self.assertEqual(self.bot.problems.daily.await_args_list[1].args, ("gfg",))
        self.assertTrue(self.ctx.followup.send.call_args.kwargs["ephemeral"])

    async def test_settings_persist_and_are_scoped_to_server(self):
        await self.settings.configure.callback(self.settings, self.ctx, self.channel)
        restarted = Storage(self.bot.storage.path)
        self.assertEqual(restarted.potd_settings(1)["channel_id"], 2)
        self.assertTrue(restarted.potd_settings(1)["enabled"])
        self.assertIsNone(restarted.potd_settings(2))
        self.assertTrue(self.ctx.followup.send.call_args.kwargs["view"].is_components_v2())

    async def test_invalid_channel_or_missing_bot_permissions_do_not_change_settings(self):
        self.channel.guild = SimpleNamespace(id=2)
        with self.assertRaises(ServiceError):
            await self.settings.configure.callback(self.settings, self.ctx, self.channel)
        self.channel.guild = self.ctx.guild
        self.channel.permissions_for = lambda member: SimpleNamespace(
            view_channel=True, send_messages=False, embed_links=True
        )
        with self.assertRaises(ServiceError):
            await self.settings.configure.callback(self.settings, self.ctx, self.channel)
        self.assertIsNone(self.bot.storage.potd_settings(1))

    async def test_enabled_channel_gets_both_daily_problems_once_after_restart(self):
        self.bot.storage.set_potd_settings(1, 2, True)

        async def daily(provider):
            return normalize_daily(
                provider,
                leetcode_payload() if provider == "leetcode" else gfg_payload(),
                datetime.now(UTC),
            )

        self.bot.problems.daily = AsyncMock(side_effect=daily)
        with patch.object(self.bot, "get_channel", return_value=self.channel):
            await self.daily.deliver_daily()
            self.assertEqual(self.channel.send.await_count, 2)
            self.bot.storage = Storage(self.bot.storage.path)
            await self.daily.deliver_daily()
        self.assertEqual(self.channel.send.await_count, 2)
        for call in self.channel.send.call_args_list:
            self.assertTrue(call.kwargs["view"].is_components_v2())
            self.assertIn("Mark as solved", str(call.kwargs["view"].to_components()))

    async def test_disabled_schedule_never_posts(self):
        self.bot.storage.set_potd_settings(1, 2, False)
        with patch.object(self.bot, "get_channel", return_value=self.channel):
            await self.daily.deliver_daily()
        self.channel.send.assert_not_awaited()

    async def test_explicit_disable_overrides_uncached_legacy_channel(self):
        self.bot.storage.set_potd_settings(1, 2, False)
        self.bot.potd_channel_id = 3
        legacy = SimpleNamespace(id=3, guild=SimpleNamespace(id=1))
        with (
            patch.object(self.bot, "get_channel", return_value=None),
            patch.object(self.bot, "fetch_channel", new_callable=AsyncMock, return_value=legacy),
            patch.object(self.daily, "deliver_to_channel", new_callable=AsyncMock) as deliver,
        ):
            await self.daily.deliver_daily()
        deliver.assert_not_awaited()

    async def test_disabling_does_not_erase_history(self):
        self.bot.storage.set_potd_settings(1, 2, True)
        self.bot.storage.mark_posted(2, "leetcode", "2026-11-20", 100)
        await self.settings.configure.callback(self.settings, self.ctx, self.channel, False)
        self.assertFalse(self.bot.storage.potd_settings(1)["enabled"])
        self.assertTrue(self.bot.storage.was_posted(2, "leetcode", "2026-11-20"))


if __name__ == "__main__":
    unittest.main()
