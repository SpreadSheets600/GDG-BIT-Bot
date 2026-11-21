import asyncio
import logging
import os
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.services.problems import ProblemService, ServiceError
from bot.services.progress import ProgressService
from bot.services.storage import Storage
from bot.ui.cards import CardContent, MessageCard, notice

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)


def optional_id(name):
    value = os.getenv(name, "").strip()
    if not value:
        return None
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise ValueError(f"{name} must be a positive numeric Discord ID.")
    return int(value)


class POTDBot(discord.Bot):
    def __init__(self, *, database_path=None, guild_id=None, channel_id=None):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(
            intents=intents,
            debug_guilds=[guild_id] if guild_id else None,
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Activity(type=discord.ActivityType.watching, name="Over GDG BIT"),
        )
        self.started_at = datetime.now(UTC)
        self.problems = ProblemService()
        self.storage = Storage(database_path or ROOT / "data" / "bot.db")
        self.progress = ProgressService(self.storage, self.problems)
        self.potd_channel_id = channel_id

        self.load_extension("bot.cogs.platforms")
        self.load_extension("bot.cogs.accounts")
        self.load_extension("bot.cogs.community")
        self.load_extension("bot.cogs.help")
        self.load_extension("bot.cogs.memes")
        self.load_extension("bot.cogs.potd_settings")
        self.load_extension("bot.cogs.server_tools")

    async def close(self):
        server_tools = self.get_cog("ServerToolsCog")
        if server_tools:
            task = server_tools.refresh_counters.get_task()
            server_tools.refresh_counters.cancel()
            if task:
                with suppress(asyncio.CancelledError):
                    await task
        cog = self.get_cog("PlatformsCog")

        if cog:
            task = cog.deliver_daily.get_task()
            cog.deliver_daily.cancel()

            if task:
                with suppress(asyncio.CancelledError):
                    await task

        memes = self.get_cog("MemesCog")

        if memes:
            task = memes.deliver_memes.get_task()
            memes.deliver_memes.cancel()

            if task:
                with suppress(asyncio.CancelledError):
                    await task
        try:
            await self.problems.close()
        finally:
            await super().close()

    async def on_ready(self):
        log.info(
            "Connected as %s (%s); %s top-level commands loaded",
            self.user,
            self.user.id,
            len(self.pending_application_commands),
        )

    async def on_application_command_error(self, ctx, error):
        original = getattr(error, "original", error)

        if isinstance(original, ServiceError):
            message = str(original)

        elif isinstance(original, commands.CommandOnCooldown):
            message = f"Try again in {original.retry_after:.1f} seconds."

        elif isinstance(original, commands.CheckFailure):
            message = "You do not have permission to use this command."

        else:
            log.error(
                "Command %s failed",
                ctx.command,
                exc_info=(type(original), original, original.__traceback__),
            )
            message = "The command could not be completed. Please try again shortly."
        try:
            if ctx.interaction.response.is_done():
                await ctx.followup.send(
                    view=notice(self, "Unable to complete this action", message), ephemeral=True
                )
            else:
                await ctx.respond(
                    view=notice(self, "Unable to complete this action", message), ephemeral=True
                )
        except discord.HTTPException:
            log.exception("Could not send command error response")


def create_bot(**kwargs):
    bot = POTDBot(**kwargs)

    @bot.slash_command(name="ping", description="Check the bot's response time and uptime")
    async def ping(ctx: discord.ApplicationContext):
        uptime = str(datetime.now(UTC) - bot.started_at).split(".")[0]
        await ctx.respond(
            view=notice(
                bot, "Bot status", f"**Latency**  {bot.latency * 1000:.0f} ms\n**Uptime**  {uptime}"
            )
        )

    @bot.slash_command(name="info", description="About the bot and its creator")
    async def info(ctx: discord.ApplicationContext):
        embed = CardContent(
            title="GDG BIT Discord Bot",
            color=4359668,
            description="The GDG BIT community bot: LeetCode and GeeksforGeeks daily problems, verified solves, XP, streaks, leaderboards, developer memes, server tags and member counters.",
        )

        embed.add_field(
            name="Developer",
            value="`SpreadSheets600`",
            inline=False,
        )

        embed.add_field(
            name="Links",
            value="[Website](https://spreadsheets600.buzz)\n[GitHub](https://github.com/SpreadSheets600/GDG-BIT-POTD)",
            inline=False,
        )

        embed.add_field(
            name="Bot status",
            value=f"Servers: **{len(bot.guilds)}**\nUptime: **{str(datetime.now(UTC) - bot.started_at).split('.')[0]}**",
            inline=False,
        )

        embed.add_field(
            name="Help", value="Use `/help` for setup, commands and troubleshooting.", inline=False
        )

        embed.add_field(name="Server tools", value="`/rules` · `/socials` · `/tags` · `/counters`")

        if bot.user:
            embed.add_field(
                name="Created At",
                value=f"`{bot.user.created_at.strftime('%Y-%m-%d %H:%M:%S')} UTC`",
            )
            embed.set_thumbnail(url=bot.user.display_avatar.url)
        await ctx.respond(view=MessageCard(bot, embed))

    return bot


def main():
    load_dotenv(ROOT / ".env")
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = (os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or "").strip()
    if not token:
        raise SystemExit(
            "Set DISCORD_TOKEN in .env before starting the bot (legacy TOKEN also works)."
        )
    path = os.getenv("DATABASE_PATH", "").strip()
    database_path = Path(path).expanduser() if path else ROOT / "data" / "bot.db"
    if not database_path.is_absolute():
        database_path = ROOT / database_path
    try:
        bot = create_bot(
            database_path=database_path,
            guild_id=optional_id("DISCORD_GUILD_ID"),
            channel_id=optional_id("POTD_CHANNEL_ID"),
        )
    except (ValueError, sqlite3.Error, OSError) as exc:
        raise SystemExit(f"Bot configuration failed: {exc}") from exc
    bot.run(token)


if __name__ == "__main__":
    main()
