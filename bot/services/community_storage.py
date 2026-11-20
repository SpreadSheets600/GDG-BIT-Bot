import sqlite3
from datetime import date, datetime, time, timedelta

from bot.services.platforms import platform_name
from bot.services.problems import IST, ServiceError


def streaks(days, today):
    days = sorted({date.fromisoformat(day) for day in days})
    longest = run = 0
    previous = None
    for day in days:
        run = run + 1 if previous and day == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    current = 0
    if days and days[-1] in {today, today - timedelta(days=1)}:
        current = 1
        for index in range(len(days) - 1, 0, -1):
            if days[index] - days[index - 1] != timedelta(days=1):
                break
            current += 1
    return (current, longest)


def tables(provider):
    platform_name(provider)
    prefix = "" if provider == "leetcode" else "GFG"
    return (prefix + "AccountProofs", prefix + "ConnectedAccounts", prefix + "Solves")


class CommunityStore:
    @staticmethod
    def init_community(db):
        for provider in ("leetcode", "gfg"):
            proofs, accounts, solves = tables(provider)
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {proofs} (\n                user_id INTEGER PRIMARY KEY, username TEXT NOT NULL, token TEXT NOT NULL,\n                expires_at INTEGER NOT NULL)"
            )
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {accounts} (\n                user_id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,\n                connected_at INTEGER NOT NULL)"
            )
            db.execute(
                f"CREATE TABLE IF NOT EXISTS {solves} (\n                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, day TEXT NOT NULL,\n                username TEXT NOT NULL COLLATE NOCASE, slug TEXT NOT NULL, title TEXT NOT NULL,\n                difficulty TEXT NOT NULL, points INTEGER NOT NULL, submission_id TEXT NOT NULL,\n                submitted_at INTEGER NOT NULL, credited_at INTEGER NOT NULL,\n                PRIMARY KEY (guild_id, user_id, day), UNIQUE (guild_id, username, day))"
            )
            db.execute(f"CREATE INDEX IF NOT EXISTS {solves}_board ON {solves}(guild_id, day)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS CommunityMembers (\n            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, display_name TEXT NOT NULL,\n            PRIMARY KEY (guild_id, user_id))"
        )

    def connected_account(self, user_id, provider="leetcode"):
        _, accounts, _ = tables(provider)
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(f"SELECT * FROM {accounts} WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def begin_proof(self, user_id, username, token, expires_at, provider="leetcode"):
        proofs, accounts, _ = tables(provider)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                f"SELECT user_id FROM {accounts} WHERE username=? COLLATE NOCASE OR user_id=?",
                (username, user_id),
            ).fetchone()
            if existing:
                raise ServiceError(
                    f"This Discord account or {platform_name(provider)} username is already connected. Use /profile with platform:{provider}."
                )
            db.execute(
                f"INSERT INTO {proofs} VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, token=excluded.token, expires_at=excluded.expires_at",
                (user_id, username, token, expires_at),
            )

    def pending_proof(self, user_id, provider="leetcode"):
        proofs, _, _ = tables(provider)
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(f"SELECT * FROM {proofs} WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def confirm_proof(self, user_id, token, now, provider="leetcode"):
        proofs, accounts, _ = tables(provider)
        column = "leetcode_id" if provider == "leetcode" else "gfg_id"
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                proof = db.execute(
                    f"SELECT username, expires_at FROM {proofs} WHERE user_id=? AND token=?",
                    (user_id, token),
                ).fetchone()
                if not proof or proof[1] <= now:
                    raise ServiceError(
                        f"Your account proof expired or changed. Start again with /connect {provider}."
                    )
                db.execute(f"INSERT INTO {accounts} VALUES (?, ?, ?)", (user_id, proof[0], now))
                db.execute("INSERT OR IGNORE INTO Users VALUES (?)", (user_id,))
                db.execute(
                    f"INSERT INTO Profiles(user_id, {column}) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET {column}=excluded.{column}",
                    (user_id, proof[0]),
                )
                db.execute(f"DELETE FROM {proofs} WHERE user_id=?", (user_id,))
                return proof[0]
        except sqlite3.IntegrityError as exc:
            raise ServiceError(
                f"That {platform_name(provider)} account is already connected to a Discord account."
            ) from exc

    def touch_member(self, guild_id, user_id, display_name):
        with self.connect() as db:
            db.execute(
                "INSERT INTO CommunityMembers VALUES (?, ?, ?) ON CONFLICT(guild_id,user_id) DO UPDATE SET display_name=excluded.display_name",
                (guild_id, user_id, display_name[:100]),
            )

    def recorded_solve(self, guild_id, user_id, day, provider="leetcode"):
        _, _, solves = tables(provider)
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                f"SELECT * FROM {solves} WHERE guild_id=? AND user_id=? AND day=?",
                (guild_id, user_id, day),
            ).fetchone()
        return dict(row) if row else None

    def record_solve(self, guild_id, user_id, username, problem, submission, credited_at):
        _, accounts, solves = tables(problem.provider)
        points = {"school": 5, "basic": 5, "easy": 10, "medium": 20, "hard": 30}.get(
            problem.difficulty.lower()
        )
        if points is None:
            raise ServiceError(
                "The platform returned an unknown difficulty; points were not awarded."
            )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute(
                f"SELECT username FROM {accounts} WHERE user_id=?", (user_id,)
            ).fetchone()
            if not owner or owner[0].casefold() != username.casefold():
                raise ServiceError("Connect and verify your account before earning points.")
            inserted = (
                db.execute(
                    f"INSERT OR IGNORE INTO {solves} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        guild_id,
                        user_id,
                        problem.date.isoformat(),
                        username,
                        submission["titleSlug"],
                        problem.title,
                        problem.difficulty,
                        points,
                        str(submission["id"]),
                        int(submission["timestamp"]),
                        credited_at,
                    ),
                ).rowcount
                > 0
            )
        return inserted

    def leaderboard(self, guild_id, today, period="all-time", provider="leetcode"):
        if provider == "all":
            return self.server_leaderboard(guild_id, today, period)
        _, accounts, solves = tables(provider)
        if period not in {"all-time", "week", "month"}:
            raise ServiceError("Choose all-time, week or month.")
        start = "0001-01-01"
        if period == "week":
            start = (today - timedelta(days=today.weekday())).isoformat()
        elif period == "month":
            start = today.replace(day=1).isoformat()
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"SELECT s.user_id, m.display_name, a.username,\n                SUM(s.points) AS points, COUNT(*) AS solves, MIN(s.credited_at) AS first_credit\n                FROM {solves} s JOIN CommunityMembers m ON m.guild_id=s.guild_id AND m.user_id=s.user_id\n                JOIN {accounts} a ON a.user_id=s.user_id\n                WHERE s.guild_id=? AND s.day>=? AND s.day<=?\n                GROUP BY s.user_id ORDER BY points DESC, solves DESC, first_credit ASC, s.user_id ASC",
                (guild_id, start, today.isoformat()),
            ).fetchall()
        return [dict(row, rank=index) for index, row in enumerate(rows, 1)]

    def server_leaderboard(self, guild_id, today, period="all-time"):
        if period not in {"all-time", "week", "month"}:
            raise ServiceError("Choose all-time, week or month.")
        start = 0
        if period != "all-time":
            first_day = (
                today - timedelta(days=today.weekday())
                if period == "week"
                else today.replace(day=1)
            )
            start = int(datetime.combine(first_day, time(), IST).timestamp())
        end = int(datetime.combine(today + timedelta(days=1), time(), IST).timestamp())
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "WITH checkins AS (\n                SELECT guild_id, user_id, points, credited_at, 'leetcode' AS platform FROM Solves WHERE guild_id=?\n                UNION ALL\n                SELECT guild_id, user_id, points, credited_at, 'gfg' AS platform FROM GFGSolves WHERE guild_id=?\n            ) SELECT s.user_id, m.display_name, SUM(s.points) AS points,\n                COUNT(*) AS solves, MIN(s.credited_at) AS first_credit,\n                SUM(CASE WHEN s.platform='leetcode' THEN 1 ELSE 0 END) AS leetcode_solves,\n                SUM(CASE WHEN s.platform='gfg' THEN 1 ELSE 0 END) AS gfg_solves\n            FROM checkins s JOIN CommunityMembers m ON m.guild_id=s.guild_id AND m.user_id=s.user_id\n            WHERE s.credited_at>=? AND s.credited_at<?\n            GROUP BY s.user_id ORDER BY points DESC, solves DESC, first_credit ASC, s.user_id ASC",
                (guild_id, guild_id, start, end),
            ).fetchall()
        return [dict(row, rank=index) for index, row in enumerate(rows, 1)]

    def progress_summary(self, guild_id, user_id, today, provider="leetcode"):
        _, _, solves = tables(provider)
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"SELECT * FROM {solves} WHERE guild_id=? AND user_id=? AND day<=? ORDER BY day DESC",
                (guild_id, user_id, today.isoformat()),
            ).fetchall()
        current, best = streaks([row["day"] for row in rows], today)
        points = sum(row["points"] for row in rows)
        return {
            "solves": len(rows),
            "points": points,
            "current_streak": current,
            "best_streak": best,
            "level": 1 + points // 100,
            "today_solved": bool(rows and rows[0]["day"] == today.isoformat()),
            "recent": [dict(row) for row in rows[:7]],
        }

    def today_solvers(self, guild_id, today, provider="leetcode"):
        _, _, solves = tables(provider)
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"SELECT s.*, m.display_name FROM {solves} s\n                JOIN CommunityMembers m ON m.guild_id=s.guild_id AND m.user_id=s.user_id\n                WHERE s.guild_id=? AND s.day=? ORDER BY s.submitted_at ASC, s.user_id ASC",
                (guild_id, today.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]
