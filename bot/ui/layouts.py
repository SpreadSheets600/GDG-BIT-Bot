import discord

from bot.services.platforms import platform_name, profile_url
from bot.ui.base import BotLayoutView, context
from bot.ui.formatting import COLOR, clean


def separator(*, divider=True):
    return discord.ui.Separator(divider=divider, spacing=discord.SeparatorSpacingSize.large)


def action_button(bot, provider, action, label, *, style=discord.ButtonStyle.secondary):
    prefix = "gdg:gfg:" if provider == "gfg" else "gdg:"
    ids = {
        "check": "daily:check",
        "board": "community:leaderboard",
        "profile": "community:profile",
        "details": "community:details",
        "help": "community:help",
    }
    button = discord.ui.Button(label=label, style=style, custom_id=prefix + ids[action])

    async def callback(interaction):
        ctx = context(interaction)
        cog = bot.get_cog("CommunityCog")
        if action == "check":
            await cog.check_today(ctx, provider)
        elif action == "board":
            await cog.show_board(ctx, ephemeral=True, provider="all")
        elif action == "profile":
            await cog.show_profile(ctx, ephemeral=True, provider=provider)
        elif action == "details":
            await cog.show_details(ctx, provider)
        else:
            await bot.get_cog("HelpCog").show_help(ctx)

    button.callback = callback
    return button


class ProfileLayout(BotLayoutView):
    def __init__(
        self, bot, *, provider, member, account, summary, standing, server_name, accounts=None
    ):
        super().__init__(bot)
        name = platform_name(provider)
        card = discord.ui.Container(color=COLOR)
        card.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(f"## {clean(member.display_name, 100)}"),
                accessory=discord.ui.Thumbnail(member.display_avatar.url),
            )
        )
        accounts = accounts or {provider: account}
        connections = "\n".join(
            (
                f"[{platform_name(platform)} / {clean(link['username'], 100)}]({profile_url(platform, link['username'])})"
                for platform, link in accounts.items()
            )
        )
        card.add_item(discord.ui.TextDisplay(f"**Connected accounts**\n{connections}"))
        card.add_item(separator())
        rank = f"#{standing['rank']}" if standing else "Unranked"
        xp = standing["points"] if standing else 0
        card.add_item(
            discord.ui.TextDisplay(
                f"### Server standing\n**{rank}**  ·  **{xp} XP**\n-# LeetCode and GFG combined"
            )
        )
        card.add_item(separator(divider=False))
        card.add_item(
            discord.ui.TextDisplay(
                f"### {name} progress\n**Level {summary['level']}**  ·  {summary['points']} XP  ·  {summary['solves']} daily solves\n\n**Current streak**  {summary['current_streak']} days\n**Personal best**  {summary['best_streak']} days"
            )
        )
        card.add_item(separator())
        card.add_item(
            discord.ui.ActionRow(
                action_button(bot, provider, "details", "My details"),
                action_button(bot, provider, "help", "Help"),
            )
        )
        reset = "05:30 IST / 00:00 UTC" if provider == "leetcode" else "00:00 IST"
        card.add_item(discord.ui.TextDisplay(f"-# {clean(server_name, 100)} · Daily reset {reset}"))
        self.add_item(card)


class DetailsLayout(BotLayoutView):
    def __init__(self, bot, *, provider, summary, platform_rank, badges):
        super().__init__(bot, timeout=300)
        name = platform_name(provider)
        card = discord.ui.Container(color=COLOR)
        card.add_item(
            discord.ui.TextDisplay(
                f"## Your {name} details\nRank: **{platform_rank}** · Next level in **{100 - summary['points'] % 100} XP**"
            )
        )
        card.add_item(separator())
        card.add_item(discord.ui.TextDisplay(f"### Badges\n{badges}"))
        card.add_item(separator())
        recent = "\n\n".join(
            f"**{row['day']}**\n{clean(row['title'], 150)} · +{row['points']} XP"
            for row in summary["recent"]
        )
        card.add_item(
            discord.ui.TextDisplay(
                f"### Recent check-ins\n{recent or 'No verified daily solves yet.'}"
            )
        )
        card.add_item(separator())
        card.add_item(
            discord.ui.ActionRow(
                action_button(bot, provider, "profile", "My profile"),
                action_button(bot, provider, "help", "Help"),
            )
        )
        self.add_item(card)
