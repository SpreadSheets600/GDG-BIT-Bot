import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.webhook.async_ import handle_message_parameters

from bot.main import create_bot
from bot.ui.challenge import ChallengeView, LeaderboardView
from bot.ui.help import TOPICS, HelpView
from bot.ui.layouts import DetailsLayout, ProfileLayout


def walk(components):
    for component in components:
        yield component
        yield from walk(component.get("components", []))
        if component.get("accessory"):
            yield from walk([component["accessory"]])


class ComponentsV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot = create_bot(database_path=Path(self.tmp.name) / "v2.db")
        self.addAsyncCleanup(self.bot.close)
        self.summary = {
            "today_solved": False,
            "points": 0,
            "level": 1,
            "solves": 0,
            "current_streak": 0,
            "best_streak": 0,
            "recent": [],
        }
        self.member = SimpleNamespace(
            id=1,
            display_name="SOHAM",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        self.interaction = SimpleNamespace(
            user=self.member,
            guild=SimpleNamespace(id=123, name="GDGoC BIT"),
            guild_id=123,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    def profile(self, provider="leetcode"):
        return ProfileLayout(
            self.bot,
            provider=provider,
            member=self.member,
            account={"username": "alice"},
            summary=self.summary,
            standing=None,
            server_name="GDGoC BIT",
        )

    async def test_v2_messages_set_required_flag_and_omit_embeds(self):
        views = [self.profile(), LeaderboardView(self.bot, [], "all-time", 1)]
        views.extend(HelpView(self.bot, 1, topic) for topic in TOPICS)
        for view in views:
            self.assertIsInstance(view, discord.ui.DesignerView)
            payload = handle_message_parameters(view=view, ephemeral=True).payload
            self.assertTrue(payload["flags"] & 1 << 15)
            self.assertTrue(payload["flags"] & 64)
            self.assertNotIn("embeds", payload)
            self.assertNotIn("content", payload)
            components = list(walk(payload["components"]))
            self.assertLessEqual(len(components), 40)
            self.assertLessEqual(sum(len(item.get("content", "")) for item in components), 4000)
            ids = [item["custom_id"] for item in components if "custom_id" in item]
            self.assertEqual(len(ids), len(set(ids)))

    async def test_profile_has_spaced_sections_and_no_checkin_action(self):
        components = list(walk(self.profile().to_components()))
        self.assertEqual(components[0]["type"], 17)
        self.assertTrue(
            any(item["type"] == 9 and item["accessory"]["type"] == 11 for item in components)
        )
        self.assertTrue(any(item["type"] == 14 and item["spacing"] == 2 for item in components))
        self.assertNotIn("Badges", json.dumps(components))
        self.assertNotIn("Recent check-ins", json.dumps(components))
        self.assertTrue(self.profile().is_persistent())
        payload = json.dumps(components)
        self.assertNotIn("daily:check", payload)
        self.assertNotIn("Today's POTD", payload)
        self.assertNotIn("community:leaderboard", payload)
        self.assertNotIn("Verified account", payload)

    async def test_gfg_buttons_use_restart_stable_platform_specific_ids(self):
        new_ids = {
            item["custom_id"]
            for item in walk(self.profile("gfg").to_components())
            if "custom_id" in item
        }
        legacy = ChallengeView(self.bot, "gfg")
        registered = {item.custom_id for item in legacy.children}
        self.assertTrue(new_ids <= registered)
        self.assertIn("gdg:gfg:community:details", new_ids)

    async def test_details_are_private_and_keep_badges_separate_from_summary(self):
        self.bot.storage.begin_proof(1, "alice", "proof", 200)
        self.bot.storage.confirm_proof(1, "proof", 100)
        ctx = SimpleNamespace(
            author=self.member,
            guild=self.interaction.guild,
            guild_id=123,
            defer=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await self.bot.get_cog("CommunityCog").show_details(ctx)
        result = ctx.followup.send.call_args.kwargs
        self.assertTrue(result["ephemeral"])
        self.assertIsInstance(result["view"], DetailsLayout)
        self.assertIn("### Badges", json.dumps(result["view"].to_components()))
        self.assertNotIn("embed", result)

    async def test_leaderboard_pagination_edits_v2_layout_without_embeds(self):
        rows = [
            {"rank": n, "display_name": f"Member {n}", "points": 10, "solves": 1}
            for n in range(1, 12)
        ]
        view = LeaderboardView(self.bot, rows, "all-time", 1)
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view.next.callback(interaction)
        result = interaction.response.edit_message.call_args.kwargs
        self.assertNotIn("embed", result)
        self.assertIn("Member 11", json.dumps(result["view"].to_components()))
        self.assertTrue(view.next.disabled)


if __name__ == "__main__":
    unittest.main()
