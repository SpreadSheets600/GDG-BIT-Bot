import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import aiohttp

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
DAILY_QUERY = "{\n  activeDailyCodingChallengeQuestion {\n    date link question {\n      questionFrontendId title difficulty topicTags { name } hints likes dislikes\n    }\n  }\n}"
PROFILE_QUERY = "query($username: String!) {\n  allQuestionsCount { difficulty count }\n  matchedUser(username: $username) {\n    username githubUrl linkedinUrl twitterUrl\n    profile { realName aboutMe userAvatar school countryName ranking reputation skillTags websites }\n    submitStatsGlobal {\n      acSubmissionNum { difficulty count submissions }\n      totalSubmissionNum { difficulty count submissions }\n    }\n    contributions { points }\n  }\n}"


class ServiceError(Exception):
    pass


class UserNotFound(ServiceError):
    pass


def valid_username(value: str) -> str:
    value = value.strip()
    if not re.fullmatch("[A-Za-z0-9_-]{1,64}", value):
        raise ServiceError(
            "Enter a username (1–64 letters, numbers, underscores or hyphens), not a profile URL."
        )
    return value


def http_url(value, host: str | None = None) -> str | None:
    if not isinstance(value, str) or len(value) > 512:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return None
    if host and parsed.hostname not in {host, f"www.{host}"}:
        return None
    return value


@dataclass(frozen=True)
class DailyProblem:
    provider: str
    date: date
    title: str
    url: str
    difficulty: str
    tags: tuple[str, ...]
    hints: tuple[str, ...]
    resets_at: datetime
    likes: int | None = None
    dislikes: int | None = None


def normalize_daily(provider: str, payload: dict, now: datetime) -> DailyProblem:
    try:
        if not isinstance(payload, dict):
            raise ValueError("Expected an object")
        if provider == "leetcode":
            if "data" in payload:
                daily = payload["data"]["activeDailyCodingChallengeQuestion"]
                question = daily["question"]
                title, link = (question["title"], daily["link"])
            else:
                daily = question = payload
                title, link = (daily["questionTitle"], daily["questionLink"])
            zone = UTC
            tags = tuple(tag["name"] for tag in question.get("topicTags", []))
            hints = tuple(question.get("hints") or [])
            url = http_url(urljoin("https://leetcode.com", link), "leetcode.com")
        elif provider == "gfg":
            daily = question = payload
            zone = IST
            title = daily["problem_name"]
            url = http_url(daily["problem_url"], "geeksforgeeks.org")
            tags = tuple((daily.get("tags") or {}).get("topic_tags") or [])
            hints = ()
        else:
            raise ValueError("Unknown provider")
        problem_date = date.fromisoformat(daily["date"].split(" ")[0])
        if problem_date != now.astimezone(zone).date():
            raise ValueError(
                f"Daily problem date is {problem_date}, expected {now.astimezone(zone).date()}"
            )
        if not isinstance(title, str) or not title.strip() or (not url):
            raise ValueError("Missing title or invalid problem URL")
        if not all(isinstance(item, str) for item in (*tags, *hints)):
            raise ValueError("Invalid tags or hints")
        return DailyProblem(
            provider,
            problem_date,
            title,
            url,
            str(question.get("difficulty") or "Unknown"),
            tags,
            hints,
            datetime.combine(problem_date + timedelta(days=1), time(), zone),
            question.get("likes"),
            question.get("dislikes"),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ServiceError(
            f"{provider.upper()} has not returned a valid problem for today. Please try again shortly."
        ) from exc


class ProblemService:
    def __init__(self):
        self._session = None
        self._cache = {}
        self._locks = {provider: asyncio.Lock() for provider in ("leetcode", "gfg")}

    async def close(self):
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def request(
        self, method: str, url: str, *, allow_partial=False, as_text=False, **kwargs
    ) -> dict | str:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"User-Agent": "GDG-BIT-POTD/2.0", "Referer": "https://leetcode.com/"},
            )
        for attempt in range(2):
            try:
                async with self._session.request(method, url, **kwargs) as response:
                    if (response.status == 429 or response.status >= 500) and attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    response.raise_for_status()
                    if as_text:
                        return await response.text()
                    data = await response.json()
                    if not isinstance(data, dict) or (data.get("errors") and (not allow_partial)):
                        raise ValueError("Invalid JSON object")
                    return data
            except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
                log.warning("Upstream request failed: %s %s (%s)", method, url, type(exc).__name__)
                if attempt == 0 and (not isinstance(exc, aiohttp.ClientResponseError)):
                    await asyncio.sleep(0.5)
                    continue
                raise ServiceError(
                    "The coding platform is unavailable right now. Please try again shortly."
                ) from exc
        raise ServiceError("The coding platform is unavailable right now.")

    async def daily(self, provider: str) -> DailyProblem:
        if provider not in self._locks:
            raise ServiceError("Choose leetcode or gfg.")
        async with self._locks[provider]:
            now = datetime.now(UTC)
            cached = self._cache.get(provider)
            if cached and now < cached[0]:
                return cached[1]
            if provider == "leetcode":
                try:
                    payload = await self.request(
                        "POST", "https://leetcode.com/graphql/", json={"query": DAILY_QUERY}
                    )
                    problem = normalize_daily(provider, payload, datetime.now(UTC))
                except ServiceError:
                    log.warning("LeetCode direct daily request failed; trying Alfa fallback")
                    payload = await self.request(
                        "GET", "https://alfa-leetcode-api.onrender.com/daily"
                    )
                    problem = normalize_daily(provider, payload, datetime.now(UTC))
            else:
                payload = await self.request(
                    "GET",
                    "https://practiceapi.geeksforgeeks.org/api/vr/problems-of-day/problem/today/",
                )
                problem = normalize_daily(provider, payload, datetime.now(UTC))
            self._cache[provider] = (
                min(datetime.now(UTC) + timedelta(minutes=5), problem.resets_at),
                problem,
            )
            return problem

    async def profile(self, username: str) -> dict:
        username = valid_username(username)
        result = await self.request(
            "POST",
            "https://leetcode.com/graphql/",
            allow_partial=True,
            json={"query": PROFILE_QUERY, "variables": {"username": username}},
        )
        try:
            user = result["data"]["matchedUser"]
            errors = result.get("errors") or []
            missing = any(
                "user does not exist" in str(error.get("message", "")).lower() for error in errors
            )
            if user is None and (not errors or missing):
                raise UserNotFound(
                    f"LeetCode user '{username}' was not found. Check the username and try again."
                )
            if errors:
                raise ValueError("GraphQL returned errors")
            if not isinstance(user, dict) or not isinstance(user.get("profile"), dict):
                raise ValueError("Invalid profile")
            user["allQuestionsCount"] = result["data"].get("allQuestionsCount") or []
            return user
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError(
                "LeetCode returned an incomplete profile. Please try again shortly."
            ) from exc
