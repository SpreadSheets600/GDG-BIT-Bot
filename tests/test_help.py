import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from bot.cogs.community import badges
from bot.main import create_bot
from bot.ui.challenge import ChallengeView, LeaderboardView
from bot.ui.help import TOPICS, HelpView, help_content

EMOJI = re.compile("[🌀-\U0001faff☀-➿]")


class HelpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot = create_bot(database_path=Path(self.tmp.name) / "help.db")
        self.addAsyncCleanup(self.bot.close)
        self.ctx = SimpleNamespace(
            author=SimpleNamespace(id=1),
            defer=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_help_command_is_registered_and_guide_is_private(self):
        cog = self.bot.get_cog("HelpCog")
        command = next(
            item for item in self.bot.pending_application_commands if item.name == "help"
        )
        self.assertEqual(command.to_dict()["name"], "help")
        await cog.help.callback(cog, self.ctx)
        self.ctx.defer.assert_awaited_once_with(ephemeral=True)
        response = self.ctx.followup.send.call_args.kwargs
        self.assertTrue(response["ephemeral"])
        self.assertIsInstance(response["view"], HelpView)
        self.assertIn("/connect gfg", str(response["view"].to_components()))

    async def test_topics_fit_discord_limits_and_have_no_emojis(self):
        for topic in TOPICS:
            embed = help_content(topic)
            self.assertLessEqual(len(embed), 6000)
            self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
            self.assertIsNone(EMOJI.search(str(embed.to_dict())))
        self.assertIn("old timestamp", str(help_content("troubleshooting").to_dict()))

    async def test_topic_selection_updates_the_existing_guide(self):
        view = HelpView(self.bot, 1)
        menu = view.menu
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        with patch.object(type(menu), "values", new_callable=PropertyMock, return_value=["daily"]):
            await menu.callback(interaction)
        result = interaction.response.edit_message.call_args.kwargs
        self.assertNotIn("embed", result)
        self.assertIn("Help | Daily problems", str(result["view"].to_components()))
        self.assertIs(result["view"], view)
        self.assertTrue(next(option for option in menu.options if option.value == "daily").default)

    async def test_help_interactions_are_owned_by_requesting_member(self):
        view = HelpView(self.bot, 1)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=2), response=SimpleNamespace(send_message=AsyncMock())
        )
        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()
        interaction.user.id = 1
        self.assertTrue(await view.interaction_check(interaction))

    async def test_persistent_help_button_opens_guide_for_clicking_member(self):
        cog = self.bot.get_cog("HelpCog")
        interaction = SimpleNamespace(
            guild_id=123,
            guild=SimpleNamespace(id=123),
            user=SimpleNamespace(id=2),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        for provider in ("leetcode", "gfg"):
            view = ChallengeView(self.bot, provider)
            self.assertTrue(view.is_persistent())
            with patch.object(cog, "show_help", new_callable=AsyncMock) as show:
                await view.help.callback(interaction)
            self.assertEqual(show.call_args.args[0].author.id, 2)

    async def test_generated_leaderboard_and_badges_use_plain_text(self):
        rows = [
            {
                "rank": 1,
                "display_name": "Alice",
                "points": 30,
                "solves": 2,
                "leetcode_solves": 1,
                "gfg_solves": 1,
            }
        ]
        embed = LeaderboardView(self.bot, rows, "all-time", 1).content()
        self.assertTrue(embed.description.startswith("1."))
        self.assertIsNone(EMOJI.search(str(embed.to_dict())))
        self.assertIsNone(EMOJI.search(badges({"solves": 100, "best_streak": 30})))


if __name__ == "__main__":
    unittest.main()
