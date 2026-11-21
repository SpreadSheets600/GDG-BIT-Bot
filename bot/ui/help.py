import discord

from bot.ui.base import BotLayoutView
from bot.ui.cards import CardContent, notice
from bot.ui.formatting import COLOR
from bot.ui.layouts import separator

TOPICS = {
    "start": "Getting started",
    "daily": "Daily problems",
    "progress": "Profiles and leaderboard",
    "troubleshooting": "Troubleshooting",
    "commands": "All commands",
    "memes": "Developer memes",
}


def help_content(topic="start"):
    embed = CardContent(title=f"Help | {TOPICS[topic]}", color=COLOR)
    if topic == "start":
        embed.description = (
            "Connect an account once, then check in after solving each daily problem."
        )
        fields = [
            (
                "1. Connect your account",
                "Use `/connect leetcode username:your_name` or `/connect gfg username:your_name`.",
            ),
            (
                "2. Verify ownership",
                "Add the supplied code to that account's **About Me**, save it, then click **Verify my account**. The code expires after 15 minutes; remove it after verification.",
            ),
            (
                "3. Solve and check in",
                "Open `/leetcode potd view` or `/gfg potd view`. Solve the problem on your connected account, then click **Mark as solved** on the POTD message or use `/solved platform:leetcode` or `/solved platform:gfg`. The bot verifies your accepted submission before awarding XP.",
            ),
            (
                "4. Track your progress",
                "Use `/profile`, `/streak`, or `/leaderboard` in your server. Select `platform:gfg` for your GFG profile and streak. The leaderboard combines both platforms by default.",
            ),
        ]
    elif topic == "daily":
        embed.description = (
            "Daily check-ins count only in the Discord server where you mark the problem solved."
        )
        fields = [
            (
                "Scheduled delivery (administrators)",
                "Administrators can use `/potd configure channel:#potd` to enable both platforms in one channel, `/potd status` to view settings, and `/potd repost` to repost today's problems. Set `enabled:false` to disable scheduled delivery. LeetCode resets at 05:30 IST; GFG at 00:00 IST. No role pings are added.",
            ),
            (
                "Today's problems",
                "`/leetcode potd view`\n`/gfg potd view`\n`/leetcode potd hint` for private LeetCode hints.",
            ),
            (
                "Mark a solve",
                "Use `/solved platform:leetcode` or `/solved platform:gfg`. Your account must be verified. Repeated check-ins do not award extra XP.",
            ),
            (
                "Points",
                "Easy: **10 XP** · Medium: **20 XP** · Hard: **30 XP**\nGFG School/Basic: **5 XP**. Every 100 platform XP gains a level.",
            ),
            (
                "Daily reset",
                "LeetCode: **00:00 UTC / 05:30 IST**.\nGFG: **00:00 IST**. Check in before the platform resets. The /solved command checks today's current problem.",
            ),
        ]
    elif topic == "progress":
        embed.description = "Rankings contain this server's verified POTD check-ins."
        fields = [
            (
                "Profiles",
                "`/profile` or `/profile platform:gfg`\nAdd `member` to view another member. Server standing combines both platforms; XP, levels and streaks are also shown for the selected platform.",
            ),
            (
                "Leaderboard",
                "`/leaderboard` combines LeetCode and GFG.\nFilter with `platform:leetcode` or `platform:gfg`, and `period:week` or `period:month`. Use Previous/Next to change pages.",
            ),
            (
                "Streaks and history",
                "`/streak [platform]` · `/details [platform]` · `/history [platform]` · `/solvers [platform]`\nStreaks are separate for each platform. Missing a platform day breaks the current streak; your personal best remains.",
            ),
            (
                "Badges",
                "First solve, 3/7/30-day streaks, and 100 daily solves. Badges unlock from verified check-ins.",
            ),
        ]
    elif topic == "troubleshooting":
        fields = [
            (
                "Account not connected",
                "Saving a username with `/register_leetcode_id` or `/register_gfg_id` does not prove ownership. Use `/connect leetcode` or `/connect gfg`, then verify the bio code.",
            ),
            (
                "Verification code not found",
                "Put the exact code in the correct account's **About Me**, save it, wait a moment and click Verify again. If it expired, start a new connection request. `/connect verify platform:gfg` verifies GFG.",
            ),
            (
                "Solve not found",
                "Check that the problem was accepted on your linked account during today's platform day. Keep activity public and retry after 15 seconds. LeetCode exposes recent accepts only; GFG may retain an old timestamp for a previously solved problem.",
            ),
            (
                "Unranked or no streak",
                "Connect your account, then mark a daily solve as solved in this server. Historical platform totals do not automatically earn server XP. After a reset, yesterday's problem cannot be checked in.",
            ),
            (
                "Platform temporarily unavailable",
                "Retry shortly. Unavailable or incomplete data awards no points; it does not erase existing progress.",
            ),
        ]
    elif topic == "memes":
        embed.description = "Developer memes go to the server's configured meme channel with a reaction members can click."
        fields = [
            (
                "Request a meme",
                "Use `/meme` in this server. The meme appears in the configured channel without an extra confirmation message. Requests share a 30-second server cooldown.",
            ),
            (
                "Choose the channel",
                "An administrator can use `/memes configure channel:#memes`. This defaults to on-demand posting.",
            ),
            (
                "Automatic posting",
                "Use `/memes configure channel:#memes autopost:true interval_hours:3` to wait three hours between posts. The first post follows one full interval; manual memes do not delay automatic posts. Set `autopost:false` to stop automatic posts. `/memes status` shows the current settings.",
            ),
            (
                "Permissions",
                "The bot needs View Channel, Send Messages and Embed Links in the target channel. Read Message History and Add Reactions enable reactions; missing reaction permissions do not block posting.",
            ),
            (
                "Source",
                "Images come from r/ProgrammerHumor through Meme API. Posts flagged NSFW/spoiler and already posted images are skipped. Source and author attribution are included.",
            ),
        ]
    else:
        fields = [
            ("Connect", "`/connect leetcode` · `/connect gfg` · `/connect verify [platform]`"),
            (
                "Daily problems",
                "`/leetcode potd view` · `/gfg potd view` · `/leetcode potd hint` · `/solved [platform]`",
            ),
            (
                "Server progress",
                "`/profile [member] [platform]` · `/leaderboard [period] [platform]`\n`/streak [platform]` · `/solvers [platform]` · `/history [platform]`",
            ),
            (
                "Public platform stats",
                "`/leetcode profile [username]` · `/leetcode stats [username]` · `/gfg profile [username]`",
            ),
            (
                "Saved handles",
                "`/register` · `/register_leetcode_id` · `/register_gfg_id` · `/register_cf_id`\nSaved handles are separate from verified account connections.",
            ),
            (
                "POTD settings (administrator only)",
                "`/potd configure channel:#potd [enabled]` · `/potd status`",
            ),
            ("Developer memes", "`/meme` · `/memes status` · `/memes configure` (admin)"),
            (
                "Server messages",
                "`/rules` · `/socials` show saved messages. Admins copy a message into a tag with `/tags set name:rules message_link:...`. Admin-only prefix commands: `!rules`, `!socials`, `!tag set name message_link`, `!tag delete name`, `!tag list`, and any saved `!name`.",
            ),
            (
                "Member counters (administrator only)",
                "`/counters configure core_team:@CoreTeam` creates Total members and Core team voice counters. `/counters add role:@Role label:Team` adds a role counter. `/counters remove channel:...` stops tracking. Counts refresh every five minutes. Enable Server Members and Message Content intents in the Developer Portal; grant Manage Channels.",
            ),
            ("Bot", "`/help [topic]` · `/ping` · `/info`"),
        ]
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text="Choose a topic below. This guide is visible only to you.")
    return embed


