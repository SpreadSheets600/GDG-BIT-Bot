# GDG BIT Bot

LeetCode/GFG POTDs, verified solves, server leaderboards, streaks, and developer memes. Python 3.12+.

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Fill `DISCORD_TOKEN` in `.env`, then run `python -m bot`.

Invite with `bot` and `applications.commands` scopes. Allow View Channel, Send Messages and Embed Links. Read Message History and Add Reactions enable meme reactions. Grant Manage Channels for member counters.

In the Discord Developer Portal → your application → Bot → Privileged Gateway Intents, enable **Server Members Intent** and **Message Content Intent**, then restart the bot. Both are now requested: role counters need member data, and `!` tags need message content.

## Commands

```text
/help
/connect leetcode username:your_name
/connect gfg username:your_name
/solved platform:leetcode
/profile
/leaderboard
/meme
```

Verify account ownership using the bio code shown by `/connect`.

## Channel setup

```text
/potd configure channel:#potd
/memes configure channel:#memes autopost:true interval_hours:3
```

POTD and meme settings require Administrator. Meme intervals accept 1–168 hours. Set `autopost:false` to disable. Automatic posting must be explicitly enabled with `autopost:true`; the first post follows one full interval. Manual `/meme` requests no longer postpone automatic delivery. Temporary schedule-loading failures retry the next minute, and missing reaction permissions do not block memes.

## Server messages and counters

`/info` shows the bot's purpose, creator, website, source, uptime and account creation date. `/help topic:commands` lists all commands.

Save your actual rules and social links as an administrator:

```text
/tags set name:rules message_link:https://discord.com/channels/<server>/<channel>/<message>
/tags set name:socials message_link:https://discord.com/channels/<server>/<channel>/<message>
!tag set welcome https://discord.com/channels/<server>/<channel>/<message>
```

Everyone can view `/rules` and `/socials`. Administrators can publish `!rules`, `!socials`, or any saved `!name`. Creating, editing, deleting, listing and posting prefix tags is admin-only. Post the message anywhere in the server (a normal message or a webhook message with embeds), right-click it → **Copy Message Link**, and pass that link. The bot copies everything: text, every embed (color, author, title, description, fields, thumbnail, image, footer, timestamp) component layouts (containers, media galleries, text, dividers and link buttons from Discohook-style webhook messages are reposted as-is), and attachments (images and videos are shown, other files are linked). Embeds are rebuilt as cards that look the same. Buttons and menus that belong to another app are dropped, since they would not work from this bot. Each time a tag is posted, the bot re-reads the source message, so edits show up and attachment links stay fresh; if the source is deleted it posts the last saved copy. The bot needs View Channel and Read Message History in the source channel. A copied message must fit in 4000 characters and 40 parts. Tags are scoped to each server, persist across restarts, and do not ping users or roles. Use `/tags list`, `/tags delete name:welcome`, `!tag list` or `!tag delete welcome` to manage them. Tag names accept 1–32 letters, numbers, underscores or hyphens; `tag` is reserved.

Create member count voice channels as an administrator:

```text
/counters configure core_team:@CoreTeam
/counters add role:@Organizers label:Organizers
/counters remove channel:Total members: 100
```

The setup creates **Total members** and **Core team** channels, reuses existing counters on repeated setup, and refreshes counts every five minutes. Total members includes bots; each role count includes all members holding that role. Deleted counter channels are recreated while tracked. Removing tracking keeps the channel so an admin can delete it manually. Missing roles are skipped until an admin removes or replaces their counter.

Counter channels are visible with Connect denied and no role/member permission allows. Discord administrators can always bypass channel restrictions. The bot maintains these channels' names and permission overwrites.

## Data

SQLite: **`data/bot.db`** stores connected accounts, solves, settings, server tags, and member counters. Back it up.

## Checks

```sh
ruff check bot tests
ruff format --check bot tests
python -m unittest discover -s tests
gdg-check
```

## VPS service

Use `/root/GDG-BIT-Bot` with its `.venv`. Copy `.env` and `data/bot.db` to preserve accounts and settings.

```sh
cp deploy/gdg-bit-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gdg-bit-bot
journalctl -u gdg-bit-bot -f
```
