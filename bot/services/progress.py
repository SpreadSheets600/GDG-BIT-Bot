import asyncio
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from bot.services.gfg import GFGService
from bot.services.platforms import platform_name, platform_zone
from bot.services.problems import ServiceError

RECENT_QUERY = "query($username: String!) {\n  recentAcSubmissionList(username: $username, limit: 100) { id titleSlug timestamp }\n}"


def matching_submission(problem, submissions, now):
    zone = platform_zone(problem.provider)
    if now >= problem.resets_at or now.astimezone(zone).date() != problem.date:
        raise ServiceError("The daily problem just changed. Check the new problem and try again.")
    parts = urlparse(problem.url).path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "problems":
        raise ServiceError("The daily problem URL is invalid. No points were awarded.")
    slug = parts[1]
    start = int((problem.resets_at - timedelta(days=1)).timestamp())
    end = min(int(problem.resets_at.timestamp()), int(now.timestamp()) + 1)
    valid = []
    for item in submissions:
        try:
            timestamp = int(item["timestamp"])
            if item["titleSlug"] == slug and start <= timestamp < end and str(item["id"]).isdigit():
                valid.append(item)
        except (KeyError, TypeError, ValueError):
            continue
    return min(valid, key=lambda item: int(item["timestamp"])) if valid else None


class ProgressService:
    def __init__(self, storage, problems):
        self.storage = storage
        self.problems = problems
        self.gfg = GFGService(problems)
        self._checks = {}
        self._lock = asyncio.Lock()

    async def platform_profile(self, username, provider, *, with_bio=False):
        platform_name(provider)
        if provider == "gfg":
            return await self.gfg.profile(username, with_bio=with_bio)
        return await self.problems.profile(username)

    async def begin_link(self, user_id, username, provider="leetcode"):
        profile = await self.platform_profile(username, provider)
        token = f"gdg-bit-{provider}-{user_id}-{secrets.token_hex(8)}"
        await asyncio.to_thread(
            self.storage.begin_proof,
            user_id,
            profile["username"],
            token,
            int(time.time()) + 900,
            provider,
        )
        return (profile["username"], token)

    async def verify_link(self, user_id, provider="leetcode"):
        proof = await asyncio.to_thread(self.storage.pending_proof, user_id, provider)
        if not proof or proof["expires_at"] <= int(time.time()):
            raise ServiceError(f"Start a new 15-minute proof with /connect {provider} first.")
        profile = await self.platform_profile(proof["username"], provider, with_bio=True)
        if profile["username"].casefold() != proof["username"].casefold():
            raise ServiceError(
                f"The platform returned a different account. Restart /connect {provider}."
            )
        if proof["token"] not in (profile["profile"].get("aboutMe") or ""):
            raise ServiceError(
                f"The code is not in your {platform_name(provider)} About Me yet. Save it there and press Verify again."
            )
        return await asyncio.to_thread(
            self.storage.confirm_proof, user_id, proof["token"], int(time.time()), provider
        )

    async def check_solve(self, guild_id, user_id, display_name, provider="leetcode"):
        if not guild_id:
            raise ServiceError(
                "Check solves inside your Discord server so they count on its leaderboard."
            )
        account = await asyncio.to_thread(self.storage.connected_account, user_id, provider)
        if not account:
            raise ServiceError(
                f"Verify your {platform_name(provider)} account with /connect {provider} first. Saved usernames alone do not verify ownership."
            )
        problem = await self.problems.daily(provider)
        await asyncio.to_thread(self.storage.touch_member, guild_id, user_id, display_name)
        previous = await asyncio.to_thread(
            self.storage.recorded_solve, guild_id, user_id, problem.date.isoformat(), provider
        )
        if previous:
            return (False, previous, await self.summary(guild_id, user_id, provider))
        async with self._lock:
            key = (guild_id, user_id, provider)
            checked_at = time.monotonic()
            if checked_at - self._checks.get(key, float("-inf")) < 15:
                raise ServiceError(
                    f"Please wait 15 seconds between solve checks; {platform_name(provider)} can take a moment to update."
                )
            self._checks = {
                key: value for key, value in self._checks.items() if checked_at - value < 15
            }
            self._checks[key] = checked_at
        if provider == "gfg":
            submissions = await self.gfg.submissions(account["username"])
        else:
            payload = await self.problems.request(
                "POST",
                "https://leetcode.com/graphql/",
                json={"query": RECENT_QUERY, "variables": {"username": account["username"]}},
            )
            try:
                submissions = payload["data"]["recentAcSubmissionList"]
                if not isinstance(submissions, list):
                    raise TypeError("Expected submissions")
            except (KeyError, TypeError) as exc:
                raise ServiceError(
                    "LeetCode's submission feed is unavailable. No points were awarded; try again shortly."
                ) from exc
        now = datetime.now(UTC)
        submission = matching_submission(problem, submissions, now)
        if not submission:
            raise ServiceError(
                f"No accepted submission for today's problem is visible in your {platform_name(provider)} activity. Submit today's problem, keep your activity public, and check again shortly. Public feeds may be delayed or limited; older solves do not count."
            )
        created = await asyncio.to_thread(
            self.storage.record_solve,
            guild_id,
            user_id,
            account["username"],
            problem,
            submission,
            int(now.timestamp()),
        )
        record = await asyncio.to_thread(
            self.storage.recorded_solve, guild_id, user_id, problem.date.isoformat(), provider
        )
        return (created, record, await self.summary(guild_id, user_id, provider))

    async def summary(self, guild_id, user_id, provider="leetcode"):
        return await asyncio.to_thread(
            self.storage.progress_summary,
            guild_id,
            user_id,
            datetime.now(platform_zone(provider)).date(),
            provider,
        )
