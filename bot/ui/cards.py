import json
from dataclasses import dataclass
from datetime import datetime

import discord

from bot.services.problems import ServiceError
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


def _link(text, url):
    return f"[{text}]({url})" if url else text


def _gallery(*items):
    return discord.ui.MediaGallery(
        *(discord.MediaGalleryItem(url, spoiler=spoiler) for url, spoiler in items)
    )


def _embed_card(embed):
    """Rebuild a Discord embed as a components v2 container."""
    card = discord.ui.Container(color=embed.get("color"))
    author = embed.get("author") or {}
    head = []
    if author.get("name"):
        head.append(f"-# {_link(author['name'], author.get('url'))}")
    if embed.get("title"):
        head.append(f"### {_link(embed['title'], embed.get('url'))}")
    if embed.get("description"):
        head.append(embed["description"])
    thumbnail = (embed.get("thumbnail") or {}).get("url")
    if head and thumbnail:
        card.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay("\n".join(head)),
                accessory=discord.ui.Thumbnail(thumbnail),
            )
        )
    elif head:
        card.add_item(discord.ui.TextDisplay("\n".join(head)))
    elif thumbnail:
        card.add_item(_gallery((thumbnail, False)))
    fields = [f"**{field['name']}**\n{field['value']}" for field in embed.get("fields") or []]
    if fields:
        card.add_item(discord.ui.TextDisplay("\n\n".join(fields)))
    image = (embed.get("image") or {}).get("url")
    if image:
        card.add_item(_gallery((image, False)))
    footer = [(embed.get("footer") or {}).get("text")]
    if embed.get("timestamp"):
        stamp = datetime.fromisoformat(embed["timestamp"])
        footer.append(f"<t:{int(stamp.timestamp())}:f>")
    footer = " • ".join(part for part in footer if part)
    if footer:
        card.add_item(discord.ui.TextDisplay(f"-# {footer}"))
    return card if card.items else None


def copied_message(bot, data):
    """A saved message snapshot: text, embeds as cards, its components, then attachments."""
    view = BotLayoutView(bot)
    if data.get("content"):
        view.add_item(discord.ui.TextDisplay(data["content"]))
    for embed in data.get("embeds", []):
        card = _embed_card(embed)
        if card:
            view.add_item(card)
    components = data.get("components", [])
    if components:
        for item in discord.ui.DesignerView.from_dict(components, timeout=None).children:
            view.add_item(item)
    # Uploads already shown by the components (e.g. in a media gallery) are not repeated.
    shown = json.dumps(components)
    attachments = [
        item for item in data.get("attachments", []) if item["url"].split("?")[0] not in shown
    ]
    media = [
        (item["url"], item.get("spoiler", False))
        for item in attachments
        if item.get("content_type", "").startswith(("image/", "video/"))
    ]
    if media:
        view.add_item(_gallery(*media))
    files = [
        f"📎 {_link(item['filename'], item['url'])}"
        for item in attachments
        if not item.get("content_type", "").startswith(("image/", "video/"))
    ]
    if files:
        view.add_item(discord.ui.TextDisplay("\n".join(files)))
    return view


def check_message_limits(view):
    """Discord allows 40 components and 4000 characters of text per v2 message."""
    count = text = 0
    stack = list(view.to_components())
    while stack:
        item = stack.pop()
        count += 1
        text += len(item.get("content", ""))
        stack.extend(item.get("components", []))
        if item.get("accessory"):
            stack.append(item["accessory"])
    if count > 40 or text > 4000:
        raise ServiceError(
            "That message is too large to repost (Discord allows 4000 characters "
            "and 40 parts per message). Split it into smaller messages."
        )


def verification_row(bot, provider):
    custom_id = "gdg:gfg:connect:verify" if provider == "gfg" else "gdg:connect:verify"
    button = discord.ui.Button(
        label="Verify my account", style=discord.ButtonStyle.success, custom_id=custom_id
    )

    async def callback(interaction):
        await bot.get_cog("CommunityCog").finish_connection(context(interaction), provider)

    button.callback = callback
    return discord.ui.ActionRow(button)
