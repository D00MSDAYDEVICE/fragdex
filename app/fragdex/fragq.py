"""Steam and Discord links from FragQ servers (Discord pickup bots), read with their connector keys.

Several FragQ servers, each run by its own admin, can feed one Fragdex. Each server's answer is stored as it was
given, one row per connection and Steam ID, and a player's Discord ID is worked out from all the rows together.
That way the result doesn't depend on which server synced last, and it doesn't flip between servers that disagree.

A link is "verified" when the player proved the Steam account to FragQ (Steam sign-in, or an in-game !link confirmed
in Discord) and the connection is trusted; otherwise it is "claimed", whatever the server says. A value entered by
hand or imported is never overwritten. A link that is missing from a server's list is kept: it may only mean the
member left that Discord server, and those are the IDs most worth keeping.
"""
import itertools
import json
import logging
import re

import httpx

from . import __version__, config, db
from .importers.common import Sighting, clean_name, normalize_discord_id, normalize_name, normalize_steam_id

log = logging.getLogger("fragdex.fragq")

PATH = "/api/connect/v1/steam/links"
URL_RE = re.compile(r"https?://[^\s/?#]+(/[^\s?#]*)?")
MAX_CONFLICTS = 200
LINK_COLUMNS = ("l.discord_id, l.discord_name, l.persona, l.connection_id, (l.verified AND c.trusted) AS proven,"
                " c.name AS connection")


def parse_url(value):
    """FragQ's address from the Settings form. Returns (url, error); an empty value is allowed."""
    s = str(value or "").strip().rstrip("/")
    if s and not URL_RE.fullmatch(s):
        return None, "Enter FragQ's address, such as https://fragq.example.com."
    return s, None


def _client():
    return httpx.Client(timeout=30, headers={"User-Agent": f"Fragdex/{__version__}"})


def _refusal(r):
    """FragQ's own reason for a refused request, when it gave one."""
    try:
        return str(r.json().get("error") or "")[:200]
    except (ValueError, AttributeError):
        return ""


