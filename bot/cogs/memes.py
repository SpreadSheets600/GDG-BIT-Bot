import asyncio
import logging
import time
from contextlib import suppress

import discord
from discord.ext import commands, tasks

from bot.services.memes import MemeService
from bot.services.problems import ServiceError
from bot.ui.cards import notice
from bot.ui.memes import MemeLayout

log = logging.getLogger(__name__)
REACTION = "😂"


class MemesCog(commands.Cog):
    memes = discord.SlashCommandGroup("memes", "Configure developer meme delivery")

    def __init__(self, bot):
        self.bot = bot
        self.service = MemeService(bot.problems, bot.storage)
        self.locks = {}
        self.last_post = {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.deliver_memes.is_running():
            self.deliver_memes.start()

    def cog_unload(self):
        self.deliver_memes.cancel()

    @staticmethod
    def check_channel(channel, guild_id):
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
            raise ServiceError("Choose a text channel in this server for developer memes.")
        permissions = channel.permissions_for(channel.guild.me)
        required = (
            "view_channel",
            "send_messages",
            "embed_links",
            "read_message_history",
            "add_reactions",
        )
        if any(not getattr(permissions, name) for name in required):
            raise ServiceError(
                "The meme channel needs View Channel, Send Messages, Embed Links, Read Message History and Add Reactions permissions for this bot."
            )

    async def post(self, guild_id, *, scheduled_slot=None):
        async with self.locks.setdefault(guild_id, asyncio.Lock()):
            settings = await asyncio.to_thread(self.bot.storage.meme_settings, guild_id)
            if not settings:
                raise ServiceError(
                    "An admin must choose a meme channel with /memes configure first."
                )
            if scheduled_slot and await asyncio.to_thread(
                self.bot.storage.meme_scheduled_posted, guild_id, scheduled_slot
            ):
                return (None, True)
            if scheduled_slot and (
                not settings["enabled"]
                or str(settings["next_post_at"]) != str(scheduled_slot)
                or time.time() < settings["next_post_at"]
            ):
                return (None, True)
            if (
                not scheduled_slot
                and time.monotonic() - self.last_post.get(guild_id, float("-inf")) < 30
            ):
                raise ServiceError(
                    "A meme was just posted. Wait 30 seconds before requesting another."
                )
            channel = self.bot.get_channel(settings["channel_id"]) or await self.bot.fetch_channel(
                settings["channel_id"]
            )
            self.check_channel(channel, guild_id)
            meme = await self.service.fetch(guild_id)
            message = await channel.send(view=MemeLayout(self.bot, meme))
            await asyncio.to_thread(
                self.bot.storage.record_meme,
                guild_id,
                meme,
                message.id,
                int(time.time()),
                scheduled_slot,
            )
            self.last_post[guild_id] = time.monotonic()
            try:
                await message.add_reaction(REACTION)
                return (message, True)
            except discord.HTTPException:
                log.exception("Meme posted but reaction could not be added")
                return (message, False)

    @discord.slash_command(
        name="meme",
        description="Post a developer meme in this server's configured meme channel",
        contexts={discord.InteractionContextType.guild},
    )
    async def meme(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        _, reacted = await self.post(ctx.guild.id)
        if not reacted:
            await ctx.followup.send(
                view=notice(
                    self.bot,
                    "Reaction unavailable",
                    "The meme was posted, but its reaction could not be added. Check Add Reactions permissions.",
                ),
                ephemeral=True,
            )
            return
        with suppress(discord.NotFound):
            await ctx.interaction.delete_original_response()

    @memes.command(
        name="configure",
        description="Choose the meme channel and wait between automatic posts",
        contexts={discord.InteractionContextType.guild},
        default_member_permissions=discord.Permissions(manage_guild=True),
    )
    @commands.has_guild_permissions(manage_guild=True)
    async def configure(
        self,
        ctx: discord.ApplicationContext,
        channel: discord.TextChannel,
        autopost: bool = False,
        interval_hours: discord.Option(int, min_value=1, max_value=168) = 24,
    ):
        await ctx.defer(ephemeral=True)
        self.check_channel(channel, ctx.guild.id)
        await asyncio.to_thread(
            self.bot.storage.set_meme_settings, ctx.guild.id, channel.id, autopost, interval_hours
        )
        status = (
            f"Automatic posting every {interval_hours} hours. The first post follows one full interval."
            if autopost
            else "On-demand only. Use /meme to post."
        )
        await ctx.followup.send(
            view=notice(
                self.bot,
                "Meme channel configured",
                f"Channel: {channel.mention}\n{status}\nA reaction is added to every meme so members can join in.",
            ),
            ephemeral=True,
        )

    @memes.command(
        name="status",
        description="Show the developer meme channel and posting schedule",
        contexts={discord.InteractionContextType.guild},
    )
    async def status(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        settings = await asyncio.to_thread(self.bot.storage.meme_settings, ctx.guild.id)
        text = "No meme channel configured. An admin can set one with /memes configure."
        if settings:
            mode = (
                f"Every {settings['interval_hours']} hours · Next post <t:{settings['next_post_at']}:R>"
                if settings["enabled"]
                else "On-demand only"
            )
            text = f"Channel: <#{settings['channel_id']}>\nMode: {mode}\nUse /meme to request a developer meme."
        await ctx.followup.send(view=notice(self.bot, "Developer memes", text), ephemeral=True)

    @tasks.loop(minutes=1)
    async def deliver_memes(self):
        settings = await asyncio.to_thread(self.bot.storage.enabled_meme_settings)
        for config in settings:
            if time.time() < config["next_post_at"]:
                continue
            try:
                await self.post(config["guild_id"], scheduled_slot=str(config["next_post_at"]))
            except Exception:
                log.exception("Developer meme delivery failed; will retry later")

    @deliver_memes.before_loop
    async def before_delivery(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(MemesCog(bot))
