import json
import re
import sqlite3

from bot.services.problems import ServiceError


def tag_name(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,32}", value) or value == "tag":
        raise ServiceError(
            "Tag names need 1–32 letters, numbers, underscores or hyphens; 'tag' is reserved."
        )
    return value


MESSAGE_LINK = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)/?"
)


def parse_message_link(value):
    match = MESSAGE_LINK.fullmatch(value.strip().strip("<>"))
    if not match:
        raise ServiceError(
            "Paste a Discord message link (right-click the message → Copy Message Link)."
        )
    return tuple(int(part) for part in match.groups())


LINK_BUTTON = 5
INTERACTIVE = {3, 5, 6, 7, 8}  # select menus only work for the app that sent them
FILE = 13  # file components point at the source message's uploads; attachments cover them


def _portable(component):
    """Drop parts that only work in the source message; keep layout, text, media and links."""
    # Component ids are per message; let Discord assign fresh ones.
    component = {key: value for key, value in component.items() if key != "id"}
    kind = component.get("type")
    if kind in INTERACTIVE or kind == FILE:
        return None
    if kind == 2:
        return component if component.get("style") == LINK_BUTTON else None
    if kind == 9:  # section: text with a button or thumbnail beside it
        accessory = _portable(component.get("accessory") or {})
        if accessory is None:
            return {"type": 10, "content": _section_text(component)}
        return {**component, "accessory": accessory}
    if "components" in component:
        children = [item for item in map(_portable, component["components"]) if item]
        if not children:
            return None
        return {**component, "components": children}
    return component


def _section_text(section):
    return "\n".join(item.get("content", "") for item in section.get("components", []))


def snapshot_message(message, link):
    """Everything needed to repost a message: its text, embeds and attachments."""
    data = {
        "v": 1,
        "source": link,
        "content": message.content.strip(),
        "embeds": [embed.to_dict() for embed in message.embeds],
        "components": [
            item
            for item in (_portable(component.to_dict()) for component in message.components)
            if item
        ],
        "attachments": [
            {
                "url": item.url,
                "filename": item.filename,
                "content_type": item.content_type or "",
                "spoiler": item.is_spoiler(),
            }
            for item in message.attachments
        ],
    }
    if not (data["content"] or data["embeds"] or data["components"] or data["attachments"]):
        raise ServiceError("That message has nothing to copy.")
    return data


def load_tag(raw):
    """Saved tags are message snapshots; older tags are plain text."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("v") == 1:
        return data
    return {"content": raw, "embeds": [], "components": [], "attachments": []}


class ServerToolsStore:
    @staticmethod
    def init_server_tools(db):
        db.execute(
            "CREATE TABLE IF NOT EXISTS Tags (guild_id INTEGER, name TEXT, content TEXT NOT NULL, PRIMARY KEY(guild_id, name))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS MemberCounters (guild_id INTEGER, role_id INTEGER, channel_id INTEGER NOT NULL, label TEXT NOT NULL, PRIMARY KEY(guild_id, role_id))"
        )

    def set_tag(self, guild_id, name, content):
        name = tag_name(name)
        if isinstance(content, dict):
            content = json.dumps(content)
        else:
            content = content.strip()
            if not 1 <= len(content) <= 2000:
                raise ServiceError("Tag messages must contain 1–2000 characters of text.")
        with self.connect() as db:
            db.execute(
                "INSERT INTO Tags VALUES (?, ?, ?) ON CONFLICT(guild_id, name) DO UPDATE SET content=excluded.content",
                (guild_id, name, content),
            )

    def get_tag(self, guild_id, name):
        with self.connect() as db:
            row = db.execute(
                "SELECT content FROM Tags WHERE guild_id=? AND name=?", (guild_id, tag_name(name))
            ).fetchone()
        return row[0] if row else None

    def delete_tag(self, guild_id, name):
        with self.connect() as db:
            return bool(
                db.execute(
                    "DELETE FROM Tags WHERE guild_id=? AND name=?", (guild_id, tag_name(name))
                ).rowcount
            )

    def list_tags(self, guild_id):
        with self.connect() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT name FROM Tags WHERE guild_id=? ORDER BY name", (guild_id,)
                )
            ]

    def counters(self, guild_id):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in db.execute("SELECT * FROM MemberCounters WHERE guild_id=?", (guild_id,))
            ]

    def set_counter(self, guild_id, role_id, channel_id, label):
        with self.connect() as db:
            db.execute(
                "INSERT INTO MemberCounters VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, role_id) DO UPDATE SET channel_id=excluded.channel_id, label=excluded.label",
                (guild_id, role_id, channel_id, label),
            )

    def remove_counter(self, guild_id, channel_id):
        with self.connect() as db:
            return bool(
                db.execute(
                    "DELETE FROM MemberCounters WHERE guild_id=? AND channel_id=?",
                    (guild_id, channel_id),
                ).rowcount
            )
