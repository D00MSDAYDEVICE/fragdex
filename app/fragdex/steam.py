"""Steam Web API enrichment: persona name, avatar and ban status."""
import datetime as dt

import httpx

from . import config, db

API = "https://api.steampowered.com"


def _get(client, path, params):
    r = client.get(f"{API}/{path}", params=params)
    if r.status_code != 200:
        # never let the URL (which carries the key) reach logs or the Settings page
        hint = " - check the Steam Web API key" if r.status_code in (401, 403) else ""
        raise RuntimeError(f"Steam API {path.split('/')[1]} returned HTTP {r.status_code}{hint}")
    return r.json()


def fetch(key, ids):
    ids = list(ids)[:100]
    params = {"key": key, "steamids": ",".join(ids)}
    with httpx.Client(timeout=20) as c:
        summaries = {p["steamid"]: p for p in
                     _get(c, "ISteamUser/GetPlayerSummaries/v2/", params).get("response", {}).get("players", [])}
        bans = {p["SteamId"]: p for p in _get(c, "ISteamUser/GetPlayerBans/v1/", params).get("players", [])}
    return summaries, bans


def store(conn, ids, summaries, bans):
    ts = db.now()
    for sid in ids:
        s = summaries.get(sid, {})
        b = bans.get(sid, {})
        conn.execute(
            """UPDATE players SET steam_persona=?, steam_avatar=?, steam_visibility=?, vac_bans=?, game_bans=?,
               community_banned=?, days_since_last_ban=?, steam_checked_at=? WHERE steam_id=?""",
            (s.get("personaname"), s.get("avatarmedium"), s.get("communityvisibilitystate"),
             b.get("NumberOfVACBans"), b.get("NumberOfGameBans"),
             int(b["CommunityBanned"]) if "CommunityBanned" in b else None,
             b.get("DaysSinceLastBan"), ts, sid))


def refresh(conn, ids):
    key = db.steam_api_key(conn)
    if not key:
        raise RuntimeError("No Steam Web API key configured")
    ids = list(ids)
    summaries, bans = fetch(key, ids)
    store(conn, ids, summaries, bans)
    return len(summaries)


def cutoff():
    """Players checked before this time are due for a refresh."""
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=config.STEAM_REFRESH_HOURS)
            ).strftime("%Y-%m-%d %H:%M:%S")


def progress(conn):
    row = conn.execute("""
        SELECT COUNT(*) total,
               COALESCE(SUM(steam_checked_at IS NOT NULL AND steam_checked_at >= ?), 0) fresh,
               COALESCE(SUM(steam_avatar IS NOT NULL AND steam_avatar != ''), 0) avatars
        FROM players""", (cutoff(),)).fetchone()
    return {"total": row["total"], "done": row["fresh"], "pending": row["total"] - row["fresh"],
            "avatars": row["avatars"]}
