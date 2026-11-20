import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.main import create_bot
from bot.services.problems import IST, DailyProblem
from bot.services.storage import Storage
from bot.ui.challenge import ChallengeView


class ServerLeaderboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Storage(Path(self.tmp.name) / "server.db")
        self.day = date(2026, 11, 20)
        self.now = datetime(2026, 11, 20, 12, tzinfo=IST)

    def award(self, user, provider, guild=1, points="Easy", now=None):
        now = now or self.now
        zone = IST if provider == "gfg" else UTC
        day = now.astimezone(zone).date()
        if not self.db.connected_account(user, provider):
            self.db.begin_proof(user, f"coder{user}", "proof", int(now.timestamp()) + 100, provider)
            self.db.confirm_proof(user, "proof", int(now.timestamp()), provider)
        self.db.touch_member(guild, user, f"Member {user}")
        resets = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
        problem = DailyProblem(
            provider,
            day,
            "Example",
            "https://example.com/problems/example/1",
            points,
            (),
            (),
            resets,
        )
        return self.db.record_solve(
            guild,
            user,
            f"coder{user}",
            problem,
            {"id": "123", "titleSlug": "example", "timestamp": str(int(now.timestamp()))},
            int(now.timestamp()),
        )

    def test_both_platforms_contribute_to_one_member_row(self):
        self.award(1, "leetcode")
        self.award(1, "gfg", points="Medium")
        self.award(2, "gfg")
        rows = self.db.leaderboard(1, self.day, provider="all")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["user_id"], 1)
        self.assertEqual(rows[0]["points"], 30)
        self.assertEqual(rows[0]["solves"], 2)
        self.assertEqual(rows[0]["leetcode_solves"], 1)
        self.assertEqual(rows[0]["gfg_solves"], 1)

    def test_other_servers_and_unverified_handles_do_not_enter_standings(self):
        self.award(1, "gfg", guild=1)
        self.award(2, "gfg", guild=2, points="Hard")
        self.db.set_profile(3, "leetcode", "someone")
        self.db.touch_member(1, 3, "Saved handle only")
        self.assertEqual([row["user_id"] for row in self.db.server_leaderboard(1, self.day)], [1])
        self.assertEqual([row["user_id"] for row in self.db.server_leaderboard(2, self.day)], [2])
        self.assertEqual(self.db.server_leaderboard(3, self.day), [])

    def test_repeat_marking_and_restarts_never_inflate_the_shared_score(self):
        self.assertTrue(self.award(1, "gfg"))
        self.assertFalse(self.award(1, "gfg"))
        self.db = Storage(self.db.path)
        self.assertFalse(self.award(1, "gfg"))
        self.assertEqual(self.db.server_leaderboard(1, self.day)[0]["points"], 10)

    def test_platform_filters_remain_separate(self):
        self.award(1, "leetcode")
        self.award(1, "gfg", points="Hard")
        self.assertEqual(self.db.leaderboard(1, self.day)[0]["points"], 10)
        self.assertEqual(self.db.leaderboard(1, self.day, provider="gfg")[0]["points"], 30)
        self.assertEqual(self.db.server_leaderboard(1, self.day)[0]["points"], 40)

    def test_combined_week_uses_server_checkin_calendar(self):
        monday = datetime(2026, 11, 16, 0, 5, tzinfo=IST)
        self.award(1, "leetcode", now=monday)
        self.award(2, "gfg", now=monday - timedelta(minutes=10))
        rows = self.db.server_leaderboard(1, self.day, "week")
        self.assertEqual([row["user_id"] for row in rows], [1])
        self.assertEqual(len(self.db.server_leaderboard(1, self.day)), 2)


class ServerLeaderboardCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_and_problem_buttons_open_named_server_combined_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = create_bot(database_path=Path(tmp) / "bot.db")
            try:
                cog = bot.get_cog("CommunityCog")
                ctx = SimpleNamespace(
                    author=SimpleNamespace(id=1),
                    guild=SimpleNamespace(id=123, name="GDGoC BIT"),
                    guild_id=123,
                    defer=AsyncMock(),
                    followup=SimpleNamespace(send=AsyncMock()),
                )
                await cog.leaderboard.callback(cog, ctx)
                card = str(ctx.followup.send.call_args.kwargs["view"].to_components())
                self.assertIn("GDGoC BIT", card)
                self.assertIn("LeetCode + GFG", card)
                for provider in ("leetcode", "gfg"):
                    interaction = SimpleNamespace(
                        user=ctx.author,
                        guild=ctx.guild,
                        guild_id=123,
                        response=SimpleNamespace(defer=AsyncMock()),
                        followup=ctx.followup,
                    )
                    view = ChallengeView(bot, provider)
                    self.assertEqual(view.check.label, "Mark as solved")
                    await view.board.callback(interaction)
                    self.assertIn(
                        "LeetCode + GFG",
                        str(ctx.followup.send.call_args.kwargs["view"].to_components()),
                    )
            finally:
                await bot.close()


if __name__ == "__main__":
    unittest.main()
