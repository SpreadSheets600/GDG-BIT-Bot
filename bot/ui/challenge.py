import discord

from bot.services.platforms import platform_name, platform_zone
from bot.ui.base import BotLayoutView, BotView, context
from bot.ui.cards import CardContent, notice
from bot.ui.formatting import COLOR, clean
from bot.ui.layouts import separator


class VerifyView(BotView):
    def __init__(self, bot, provider="leetcode"):
        super().__init__(bot)
        self.provider = provider
        platform_name(provider)
        if provider == "gfg":
            self.verify.custom_id = "gdg:gfg:connect:verify"

    @discord.ui.button(
        label="Verify my account", style=discord.ButtonStyle.success, custom_id="gdg:connect:verify"
    )
    async def verify(self, button, interaction):
        await self.bot.get_cog("CommunityCog").finish_connection(
            context(interaction), self.provider
        )


class ChallengeView(BotView):
    def __init__(self, bot, provider="leetcode", url=None):
        super().__init__(bot)
        self.provider = provider
        platform_name(provider)
        if provider == "gfg":
            for child in self.children:
                child.custom_id = child.custom_id.replace("gdg:", "gdg:gfg:", 1)
        if url:
            self.add_item(discord.ui.Button(label="Solve problem", url=url, row=0))

    @discord.ui.button(
        label="Mark as solved",
        style=discord.ButtonStyle.success,
        custom_id="gdg:daily:check",
        row=1,
    )
    async def check(self, button, interaction):
        await self.bot.get_cog("CommunityCog").check_today(context(interaction), self.provider)

    @discord.ui.button(
        label="Leaderboard",
        style=discord.ButtonStyle.primary,
        custom_id="gdg:community:leaderboard",
        row=1,
    )
    async def board(self, button, interaction):
        await self.bot.get_cog("CommunityCog").show_board(
            context(interaction), ephemeral=True, provider="all"
        )

    @discord.ui.button(label="My profile", custom_id="gdg:community:profile", row=1)
    async def profile(self, button, interaction):
        await self.bot.get_cog("CommunityCog").show_profile(
            context(interaction), ephemeral=True, provider=self.provider
        )

    @discord.ui.button(label="Help", custom_id="gdg:community:help", row=1)
    async def help(self, button, interaction):
        await self.bot.get_cog("HelpCog").show_help(context(interaction))

    @discord.ui.button(label="My details", custom_id="gdg:community:details", row=2)
    async def details(self, button, interaction):
        await self.bot.get_cog("CommunityCog").show_details(context(interaction), self.provider)


class LeaderboardView(BotLayoutView):
    def __init__(self, bot, rows, period, owner_id, provider="all", server_name="This server"):
        super().__init__(bot, timeout=180)
        self.provider = provider
        self.server_name = server_name
        self.rows = rows
        self.period = period
        self.owner_id = owner_id
        self.page = 0
        self.previous = discord.ui.Button(label="Previous", custom_id="gdg:board:previous")
        self.next = discord.ui.Button(label="Next", custom_id="gdg:board:next")
        self.previous.callback = self.previous_page
        self.next.callback = self.next_page
        self.update_buttons()
        self.render()

    def update_buttons(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * 10 >= len(self.rows)

    def content(self):
        name = "LeetCode + GFG" if self.provider == "all" else platform_name(self.provider)
        zone = "Asia/Kolkata" if self.provider == "all" else str(platform_zone(self.provider))
        lines = []
        for row in self.rows[self.page * 10 : (self.page + 1) * 10]:
            position = f"{row['rank']}."
            lines.append(
                f"{position} **{clean(row['display_name'], 70)}** · {row['points']} XP · {row['solves']} solves"
            )
            if self.provider == "all":
                lines[-1] += (
                    f"\nLeetCode: {row.get('leetcode_solves', 0)} · GFG: {row.get('gfg_solves', 0)}"
                )
        embed = CardContent(
            title=f"{clean(self.server_name, 100)} · {name} leaderboard",
            description="\n\n".join(lines)
            or "No verified daily solves yet. Connect with /connect leetcode or /connect gfg, solve today's POTD, then mark it as solved.",
            color=COLOR,
        )
        embed.set_footer(
            text=f"{self.period} · Server POTD check-ins only · Page {self.page + 1}/{max(1, (len(self.rows) + 9) // 10)} · {name} · Easy 10 / Medium 20 / Hard 30 XP · {zone}"
        )
        return embed

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            view=notice(
                self.bot,
                "Open your own view",
                "Open /leaderboard to browse your own leaderboard view.",
            ),
            ephemeral=True,
        )
        return False

    def render(self):
        data = self.content()
        self.clear_items()
        card = discord.ui.Container(color=COLOR)
        card.add_item(discord.ui.TextDisplay(f"## {data.title}"))
        card.add_item(separator())
        card.add_item(discord.ui.TextDisplay(data.description))
        card.add_item(separator())
        card.add_item(discord.ui.ActionRow(self.previous, self.next))
        card.add_item(discord.ui.TextDisplay(f"-# {data.footer}"))
        self.add_item(card)

    async def previous_page(self, interaction):
        self.page = max(0, self.page - 1)
        self.update_buttons()
        self.render()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction):
        self.page = min(max(0, (len(self.rows) - 1) // 10), self.page + 1)
        self.update_buttons()
        self.render()
        await interaction.response.edit_message(view=self)
