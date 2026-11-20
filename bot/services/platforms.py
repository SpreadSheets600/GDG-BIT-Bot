from datetime import UTC
from urllib.parse import quote

from bot.services.problems import IST, ServiceError


def platform_name(provider):
    try:
        return {"leetcode": "LeetCode", "gfg": "GeeksforGeeks"}[provider]
    except KeyError as exc:
        raise ServiceError("Choose leetcode or gfg.") from exc


def platform_zone(provider):
    platform_name(provider)
    return UTC if provider == "leetcode" else IST


def profile_url(provider, username):
    platform_name(provider)
    base = (
        "https://leetcode.com/u/"
        if provider == "leetcode"
        else "https://www.geeksforgeeks.org/profile/"
    )
    return base + quote(username, safe="") + "/"
