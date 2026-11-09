"""Discord usernames for the Discord IDs in the database, looked up with a bot token."""
import datetime as dt
import time

import httpx

from . import __version__, config

API = "https://discord.com/api/v10"
USER_AGENT = f"DiscordBot (https://github.com/D00MSDAYDEVICE/fragdex, {__version__})"
BATCH = 50
PAUSE = 1.0     # seconds between lookups: Discord allows far more, and nothing here is urgent

# discord_id also holds usernames, which can't be looked up (normalize_discord_id never stores all digits as a name)
NUMERIC_SQL = "(discord_id != '' AND discord_id NOT GLOB '*[^0-9]*')"


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__(f"Discord rate limit: wait {retry_after:.0f}s")
        self.retry_after = retry_after


def _client():
    return httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})


def username(user):
    name = user.get("username") or ""
    disc = user.get("discriminator") or "0"
    return f"{name}#{disc}" if name and disc != "0" else name    # only bots still have a #1234


def _lookup(client, token, discord_id):
    """The user's name, or '' if Discord has no such user."""
    r = client.get(f"{API}/users/{discord_id}", headers={"Authorization": f"Bot {token}"})
    if r.status_code == 200:
        return username(r.json()), r
    if r.status_code in (400, 404):
        return "", r
    if r.status_code == 429:
        raise RateLimited(float(r.headers.get("Retry-After") or 60))
    hint = " - check the Discord bot token" if r.status_code in (401, 403) else ""
    raise RuntimeError(f"Discord returned HTTP {r.status_code}{hint}")


def refresh(conn, token, ids):
    """Look up each ID and store the result as it arrives, so a rate limit or an error loses nothing."""
    with _client() as client:
        for discord_id in ids:
            name, r = _lookup(client, token, discord_id)
            # a user Discord no longer knows keeps the last name seen
            conn.execute("UPDATE players SET discord_name=CASE WHEN ?!='' THEN ? ELSE discord_name END,"
                         " discord_checked_at=? WHERE discord_id=?",
                         (name, name, dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), discord_id))
            wait = PAUSE
            if r.headers.get("X-RateLimit-Remaining") == "0":
                wait = max(wait, float(r.headers.get("X-RateLimit-Reset-After") or 0))
            time.sleep(wait)


def cutoff():
    """IDs checked before this time are due for another look."""
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=config.DISCORD_REFRESH_DAYS)
            ).strftime("%Y-%m-%d %H:%M:%S")


def due(conn):
    return [r[0] for r in conn.execute(
        f"SELECT discord_id FROM players WHERE {NUMERIC_SQL} AND (discord_checked_at IS NULL OR discord_checked_at < ?)"
        " GROUP BY discord_id ORDER BY MIN(discord_checked_at IS NOT NULL), MIN(discord_checked_at) LIMIT ?",
        (cutoff(), BATCH))]


def progress(conn):
    row = conn.execute(f"""
        SELECT COUNT(*) total, COALESCE(SUM(discord_name != ''), 0) named,
               COALESCE(SUM(discord_checked_at IS NULL OR discord_checked_at < ?), 0) pending
        FROM players WHERE {NUMERIC_SQL}""", (cutoff(),)).fetchone()
    return {"total": row["total"], "named": row["named"], "pending": row["pending"]}
