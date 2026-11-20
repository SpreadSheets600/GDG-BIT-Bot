import discord

from bot.ui.base import BotLayoutView
from bot.ui.formatting import COLOR, clean


class MemeLayout(BotLayoutView):
    def __init__(self, bot, meme):
        super().__init__(bot)
        card = discord.ui.Container(color=COLOR)
        card.add_item(discord.ui.TextDisplay(f"## {clean(meme['title'], 200)}"))
        gallery = discord.ui.MediaGallery(
            discord.MediaGalleryItem(url=meme["url"], description=clean(meme["title"], 200))
        )
        card.add_item(gallery)
        card.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacingSize.large))
        card.add_item(
            discord.ui.TextDisplay(
                f"-# Developer memes · r/ProgrammerHumor · u/{clean(meme.get('author', 'unknown'), 80)}"
            )
        )
        self.add_item(card)