class HelpSelect(discord.ui.Select):
    def __init__(self, topic):
        super().__init__(
            placeholder="Choose a help topic",
            options=[
                discord.SelectOption(label=label, value=value, default=value == topic)
                for value, label in TOPICS.items()
            ],
        )

    async def callback(self, interaction):
        selected = self.values[0]
        for option in self.options:
            option.default = option.value == selected
        self.view.render(selected)
        await interaction.response.edit_message(view=self.view)


class HelpView(BotLayoutView):
    def __init__(self, bot, owner_id, topic="start"):
        super().__init__(bot, timeout=300)
        self.owner_id = owner_id
        self.menu = HelpSelect(topic)
        self.render(topic)

    def render(self, topic):
        data = help_content(topic)
        self.clear_items()
        card = discord.ui.Container(color=COLOR)
        card.add_item(discord.ui.TextDisplay(f"## {data.title}"))
        if data.description:
            card.add_item(discord.ui.TextDisplay(data.description))
        for field in data.fields:
            card.add_item(separator())
            card.add_item(discord.ui.TextDisplay(f"### {field.name}\n{field.value}"))
        card.add_item(separator())
        card.add_item(discord.ui.ActionRow(self.menu))
        card.add_item(
            discord.ui.TextDisplay("-# Visible only to you · Use /help to reopen this guide")
        )
        self.add_item(card)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            view=notice(self.bot, "Open your own view", "Open /help to browse your own guide."),
            ephemeral=True,
        )
        return False
