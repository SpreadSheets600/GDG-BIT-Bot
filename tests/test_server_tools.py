import inspect
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.components import _component_factory
from discord.ext import commands

from bot.main import create_bot
from bot.services.problems import ServiceError
from bot.services.server_tools import load_tag
from bot.services.storage import Storage


class ServerToolsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "bot.db"
        self.bot = create_bot(database_path=self.path)
        self.addAsyncCleanup(self.bot.close)
        self.cog = self.bot.get_cog("ServerToolsCog")
        self.channel = SimpleNamespace(send=AsyncMock())
        self.source_messages = {}

        async def fetch_message(message_id):
            if message_id not in self.source_messages:
                raise discord.NotFound(MagicMock(status=404), "Unknown Message")
            source = self.source_messages[message_id]
            if isinstance(source, str):
                source = SimpleNamespace(content=source, embeds=[], attachments=[])
            source.components = getattr(source, "components", [])
            return source

        self.source_channel = SimpleNamespace(fetch_message=AsyncMock(side_effect=fetch_message))

        def get_channel(channel_id):
            return self.source_channel if channel_id == 5 else None

        self.message = SimpleNamespace(
            guild=SimpleNamespace(
                id=1, get_channel_or_thread=get_channel, fetch_channel=AsyncMock()
            ),
            author=SimpleNamespace(
                bot=False, guild_permissions=SimpleNamespace(administrator=True)
            ),
            channel=self.channel,
            content="",
        )
        self.role = MagicMock(spec=discord.Role)
        self.role.id = 10
        self.role.members = [object(), object()]
        self.everyone = MagicMock(spec=discord.Role)
        self.everyone.id = 1
        self.channels = {}
        self.next_channel_id = 20

        self.categories = []

        async def create_category(name, *, reason):
            category = SimpleNamespace(id=99, name=name)
            self.categories.append(category)
            return category

        async def create(name, *, category, overwrites, reason):
            channel = MagicMock(spec=discord.VoiceChannel)
            channel.id = self.next_channel_id
            self.next_channel_id += 1
            channel.name = name
            channel.category_id = category.id
            channel.overwrites = overwrites
            channel.edit = AsyncMock()
            self.channels[channel.id] = channel
            return channel

        self.guild = SimpleNamespace(
            id=1,
            member_count=12,
            chunked=False,
            chunk=AsyncMock(),
            default_role=self.everyone,
            get_channel_or_thread=get_channel,
            fetch_channel=AsyncMock(),
            get_role=lambda role_id: self.role if role_id == self.role.id else None,
            get_channel=self.channels.get,
            create_voice_channel=AsyncMock(side_effect=create),
            categories=self.categories,
            create_category=AsyncMock(side_effect=create_category),
            me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_channels=True)),
        )
        self.ctx = SimpleNamespace(
            guild=self.guild,
            defer=AsyncMock(),
            respond=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_tags_are_persistent_scoped_and_validate_messages(self):
        self.bot.storage.set_tag(1, "RULES", "Be kind.")
        self.assertEqual(Storage(self.path).get_tag(1, "rules"), "Be kind.")
        self.assertIsNone(self.bot.storage.get_tag(2, "rules"))
        self.bot.storage.set_tag(1, "rules", "Updated rules")
        self.assertEqual(self.bot.storage.list_tags(1), ["rules"])
        for name, content in (("tag", "x"), ("bad name", "x"), ("a", " "), ("a", "x" * 2001)):
            with self.assertRaises(ServiceError):
                self.bot.storage.set_tag(1, name, content)
        self.assertTrue(self.bot.storage.delete_tag(1, "rules"))
        self.assertFalse(self.bot.storage.delete_tag(1, "rules"))

    async def test_admin_can_manage_and_publish_arbitrary_prefix_tags(self):
        self.source_messages[7] = "Welcome @everyone!\n**Read** the rules."
        self.message.content = "!tag set welcome https://discord.com/channels/1/5/7"
        await self.cog.on_message(self.message)
        self.assertEqual(
            load_tag(self.bot.storage.get_tag(1, "welcome"))["content"],
            "Welcome @everyone!\n**Read** the rules.",
        )
        self.channel.send.reset_mock()
        self.message.content = "!WELCOME"
        await self.cog.on_message(self.message)
        components = self.channel.send.call_args.kwargs["view"].to_components()
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0]["type"], 10)
        self.assertEqual(components[0]["content"], "Welcome @everyone!\n**Read** the rules.")
        self.assertFalse(self.channel.send.call_args.kwargs["allowed_mentions"].everyone)
        self.message.content = "!tag delete welcome"
        await self.cog.on_message(self.message)
        self.assertIsNone(self.bot.storage.get_tag(1, "welcome"))

    async def test_non_admin_bots_and_dms_cannot_modify_or_post_tags(self):
        self.bot.storage.set_tag(1, "socials", "Our social links")
        self.message.author.guild_permissions.administrator = False
        for content in ("!socials", "!tag set socials Replaced", "!tag delete socials"):
            self.message.content = content
            await self.cog.on_message(self.message)
        self.assertEqual(self.bot.storage.get_tag(1, "socials"), "Our social links")
        self.message.author.guild_permissions.administrator = True
        self.message.author.bot = True
        await self.cog.on_message(self.message)
        self.message.author.bot = False
        self.message.guild = None
        await self.cog.on_message(self.message)
        self.channel.send.assert_not_awaited()

    async def test_rules_and_socials_use_saved_server_content(self):
        for name in ("rules", "socials"):
            with self.assertRaisesRegex(ServiceError, "/tags set"):
                await getattr(self.cog, name).callback(self.cog, self.ctx)
            self.bot.storage.set_tag(1, name, f"Custom {name}")
            await getattr(self.cog, name).callback(self.cog, self.ctx)
            view = self.ctx.respond.call_args.kwargs["view"]
            self.assertEqual(view.to_components()[0]["content"], f"Custom {name}")

    async def test_set_tag_copies_message_from_link_and_rejects_bad_links(self):
        self.source_messages[7] = "  Our links:\n- https://example.com  "
        await self.cog.set_tag.callback(
            self.cog, self.ctx, "socials", "<https://ptb.discord.com/channels/1/5/7>"
        )
        self.assertEqual(
            load_tag(self.bot.storage.get_tag(1, "socials"))["content"],
            "Our links:\n- https://example.com",
        )
        self.source_messages[8] = SimpleNamespace(content="   ", embeds=[], attachments=[])
        for link, error in (
            ("just some text", "message link"),
            ("https://discord.com/channels/2/5/7", "this server"),
            ("https://discord.com/channels/1/5/99", "Could not find"),
            ("https://discord.com/channels/1/5/8", "nothing to copy"),
        ):
            with self.assertRaisesRegex(ServiceError, error):
                await self.cog.set_tag.callback(self.cog, self.ctx, "socials", link)
        self.assertEqual(
            load_tag(self.bot.storage.get_tag(1, "socials"))["content"],
            "Our links:\n- https://example.com",
        )

    async def test_webhook_embeds_and_attachments_are_copied_and_refreshed(self):
        embed = discord.Embed(
            title="Server Rules",
            url="https://example.com/rules",
            description="Be kind.",
            color=0x34A853,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        embed.set_author(name="GDG BIT", url="https://example.com")
        embed.add_field(name="1. Respect", value="No harassment.")
        embed.add_field(name="2. No spam", value="Keep it on topic.")
        embed.set_thumbnail(url="https://cdn.example.com/logo.png")
        embed.set_image(url="https://cdn.example.com/banner.png")
        embed.set_footer(text="Updated yearly")
        second = discord.Embed(description="Socials: https://example.com/links")
        files = [
            SimpleNamespace(
                url="https://cdn.example.com/a.png?ex=1",
                filename="a.png",
                content_type="image/png",
                is_spoiler=lambda: False,
            ),
            SimpleNamespace(
                url="https://cdn.example.com/guide.pdf",
                filename="guide.pdf",
                content_type="application/pdf",
                is_spoiler=lambda: False,
            ),
        ]
        self.source_messages[7] = SimpleNamespace(
            content="", embeds=[embed, second], attachments=files
        )
        self.message.content = "!tag set rules https://discord.com/channels/1/5/7"
        await self.cog.on_message(self.message)
        saved = load_tag(self.bot.storage.get_tag(1, "rules"))
        self.assertEqual(len(saved["embeds"]), 2)

        self.channel.send.reset_mock()
        self.message.content = "!rules"
        await self.cog.on_message(self.message)
        view = self.channel.send.call_args.kwargs["view"]
        self.assertTrue(view.is_components_v2())
        rendered = str(view.to_components())
        for text in (
            "[GDG BIT](https://example.com)",
            "### [Server Rules](https://example.com/rules)",
            "Be kind.",
            "**1. Respect**",
            "No harassment.",
            "logo.png",
            "banner.png",
            "Updated yearly • <t:1767225600:f>",
            "Socials: https://example.com/links",
            "a.png?ex=1",
            "[guide.pdf](https://cdn.example.com/guide.pdf)",
        ):
            self.assertIn(text, rendered)
        containers = [item for item in view.to_components() if item["type"] == 17]
        self.assertEqual(containers[0]["accent_color"], 0x34A853)

        # Edits to the source are picked up; a deleted source falls back to the saved copy.
        files[0].url = "https://cdn.example.com/a.png?ex=2"
        await self.cog.on_message(self.message)
        self.assertIn("a.png?ex=2", str(self.channel.send.call_args.kwargs["view"].to_components()))
        del self.source_messages[7]
        await self.cog.on_message(self.message)
        self.assertIn("a.png?ex=2", str(self.channel.send.call_args.kwargs["view"].to_components()))

    async def test_components_v2_webhook_layout_is_copied_without_foreign_controls(self):
        gif = "https://media.discordapp.net/attachments/1/2/giphy.gif?ex=1"
        raw = [
            {
                "type": 17,
                "id": 1,
                "accent_color": 15548997,
                "components": [
                    {"type": 12, "id": 2, "items": [{"media": {"url": gif}}]},
                    {"type": 10, "id": 3, "content": "## WHAT IS GDG ?"},
                    {"type": 14, "id": 4, "divider": True, "spacing": 1},
                    {
                        "type": 9,
                        "id": 5,
                        "components": [{"type": 10, "id": 6, "content": "Pick a role"}],
                        "accessory": {"type": 2, "style": 1, "custom_id": "x", "label": "Go"},
                    },
                ],
            },
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 5, "label": "GitHub", "url": "https://github.com/x"},
                    {"type": 2, "style": 1, "label": "Other app", "custom_id": "y"},
                ],
            },
            {"type": 1, "components": [{"type": 3, "custom_id": "s", "options": []}]},
        ]
        self.source_messages[7] = SimpleNamespace(
            content="",
            embeds=[],
            attachments=[],
            components=[_component_factory(item) for item in raw],
        )
        await self.cog.set_tag.callback(
            self.cog, self.ctx, "about", "https://discord.com/channels/1/5/7"
        )
        view = await self.cog.tag_view(self.guild, "about")
        container, buttons = view.to_components()
        self.assertEqual(container["accent_color"], 15548997)
        self.assertEqual([item["type"] for item in container["components"]], [12, 10, 14, 10])
        self.assertEqual(container["components"][0]["items"][0]["media"]["url"], gif)
        self.assertEqual(container["components"][3]["content"], "Pick a role")
        self.assertEqual([item["label"] for item in buttons["components"]], ["GitHub"])
        self.assertNotIn("custom_id", str(view.to_components()))

    async def test_oversized_messages_are_rejected(self):
        embeds = [discord.Embed(description="x" * 1500) for _ in range(3)]
        self.source_messages[7] = SimpleNamespace(content="", embeds=embeds, attachments=[])
        with self.assertRaisesRegex(ServiceError, "too large"):
            await self.cog.set_tag.callback(
                self.cog, self.ctx, "rules", "https://discord.com/channels/1/5/7"
            )
        self.assertIsNone(self.bot.storage.get_tag(1, "rules"))

    async def test_all_admin_commands_enforce_runtime_permissions(self):
        ctx = SimpleNamespace(
            guild=object(),
            author=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False)),
        )
        for name in (
            "set_tag",
            "delete_tag",
            "list_tags",
            "configure_counters",
            "add_counter",
            "remove_counter",
        ):
            command = getattr(self.cog, name)
            self.assertTrue(command.checks, name)
            with self.assertRaises(commands.MissingPermissions):
                result = command.checks[0](ctx)
                if inspect.isawaitable(result):
                    await result

    async def test_counters_create_locked_voice_channels_and_reuse_after_restart(self):
        await self.cog.sync_counters(self.guild, core_team=self.role)
        self.assertEqual(
            {channel.name for channel in self.channels.values()},
            {"Total Members: 12", "Core Team: 2"},
        )
        self.guild.chunk.assert_awaited_once_with(cache=True)
        self.guild.create_category.assert_awaited_once()
        for channel in self.channels.values():
            self.assertEqual(channel.category_id, 99)
            self.assertFalse(channel.overwrites[self.everyone].connect)
            self.assertTrue(channel.overwrites[self.everyone].view_channel)
        self.bot.storage = Storage(self.path)
        self.guild.chunked = True
        await self.cog.sync_counters(self.guild, core_team=self.role)
        self.assertEqual(self.guild.create_voice_channel.await_count, 2)
        self.guild.create_category.assert_awaited_once()
        for channel in self.channels.values():
            channel.edit.assert_not_awaited()
        self.guild.member_count = 15
        self.role.members.append(object())
        await self.cog.sync_counters(self.guild)
        names = {channel.edit.call_args.kwargs["name"] for channel in self.channels.values()}
        self.assertEqual(names, {"Total Members: 15", "Core Team: 3"})

    async def test_deleted_counter_is_recreated_and_role_label_updates_persist(self):
        await self.cog.sync_counters(self.guild, core_team=self.role)
        role_config = next(
            row for row in self.bot.storage.counters(1) if row["role_id"] == self.role.id
        )
        del self.channels[role_config["channel_id"]]
        await self.cog.sync_counters(self.guild)
        self.assertEqual(self.guild.create_voice_channel.await_count, 3)
        await self.cog.sync_counters(self.guild, extra_role=self.role, label="Organizers")
        config = next(
            row for row in Storage(self.path).counters(1) if row["role_id"] == self.role.id
        )
        self.assertEqual(config["label"], "Organizers")
        self.assertEqual(len(self.channels), 2)
        self.assertTrue(self.bot.storage.remove_counter(1, config["channel_id"]))
        self.assertEqual(len(self.bot.storage.counters(1)), 1)

    async def test_no_channel_creation_when_member_fetch_or_permissions_fail(self):
        self.guild.me.guild_permissions.manage_channels = False
        with self.assertRaises(ServiceError):
            await self.cog.sync_counters(self.guild, core_team=self.role)
        self.guild.create_voice_channel.assert_not_awaited()
        self.guild.me.guild_permissions.manage_channels = True
        self.guild.chunk.side_effect = RuntimeError("Member cache unavailable")
        with self.assertRaises(RuntimeError):
            await self.cog.sync_counters(self.guild, core_team=self.role)
        self.guild.create_voice_channel.assert_not_awaited()
        self.assertEqual(self.bot.storage.counters(1), [])

    async def test_info_describes_creator_and_bot_creation_time(self):
        user = SimpleNamespace(
            created_at=datetime(2020, 1, 2, tzinfo=UTC),
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        command = next(
            command for command in self.bot.pending_application_commands if command.name == "info"
        )
        with patch.object(type(self.bot), "user", new_callable=lambda: property(lambda bot: user)):
            await command.callback(self.ctx)
        text = str(self.ctx.respond.call_args.kwargs["view"].to_components())
        for expected in (
            "SpreadSheets600",
            "2020-01-02",
            "spreadsheets600.buzz",
            "GitHub",
            "daily problems",
        ):
            self.assertIn(expected, text)
