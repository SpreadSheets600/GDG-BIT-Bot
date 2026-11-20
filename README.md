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

Invite with `bot` and `applications.commands` scopes. Allow messages, embeds, message history, and reactions.

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

POTD settings require Administrator; meme settings require Manage Server. Meme intervals accept 1–168 hours. Set `autopost:false` to disable.

## Data

SQLite: **`data/bot.db`** stores connected accounts, solves, and settings. Back it up.

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
