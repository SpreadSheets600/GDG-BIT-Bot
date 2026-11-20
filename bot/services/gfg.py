import json
import re
from datetime import datetime
from html.parser import HTMLParser

from bot.services.problems import IST, ServiceError, UserNotFound, valid_username


class ProfileScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_script = False
        self.scripts = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.in_script = True
            self.parts = []

    def handle_data(self, text):
        if self.in_script:
            self.parts.append(text)

    def handle_endtag(self, tag):
        if tag == "script" and self.in_script:
            self.scripts.append("".join(self.parts))
            self.in_script = False


def profile_bio(html, username):
    parser = ProfileScripts()
    parser.feed(html)
    chunks = []
    for script in parser.scripts:
        prefix = "self.__next_f.push("
        if not script.startswith(prefix):
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(script[len(prefix) :])
            if isinstance(value, list) and len(value) > 1 and isinstance(value[1], str):
                chunks.append(value[1])
        except (TypeError, ValueError):
            continue
    stream = "".join(chunks)
    for match in re.finditer('"mentor"\\s*:\\s*', stream):
        try:
            record, _ = json.JSONDecoder().raw_decode(stream[match.end() :])
        except ValueError:
            continue
        if (
            isinstance(record, dict)
            and str(record.get("handle", "")).casefold() == username.casefold()
        ):
            bio = record.get("bio")
            if bio is None:
                return ""
            if isinstance(bio, str):
                return bio
    raise ServiceError(
        "GFG's public profile bio could not be read. No account was verified; try again shortly."
    )


def accepted_submissions(payload):
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "success"
        or (not isinstance(payload.get("result"), dict))
    ):
        raise ServiceError(
            "GFG's solved-problem feed is unavailable or private. Please try again shortly."
        )
    submissions = []
    for group in payload["result"].values():
        if not isinstance(group, dict):
            raise ServiceError(
                "GFG returned an unexpected solved-problem list. No points were awarded."
            )
        for problem_id, item in group.items():
            if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
                continue
            try:
                accepted_at = datetime.fromisoformat(item["user_subtime"])
                if accepted_at.tzinfo is None:
                    accepted_at = accepted_at.replace(tzinfo=IST)
                submissions.append(
                    {
                        "id": str(problem_id),
                        "titleSlug": item["slug"].strip("/"),
                        "timestamp": str(int(accepted_at.timestamp())),
                    }
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
    return submissions


class GFGService:
    def __init__(self, problems):
        self.problems = problems

    async def profile(self, username, *, with_bio=False):
        username = valid_username(username)
        try:
            payload = await self.problems.request(
                "GET",
                "https://authapi.geeksforgeeks.org/api-get/user-profile-info/",
                params={"handle": username, "article_count": "false", "redirect": "true"},
            )
        except ServiceError as exc:
            if getattr(exc.__cause__, "status", None) in {400, 404}:
                raise UserNotFound(
                    "That GFG profile was not found. Check the username and keep your profile public."
                ) from exc
            raise
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise ServiceError("GFG returned an incomplete profile. Please try again shortly.")
        result = {
            "username": username,
            "profile": {
                "realName": data["name"],
                "userAvatar": data.get("profile_image_url"),
                "totalSolved": data.get("total_problems_solved"),
                "score": data.get("score"),
                "aboutMe": "",
            },
        }
        if with_bio:
            html = await self.problems.request(
                "GET", f"https://www.geeksforgeeks.org/profile/{username}", as_text=True
            )
            result["profile"]["aboutMe"] = profile_bio(html, username)
        return result

    async def submissions(self, username):
        payload = await self.problems.request(
            "POST",
            "https://practiceapi.geeksforgeeks.org/api/v1/user/problems/submissions/",
            json={"handle": valid_username(username), "requestType": "", "year": "", "month": ""},
        )
        return accepted_submissions(payload)