def fetch(url, key):
    """Every link FragQ will give this key. Raises RuntimeError with a reason an admin can act on."""
    try:
        with _client() as client:
            r = client.get(url + PATH, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        raise RuntimeError(f"Couldn't reach FragQ at {url}: {type(e).__name__}") from None
    if r.status_code == 404:
        raise RuntimeError("FragQ has no links route at this address. Update FragQ to 1.1.0 or later, and use the "
                           "FragQ address from its Fragdex panel, not the Connector API address.")
    if r.status_code != 200:
        hint = {401: "Check the connector key.", 403: "The connector needs Read linked accounts."}.get(r.status_code, "")
        raise RuntimeError(" ".join(x for x in (f"FragQ returned HTTP {r.status_code}.", _refusal(r), hint) if x))
    try:
        links = r.json()["links"]
    except (ValueError, KeyError, TypeError):
        links = None
    if not isinstance(links, list):
        raise RuntimeError("FragQ's answer wasn't a list of links. Check the address.")
    return links


# ---------------------------------------------------------------- connections

def connections(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM fragq_connections ORDER BY id")]


def connection(conn, connection_id):
    row = conn.execute("SELECT * FROM fragq_connections WHERE id=?", (connection_id,)).fetchone()
    return dict(row) if row else None


def address(c):
    """(address, key) of a connection."""
    return (config.FRAGQ_URL_ENV, config.FRAGQ_KEY_ENV) if c["from_env"] else (c["url"], c["key"])


def add_connection(conn, name, url, key, trusted=False):
    return conn.execute("INSERT INTO fragq_connections(name, url, key, trusted, created_at) VALUES(?,?,?,?,?)",
                        (name, url, key, int(trusted), db.now())).lastrowid


def _linked_ids(conn, connection_id):
    return [r[0] for r in conn.execute("SELECT steam_id FROM fragq_links WHERE connection_id=?", (connection_id,))]


def update_connection(conn, c, name, url, key, trusted):
    """Save a connection's settings. An empty key keeps the current one. Changing the trust re-decides its players."""
    with db.transaction(conn):
        conn.execute("UPDATE fragq_connections SET name=?, url=?, key=?, trusted=? WHERE id=?",
                     (name, url, key or c["key"], int(trusted), c["id"]))
        if bool(trusted) != bool(c["trusted"]):
            _settle(conn, _linked_ids(conn, c["id"]))


def delete_connection(conn, c):
    """Forget a connection and what it said. Players keep their Discord IDs, as when a link leaves a list, but a
    "verified" label that nothing vouches for any more becomes "claimed"."""
    with db.transaction(conn):
        sids = _linked_ids(conn, c["id"])
        conn.execute("DELETE FROM fragq_connections WHERE id=?", (c["id"],))
        _settle(conn, sids)


# ---------------------------------------------------------------- working out a player's value

def _clean(link):
    """(steam_id, discord_id, username, Steam name, proven), or None if the entry can't be used."""
    if not isinstance(link, dict):
        return None
    sid = normalize_steam_id(link.get("steam_id") if isinstance(link.get("steam_id"), str) else None)
    did = normalize_discord_id(link["discord_id"]) if isinstance(link.get("discord_id"), str) else None
    if not sid or not did or not did.isdigit():
        return None
    name = " ".join(link["discord_name"].split())[:64] if isinstance(link.get("discord_name"), str) else ""
    persona = normalize_name(link["persona"]) if isinstance(link.get("persona"), str) else ""
    return sid, did, name, persona, int(link.get("verified_by") in ("game", "steam"))


def _decide(p, links):
    """What the links of every connection mean for one player. The answer depends only on them and on the player's
    current value, so repeated syncs settle instead of flipping. links must be in a fixed order.
    Returns (discord_id, username, label, conflict, kept_empty)."""
    labels, names = {}, {}
    for link in links:
        did = link["discord_id"]
        labels[did] = "verified" if link["proven"] or labels.get(did) == "verified" else "claimed"
        if link["discord_name"]:
            names.setdefault(did, link["discord_name"])
    current, name, status = p["discord_id"], p["discord_name"], p["discord_status"]

    if not current and p["discord_source"] == "manual":
        return current, name, status, False, True       # an admin cleared it on purpose

    if current and p["discord_source"] != "fragq":      # entered by hand or imported: FragQ only adds to it
        match = current if current in labels else None
        if match is None and not current.isdigit():     # a typed username that FragQ knows the ID of
            match = next((d for d, n in names.items() if n.lower() == current.lower()), None)
        conflict = any(d != match for d in labels)
        if match is None:
            return current, name, status, conflict, False
        return match, names.get(match) or name, labels[match], conflict, False

    proven = [d for d, label in labels.items() if label == "verified"]
    choices = proven or list(labels)
    if len(choices) != 1:       # no way to pick: keep what is there, and let an admin decide
        return current, names.get(current) or name, labels.get(current, status), True, False
    target = choices[0]
    return target, names.get(target) or (name if target == current else ""), labels[target], False, False


def _resolve(conn, sid, stats, new):
    """Bring one player in line with the links stored for them. Returns True if an admin has a conflict to decide."""
    links = conn.execute(f"""SELECT {LINK_COLUMNS} FROM fragq_links l JOIN fragq_connections c ON c.id = l.connection_id
                             WHERE l.steam_id=? ORDER BY c.id""", (sid,)).fetchall()
    if not links:
        return False
    query = "SELECT discord_id, discord_name, discord_source, discord_status FROM players WHERE steam_id=?"
    p = conn.execute(query, (sid,)).fetchone()
    if p is None:
        persona = next((link["persona"] for link in links if link["persona"]), "")
        if not persona:     # no server has read the Steam name yet; don't add a nameless player
            stats["waiting"] += 1
            return False
        db.add_sightings(conn, [Sighting(steam_id=sid, name=persona)], "fragq", "FragQ link")
        new.add(sid)
        stats["new_players"] += 1
        p = conn.execute(query, (sid,)).fetchone()

    did, name, label, conflict, kept_empty = _decide(p, links)
    stats["kept_empty"] += kept_empty
    current, source = p["discord_id"], p["discord_source"]
    if did != current and (not current or source == "fragq"):
        conn.execute("UPDATE players SET discord_id=?, discord_name=?, discord_checked_at=NULL, discord_source='fragq',"
                     " discord_status=?, updated_at=? WHERE steam_id=?", (did, name, label, db.now(), sid))
        stats["updated" if current else "filled"] += 1
    elif did != current or name != p["discord_name"] or label != p["discord_status"]:
        conn.execute("UPDATE players SET discord_id=?, discord_name=?, discord_status=?, updated_at=? WHERE steam_id=?",
                     (did, name, label, db.now(), sid))
        stats["relabelled"] += label != p["discord_status"]
    return conflict


def _new_stats():
    return dict(links=0, filled=0, updated=0, relabelled=0, new_players=0, conflicts=0, kept_empty=0, waiting=0, bad=0)


def _settle(conn, sids):
    """Decide these players again after a connection's trust changed or it was removed (inside a transaction)."""
    stats, new = _new_stats(), set()
    for sid in sids:
        _resolve(conn, sid, stats, new)
    db.refresh_players(conn, new)
    for i in range(0, len(sids), 500):
        chunk = sids[i:i + 500]
        conn.execute(f"""UPDATE players SET discord_status='claimed', updated_at=?
                         WHERE discord_status='verified' AND steam_id IN ({','.join('?' * len(chunk))}) AND NOT EXISTS(
                             SELECT 1 FROM fragq_links l JOIN fragq_connections c ON c.id = l.connection_id
                             WHERE l.steam_id = players.steam_id AND l.discord_id = players.discord_id
                               AND l.verified = 1 AND c.trusted = 1)""", (db.now(), *chunk))


def apply(conn, connection_id, links):
    """Store one server's list and settle the players in it. Safe to repeat: players only change when the outcome
    does. Rows for Steam IDs missing from the list stay. Returns the counts for the Settings page."""
    stats, new, sids = _new_stats(), set(), {}
    with db.transaction(conn):
        for raw in links:
            link = _clean(raw)
            if link is None:
                stats["bad"] += 1
                continue
            stats["links"] += 1
            sid, did, name, persona, proven = link
            sids[sid] = True
            conn.execute("""INSERT INTO fragq_links(connection_id, steam_id, discord_id, discord_name, persona, verified,
                                                    updated_at) VALUES(?,?,?,?,?,?,?)
                            ON CONFLICT(connection_id, steam_id) DO UPDATE SET discord_id=excluded.discord_id,
                                discord_name=excluded.discord_name, verified=excluded.verified,
                                persona=CASE WHEN excluded.persona != '' THEN excluded.persona ELSE persona END,
                                updated_at=excluded.updated_at""",
                         (connection_id, sid, did, name, persona, proven, db.now()))
        for sid in sids:
            stats["conflicts"] += _resolve(conn, sid, stats, new)
        db.refresh_players(conn, new)
    return stats


def conflicts(conn):
    """Players whose links can't be settled automatically, for an admin to decide."""
    rows = conn.execute(f"""SELECT p.steam_id, p.last_name, p.discord_id AS current, p.discord_name AS current_name,
                                   p.discord_source, p.discord_status, {LINK_COLUMNS}
                            FROM fragq_links l JOIN fragq_connections c ON c.id = l.connection_id
                            JOIN players p ON p.steam_id = l.steam_id ORDER BY p.steam_id, c.id""").fetchall()
    out = []
    for sid, group in itertools.groupby(rows, key=lambda r: r["steam_id"]):
        links = list(group)
        first = links[0]
        player = {"discord_id": first["current"], "discord_name": first["current_name"],
                  "discord_source": first["discord_source"], "discord_status": first["discord_status"]}
        if _decide(player, links)[3]:
            out.append({"steam_id": sid, "name": clean_name(first["last_name"]), "fragdex": first["current"],
                        "links": [{"connection_id": r["connection_id"], "connection": r["connection"],
                                   "fragq": r["discord_id"], "label": "verified" if r["proven"] else "claimed"}
                                  for r in links]})
    return out


# ---------------------------------------------------------------- syncing

def sync(conn, c, url, key):
    """Read one connection. Raises RuntimeError if FragQ can't be read; what it said before stays."""
    stats = apply(conn, c["id"], fetch(url, key))
    stats["at"] = db.now()
    mark = db.key_mark(key)
    stats["filled_total"] = (c["filled_total"] if c["filled_key"] == mark else 0) + stats["filled"]
    conn.execute("UPDATE fragq_connections SET last_result=?, last_error='', filled_key=?, filled_total=? WHERE id=?",
                 (json.dumps(stats), mark, stats["filled_total"], c["id"]))
    return stats


def sync_all(conn):
    """Read every connection; one that fails doesn't affect the others. Returns what changed overall."""
    changed = dict(discord=False, players=False)
    for c in connections(conn):
        url, key = address(c)
        if not url or not key:
            continue
        try:
            stats = sync(conn, c, url, key)
        except Exception as e:  # noqa: BLE001 - recorded on the connection, and the next one still runs
            log.warning("FragQ sync of '%s' failed: %s", c["name"], e)
            conn.execute("UPDATE fragq_connections SET last_error=? WHERE id=?", (f"{db.now()}: {e}"[:300], c["id"]))
            continue
        changed["discord"] |= bool(stats["filled"] or stats["updated"])
        changed["players"] |= bool(stats["new_players"])
    return changed
