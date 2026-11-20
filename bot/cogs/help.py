import discord
from discord.ext import commands

from bot.ui.help import HelpView


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(
        name="help", description="Learn how to connect accounts, verify solves and use the bot"
    )
    async def help(
        self,
        ctx: discord.ApplicationContext,
        topic: discord.Option(
            str, choices=["start", "daily", "progress", "troubleshooting", "commands", "memes"]
        ) = "start",
    ):
        await self.show_help(ctx, topic)

    async def show_help(self, ctx, topic="start"):
        await ctx.defer(ephemeral=True)
        await ctx.followup.send(view=HelpView(self.bot, ctx.author.id, topic), ephemeral=True)


def setup(bot):
    bot.add_cog(HelpCog(bot))
