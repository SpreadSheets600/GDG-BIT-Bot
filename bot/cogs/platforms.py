import asyncio
import logging
import sqlite3
from datetime import UTC, datetime
from urllib.parse import quote

import discord
from discord import SlashCommandGroup
from discord.ext import commands, tasks

from bot.services.platforms import profile_url
from bot.services.problems import IST, ServiceError, http_url, valid_username
from bot.ui.cards import CardContent, MessageCard
from bot.ui.formatting import clean, daily_content
from bot.ui.layouts import action_button

log = logging.getLogger(__name__)
COLOR = 4359668


class PlatformsCog(commands.Cog):
    leetcode = SlashCommandGroup(
        name="leetcode", description="LeetCode daily problems and profiles"
    )
    potd = leetcode.create_subgroup(name="potd", description="Problem of the Day")
    gfg = SlashCommandGroup(name="gfg", description="GeeksforGeeks daily problems")
    gfg_potd = gfg.create_subgroup(name="potd", description="Problem of the Day")

    def __init__(self, bot):
        self.bot = bot

    @potd.command(name="view", description="Get today's LeetCode problem")
    async def view(self, ctx: discord.ApplicationContext):
        await self.show_daily(ctx, "leetcode")

    @gfg_potd.command(name="view", description="Get today's GeeksforGeeks problem")
    async def gfg_view(self, ctx: discord.ApplicationContext):
        await self.show_daily(ctx, "gfg")

    @gfg.command(
        name="profile", description="Show GFG coding stats; defaults to your saved username"
    )
    async def gfg_profile(self, ctx: discord.ApplicationContext, username: str | None = None):
        await ctx.defer()
        if not username:
            saved = await asyncio.to_thread(self.bot.storage.get_profile, ctx.author.id)
            username = saved.get("gfg_id")
        if not username:
            raise ServiceError("Provide a GFG username or connect yours with /connect gfg first.")
        data = await self.bot.progress.gfg.profile(username)
        profile = data["profile"]
        embed = CardContent(
            title=clean(profile.get("realName") or username, 256),
            url=profile_url("gfg", data["username"]),
            color=COLOR,
        )
        embed.add_field(name="Problems solved", value=clean(profile.get("totalSolved")))
        embed.add_field(name="Coding score", value=clean(profile.get("score")))
        avatar = http_url(profile.get("userAvatar"))
        if avatar:
            embed.set_thumbnail(url=avatar)
        embed.set_footer(
            text="Public GFG stats · /profile platform:gfg shows this server's XP and streak"
        )
        await ctx.followup.send(view=MessageCard(self.bot, embed))

    async def show_daily(self, ctx, provider):
        await ctx.defer()
        problem = await self.bot.problems.daily(provider)
        await ctx.followup.send(view=self.daily_card(problem))

    def daily_card(self, problem):
        provider = problem.provider
        rows = [
            discord.ui.ActionRow(discord.ui.Button(label="Solve problem", url=problem.url)),
            discord.ui.ActionRow(
                action_button(
                    self.bot,
                    provider,
                    "check",
                    "Mark as solved",
                    style=discord.ButtonStyle.success,
                ),
                action_button(self.bot, provider, "board", "Leaderboard"),
                action_button(self.bot, provider, "profile", "My profile"),
                action_button(self.bot, provider, "help", "Help"),
            ),
        ]
        return MessageCard(self.bot, daily_content(problem), actions=rows)

    @potd.command(name="hint", description="Privately show hints for today's LeetCode problem")
    async def hint(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        problem = await self.bot.problems.daily("leetcode")
        description = "\n\n".join(
            (f"{i}. {clean(hint, 1000)}" for i, hint in enumerate(problem.hints, 1))
        )
        embed = CardContent(
            title="Hints for today's problem",
            url=problem.url,
            description=description[:3800] or "No hints are available for this problem.",
            color=COLOR,
        )
        await ctx.followup.send(view=MessageCard(self.bot, embed), ephemeral=True)

    async def resolve_username(self, ctx, username):
        if username:
            return valid_username(username)
        profile = await asyncio.to_thread(self.bot.storage.get_profile, ctx.author.id)
        saved = profile.get("leetcode_id")
        if not saved:
            raise ServiceError(
                "Provide a username, or save yours with /register_leetcode_id first."
            )
        return saved

    @leetcode.command(
        name="profile", description="Show a LeetCode profile; defaults to your saved username"
    )
    async def profile(self, ctx: discord.ApplicationContext, username: str | None = None):
        await ctx.defer()
        username = await self.resolve_username(ctx, username)
        data = await self.bot.problems.profile(username)
        profile = data["profile"]
        url = f"https://leetcode.com/u/{quote(data['username'], safe='')}/"
        embed = CardContent(
            title=clean(profile.get("realName") or data["username"], 256),
            url=url,
            description=clean(profile.get("aboutMe"), 1200),
            color=COLOR,
        )
        avatar = http_url(profile.get("userAvatar"))
        if avatar:
            embed.set_thumbnail(url=avatar)
        for label, key in (
            ("School", "school"),
            ("Country", "countryName"),
            ("Ranking", "ranking"),
            ("Reputation", "reputation"),
        ):
            embed.add_field(name=label, value=clean(profile.get(key), 400))
        embed.add_field(
            name="Skills", value=clean(", ".join(profile.get("skillTags") or []), 400), inline=False
        )
        buttons = [discord.ui.Button(label="LeetCode", url=url)]
        websites = profile.get("websites") or []
        for label, value in (
            ("GitHub", data.get("githubUrl")),
            ("LinkedIn", data.get("linkedinUrl")),
            ("Twitter", data.get("twitterUrl")),
            ("Website", websites[0] if websites else None),
        ):
            link = http_url(value)
            if link:
                buttons.append(discord.ui.Button(label=label, url=link))
        await ctx.followup.send(
            view=MessageCard(self.bot, embed, actions=[discord.ui.ActionRow(*buttons)])
        )

    @leetcode.command(
        name="stats", description="Show LeetCode solving stats; defaults to your saved username"
    )
    async def stats(self, ctx: discord.ApplicationContext, username: str | None = None):
        await ctx.defer()
        username = await self.resolve_username(ctx, username)
        data = await self.bot.problems.profile(username)
        totals = {item["difficulty"]: item["count"] for item in data.get("allQuestionsCount", [])}
        stats = data.get("submitStatsGlobal") or {}
        solved = {item["difficulty"]: item["count"] for item in stats.get("acSubmissionNum", [])}
        submissions = {
            item["difficulty"]: item["submissions"] for item in stats.get("totalSubmissionNum", [])
        }
        embed = CardContent(title=clean(f"LeetCode stats for {data['username']}", 256), color=COLOR)
        for difficulty in ("All", "Easy", "Medium", "Hard"):
            embed.add_field(
                name="Total solved" if difficulty == "All" else difficulty,
                value=f"{solved.get(difficulty, 0)} / {totals.get(difficulty, '?')} solved\n{submissions.get(difficulty, 0)} submissions",
                inline=difficulty != "All",
            )
        embed.add_field(name="Ranking", value=clean(data["profile"].get("ranking")))
        embed.add_field(
            name="Contribution points", value=clean((data.get("contributions") or {}).get("points"))
        )
        await ctx.followup.send(view=MessageCard(self.bot, embed))

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.deliver_daily.is_running():
            self.deliver_daily.start()

    def cog_unload(self):
        self.deliver_daily.cancel()

    @tasks.loop(seconds=30)
    async def deliver_daily(self):
        try:
            settings = await asyncio.to_thread(self.bot.storage.configured_potd_channels)
        except sqlite3.Error:
            log.exception("Could not read POTD settings; retrying on the next check")
            return
        destinations = [item["channel_id"] for item in settings if item["enabled"]]
        legacy = self.bot.potd_channel_id
        if legacy and legacy not in {item["channel_id"] for item in settings}:
            try:
                channel = self.bot.get_channel(legacy) or await self.bot.fetch_channel(legacy)
                guild = getattr(channel, "guild", None)
                if not guild or guild.id not in {item["guild_id"] for item in settings}:
                    destinations.append(legacy)
            except discord.HTTPException:
                log.exception("Could not resolve the legacy POTD channel")
        for channel_id in dict.fromkeys(destinations):
            await self.deliver_to_channel(channel_id)

    async def deliver_to_channel(self, channel_id):
        for provider in ("leetcode", "gfg"):
            try:
                zone = UTC if provider == "leetcode" else IST
                day = datetime.now(zone).date().isoformat()
                if await asyncio.to_thread(self.bot.storage.was_posted, channel_id, provider, day):
                    continue
                problem = await self.bot.problems.daily(provider)
                day = problem.date.isoformat()
                if await asyncio.to_thread(self.bot.storage.was_posted, channel_id, provider, day):
                    continue
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(
                    channel_id
                )
                message = await channel.send(view=self.daily_card(problem))
                await asyncio.to_thread(
                    self.bot.storage.mark_posted, channel_id, provider, day, message.id
                )
                log.info("Posted %s daily problem for %s", provider, day)
            except Exception:
                log.exception(
                    "Could not deliver %s POTD; will retry on the next 30-second check", provider
                )

    @deliver_daily.before_loop
    async def before_delivery(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(PlatformsCog(bot))
