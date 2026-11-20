from dataclasses import dataclass

import discord

from bot.ui.base import BotLayoutView, context
from bot.ui.formatting import COLOR


@dataclass
class CardField:
    name: str
    value: str


class CardContent:
    def __init__(self, *, title, description=None, url=None, color=COLOR):
        self.title = title
        self.description = description
        self.url = url
        self.color = color
        self.fields = []
        self.footer = None
        self.thumbnail = None
        self.author = None

    def add_field(self, *, name, value, inline=False):
        self.fields.append(CardField(str(name), str(value)))
        return self

    def set_footer(self, *, text):
        self.footer = text
        return self

    def set_thumbnail(self, *, url):
        self.thumbnail = url
        return self

    def set_author(self, *, name):
        self.author = name
        return self

    def __len__(self):
        return sum(
            len(value or "") for value in (self.title, self.description, self.footer, self.author)
        ) + sum(len(field.name) + len(field.value) for field in self.fields)

    def to_dict(self):
        return {
            "title": self.title,
            "description": self.description,
            "fields": [vars(field) for field in self.fields],
            "footer": self.footer,
        }


class MessageCard(BotLayoutView):
    def __init__(self, bot, content, *, actions=(), timeout=None):
        super().__init__(bot, timeout=timeout)
        self.content = content
        card = discord.ui.Container(color=content.color)
        remaining = 3900

        def text(value):
            nonlocal remaining
            if remaining <= 0 or not value:
                return None
            value = str(value)
            if len(value) > remaining:
                value = value[: max(0, remaining - 3)] + "..."
            remaining -= len(value)
            return discord.ui.TextDisplay(value)

        title = f"## [{content.title}]({content.url})" if content.url else f"## {content.title}"
        if content.author:
            title = f"-# {content.author}\n" + title
        header = text(title)
        if content.thumbnail:
            card.add_item(
                discord.ui.Section(header, accessory=discord.ui.Thumbnail(content.thumbnail))
            )
        else:
            card.add_item(header)
        body = text(content.description)
        if body:
            card.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacingSize.large))
            card.add_item(body)
        for field in content.fields:
            value = text(f"### {field.name}\n{field.value}")
            if value:
                card.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacingSize.large))
                card.add_item(value)
        footer = text(f"-# {content.footer}") if content.footer else None
        if footer:
            card.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacingSize.large))
            card.add_item(footer)
        if actions:
            card.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacingSize.large))
            for row in actions:
                card.add_item(row)
        self.add_item(card)


def notice(bot, title, message):
    return MessageCard(bot, CardContent(title=title, description=message))


def verification_row(bot, provider):
    custom_id = "gdg:gfg:connect:verify" if provider == "gfg" else "gdg:connect:verify"
    button = discord.ui.Button(
        label="Verify my account", style=discord.ButtonStyle.success, custom_id=custom_id
    )

    async def callback(interaction):
        await bot.get_cog("CommunityCog").finish_connection(context(interaction), provider)

    button.callback = callback
    return discord.ui.ActionRow(button)
