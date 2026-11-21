import asyncio
import copy
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web

from bot.cogs.platforms import clean, daily_content
from bot.main import create_bot
from bot.services.problems import IST, ProblemService, ServiceError, UserNotFound, normalize_daily
from bot.services.storage import Storage


def leetcode_payload(now=None):
    now = now or datetime.now(UTC)
    return {
        "data": {
            "activeDailyCodingChallengeQuestion": {
                "date": now.astimezone(UTC).date().isoformat(),
                "link": "/problems/two-sum/",
                "question": {
                    "title": "Two Sum",
                    "difficulty": "Easy",
                    "topicTags": [{"name": "Array"}],
                    "hints": ["Use a <code>hash map</code>."],
                    "likes": 3,
                    "dislikes": 0,
                },
            }
        }
    }


def gfg_payload(now=None):
    now = now or datetime.now(UTC)
    return {
        "date": now.astimezone(IST).date().isoformat() + " 00:00:00",
        "problem_name": "Example",
        "problem_url": "https://www.geeksforgeeks.org/problems/example/1",
        "difficulty": "Easy",
        "tags": {"topic_tags": ["Arrays"]},
    }


class NormalizationTests(unittest.TestCase):
    def test_platform_dates_and_resets_differ_at_utc_boundary(self):
        now = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)
        lc = normalize_daily("leetcode", leetcode_payload(now), now)
        gfg = normalize_daily("gfg", gfg_payload(now), now)
        self.assertEqual(lc.date.isoformat(), "2026-11-20")
        self.assertEqual(gfg.date.isoformat(), "2026-11-21")
        self.assertEqual(lc.resets_at.hour, 0)
        self.assertEqual(gfg.resets_at.utcoffset(), timedelta(hours=5, minutes=30))
        self.assertGreater(lc.resets_at, now)
        self.assertGreater(gfg.resets_at, now)

    def test_yesterday_is_rejected(self):
        now = datetime.now(UTC)
        for provider, payload in (
            ("leetcode", leetcode_payload(now - timedelta(days=1))),
            ("gfg", gfg_payload(now - timedelta(days=1))),
        ):
            with self.subTest(provider=provider), self.assertRaises(ServiceError):
                normalize_daily(provider, payload, now)

    def test_bad_payloads_are_rejected(self):
        for payload in (
            {},
            {"data": None},
            [],
            {"data": {"activeDailyCodingChallengeQuestion": None}},
        ):
            with self.subTest(payload=payload), self.assertRaises(ServiceError):
                normalize_daily("leetcode", payload, datetime.now(UTC))

    def test_problem_links_must_belong_to_provider(self):
        payload = leetcode_payload()
        payload["data"]["activeDailyCodingChallengeQuestion"]["link"] = (
            "https://example.com/phishing"
        )
        with self.assertRaises(ServiceError):
            normalize_daily("leetcode", payload, datetime.now(UTC))

    def test_alfa_fallback_format(self):
        problem = normalize_daily(
            "leetcode",
            {
                "questionTitle": "Two Sum",
                "questionLink": "https://leetcode.com/problems/two-sum/",
                "date": datetime.now(UTC).date().isoformat(),
                "difficulty": "Easy",
            },
            datetime.now(UTC),
        )
        self.assertEqual(problem.title, "Two Sum")
        self.assertEqual(problem.hints, ())

    def test_embed_and_html_limits(self):
        payload = leetcode_payload()
        question = payload["data"]["activeDailyCodingChallengeQuestion"]["question"]
        question["title"] = "x" * 600
        question["topicTags"] = [{"name": "tag" * 600}]
        embed = daily_content(normalize_daily("leetcode", payload, datetime.now(UTC)))
        self.assertLessEqual(len(embed.title), 256)
        self.assertLessEqual(len(embed), 6000)
        self.assertEqual(clean("Use a <code>map</code> &amp; sort."), "Use a map & sort.")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "bot.db"
        self.db = Storage(self.path)

    def test_registration_is_idempotent(self):
        self.assertTrue(self.db.register(1))
        self.db.set_profile(1, "leetcode", "alice")
        self.assertFalse(self.db.register(1))
        self.assertEqual(self.db.get_profile(1)["leetcode_id"], "alice")

    def test_handles_saved_without_prior_registration_and_survive_restart(self):
        for platform in ("leetcode", "gfg", "cf"):
            self.db.set_profile(1, platform, platform + "_handle")
        restarted = Storage(self.path)
        self.assertEqual(restarted.get_profile(1)["gfg_id"], "gfg_handle")
        self.assertEqual(restarted.get_profile(1)["cf_id"], "cf_handle")
        self.assertEqual(restarted.get_profile(2), {})

    def test_daily_delivery_key_includes_date_provider_and_channel(self):
        self.db.mark_posted(1, "leetcode", "2026-11-20", 123)
        self.assertTrue(Storage(self.path).was_posted(1, "leetcode", "2026-11-20"))
        self.assertFalse(self.db.was_posted(1, "gfg", "2026-11-20"))
        self.assertFalse(self.db.was_posted(2, "leetcode", "2026-11-20"))
        self.assertFalse(self.db.was_posted(1, "leetcode", "2026-11-21"))

    def test_original_database_schema_is_preserved(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO Users VALUES (55)")
            db.execute("INSERT INTO Profiles VALUES (55, 'old_handle', NULL, NULL)")
        self.assertEqual(Storage(self.path).get_profile(55)["leetcode_id"], "old_handle")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = ProblemService()
        self.addAsyncCleanup(self.service.close)

    async def test_daily_cache_coalesces_concurrent_requests(self):
        self.service.request = AsyncMock(return_value=leetcode_payload())
        a, b = await asyncio.gather(self.service.daily("leetcode"), self.service.daily("leetcode"))
        self.assertEqual(a, b)
        self.service.request.assert_awaited_once()

    async def test_cache_expires_and_fetches_again(self):
        self.service.request = AsyncMock(return_value=leetcode_payload())
        problem = await self.service.daily("leetcode")
        self.service._cache["leetcode"] = (datetime.now(UTC) - timedelta(seconds=1), problem)
        await self.service.daily("leetcode")
        self.assertEqual(self.service.request.await_count, 2)

    async def test_cache_expires_at_midnight_even_within_five_minutes(self):
        before = datetime(2026, 11, 20, 23, 59, 30, tzinfo=UTC)
        after = before + timedelta(seconds=31)
        self.service.request = AsyncMock(return_value=leetcode_payload(before))
        with patch("bot.services.problems.datetime", wraps=datetime) as clock:
            clock.now.return_value = before
            await self.service.daily("leetcode")
            self.assertEqual(self.service._cache["leetcode"][0], datetime(2026, 11, 21, tzinfo=UTC))
            clock.now.return_value = after
            self.service.request.return_value = leetcode_payload(after)
            problem = await self.service.daily("leetcode")
        self.assertEqual(problem.date.isoformat(), "2026-11-21")
        self.assertEqual(self.service.request.await_count, 2)

    async def test_fallback_on_upstream_failure(self):
        fallback = {
            "questionTitle": "Fallback",
            "questionLink": "https://leetcode.com/problems/example/",
            "date": datetime.now(UTC).date().isoformat(),
        }
        self.service.request = AsyncMock(side_effect=[ServiceError("Down"), fallback])
        result = await self.service.daily("leetcode")
        self.assertEqual(result.title, "Fallback")
        self.assertIn("alfa-leetcode", self.service.request.call_args.args[1])

    async def test_stale_primary_tries_fallback_but_stale_fallback_never_cached(self):
        yesterday = leetcode_payload(datetime.now(UTC) - timedelta(days=1))
        self.service.request = AsyncMock(return_value=yesterday)
        with self.assertRaises(ServiceError):
            await self.service.daily("leetcode")
        self.assertEqual(self.service.request.await_count, 2)
        self.assertNotIn("leetcode", self.service._cache)

    async def test_missing_user_with_graphql_error(self):
        self.service.request = AsyncMock(
            return_value={
                "errors": [{"message": "That user does not exist."}],
                "data": {"matchedUser": None},
            }
        )
        with self.assertRaises(UserNotFound):
            await self.service.profile("missing")

    async def test_other_graphql_errors_are_not_reported_as_missing_users(self):
        self.service.request = AsyncMock(
            return_value={"errors": [{"message": "Internal error"}], "data": {"matchedUser": None}}
        )
        with self.assertRaises(ServiceError) as error:
            await self.service.profile("missing")
        self.assertNotIsInstance(error.exception, UserNotFound)

    async def test_invalid_username_does_not_call_upstream(self):
        self.service.request = AsyncMock()
        with self.assertRaises(ServiceError):
            await self.service.profile("https://leetcode.com/u/alice")
        self.service.request.assert_not_awaited()

    async def start_server(self, handler):
        app = web.Application()
        app.router.add_route("*", "/", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/"

    async def test_http_retry_after_transient_failure(self):
        calls = []

        async def handler(request):
            calls.append(1)
            return web.Response(status=503) if len(calls) == 1 else web.json_response({"ok": True})

        url = await self.start_server(handler)
        self.assertEqual(await self.service.request("GET", url), {"ok": True})
        self.assertEqual(len(calls), 2)

    async def test_http_permanent_failure_returns_service_error(self):

        async def handler(request):
            return web.Response(status=403)

        url = await self.start_server(handler)
        with self.assertRaises(ServiceError):
            await self.service.request("GET", url)

    async def test_non_json_and_non_object_responses_are_handled(self):
        for response in (web.Response(text="bad gateway"), web.json_response([])):

            async def handler(request, result=response):
                return copy.copy(result)

            url = await self.start_server(handler)
            with self.assertRaises(ServiceError):
                await self.service.request("GET", url)

    async def test_timeout_is_bounded_and_reported(self):

        async def handler(request):
            await asyncio.sleep(0.05)
            return web.json_response({"ok": True})

        url = await self.start_server(handler)
        self.service._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=0.01))
        with self.assertRaises(ServiceError):
            await self.service.request("GET", url)

    async def test_rate_limit_is_retried_once(self):
        calls = []

        async def handler(request):
            calls.append(1)
            return web.Response(status=429)

        url = await self.start_server(handler)
        with self.assertRaises(ServiceError):
            await self.service.request("GET", url)
        self.assertEqual(len(calls), 2)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bot = create_bot(database_path=Path(self.tmp.name) / "bot.db", channel_id=123)
        self.addAsyncCleanup(self.bot.close)
        self.cog = self.bot.get_cog("PlatformsCog")
        self.ctx = SimpleNamespace(
            defer=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
            respond=AsyncMock(),
            author=SimpleNamespace(id=42),
            command="test",
            interaction=SimpleNamespace(response=SimpleNamespace(is_done=lambda: True)),
        )

    async def test_core_commands_load_without_discord_login(self):
        names = {command.name for command in self.bot.pending_application_commands}
        self.assertEqual(
            names,
            {
                "leetcode",
                "gfg",
                "register",
                "register_leetcode_id",
                "register_gfg_id",
                "register_cf_id",
                "ping",
                "info",
                "help",
                "meme",
                "memes",
                "connect",
                "profile",
                "solved",
                "streak",
                "leaderboard",
                "solvers",
                "history",
                "details",
                "potd",
                "rules",
                "socials",
                "tags",
                "counters",
            },
        )
        self.assertTrue(self.bot.intents.message_content)
        self.assertTrue(self.bot.intents.members)
        for command in self.bot.pending_application_commands:
            self.assertIn("name", command.to_dict())

    async def test_view_defers_and_responds_with_problem(self):
        problem = normalize_daily("leetcode", leetcode_payload(), datetime.now(UTC))
        self.bot.problems.daily = AsyncMock(return_value=problem)
        await self.cog.view.callback(self.cog, self.ctx)
        self.ctx.defer.assert_awaited_once()
        self.assertEqual(self.ctx.followup.send.call_args.kwargs["view"].content.url, problem.url)

    async def test_hints_are_private_and_html_free(self):
        self.bot.problems.daily = AsyncMock(
            return_value=normalize_daily("leetcode", leetcode_payload(), datetime.now(UTC))
        )
        await self.cog.hint.callback(self.cog, self.ctx)
        kwargs = self.ctx.followup.send.call_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertNotIn("<code>", kwargs["view"].content.description)

    async def test_saved_handle_used_and_missing_handle_explained(self):
        with self.assertRaises(ServiceError):
            await self.cog.resolve_username(self.ctx, None)
        self.bot.storage.set_profile(42, "leetcode", "alice")
        self.assertEqual(await self.cog.resolve_username(self.ctx, None), "alice")
        self.assertEqual(await self.cog.resolve_username(self.ctx, "bob"), "bob")

    async def test_profile_optional_fields_and_unsafe_links(self):
        self.bot.problems.profile = AsyncMock(
            return_value={"username": "alice", "profile": {"websites": ["javascript:alert(1)"]}}
        )
        await self.cog.profile.callback(self.cog, self.ctx, "alice")
        kwargs = self.ctx.followup.send.call_args.kwargs
        self.assertNotIn("javascript:", str(kwargs["view"].to_components()))
        self.assertTrue(kwargs["view"].is_components_v2())

    async def test_stats_with_empty_optional_fields(self):
        self.bot.problems.profile = AsyncMock(return_value={"username": "alice", "profile": {}})
        await self.cog.stats.callback(self.cog, self.ctx, "alice")
        self.ctx.followup.send.assert_awaited_once()

    async def test_registration_handle_is_verified_before_saved(self):
        users = self.bot.get_cog("AccountsCog")
        self.bot.problems.profile = AsyncMock(side_effect=UserNotFound("Missing"))
        with self.assertRaises(UserNotFound):
            await users.save_handle(self.ctx, "leetcode", "missing")
        self.assertEqual(self.bot.storage.get_profile(42), {})
        self.bot.problems.profile = AsyncMock(return_value={"username": "Alice"})
        await users.save_handle(self.ctx, "leetcode", "alice")
        self.assertEqual(self.bot.storage.get_profile(42)["leetcode_id"], "Alice")

    async def test_error_handler_responds_after_deferral(self):
        await self.bot.on_application_command_error(self.ctx, ServiceError("Try later"))
        self.ctx.followup.send.assert_awaited_once()
        self.assertIn(
            "Try later", str(self.ctx.followup.send.call_args.kwargs["view"].to_components())
        )
        self.assertTrue(self.ctx.followup.send.call_args.kwargs["ephemeral"])
        self.ctx.respond.assert_not_awaited()

    async def test_error_handler_responds_before_deferral(self):
        self.ctx.interaction.response.is_done = lambda: False
        await self.bot.on_application_command_error(self.ctx, ServiceError("Try later"))
        self.ctx.respond.assert_awaited_once()
        self.assertIn("Try later", str(self.ctx.respond.call_args.kwargs["view"].to_components()))
        self.assertTrue(self.ctx.respond.call_args.kwargs["ephemeral"])

    async def test_automatic_delivery_survives_restart_without_reposting(self):

        async def daily(provider):
            return normalize_daily(
                provider,
                leetcode_payload() if provider == "leetcode" else gfg_payload(),
                datetime.now(UTC),
            )

        self.bot.problems.daily = AsyncMock(side_effect=daily)
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=100)))
        with patch.object(self.bot, "get_channel", return_value=channel):
            await self.cog.deliver_daily()
            self.assertEqual(channel.send.await_count, 2)
            self.bot.storage = Storage(self.bot.storage.path)
            await self.cog.deliver_daily()
            self.assertEqual(channel.send.await_count, 2)
            self.assertEqual(self.bot.problems.daily.await_count, 2)

    async def test_delivery_failure_on_one_provider_does_not_stop_other(self):
        self.bot.problems.daily = AsyncMock(
            side_effect=[
                ServiceError("Down"),
                normalize_daily("gfg", gfg_payload(), datetime.now(UTC)),
            ]
        )
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=100)))
        with (
            patch.object(self.bot, "get_channel", return_value=channel),
            self.assertLogs("bot.cogs.platforms", level="ERROR"),
        ):
            await self.cog.deliver_daily()
        channel.send.assert_awaited_once()
        self.assertTrue(
            self.bot.storage.was_posted(123, "gfg", datetime.now(IST).date().isoformat())
        )

    async def test_failed_discord_send_does_not_mark_as_posted(self):
        self.bot.problems.daily = AsyncMock(
            return_value=normalize_daily("leetcode", leetcode_payload(), datetime.now(UTC))
        )
        channel = SimpleNamespace(send=AsyncMock(side_effect=OSError("Disconnected")))
        with (
            patch.object(self.bot, "get_channel", return_value=channel),
            self.assertLogs("bot.cogs.platforms", level="ERROR"),
        ):
            await self.cog.deliver_daily()
        self.assertFalse(
            self.bot.storage.was_posted(123, "leetcode", datetime.now(UTC).date().isoformat())
        )


if __name__ == "__main__":
    unittest.main()
