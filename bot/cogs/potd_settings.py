import asyncio

import discord
from discord.ext import commands

from bot.services.problems import ServiceError
from bot.ui.cards import notice


class POTDSettingsCog(commands.Cog):
    potd = discord.SlashCommandGroup(
        "potd",
        "Configure scheduled daily problem delivery",
        contexts={discord.InteractionContextType.guild},
        default_member_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot):
        self.bot = bot

    @potd.command(
        name="configure", description="Set the channel for both LeetCode and GFG daily problems"
    )
    @commands.has_guild_permissions(administrator=True)
    async def configure(
        self, ctx: discord.ApplicationContext, channel: discord.TextChannel, enabled: bool = True
    ):
        await ctx.defer(ephemeral=True)
        if channel.guild.id != ctx.guild.id:
            raise ServiceError("Choose a POTD channel inside this server.")
        permissions = channel.permissions_for(ctx.guild.me)
        if enabled and (
            not (permissions.view_channel and permissions.send_messages and permissions.embed_links)
        ):
            raise ServiceError(
                "The bot needs View Channel, Send Messages and Embed Links in the POTD channel."
            )
        await asyncio.to_thread(
            self.bot.storage.set_potd_settings, ctx.guild.id, channel.id, enabled
        )
        text = f"Channel: {channel.mention}\n\n"
        if enabled:
            text += "LeetCode posts at **05:30 IST / 00:00 UTC**.\nGFG posts at **00:00 IST**.\n\nThe current daily problems will be posted on the next check if not already delivered. New problems are checked every 30 seconds after reset; delayed platform data is retried automatically."
        else:
            text += "Scheduled POTD posting is disabled. Existing messages and solve records are unchanged."
        await ctx.followup.send(
            view=notice(self.bot, "POTD delivery configured", text), ephemeral=True
        )

    @potd.command(
        name="repost",
        description="Post today's LeetCode and GFG problems again in the configured channel",
        default_member_permissions=discord.Permissions(administrator=True),
    )
    @commands.has_guild_permissions(administrator=True)
    async def repost(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        settings = await asyncio.to_thread(self.bot.storage.potd_settings, ctx.guild.id)
        if not settings:
            raise ServiceError("Configure a POTD channel first with /potd configure.")
        channel = self.bot.get_channel(settings["channel_id"]) or await self.bot.fetch_channel(
            settings["channel_id"]
        )
        if not channel or getattr(getattr(channel, "guild", None), "id", None) != ctx.guild.id:
            raise ServiceError("The configured POTD channel is no longer available in this server.")
        permissions = channel.permissions_for(ctx.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            raise ServiceError(
                "The bot needs View Channel, Send Messages and Embed Links in the POTD channel."
            )
        daily = self.bot.get_cog("PlatformsCog")
        for provider in ("leetcode", "gfg"):
            problem = await self.bot.problems.daily(provider)
            await channel.send(view=daily.daily_card(problem))
        await ctx.followup.send(
            view=notice(
                self.bot,
                "POTDs reposted",
                f"Today's LeetCode and GFG problems were reposted in {channel.mention}.",
            ),
            ephemeral=True,
        )

    @potd.command(name="status", description="Show the configured POTD channel and reset schedule")
    @commands.has_guild_permissions(administrator=True)
    async def status(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        settings = await asyncio.to_thread(self.bot.storage.potd_settings, ctx.guild.id)
        text = "No POTD channel configured. Use /potd configure channel:#your-channel."
        if settings:
            mode = "Enabled" if settings["enabled"] else "Disabled"
            text = f"Channel: <#{settings['channel_id']}>\nMode: {mode}\n\nLeetCode: **05:30 IST / 00:00 UTC**\nGFG: **00:00 IST**\n\nBoth platforms post to this channel. Each daily problem is delivered once per destination."
        await ctx.followup.send(view=notice(self.bot, "POTD delivery", text), ephemeral=True)


def setup(bot):
    bot.add_cog(POTDSettingsCog(bot))
