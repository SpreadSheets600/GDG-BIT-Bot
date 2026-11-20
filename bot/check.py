import asyncio

from bot.services.gfg import GFGService
from bot.services.problems import ProblemService, ServiceError
from bot.services.progress import RECENT_QUERY
from bot.ui.formatting import daily_content


async def check():
    service = ProblemService()
    failed = False
    try:
        for provider in ("leetcode", "gfg"):
            try:
                problem = await service.daily(provider)
                embed = daily_content(problem)
                assert len(embed) <= 4000
                print(f"OK {provider}: {problem.date} · {problem.title} · {problem.url}")
            except ServiceError as exc:
                failed = True
                print(f"FAIL {provider}: {exc}")
        try:
            profile = await service.profile("leetcode")
            print(f"OK profile/stats: {profile['username']}")
        except ServiceError as exc:
            failed = True
            print(f"FAIL profile/stats: {exc}")
        try:
            payload = await service.request(
                "POST",
                "https://leetcode.com/graphql/",
                json={"query": RECENT_QUERY, "variables": {"username": "lee215"}},
            )
            submissions = (payload.get("data") or {}).get("recentAcSubmissionList")
            if not isinstance(submissions, list):
                raise ServiceError("Accepted-submission feed has an unexpected shape")
            print(f"OK accepted-submission feed: {len(submissions)} public records")
        except ServiceError as exc:
            failed = True
            print(f"FAIL accepted-submission feed: {exc}")
        try:
            gfg = GFGService(service)
            profile = await gfg.profile("mearjuntripathi", with_bio=True)
            submissions = await gfg.submissions("mearjuntripathi")
            print(
                f"OK GFG profile/bio: {profile['username']} · {len(submissions)} timestamped solved problems"
            )
        except ServiceError as exc:
            failed = True
            print(f"FAIL GFG profile/submissions: {exc}")
    finally:
        await service.close()
    return int(failed)


def main():
    raise SystemExit(asyncio.run(check()))


if __name__ == "__main__":
    main()
