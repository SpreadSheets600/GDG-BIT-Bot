import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from discord.webhook.async_ import handle_message_parameters

from bot.main import create_bot
from bot.ui.cards import CardContent, MessageCard, notice


class AllMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot = create_bot(database_path=Path(self.tmp.name) / "cards.db")
        self.addAsyncCleanup(self.bot.close)
        self.member = SimpleNamespace(
            id=1,
            display_name="Alice",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        self.ctx = SimpleNamespace(
            author=self.member,
            guild=SimpleNamespace(id=123, name="GDGoC BIT"),
            guild_id=123,
            defer=AsyncMock(),
            respond=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def connect(self, provider, username):
        self.bot.storage.begin_proof(1, username, "proof", 200, provider)
        self.bot.storage.confirm_proof(1, "proof", 100, provider)

    async def test_profile_lists_both_verified_accounts(self):
        self.connect("leetcode", "lc-alice")
        self.connect("gfg", "gfg-alice")
        await self.bot.get_cog("CommunityCog").profile.callback(
            self.bot.get_cog("CommunityCog"), self.ctx
        )
        payload = json.dumps(self.ctx.followup.send.call_args.kwargs["view"].to_components())
        self.assertIn("lc-alice", payload)
        self.assertIn("gfg-alice", payload)
        self.assertIn("Connected accounts", payload)

    async def test_default_profile_opens_for_gfg_only_member(self):
        self.connect("gfg", "gfg-alice")
        await self.bot.get_cog("CommunityCog").show_profile(self.ctx)
        payload = json.dumps(self.ctx.followup.send.call_args.kwargs["view"].to_components())
        self.assertIn("gfg-alice", payload)
        self.assertIn("GeeksforGeeks progress", payload)

    async def test_registration_ping_info_and_empty_states_all_send_v2(self):
        await self.bot.get_cog("AccountsCog").register.callback(
            self.bot.get_cog("AccountsCog"), self.ctx
        )
        responses = [self.ctx.followup.send.call_args.kwargs]
        for name in ("ping", "info"):
            command = next(
                command for command in self.bot.pending_application_commands if command.name == name
            )
            await command.callback(self.ctx)
            responses.append(self.ctx.respond.call_args.kwargs)
        for name in ("solvers", "history"):
            cog = self.bot.get_cog("CommunityCog")
            await getattr(cog, name).callback(cog, self.ctx)
            responses.append(self.ctx.followup.send.call_args.kwargs)
        for response in responses:
            self.assertNotIn("embed", response)
            self.assertTrue(response["view"].is_components_v2())
            self.assertTrue(
                handle_message_parameters(view=response["view"]).payload["flags"] & 1 << 15
            )

    async def test_long_cards_stay_within_discord_text_limit(self):
        content = CardContent(title="Title", description="x" * 5000)
        content.add_field(name="Overflow", value="y" * 5000)
        view = MessageCard(self.bot, content)
        components = view.to_components()[0]["components"]
        self.assertLessEqual(sum(len(item.get("content", "")) for item in components), 4000)
        self.assertTrue(notice(self.bot, "Error", "Try again").is_components_v2())

    async def test_no_discord_send_path_uses_legacy_content_or_embeds(self):
        for path in Path("bot").rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr in {"send", "send_message", "respond", "edit_message"}:
                    keywords = {keyword.arg for keyword in node.keywords}
                    self.assertNotIn("embed", keywords, str(path))
                    self.assertNotIn("embeds", keywords, str(path))
                    self.assertIn("view", keywords, str(path))
                    self.assertFalse(node.args, str(path))


if __name__ == "__main__":
    unittest.main()
