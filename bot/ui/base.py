from types import SimpleNamespace

import discord


def context(interaction):

    async def respond(*, view, ephemeral=False):
        return await interaction.response.send_message(view=view, ephemeral=ephemeral)

    return SimpleNamespace(
        guild_id=interaction.guild_id,
        guild=interaction.guild,
        author=interaction.user,
        interaction=interaction,
        command="button",
        defer=interaction.response.defer,
        followup=interaction.followup,
        respond=respond,
    )


class BotView(discord.ui.View):
    def __init__(self, bot, *, timeout=None):
        super().__init__(timeout=timeout)
        self.bot = bot

    async def on_error(self, error, item, interaction):
        await self.bot.on_application_command_error(context(interaction), error)


class BotLayoutView(discord.ui.DesignerView):
    def __init__(self, bot, *, timeout=None):
        super().__init__(timeout=timeout)
        self.bot = bot

    async def on_error(self, error, item, interaction):
        await self.bot.on_application_command_error(context(interaction), error)
