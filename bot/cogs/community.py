import asyncio
from datetime import datetime

import discord
from discord.ext import commands

from bot.services.platforms import platform_name, platform_zone, profile_url
from bot.services.problems import IST, ServiceError
from bot.ui.cards import CardContent, MessageCard, notice, verification_row
from bot.ui.challenge import ChallengeView, LeaderboardView, VerifyView
from bot.ui.formatting import COLOR, clean
from bot.ui.layouts import DetailsLayout, ProfileLayout


def guild_id(ctx):
    value = getattr(ctx, "guild_id", None) or getattr(getattr(ctx, "guild", None), "id", None)
    if not value:
        raise ServiceError("Use this feature inside your Discord server.")
    return value


def badges(summary):
    earned = []
    if summary["solves"]:
        earned.append("First solve")
    for days in (3, 7, 30):
        if summary["best_streak"] >= days:
            earned.append(f"{days}-day streak")
    if summary["solves"] >= 100:
        earned.append("100 daily solves")
    return (
        " · ".join(earned) or "No badges yet. Complete your first verified daily solve to earn one."
    )


class CommunityCog(commands.Cog):
    connect = discord.SlashCommandGroup("connect", "Verify ownership of your coding account")

    def __init__(self, bot):
        self.bot = bot
        self.views = []

    @commands.Cog.listener()
    async def on_connect(self):
        if self.views:
            return
        self.views = [
            view
            for provider in ("leetcode", "gfg")
            for view in (ChallengeView(self.bot, provider), VerifyView(self.bot, provider))
        ]
        for view in self.views:
            self.bot.add_view(view)

    def cog_unload(self):
        for view in self.views:
            view.stop()

    @connect.command(name="leetcode", description="Connect LeetCode with a profile ownership proof")
    async def connect_leetcode(self, ctx: discord.ApplicationContext, username: str):
        await self.start_connection(ctx, username, "leetcode")

    @connect.command(name="gfg", description="Connect GeeksforGeeks with a profile ownership proof")
    async def connect_gfg(self, ctx: discord.ApplicationContext, username: str):
        await self.start_connection(ctx, username, "gfg")

    async def start_connection(self, ctx, username, provider):
        await ctx.defer(ephemeral=True)
        username, token = await self.bot.progress.begin_link(ctx.author.id, username, provider)
        embed = CardContent(
            title=f"Connect your {platform_name(provider)} account",
            color=COLOR,
            description=f"1. Open [your profile]({profile_url(provider, username)}) and edit **About Me**.\n2. Add this exact code alongside your bio:\n```\n{token}\n```\n3. Save it, then click **Verify my account** below (or use `/connect verify platform:{provider}`).\n\nThe code expires in 15 minutes. You can remove it after verification.",
        )
        await ctx.followup.send(
            view=MessageCard(self.bot, embed, actions=[verification_row(self.bot, provider)]),
            ephemeral=True,
        )

    @connect.command(
        name="verify", description="Verify the code you added to your platform's About Me"
    )
    async def verify(
        self,
        ctx: discord.ApplicationContext,
        platform: discord.Option(str, choices=["leetcode", "gfg"]) = "leetcode",
    ):
        await self.finish_connection(ctx, platform)

    async def finish_connection(self, ctx, provider="leetcode"):
        await ctx.defer(ephemeral=True)
        username = await self.bot.progress.verify_link(ctx.author.id, provider)
        if getattr(ctx, "guild", None):
            await asyncio.to_thread(
                self.bot.storage.touch_member, ctx.guild.id, ctx.author.id, ctx.author.display_name
            )
        await ctx.followup.send(
            view=notice(
                self.bot,
                "Account connected",
                f"Connected **{clean(username)}** on {platform_name(provider)}. You can remove the bio code.\n\nSolve today's problem, then use `/solved platform:{provider}` to verify it. `/profile` shows your connected accounts and server progress.",
            ),
            ephemeral=True,
        )

    @discord.slash_command(
        name="solved",
        description="Verify today's accepted daily submission and earn XP",
        contexts={discord.InteractionContextType.guild},
    )
    async def solved(
        self,
        ctx: discord.ApplicationContext,
        platform: discord.Option(str, choices=["leetcode", "gfg"]) = "leetcode",
    ):
        await self.check_today(ctx, platform)

    async def check_today(self, ctx, provider="leetcode"):
        await ctx.defer(ephemeral=True)
        created, record, summary = await self.bot.progress.check_solve(
            guild_id(ctx), ctx.author.id, ctx.author.display_name, provider
        )
        embed = CardContent(
            title="Daily solve verified!" if created else "Already counted today",
            description=f"**{clean(record['title'], 256)}** · {platform_name(provider)}\n{('Earned' if created else 'Previously earned')} **{record['points']} XP**. One award per platform's daily problem in this server.",
            color=COLOR,
        )
        embed.set_author(name=clean(getattr(ctx.guild, "name", "This server"), 100))
        embed.add_field(name="Streak", value=f"{summary['current_streak']} days")
        embed.add_field(name="Total", value=f"{summary['points']} XP · Level {summary['level']}")
        embed.add_field(name="Badges", value=badges(summary), inline=False)
        standings = await asyncio.to_thread(
            self.bot.storage.server_leaderboard, guild_id(ctx), datetime.now(IST).date()
        )
        standing = next((row for row in standings if row["user_id"] == ctx.author.id), None)
        if standing:
            embed.add_field(
                name="Server leaderboard",
                value=f"#{standing['rank']} · {standing['points']} total XP across LeetCode + GFG",
                inline=False,
            )
        await ctx.followup.send(view=MessageCard(self.bot, embed), ephemeral=True)

    @discord.slash_command(
        name="profile",
        description="Show a connected profile and this server's platform progress",
        contexts={discord.InteractionContextType.guild},
    )
    async def profile(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Member | None = None,
        platform: discord.Option(str, choices=["auto", "leetcode", "gfg"]) = "auto",
    ):
        await self.show_profile(ctx, member, provider=platform)

    async def show_profile(self, ctx, member=None, ephemeral=False, provider="auto"):
        await ctx.defer(ephemeral=ephemeral)
        server = guild_id(ctx)
        member = member or ctx.author
        linked = await asyncio.gather(
            *(
                asyncio.to_thread(self.bot.storage.connected_account, member.id, platform)
                for platform in ("leetcode", "gfg")
            )
        )
        accounts = {
            platform: account
            for platform, account in zip(("leetcode", "gfg"), linked, strict=True)
            if account
        }
        if provider == "auto":
            provider = next(iter(accounts), "leetcode")
        account = accounts.get(provider)
        if not account:
            message = (
                f"Connect your {platform_name(provider)} account with /connect {provider} first."
                if member.id == ctx.author.id
                else f"That member hasn't connected a verified {platform_name(provider)} account yet."
            )
            await ctx.followup.send(
                view=notice(self.bot, "Account connection", message), ephemeral=True
            )
            return
        await asyncio.to_thread(
            self.bot.storage.touch_member, server, member.id, member.display_name
        )
        summary = await self.bot.progress.summary(server, member.id, provider)
        overall = await asyncio.to_thread(
            self.bot.storage.server_leaderboard, server, datetime.now(IST).date()
        )
        standing = next((row for row in overall if row["user_id"] == member.id), None)
        view = ProfileLayout(
            self.bot,
            provider=provider,
            member=member,
            account=account,
            summary=summary,
            standing=standing,
            server_name=getattr(ctx.guild, "name", "This server"),
            accounts=accounts,
        )
        await ctx.followup.send(view=view, ephemeral=ephemeral)

    @discord.slash_command(
        name="details",
        description="Show your badges, rank details and recent check-ins",
        contexts={discord.InteractionContextType.guild},
    )
    async def details(
        self,
        ctx: discord.ApplicationContext,
        platform: discord.Option(str, choices=["leetcode", "gfg"]) = "leetcode",
    ):
        await self.show_details(ctx, platform)

    async def show_details(self, ctx, provider="leetcode"):
        await ctx.defer(ephemeral=True)
        server = guild_id(ctx)
        if not await asyncio.to_thread(self.bot.storage.connected_account, ctx.author.id, provider):
            raise ServiceError(f"Connect your account with /connect {provider} first.")
        summary = await self.bot.progress.summary(server, ctx.author.id, provider)
        rows = await asyncio.to_thread(
            self.bot.storage.leaderboard,
            server,
            datetime.now(platform_zone(provider)).date(),
            "all-time",
            provider,
        )
        rank = next((row["rank"] for row in rows if row["user_id"] == ctx.author.id), None)
        view = DetailsLayout(
            self.bot,
            provider=provider,
            summary=summary,
            platform_rank=f"#{rank}" if rank else "Unranked",
            badges=badges(summary),
        )
        await ctx.followup.send(view=view, ephemeral=True)

    @discord.slash_command(
        name="streak",
        description="Show your current streak, personal best and badges",
        contexts={discord.InteractionContextType.guild},
    )
    async def streak(
        self,
        ctx: discord.ApplicationContext,
        platform: discord.Option(str, choices=["leetcode", "gfg"]) = "leetcode",
    ):
        await ctx.defer(ephemeral=True)
        if not await asyncio.to_thread(self.bot.storage.connected_account, ctx.author.id, platform):
            raise ServiceError(f"Connect your account with /connect {platform} first.")
        summary = await self.bot.progress.summary(guild_id(ctx), ctx.author.id, platform)
        embed = CardContent(
            title=f"{summary['current_streak']}-day {platform_name(platform)} streak",
            color=COLOR,
            description=f"Personal best: **{summary['best_streak']} days**\n{badges(summary)}\n\n{('Today is complete. Come back tomorrow!' if summary['today_solved'] else f'Solve and check in before midnight {platform_zone(platform)} to keep your streak going.')}",
        )
        await ctx.followup.send(view=MessageCard(self.bot, embed), ephemeral=True)

    @discord.slash_command(
        name="leaderboard",
        description="Browse this server's verified daily solving leaderboard",
        contexts={discord.InteractionContextType.guild},
    )
    async def leaderboard(
        self,
        ctx: discord.ApplicationContext,
        period: discord.Option(str, choices=["all-time", "week", "month"]) = "all-time",
        platform: discord.Option(str, choices=["all", "leetcode", "gfg"]) = "all",
    ):
        await self.show_board(ctx, period, provider=platform)

    async def show_board(self, ctx, period="all-time", ephemeral=False, provider="all"):
        await ctx.defer(ephemeral=ephemeral)
        rows = await asyncio.to_thread(
            self.bot.storage.leaderboard,
            guild_id(ctx),
            datetime.now(IST if provider == "all" else platform_zone(provider)).date(),
            period,
            provider,
        )
        view = LeaderboardView(
            self.bot,
            rows,
            period,
            ctx.author.id,
            provider,
            getattr(ctx.guild, "name", "This server"),
        )
        await ctx.followup.send(view=view, ephemeral=ephemeral)

    @discord.slash_command(
        name="solvers",
        description="See today's verified daily solvers in this server",
        contexts={discord.InteractionContextType.guild},
    )
    async def solvers(
        self,
        ctx: discord.ApplicationContext,
        platform: discord.Option(str, choices=["leetcode", "gfg"]) = "leetcode",
    ):
        await ctx.defer()
        today = datetime.now(platform_zone(platform)).date()
        rows = await asyncio.to_thread(
            self.bot.storage.today_solvers, guild_id(ctx), today, platform
        )
        description = "\n".join(
            (
                f"{index}. **{clean(row['display_name'], 70)}** · <t:{row['submitted_at']}:t> · +{row['points']} XP"
                for index, row in enumerate(rows[:25], 1)
            )
        )
        embed = CardContent(
            title=f"{platform_name(platform)} solvers · {today}",
            description=description
            or f"Be the first! Connect your account with /connect {platform}, solve today's POTD, then use /solved platform:{platform}.",
            color=COLOR,
        )
        embed.set_footer(
            text=f"{len(rows)} verified solvers · Accepted submission time · {platform_zone(platform)}"
        )
        await ctx.followup.send(view=MessageCard(self.bot, embed))

    @discord.slash_command(
        name="history",
        description="View your last seven verified daily solves",
        contexts={discord.InteractionContextType.guild},
    )
    async def history(
        self,
        ctx: discord.ApplicationContext,
        platform: discord.Option(str, choices=["leetcode", "gfg"]) = "leetcode",
    ):
        await ctx.defer(ephemeral=True)
        summary = await self.bot.progress.summary(guild_id(ctx), ctx.author.id, platform)
        description = "\n\n".join(
            f"**{row['day']}** · {clean(row['title'], 200)}\n{row['difficulty']} · +{row['points']} XP"
            for row in summary["recent"]
        )
        await ctx.followup.send(
            view=MessageCard(
                self.bot,
                CardContent(
                    title=f"Your recent {platform_name(platform)} solves",
                    description=description
                    or f"No verified solves yet. Start with /connect {platform}, then /solved platform:{platform}.",
                    color=COLOR,
                ),
            ),
            ephemeral=True,
        )


def setup(bot):
    bot.add_cog(CommunityCog(bot))
