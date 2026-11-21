import asyncio
import logging

import discord
from discord.ext import commands, tasks

from bot.services.problems import ServiceError
from bot.services.server_tools import (
    load_tag,
    parse_message_link,
    snapshot_message,
    tag_name,
)
from bot.ui.cards import check_message_limits, copied_message, notice

log = logging.getLogger(__name__)


class ServerToolsCog(commands.Cog):
    tags = discord.SlashCommandGroup(
        "tags",
        "Admin tools for reusable server messages",
        default_member_permissions=discord.Permissions(administrator=True),
        contexts={discord.InteractionContextType.guild},
    )
    counters = discord.SlashCommandGroup(
        "counters",
        "Admin tools for member count voice channels",
        default_member_permissions=discord.Permissions(administrator=True),
        contexts={discord.InteractionContextType.guild},
    )

    def __init__(self, bot):
        self.bot = bot
        self.locks = {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.refresh_counters.is_running():
            self.refresh_counters.start()

    def cog_unload(self):
        self.refresh_counters.cancel()

    async def copy_message(self, guild, link):
        """Snapshot the linked message and check it can be reposted."""
        guild_id, channel_id, message_id = parse_message_link(link)
        if guild_id != guild.id:
            raise ServiceError("The message link must point to a message in this server.")
        try:
            channel = guild.get_channel_or_thread(channel_id) or await guild.fetch_channel(
                channel_id
            )
            source = await channel.fetch_message(message_id)
        except (discord.NotFound, AttributeError):
            raise ServiceError("Could not find that message. Check the link.") from None
        except discord.Forbidden:
            raise ServiceError(
                "The bot needs View Channel and Read Message History in that channel."
            ) from None
        data = snapshot_message(source, link)
        check_message_limits(copied_message(self.bot, data))
        return data

    async def save_tag(self, guild, name, link):
        data = await self.copy_message(guild, link)
        await asyncio.to_thread(self.bot.storage.set_tag, guild.id, name, data)

    async def tag_view(self, guild, name):
        """Repost the source message as it is now; fall back to the saved copy."""
        raw = await asyncio.to_thread(self.bot.storage.get_tag, guild.id, name)
        if raw is None:
            return None
        data = load_tag(raw)
        if data.get("source"):
            try:
                fresh = await self.copy_message(guild, data["source"])
            except (ServiceError, discord.HTTPException):
                log.info("Using saved copy of tag %s in guild %s", name, guild.id)
            else:
                if fresh != data:
                    await asyncio.to_thread(self.bot.storage.set_tag, guild.id, name, fresh)
                data = fresh
        return copied_message(self.bot, data)

    async def show_tag(self, ctx, name):
        view = await self.tag_view(ctx.guild, name)
        if view is None:
            raise ServiceError(
                f"An admin must save this message with /tags set name:{name} message_link:... first."
            )
        await ctx.respond(view=view, allowed_mentions=discord.AllowedMentions.none())

    @discord.slash_command(
        name="rules",
        description="Show this server's rules",
        contexts={discord.InteractionContextType.guild},
    )
    async def rules(self, ctx: discord.ApplicationContext):
        await self.show_tag(ctx, "rules")

    @discord.slash_command(
        name="socials",
        description="Show this server's social links",
        contexts={discord.InteractionContextType.guild},
    )
    async def socials(self, ctx: discord.ApplicationContext):
        await self.show_tag(ctx, "socials")

    @tags.command(name="set", description="Save a tag by copying a message from its link")
    @commands.has_guild_permissions(administrator=True)
    @discord.option("message_link", description="Link to the message to copy")
    async def set_tag(self, ctx: discord.ApplicationContext, name: str, message_link: str):
        await ctx.defer(ephemeral=True)
        await self.save_tag(ctx.guild, name, message_link)
        await ctx.followup.send(
            view=notice(
                self.bot,
                "Tag saved",
                f"Use `!{tag_name(name)}` to post it. Prefix tags are admin-only.",
            ),
            ephemeral=True,
        )

    @tags.command(name="delete", description="Delete a saved tag")
    @commands.has_guild_permissions(administrator=True)
    async def delete_tag(self, ctx: discord.ApplicationContext, name: str):
        await ctx.defer(ephemeral=True)
        removed = await asyncio.to_thread(self.bot.storage.delete_tag, ctx.guild.id, name)
        await ctx.followup.send(
            view=notice(self.bot, "Tags", "Tag deleted." if removed else "No such tag."),
            ephemeral=True,
        )

    @tags.command(name="list", description="List this server's saved tags")
    @commands.has_guild_permissions(administrator=True)
    async def list_tags(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        names = await asyncio.to_thread(self.bot.storage.list_tags, ctx.guild.id)
        await ctx.followup.send(
            view=notice(
                self.bot,
                "Server tags",
                ", ".join(f"`!{name}`" for name in names) or "No tags yet. Use /tags set.",
            ),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or not message.content.startswith("!"):
            return
        # All prefix publication and tag management is restricted to administrators.
        if not message.author.guild_permissions.administrator:
            return
        parts = message.content[1:].split(maxsplit=1)
        if not parts:
            return
        try:
            name = tag_name(parts[0]) if parts[0].lower() != "tag" else "tag"
            if name == "tag":
                args = parts[1].split(maxsplit=2) if len(parts) > 1 else []
                if args and args[0].lower() == "set" and len(args) == 3:
                    await self.save_tag(message.guild, args[1], args[2])
                    text = "Tag saved."
                elif args and args[0].lower() == "delete" and len(args) == 2:
                    removed = await asyncio.to_thread(
                        self.bot.storage.delete_tag, message.guild.id, args[1]
                    )
                    text = "Tag deleted." if removed else "No such tag."
                elif args == ["list"]:
                    names = await asyncio.to_thread(self.bot.storage.list_tags, message.guild.id)
                    text = ", ".join(f"`!{item}`" for item in names) or "No tags yet."
                else:
                    text = "Use `!tag set name message_link`, `!tag delete name`, or `!tag list`."
                await message.channel.send(
                    view=notice(self.bot, "Server tags", text),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            elif len(parts) == 1:
                view = await self.tag_view(message.guild, name)
                if view:
                    await message.channel.send(
                        view=view, allowed_mentions=discord.AllowedMentions.none()
                    )
        except ServiceError as exc:
            await message.channel.send(
                view=notice(self.bot, "Server tags", str(exc)),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.exception("Could not publish server tag")

    async def sync_counters(self, guild, *, core_team=None, extra_role=None, label=None):
        async with self.locks.setdefault(guild.id, asyncio.Lock()):
            configs = await asyncio.to_thread(self.bot.storage.counters, guild.id)
            wanted = {row["role_id"]: row for row in configs}
            if core_team is not None:
                for role_id, title in ((0, "Total Members"), (core_team.id, "Core Team")):
                    wanted[role_id] = {
                        **wanted.get(role_id, {}),
                        "role_id": role_id,
                        "label": title,
                    }
            if extra_role is not None:
                wanted[extra_role.id] = {
                    **wanted.get(extra_role.id, {}),
                    "role_id": extra_role.id,
                    "label": label,
                }
            configs = list(wanted.values())
            if not configs:
                return
            if not guild.me.guild_permissions.manage_channels:
                raise ServiceError("Grant the bot Manage Channels to maintain member counters.")
            if not guild.chunked:
                await guild.chunk(cache=True)

            category = discord.utils.find(
                lambda item: item.name.lower() == "statistics", guild.categories
            )
            if category is None:
                category = await guild.create_category(
                    "Statistics", reason="Member count display category"
                )

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False)
            }
            for config in configs:
                role = guild.get_role(config["role_id"]) if config["role_id"] else None
                if config["role_id"] and role is None:
                    continue
                count = len(role.members) if role else guild.member_count
                name = f"{config['label']}: {count}"
                channel = guild.get_channel(config.get("channel_id", 0))
                if channel is None:
                    channel = await guild.create_voice_channel(
                        name,
                        category=category,
                        overwrites=overwrites,
                        reason="Member count display",
                    )
                elif isinstance(channel, discord.VoiceChannel):
                    changes = {}
                    if channel.name != name:
                        changes["name"] = name
                    if channel.category_id != category.id:
                        changes["category"] = category
                    if channel.overwrites != overwrites:
                        changes["overwrites"] = overwrites
                    if changes:
                        await channel.edit(**changes, reason="Refresh member count display")
                else:
                    raise ServiceError("A tracked counter must be a voice channel.")
                await asyncio.to_thread(
                    self.bot.storage.set_counter,
                    guild.id,
                    config["role_id"],
                    channel.id,
                    config["label"],
                )

    @counters.command(
        name="configure", description="Create total member and core team voice counters"
    )
    @commands.has_guild_permissions(administrator=True)
    async def configure_counters(self, ctx: discord.ApplicationContext, core_team: discord.Role):
        await ctx.defer(ephemeral=True)
        await self.sync_counters(ctx.guild, core_team=core_team)
        await ctx.followup.send(
            view=notice(
                self.bot,
                "Counters configured",
                "Total members and Core team update every five minutes. Connect is denied for all roles; Discord administrators can bypass channel permissions.",
            ),
            ephemeral=True,
        )

    @counters.command(name="add", description="Add a voice counter for any role")
    @commands.has_guild_permissions(administrator=True)
    async def add_counter(self, ctx: discord.ApplicationContext, role: discord.Role, label: str):
        await ctx.defer(ephemeral=True)
        label = label.strip()

        if not 1 <= len(label) <= 70 or "\n" in label:
            raise ServiceError("Choose a single-line counter label of 1–70 characters.")

        await self.sync_counters(ctx.guild, extra_role=role, label=label)
        await ctx.followup.send(
            view=notice(self.bot, "Counter saved", "The role counter updates every five minutes."),
            ephemeral=True,
        )

    @counters.command(name="remove", description="Stop tracking a counter; keep its voice channel")
    @commands.has_guild_permissions(administrator=True)
    async def remove_counter(self, ctx: discord.ApplicationContext, channel: discord.VoiceChannel):
        await ctx.defer(ephemeral=True)

        async with self.locks.setdefault(ctx.guild.id, asyncio.Lock()):
            removed = await asyncio.to_thread(
                self.bot.storage.remove_counter, ctx.guild.id, channel.id
            )
        await ctx.followup.send(
            view=notice(
                self.bot,
                "Counters",
                "Tracking stopped. You can delete the channel manually."
                if removed
                else "That channel is not a tracked counter.",
            ),
            ephemeral=True,
        )

    @tasks.loop(minutes=5)
    async def refresh_counters(self):
        for guild in self.bot.guilds:
            try:
                await self.sync_counters(guild)
            except Exception:
                log.exception("Could not refresh member counters for guild %s", guild.id)

    @refresh_counters.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(ServerToolsCog(bot))
