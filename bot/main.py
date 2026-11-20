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
        super().__init__(
            intents=discord.Intents.default(),
            debug_guilds=[guild_id] if guild_id else None,
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Game(name="/leetcode potd view | /gfg potd view"),
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

    async def close(self):
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

    @bot.slash_command(name="info", description="Show bot information and available commands")
    async def info(ctx: discord.ApplicationContext):
        embed = CardContent(
            title="GDG BIT · Problem of the Day",
            color=4359668,
            description="Daily coding problems, hints, profiles and solving stats.",
        )
        embed.add_field(
            name="Daily problems",
            value="`/leetcode potd view`\n`/leetcode potd hint` (private)\n`/gfg potd view`",
            inline=False,
        )
        embed.add_field(
            name="Profiles",
            value="`/leetcode profile` · `/leetcode stats`\n`/register` · `/register_leetcode_id` · `/register_gfg_id` · `/register_cf_id`",
            inline=False,
        )
        embed.add_field(
            name="Community",
            value="`/connect leetcode` or `/connect gfg` → verify your bio code\n`/solved` · `/profile` · `/streak` · `/leaderboard` · `/solvers` · `/history`\nMark a solve → linked-account verification → server XP and streak.\n`/leaderboard` combines this server's LeetCode + GFG check-ins; filter by platform if desired.",
            inline=False,
        )
        embed.add_field(
            name="Developer memes",
            value="`/meme` posts to the configured meme channel. `/memes status` shows settings; admins use `/memes configure`.",
            inline=False,
        )
        embed.add_field(
            name="Help", value="Use `/help` for setup, commands and troubleshooting.", inline=False
        )
        embed.add_field(
            name="Source", value="[GitHub](https://github.com/SpreadSheets600/GDG-BIT-POTD)"
        )
        if bot.user:
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
