from html import unescape
from html.parser import HTMLParser

import discord

COLOR = 4359668


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "li"}:
            self.parts.append("\n")


def clean(value, limit=1024):
    parser = PlainHTML()
    parser.feed(str("Not provided" if value is None or value == "" else value))
    text = unescape("".join(parser.parts)).strip() or "Not provided"
    return discord.utils.escape_markdown(text)[:limit]


def daily_content(problem):
    from bot.ui.cards import CardContent

    embed = CardContent(
        title=clean(problem.title, 256),
        url=problem.url,
        color=COLOR,
        description=f"Solve this problem on your connected account, then click **Mark as solved** or use `/solved platform:{problem.provider}`. Verified check-ins earn XP and extend your streak in this server.",
    )
    embed.set_author(
        name="LeetCode · Problem of the Day"
        if problem.provider == "leetcode"
        else "GeeksforGeeks · Problem of the Day"
    )
    embed.add_field(name="Date", value=problem.date.isoformat())
    embed.add_field(name="Difficulty", value=clean(problem.difficulty))
    embed.add_field(name="Next problem", value=f"<t:{int(problem.resets_at.timestamp())}:R>")
    embed.add_field(
        name="Topics",
        value=clean(", ".join(problem.tags) or "No topics provided", 600),
        inline=False,
    )
    if problem.likes is not None:
        embed.set_footer(text=f"Likes: {problem.likes} | Dislikes: {problem.dislikes or 0}")
    return embed
