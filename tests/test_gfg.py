import json
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.main import create_bot
from bot.services.gfg import GFGService, accepted_submissions, profile_bio
from bot.services.problems import IST, ServiceError, normalize_daily
from bot.services.progress import ProgressService, matching_submission
from bot.services.storage import Storage
from bot.ui.challenge import ChallengeView, VerifyView


def gfg_problem(now=None):
    now = now or datetime.now(UTC)
    return normalize_daily(
        "gfg",
        {
            "date": now.astimezone(IST).date().isoformat(),
            "problem_name": "GFG Example",
            "problem_url": "https://www.geeksforgeeks.org/problems/example123/1",
            "difficulty": "Medium",
        },
        now,
    )


def gfg_feed(now=None, slug="example123"):
    now = now or datetime.now(UTC)
    return {
        "status": "success",
        "result": {
            "Medium": {
                "703255": {
                    "slug": slug,
                    "pname": "GFG Example",
                    "user_subtime": now.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                }
            }
        },
        "count": 1,
    }


def profile_html(handle="alice", bio="", extra=None):
    props = {"mentor": {"handle": handle, "bio": bio}}
    if extra:
        props.update(extra)
    payload = "6:" + json.dumps(["$", "$L2", None, props]) + "\n"
    return "<script>self.__next_f.push(" + json.dumps([1, payload]) + ")</script>"


def connect(db, provider, username="alice", user_id=1):
    now = int(time.time())
    token = provider + str(user_id)
    db.begin_proof(user_id, username, token, now + 900, provider)
    return db.confirm_proof(user_id, token, now, provider)


class GFGParserTests(unittest.TestCase):
    def test_bio_belongs_to_exact_profile_not_posts_or_recommendations(self):
        html = profile_html(bio="real bio", extra={"post": {"bio": "stolen-proof"}})
        self.assertEqual(profile_bio(html, "alice"), "real bio")
        self.assertNotIn("stolen-proof", profile_bio(html, "alice"))
        with self.assertRaises(ServiceError):
            profile_bio(profile_html(handle="someone-else", bio="proof"), "alice")

    def test_bio_handles_quotes_unicode_and_empty_fields(self):
        for bio in ('hello "world" 🔥 gdg-bit-gfg-code', "", None):
            self.assertEqual(profile_bio(profile_html(bio=bio), "ALICE"), bio or "")

    def test_split_rsc_stream_is_reassembled(self):
        original = "6:" + json.dumps(
            ["$", "$L", None, {"mentor": {"handle": "alice", "bio": "proof"}}]
        )
        chunks = [original[:35], original[35:]]
        html = "".join(
            "<script>self.__next_f.push(" + json.dumps([1, chunk]) + ")</script>"
            for chunk in chunks
        )
        self.assertEqual(profile_bio(html, "alice"), "proof")

    def test_html_or_upstream_shape_change_cannot_verify_ownership(self):
        for html in (
            "<html>proof</html>",
            "<script>broken</script>",
            "<script>self.__next_f.push(garbage)</script>",
        ):
            with self.assertRaises(ServiceError):
                profile_bio(html, "alice")

    def test_public_solved_feed_parses_india_time(self):
        now = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)
        rows = accepted_submissions(gfg_feed(now))
        self.assertEqual(int(rows[0]["timestamp"]), int(now.timestamp()))
        self.assertEqual(rows[0]["titleSlug"], "example123")
        self.assertEqual(rows[0]["id"], "703255")

    def test_timezone_aware_timestamp_keeps_original_offset(self):
        payload = gfg_feed()
        payload["result"]["Medium"]["703255"]["user_subtime"] = "2026-11-20T12:00:00+00:00"
        rows = accepted_submissions(payload)
        self.assertEqual(
            int(rows[0]["timestamp"]), int(datetime(2026, 11, 20, 12, tzinfo=UTC).timestamp())
        )

    def test_unsuccessful_or_private_payload_is_not_an_empty_solved_feed(self):
        for payload in (
            {},
            {"status": "failed", "result": {}},
            {"status": "success", "result": []},
        ):
            with self.assertRaises(ServiceError):
                accepted_submissions(payload)
        self.assertEqual(accepted_submissions({"status": "success", "result": {}}), [])

    def test_bad_timestamp_never_earns_points(self):
        payload = gfg_feed()
        payload["result"]["Medium"]["703255"]["user_subtime"] = "unknown"
        self.assertEqual(accepted_submissions(payload), [])

    def test_gfg_slug_ignores_trailing_language_route_and_obeys_ist_day(self):
        now = datetime(2026, 11, 20, 19, tzinfo=UTC)
        problem = gfg_problem(now)
        rows = accepted_submissions(gfg_feed(now))
        self.assertEqual(problem.date.isoformat(), "2026-11-21")
        self.assertIsNotNone(matching_submission(problem, rows, now))
        self.assertIsNone(
            matching_submission(
                problem, accepted_submissions(gfg_feed(now - timedelta(days=1))), now
            )
        )
        self.assertIsNone(
            matching_submission(problem, accepted_submissions(gfg_feed(now, "different-slug")), now)
        )
        with self.assertRaises(ServiceError):
            matching_submission(problem, rows, problem.resets_at)


class GFGServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Storage(Path(self.tmp.name) / "test.db")
        self.problems = SimpleNamespace(
            profile=AsyncMock(), daily=AsyncMock(return_value=gfg_problem()), request=AsyncMock()
        )
        self.service = ProgressService(self.db, self.problems)
        self.service.gfg.profile = AsyncMock(
            return_value={"username": "alice", "profile": {"aboutMe": ""}}
        )
        self.service.gfg.submissions = AsyncMock(return_value=accepted_submissions(gfg_feed()))

    async def test_gfg_ownership_proof_works_alongside_leetcode(self):
        connect(self.db, "leetcode", "lc-alice")
        _, token = await self.service.begin_link(1, "alice", "gfg")
        with self.assertRaises(ServiceError):
            await self.service.verify_link(1, "gfg")
        self.service.gfg.profile.return_value["profile"]["aboutMe"] = "My bio " + token
        await self.service.verify_link(1, "gfg")
        self.assertEqual(self.db.connected_account(1, "gfg")["username"], "alice")
        self.assertEqual(self.db.connected_account(1)["username"], "lc-alice")
        self.assertEqual(self.db.get_profile(1)["gfg_id"], "alice")
        self.assertEqual(self.db.get_profile(1)["leetcode_id"], "lc-alice")
        self.service.gfg.profile.assert_awaited_with("alice", with_bio=True)

    async def test_proofs_and_usernames_are_scoped_to_platform(self):
        connect(self.db, "leetcode")
        connect(self.db, "gfg")
        with self.assertRaises(ServiceError):
            self.db.begin_proof(2, "ALICE", "new", int(time.time()) + 900, "gfg")
        with self.assertRaises(ServiceError):
            self.db.set_profile(1, "gfg", "bob")

    async def test_accepted_gfg_daily_awards_xp_once_and_survives_restart(self):
        connect(self.db, "gfg")
        created, record, summary = await self.service.check_solve(123, 1, "Alice", "gfg")
        self.assertTrue(created)
        self.assertEqual(record["points"], 20)
        self.assertEqual(summary["current_streak"], 1)
        self.assertEqual(self.db.leaderboard(123, datetime.now(UTC).date()), [])
        restarted = ProgressService(Storage(self.db.path), self.problems)
        created, _, summary = await restarted.check_solve(123, 1, "Alice", "gfg")
        self.assertFalse(created)
        self.assertEqual(summary["points"], 20)
        self.service.gfg.submissions.assert_awaited_once()

    async def test_gfg_feed_failure_never_awards_points(self):
        connect(self.db, "gfg")
        self.service.gfg.submissions.side_effect = ServiceError("Unavailable")
        with self.assertRaises(ServiceError):
            await self.service.check_solve(123, 1, "Alice", "gfg")
        self.assertEqual(self.db.leaderboard(123, datetime.now(IST).date(), provider="gfg"), [])

    async def test_saved_gfg_handle_is_not_verified_identity(self):
        self.db.set_profile(1, "gfg", "alice")
        with self.assertRaisesRegex(ServiceError, "/connect gfg"):
            await self.service.check_solve(123, 1, "Alice", "gfg")
        self.service.gfg.submissions.assert_not_awaited()

    async def test_snapshot_of_existing_lc_database_is_preserved_when_gfg_added(self):
        connect(self.db, "leetcode")
        lc = normalize_daily(
            "leetcode",
            {
                "questionTitle": "LC",
                "questionLink": "https://leetcode.com/problems/two-sum/",
                "date": datetime.now(UTC).date().isoformat(),
                "difficulty": "Hard",
            },
            datetime.now(UTC),
        )
        self.db.touch_member(123, 1, "Alice")
        accepted = {"id": "123", "titleSlug": "two-sum", "timestamp": int(time.time())}
        self.db.record_solve(123, 1, "alice", lc, accepted, int(time.time()))
        connect(self.db, "gfg", "gfg-alice")
        await self.service.check_solve(123, 1, "Alice", "gfg")
        reopened = Storage(self.db.path)
        self.assertEqual(reopened.progress_summary(123, 1, datetime.now(UTC).date())["points"], 30)
        self.assertEqual(
            reopened.progress_summary(123, 1, datetime.now(IST).date(), "gfg")["points"], 20
        )

    async def test_gfg_service_fetches_only_bio_when_required(self):
        upstream = SimpleNamespace(
            request=AsyncMock(return_value={"data": {"name": "Alice", "total_problems_solved": 42}})
        )
        service = GFGService(upstream)
        profile = await service.profile("alice")
        self.assertEqual(profile["profile"]["totalSolved"], 42)
        upstream.request.assert_awaited_once()
        upstream.request.side_effect = [{"data": {"name": "Alice"}}, profile_html(bio="proof")]
        profile = await service.profile("alice", with_bio=True)
        self.assertEqual(profile["profile"]["aboutMe"], "proof")


class GFGCommandsTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_gfg_verify_and_solve_buttons_route_to_gfg(self):
        interaction = SimpleNamespace(
            user=self.member,
            guild=self.ctx.guild,
            guild_id=123,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=self.ctx.followup,
        )
        with patch.object(self.cog, "finish_connection", new_callable=AsyncMock) as verify:
            view = VerifyView(self.bot, "gfg")
            await view.verify.callback(interaction)
            self.assertEqual(verify.call_args.args[1], "gfg")
        with patch.object(self.cog, "check_today", new_callable=AsyncMock) as check:
            view = ChallengeView(self.bot, "gfg")
            await view.check.callback(interaction)
            self.assertEqual(check.call_args.args[1], "gfg")

    async def test_gfg_profile_and_leaderboard_show_correct_provider(self):
        connect(self.bot.storage, "gfg")
        await self.cog.show_profile(self.ctx, provider="gfg")
        self.assertIn(
            "GeeksforGeeks", str(self.ctx.followup.send.call_args.kwargs["view"].to_components())
        )
        await self.cog.show_board(self.ctx, provider="gfg")
        self.assertIn(
            "GeeksforGeeks", str(self.ctx.followup.send.call_args.kwargs["view"].to_components())
        )

    async def test_every_competition_command_serializes_platform_option(self):
        for command in (
            self.cog.solved,
            self.cog.profile,
            self.cog.streak,
            self.cog.leaderboard,
            self.cog.solvers,
            self.cog.history,
            self.cog.verify,
        ):
            options = command.to_dict()["options"]
            platform = next(option for option in options if option["name"] == "platform")
            self.assertEqual(
                {choice["value"] for choice in platform["choices"]},
                {"all", "leetcode", "gfg"}
                if command.name == "leaderboard"
                else {"auto", "leetcode", "gfg"}
                if command.name == "profile"
                else {"leetcode", "gfg"},
            )
        registered = next(
            command
            for command in self.bot.pending_application_commands
            if command.name == "connect"
        )
        connect_commands = registered.to_dict()["options"]
        self.assertIn("gfg", {command["name"] for command in connect_commands})


if __name__ == "__main__":
    unittest.main()
