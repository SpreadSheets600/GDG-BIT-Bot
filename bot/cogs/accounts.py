import asyncio

import discord
from discord.ext import commands

from bot.services.problems import valid_username
from bot.ui.cards import notice


class AccountsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="register", description="Register your Discord account")
    async def register(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        created = await asyncio.to_thread(self.bot.storage.register, ctx.author.id)
        await ctx.followup.send(
            view=notice(
                self.bot,
                "Registration complete" if created else "Already registered",
                "Save a handle with `/register_leetcode_id`, `/register_gfg_id` or `/register_cf_id`. Connect and verify ownership with `/connect leetcode` or `/connect gfg` to earn server XP."
                if created
                else "Your account is already registered. Your saved handles are unchanged.",
            ),
            ephemeral=True,
        )

    async def save_handle(self, ctx, platform, username):
        await ctx.defer(ephemeral=True)
        username = valid_username(username)
        if platform == "leetcode":
            user = await self.bot.problems.profile(username)
            username = user["username"]
        await asyncio.to_thread(self.bot.storage.set_profile, ctx.author.id, platform, username)
        message = f"Saved your **{platform.upper()}** username: **{discord.utils.escape_markdown(username)}**."
        if platform in {"leetcode", "gfg"}:
            message += f"\n\nVerify ownership with `/connect {platform}` to earn server XP."
        await ctx.followup.send(view=notice(self.bot, "Username saved", message), ephemeral=True)

    @discord.slash_command(
        name="register_leetcode_id", description="Validate and save your LeetCode username"
    )
    async def add_leetcode_id(self, ctx: discord.ApplicationContext, leetcode_id: str):
        await self.save_handle(ctx, "leetcode", leetcode_id)

    @discord.slash_command(name="register_gfg_id", description="Save your GeeksforGeeks username")
    async def add_gfg_id(self, ctx: discord.ApplicationContext, gfg_id: str):
        await self.save_handle(ctx, "gfg", gfg_id)

    @discord.slash_command(name="register_cf_id", description="Save your Codeforces username")
    async def add_cf_id(self, ctx: discord.ApplicationContext, cf_id: str):
        await self.save_handle(ctx, "cf", cf_id)


def setup(bot):
    bot.add_cog(AccountsCog(bot))
