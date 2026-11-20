import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.main import create_bot
from bot.services.community_storage import streaks
from bot.services.problems import ServiceError, normalize_daily
from bot.services.progress import ProgressService, matching_submission
from bot.services.storage import Storage
from bot.ui.challenge import ChallengeView, LeaderboardView, VerifyView


def problem(now=None):
    now = now or datetime.now(UTC)
    return normalize_daily(
        "leetcode",
        {
            "date": now.date().isoformat(),
            "questionTitle": "Two Sum",
            "questionLink": "https://leetcode.com/problems/two-sum/",
            "difficulty": "Easy",
        },
        now,
    )


def submission(now=None, slug="two-sum"):
    now = now or datetime.now(UTC)
    return {"id": "123456", "titleSlug": slug, "timestamp": str(int(now.timestamp()))}


def connect(db, user_id=1, username="alice"):
    now = int(time.time())
    db.begin_proof(user_id, username, f"proof-{user_id}", now + 100)
    db.confirm_proof(user_id, f"proof-{user_id}", now)


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 20, 12, tzinfo=UTC)
        self.problem = problem(self.now)

    def test_matches_only_correct_problem_and_current_day(self):
        accepted = submission(self.now - timedelta(seconds=30))
        result = matching_submission(
            self.problem, [submission(self.now, "other"), accepted], self.now
        )
        self.assertEqual(result, accepted)
        for item in (
            submission(self.now - timedelta(days=1)),
            submission(self.now + timedelta(minutes=1)),
            submission(self.now, "other"),
        ):
            self.assertIsNone(matching_submission(self.problem, [item], self.now))

    def test_midnight_start_is_included(self):
        accepted = submission(self.now.replace(hour=0))
        self.assertEqual(matching_submission(self.problem, [accepted], self.now), accepted)

    def test_next_day_cannot_claim_previous_problem(self):
        with self.assertRaises(ServiceError):
            matching_submission(self.problem, [submission(self.now)], self.problem.resets_at)

    def test_bad_submission_items_do_not_create_credit(self):
        items = [
            None,
            {},
            {"timestamp": "wrong"},
            {"timestamp": "123", "titleSlug": "two-sum", "id": "invalid"},
        ]
        self.assertIsNone(matching_submission(self.problem, items, self.now))

    def test_unsupported_platform_is_rejected(self):
        with self.assertRaises(ServiceError):
            matching_submission(replace(self.problem, provider="unsupported"), [], self.now)

    def test_oldest_accepted_submission_for_today_is_used(self):
        first = submission(self.now - timedelta(hours=1))
        self.assertEqual(
            matching_submission(self.problem, [submission(self.now), first], self.now), first
        )


class CommunityStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Storage(Path(self.tmp.name) / "bot.db")
        self.today = date(2026, 11, 20)

    def test_proof_is_required_before_verified_account_exists(self):
        self.db.set_profile(1, "leetcode", "alice")
        self.assertIsNone(self.db.connected_account(1))
        self.db.begin_proof(1, "alice", "secret", 100)
        self.assertIsNone(self.db.connected_account(1))
        self.assertEqual(self.db.confirm_proof(1, "secret", 99), "alice")
        self.assertIsNone(self.db.pending_proof(1))
        self.assertEqual(self.db.get_profile(1)["leetcode_id"], "alice")

    def test_expired_or_replaced_proof_cannot_connect(self):
        self.db.begin_proof(1, "alice", "old", 100)
        with self.assertRaises(ServiceError):
            self.db.confirm_proof(1, "old", 100)
        self.db.begin_proof(1, "bob", "new", 200)
        with self.assertRaises(ServiceError):
            self.db.confirm_proof(1, "old", 99)
        self.assertIsNone(self.db.connected_account(1))

    def test_verified_username_unique_case_insensitively(self):
        connect(self.db)
        with self.assertRaises(ServiceError):
            self.db.begin_proof(2, "ALICE", "another", int(time.time()) + 100)
        with self.assertRaises(ServiceError):
            self.db.begin_proof(1, "bob", "another", int(time.time()) + 100)

    def test_simultaneous_pending_proofs_cannot_claim_same_account(self):
        self.db.begin_proof(1, "alice", "one", 200)
        self.db.begin_proof(2, "ALICE", "two", 200)
        self.db.confirm_proof(1, "one", 100)
        with self.assertRaises(ServiceError):
            self.db.confirm_proof(2, "two", 100)
        self.assertIsNone(self.db.connected_account(2))
        self.assertEqual(self.db.get_profile(2), {})

    def test_saved_handle_cannot_override_connected_identity(self):
        connect(self.db)
        with self.assertRaises(ServiceError):
            self.db.set_profile(1, "leetcode", "bob")
        self.db.set_profile(1, "leetcode", "Alice")
        self.assertEqual(self.db.connected_account(1)["username"], "alice")

    def seed_solve(self, user_id=1, username="alice", guild=1, day=None, difficulty="Easy"):
        if not self.db.connected_account(user_id):
            connect(self.db, user_id, username)
        self.db.touch_member(guild, user_id, username)
        now = datetime.combine(day or self.today, datetime.min.time(), UTC) + timedelta(hours=12)
        value = replace(problem(now), difficulty=difficulty)
        return self.db.record_solve(
            guild, user_id, username, value, submission(now), int(now.timestamp())
        )

    def test_awards_once_and_survives_database_reopen(self):
        self.assertTrue(self.seed_solve())
        self.assertFalse(self.seed_solve())
        reopened = Storage(self.db.path)
        summary = reopened.progress_summary(1, 1, self.today)
        self.assertEqual(summary["points"], 10)
        self.assertEqual(summary["solves"], 1)

    def test_concurrent_awards_cannot_duplicate_points(self):
        connect(self.db)
        self.db.touch_member(1, 1, "Alice")
        now = datetime(2026, 11, 20, 12, tzinfo=UTC)

        def award(_):
            return self.db.record_solve(
                1, 1, "alice", problem(now), submission(now), int(now.timestamp())
            )

        with ThreadPoolExecutor(max_workers=5) as workers:
            results = list(workers.map(award, range(10)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.db.progress_summary(1, 1, self.today)["points"], 10)

    def test_server_scores_are_isolated(self):
        self.seed_solve(guild=1)
        self.assertEqual(self.db.leaderboard(2, self.today), [])
        self.assertEqual(self.db.progress_summary(2, 1, self.today)["solves"], 0)
        self.seed_solve(guild=2)
        self.assertEqual(self.db.progress_summary(2, 1, self.today)["solves"], 1)

    def test_week_and_month_filters_and_difficulty_scores(self):
        self.seed_solve(day=date(2026, 10, 31), difficulty="Hard")
        self.seed_solve(day=date(2026, 11, 1), difficulty="Medium")
        self.seed_solve(day=self.today, difficulty="Easy")
        self.assertEqual(self.db.leaderboard(1, self.today)[0]["points"], 60)
        self.assertEqual(self.db.leaderboard(1, self.today, "month")[0]["points"], 30)
        self.assertEqual(self.db.leaderboard(1, self.today, "week")[0]["points"], 10)

    def test_ranking_is_deterministic_for_ties(self):
        self.seed_solve(user_id=2, username="bob")
        self.seed_solve(user_id=1, username="alice")
        self.seed_solve(user_id=3, username="charlie", difficulty="Hard")
        rows = self.db.leaderboard(1, self.today)
        self.assertEqual([row["user_id"] for row in rows], [3, 1, 2])
        self.assertEqual([row["rank"] for row in rows], [1, 2, 3])

    def test_storage_requires_verified_owner(self):
        with self.assertRaises(ServiceError):
            self.seed_solve if False else self.db.record_solve(
                1, 1, "alice", problem(), submission(), int(time.time())
            )

    def test_unknown_difficulty_does_not_award_zero_or_arbitrary_points(self):
        with self.assertRaises(ServiceError):
            self.seed_solve(difficulty="Impossible")
        self.assertEqual(self.db.leaderboard(1, self.today), [])

    def test_streak_extends_breaks_and_stays_alive_until_end_of_today(self):
        self.assertEqual(streaks([], self.today), (0, 0))
        self.assertEqual(streaks(["2026-11-18", "2026-11-19"], self.today), (2, 2))
        self.assertEqual(streaks(["2026-11-18", "2026-11-19", "2026-11-20"], self.today), (3, 3))
        self.assertEqual(streaks(["2026-11-16", "2026-11-17"], self.today), (0, 2))
        self.assertEqual(streaks(["2026-11-16", "2026-11-17", "2026-11-20"], self.today), (1, 2))
        self.assertEqual(streaks(["2026-11-19", "2026-11-19"], self.today), (1, 1))

    def test_summary_badges_and_today_state(self):
        for offset in range(7):
            self.seed_solve(day=self.today - timedelta(days=offset), difficulty="Hard")
        summary = self.db.progress_summary(1, 1, self.today)
        self.assertEqual(summary["current_streak"], 7)
        self.assertEqual(summary["best_streak"], 7)
        self.assertEqual(summary["level"], 3)
        self.assertTrue(summary["today_solved"])


class ProgressServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Storage(Path(self.tmp.name) / "test.db")
        self.problems = SimpleNamespace(
            profile=AsyncMock(return_value={"username": "alice", "profile": {"aboutMe": ""}}),
            daily=AsyncMock(return_value=problem()),
            request=AsyncMock(return_value={"data": {"recentAcSubmissionList": [submission()]}}),
        )
        self.service = ProgressService(self.db, self.problems)

    async def test_cannot_connect_by_supplying_someone_elses_username(self):
        username, token = await self.service.begin_link(1, "alice")
        self.assertEqual(username, "alice")
        with self.assertRaises(ServiceError):
            await self.service.verify_link(1)
        self.assertIsNone(self.db.connected_account(1))
        self.problems.profile.return_value["profile"]["aboutMe"] = "My bio " + token
        self.assertEqual(await self.service.verify_link(1), "alice")
        self.assertEqual(self.db.connected_account(1)["username"], "alice")

    async def test_missing_or_expired_proof_rejected_before_network(self):
        with self.assertRaises(ServiceError):
            await self.service.verify_link(1)
        self.problems.profile.assert_not_awaited()
        self.db.begin_proof(1, "alice", "expired", int(time.time()) - 1)
        with self.assertRaises(ServiceError):
            await self.service.verify_link(1)
        self.problems.profile.assert_not_awaited()

    async def test_replaced_proof_during_network_call_cannot_connect_old_account(self):
        _, token = await self.service.begin_link(1, "alice")

        async def profile(username):
            self.db.begin_proof(1, "bob", "new-proof", int(time.time()) + 100)
            return {"username": "alice", "profile": {"aboutMe": token}}

        self.problems.profile.side_effect = profile
        with self.assertRaises(ServiceError):
            await self.service.verify_link(1)
        self.assertIsNone(self.db.connected_account(1))

    async def test_solve_requires_ownership_not_just_legacy_saved_handle(self):
        self.db.set_profile(1, "leetcode", "alice")
        with self.assertRaises(ServiceError):
            await self.service.check_solve(1, 1, "Alice")
        self.problems.request.assert_not_awaited()

    async def test_claim_in_dm_is_rejected(self):
        with self.assertRaises(ServiceError):
            await self.service.check_solve(None, 1, "Alice")

    async def test_accepted_daily_awards_once_even_after_restart(self):
        connect(self.db)
        created, row, summary = await self.service.check_solve(1, 1, "Alice")
        self.assertTrue(created)
        self.assertEqual(row["points"], 10)
        self.assertEqual(summary["current_streak"], 1)
        restarted = ProgressService(Storage(self.db.path), self.problems)
        created, _, summary = await restarted.check_solve(1, 1, "Alice")
        self.assertFalse(created)
        self.assertEqual(summary["points"], 10)
        self.problems.request.assert_awaited_once()

    async def test_wrong_problem_does_not_award_points_and_recheck_has_cooldown(self):
        connect(self.db)
        self.problems.request.return_value = {
            "data": {"recentAcSubmissionList": [submission(slug="wrong-problem")]}
        }
        with self.assertRaises(ServiceError):
            await self.service.check_solve(1, 1, "Alice")
        self.assertEqual(self.db.leaderboard(1, datetime.now(UTC).date()), [])
        with self.assertRaisesRegex(ServiceError, "15 seconds"):
            await self.service.check_solve(1, 1, "Alice")
        self.problems.request.assert_awaited_once()

    async def test_missing_private_and_malformed_feed_never_awards_points(self):
        connect(self.db)
        for payload in (
            {},
            {"data": None},
            {"data": {"recentAcSubmissionList": None}},
            {"data": {"recentAcSubmissionList": []}},
        ):
            service = ProgressService(self.db, self.problems)
            self.problems.request.return_value = payload
            with self.assertRaises(ServiceError):
                await service.check_solve(1, 1, "Alice")
        self.assertEqual(self.db.leaderboard(1, datetime.now(UTC).date()), [])


class CommunityCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot = create_bot(database_path=Path(self.tmp.name) / "test.db")
        self.addAsyncCleanup(self.bot.close)
        self.cog = self.bot.get_cog("CommunityCog")
        self.member = SimpleNamespace(
            id=1,
            display_name="Alice",
            display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        )
        self.ctx = SimpleNamespace(
            author=self.member,
            guild=SimpleNamespace(id=123),
            guild_id=123,
            defer=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_listener_registers_persistent_views_once_on_reconnect(self):
        await self.cog.on_connect()
        self.assertEqual(len(self.cog.views), 4)
        self.assertTrue(all(view.is_persistent() for view in self.cog.views))
        await self.cog.on_connect()
        self.assertEqual(len(self.cog.views), 4)

    async def test_challenge_buttons_have_distinct_persistent_ids_per_platform(self):
        leetcode = ChallengeView(self.bot, "leetcode", "https://leetcode.com/problems/two-sum/")
        self.assertTrue(leetcode.is_persistent())
        gfg = ChallengeView(self.bot, "gfg", "https://www.geeksforgeeks.org/problems/example/1")
        self.assertNotIn("gdg:daily:check", [child.custom_id for child in gfg.children])
        self.assertIn("gdg:gfg:daily:check", [child.custom_id for child in gfg.children])
        self.assertTrue(gfg.is_persistent())
        self.assertTrue(VerifyView(self.bot).is_persistent())

    async def test_profile_guides_unconnected_member(self):
        await self.cog.show_profile(self.ctx)
        self.assertIn(
            "/connect leetcode",
            str(self.ctx.followup.send.call_args.kwargs["view"].to_components()),
        )

    async def test_connected_profile_shows_progress_and_has_actions(self):
        connect(self.bot.storage)
        await self.cog.show_profile(self.ctx)
        response = self.ctx.followup.send.call_args.kwargs
        self.assertIn("alice", str(response["view"].to_components()))
        self.assertNotIn("embed", response)
        self.assertTrue(response["view"].is_components_v2())

    async def test_solving_button_uses_clicking_member_not_message_author(self):
        connect(self.bot.storage)
        self.bot.problems.daily = AsyncMock(return_value=problem())
        self.bot.problems.request = AsyncMock(
            return_value={"data": {"recentAcSubmissionList": [submission()]}}
        )
        interaction = SimpleNamespace(
            user=self.member,
            guild=self.ctx.guild,
            guild_id=123,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=self.ctx.followup,
        )
        view = ChallengeView(self.bot)
        await view.check.callback(interaction)
        self.assertIsNotNone(
            self.bot.storage.recorded_solve(123, 1, datetime.now(UTC).date().isoformat())
        )
        self.assertTrue(self.ctx.followup.send.call_args.kwargs["ephemeral"])

    async def test_empty_leaderboard_is_useful(self):
        await self.cog.show_board(self.ctx)
        self.assertIn(
            "No verified", str(self.ctx.followup.send.call_args.kwargs["view"].to_components())
        )

    async def test_leaderboard_pagination_and_button_owner(self):
        rows = [
            {"rank": index, "user_id": index, "display_name": "x" * 100, "points": 10, "solves": 1}
            for index in range(1, 24)
        ]
        view = LeaderboardView(self.bot, rows, "all-time", 1)
        self.assertTrue(view.previous.disabled)
        self.assertFalse(view.next.disabled)
        interaction = SimpleNamespace(
            user=self.member,
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
        )
        self.assertTrue(await view.interaction_check(interaction))
        await view.next.callback(interaction)
        self.assertEqual(view.page, 1)
        await view.next.callback(interaction)
        await view.next.callback(interaction)
        self.assertEqual(view.page, 2)
        self.assertTrue(view.next.disabled)
        self.assertLessEqual(len(view.content()), 6000)
        interaction.user = SimpleNamespace(id=2)
        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()

    async def test_solvers_and_history_empty_states(self):
        await self.cog.solvers.callback(self.cog, self.ctx)
        self.assertIn(
            "Be the first", str(self.ctx.followup.send.call_args.kwargs["view"].to_components())
        )
        await self.cog.history.callback(self.cog, self.ctx)
        self.assertIn(
            "No verified", str(self.ctx.followup.send.call_args.kwargs["view"].to_components())
        )


if __name__ == "__main__":
    unittest.main()
