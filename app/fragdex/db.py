import datetime as dt
import ipaddress
import sqlite3
from collections import defaultdict
from contextlib import contextmanager

from . import config
from .importers.common import search_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')),
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS players(
    steam_id TEXT PRIMARY KEY,
    last_name TEXT NOT NULL DEFAULT '',
    last_name_clean TEXT NOT NULL DEFAULT '',
    last_ip TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL DEFAULT '',
    last_seen TEXT NOT NULL DEFAULT '',
    name_count INTEGER NOT NULL DEFAULT 0,
    ip_count INTEGER NOT NULL DEFAULT 0,
    alt_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    steam_persona TEXT,
    steam_avatar TEXT,
    steam_visibility INTEGER,
    vac_bans INTEGER,
    game_bans INTEGER,
    community_banned INTEGER,
    days_since_last_ban INTEGER,
    steam_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS players_last_seen ON players(last_seen);
CREATE INDEX IF NOT EXISTS players_name ON players(last_name_clean);

CREATE TABLE IF NOT EXISTS imports(
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'upload',
    status TEXT NOT NULL DEFAULT 'queued',
    players INTEGER NOT NULL DEFAULT 0,
    sightings INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS imports_status ON imports(status);
CREATE INDEX IF NOT EXISTS imports_sha ON imports(sha256);

CREATE TABLE IF NOT EXISTS sightings(
    id INTEGER PRIMARY KEY,
    steam_id TEXT NOT NULL REFERENCES players(steam_id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    name_clean TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    seen_at TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    server TEXT NOT NULL DEFAULT '',
    import_id INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE(steam_id, name, ip, seen_at, source)
);
CREATE INDEX IF NOT EXISTS sightings_steam ON sightings(steam_id, seen_at);
CREATE INDEX IF NOT EXISTS sightings_ip ON sightings(ip);
CREATE INDEX IF NOT EXISTS sightings_name_clean ON sightings(name_clean);

CREATE TABLE IF NOT EXISTS tags(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT '#e8562a'
);
CREATE TABLE IF NOT EXISTS player_tags(
    steam_id TEXT NOT NULL REFERENCES players(steam_id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY(steam_id, tag_id)
);

-- every demo whose contents were imported (directly or via a fragdex-scan export)
CREATE TABLE IF NOT EXISTS imported_files(
    sha256 TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    import_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_files(
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    log_offset INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ip_in(ip, net):
    if not ip:
        return 0
    try:
        return int(ipaddress.ip_address(ip) in ipaddress.ip_network(net, strict=False))
    except ValueError:
        return 0


def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.create_function("ip_in", 2, _ip_in, deterministic=True)
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def init_db():
    import os
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        # jobs interrupted by a restart go back in the queue
        conn.execute("UPDATE imports SET status='queued' WHERE status='running'")


# ---------------------------------------------------------------- settings

def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def steam_api_key(conn):
    return config.STEAM_API_KEY_ENV or get_setting(conn, "steam_api_key")


# ---------------------------------------------------------------- writes

def add_sightings(conn, sightings, source, source_ref="", import_id=None):
    """Insert sightings (inside the caller's transaction). Returns (added, steam_ids)."""
    ts = now()
    added = 0
    sids = set()
    for s in sightings:
        conn.execute("INSERT OR IGNORE INTO players(steam_id, created_at, updated_at) VALUES(?,?,?)",
                     (s.steam_id, ts, ts))
        cur = conn.execute(
            "INSERT OR IGNORE INTO sightings(steam_id, name, name_clean, ip, seen_at, source, source_ref, server,"
            " import_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (s.steam_id, s.name, search_key(s.name), s.ip, s.seen_at, source, source_ref, s.server, import_id, ts))
        added += cur.rowcount
        sids.add(s.steam_id)
    return added, sids


def refresh_players(conn, steam_ids):
    ids = list(steam_ids)
    ts = now()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"""
            UPDATE players SET
              last_name = COALESCE((SELECT name FROM sightings s WHERE s.steam_id = players.steam_id AND name != ''
                                    ORDER BY seen_at DESC, id DESC LIMIT 1), ''),
              last_name_clean = COALESCE((SELECT name_clean FROM sightings s WHERE s.steam_id = players.steam_id
                                    AND name != '' ORDER BY seen_at DESC, id DESC LIMIT 1), ''),
              last_ip = COALESCE((SELECT ip FROM sightings s WHERE s.steam_id = players.steam_id AND ip != ''
                                  ORDER BY seen_at DESC, id DESC LIMIT 1), ''),
              first_seen = COALESCE((SELECT MIN(seen_at) FROM sightings s WHERE s.steam_id = players.steam_id
                                     AND seen_at != ''), ''),
              last_seen = COALESCE((SELECT MAX(seen_at) FROM sightings s WHERE s.steam_id = players.steam_id
                                    AND seen_at != ''), ''),
              name_count = (SELECT COUNT(DISTINCT name) FROM sightings s WHERE s.steam_id = players.steam_id
                            AND name != ''),
              ip_count = (SELECT COUNT(DISTINCT ip) FROM sightings s WHERE s.steam_id = players.steam_id AND ip != ''),
              updated_at = ?
            WHERE steam_id IN ({marks})""", (ts, *chunk))


def shared_ips_clause():
    """SQL fragment: IPs that count for alt-account linking."""
    limit = config.ALT_IP_MAX_ACCOUNTS
    having = "COUNT(DISTINCT steam_id) > 1"
    if limit > 0:
        having += f" AND COUNT(DISTINCT steam_id) <= {int(limit)}"
    return f"SELECT ip FROM sightings WHERE ip != '' GROUP BY ip HAVING {having}"


def recompute_alts(conn):
    by_ip = defaultdict(set)
    for row in conn.execute(
            f"SELECT DISTINCT ip, steam_id FROM sightings WHERE ip IN ({shared_ips_clause()})"):
        by_ip[row["ip"]].add(row["steam_id"])
    alts = defaultdict(set)
    for ids in by_ip.values():
        for sid in ids:
            alts[sid] |= ids - {sid}
    current = {r["steam_id"]: r["alt_count"] for r in conn.execute("SELECT steam_id, alt_count FROM players")}
    updates = [(len(alts.get(sid, ())), sid) for sid, n in current.items() if len(alts.get(sid, ())) != n]
    conn.executemany("UPDATE players SET alt_count=? WHERE steam_id=?", updates)


# ---------------------------------------------------------------- search

SORTS = {
    "last_seen": "p.last_seen = '' , p.last_seen {dir}",
    "first_seen": "p.first_seen = '' , p.first_seen {dir}",
    "name": "p.last_name_clean = '', p.last_name_clean {dir}",
    "steam_id": "p.steam_id {dir}",
    "aliases": "p.name_count {dir}",
    "ips": "p.ip_count {dir}",
    "alts": "p.alt_count {dir}",
}


def _like(s):
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def build_filter(f):
    """f: dict of filter params. Returns (where_sql, params)."""
    where, params = [], []
    q = (f.get("q") or "").strip()
    if q:
        net = None
        if "/" in q:
            try:
                net = str(ipaddress.ip_network(q, strict=False))
            except ValueError:
                net = None
        if net:
            where.append("p.steam_id IN (SELECT steam_id FROM sightings WHERE ip != '' AND ip_in(ip, ?))")
            params.append(net)
        else:
            key = search_key(q)
            where.append("""(p.steam_id LIKE ? ESCAPE '\\'
                OR p.steam_id IN (SELECT steam_id FROM sightings WHERE name_clean LIKE ? ESCAPE '\\'
                                  OR ip LIKE ? ESCAPE '\\')
                OR lower(COALESCE(p.steam_persona, '')) LIKE ? ESCAPE '\\'
                OR lower(p.notes) LIKE ? ESCAPE '\\')""")
            prefix = _like(q)[1:]
            params += [prefix, _like(key), prefix, _like(key), _like(key)]
    tag = f.get("tag")
    if tag:
        where.append("p.steam_id IN (SELECT steam_id FROM player_tags WHERE tag_id = ?)")
        params.append(int(tag))
    source = f.get("source")
    if source:
        where.append("p.steam_id IN (SELECT steam_id FROM sightings WHERE source = ?)")
        params.append(source)
    if f.get("has_ip"):
        where.append("p.ip_count > 0")
    if f.get("has_alts"):
        where.append("p.alt_count > 0")
    bans = f.get("bans")
    if bans == "vac":
        where.append("COALESCE(p.vac_bans, 0) > 0")
    elif bans == "any":
        where.append("(COALESCE(p.vac_bans,0) > 0 OR COALESCE(p.game_bans,0) > 0 OR p.community_banned = 1)")
    elif bans == "clean":
        where.append("p.steam_checked_at IS NOT NULL AND COALESCE(p.vac_bans,0) = 0 AND COALESCE(p.game_bans,0) = 0")
    if f.get("seen_from"):
        where.append("p.last_seen >= ?")
        params.append(f["seen_from"])
    if f.get("seen_to"):
        where.append("p.last_seen != '' AND p.last_seen <= ?")
        params.append(f["seen_to"] + " 23:59:59")
    return (" WHERE " + " AND ".join(where)) if where else "", params


def search_players(conn, f, limit=50, offset=0):
    where, params = build_filter(f)
    sort = SORTS.get(f.get("sort") or "last_seen", SORTS["last_seen"])
    direction = "ASC" if (f.get("dir") == "asc") else "DESC"
    total = conn.execute(f"SELECT COUNT(*) FROM players p{where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT p.* FROM players p{where} ORDER BY {sort.format(dir=direction)}, p.steam_id LIMIT ? OFFSET ?",
        (*params, limit, offset)).fetchall()
    return total, [dict(r) for r in rows]


def attach_details(conn, players):
    """Add names / ips / tags lists to a page of player dicts."""
    if not players:
        return players
    ids = [p["steam_id"] for p in players]
    marks = ",".join("?" * len(ids))
    by = {p["steam_id"]: p for p in players}
    for p in players:
        p["names"], p["ips"], p["tags"] = [], [], []
    for r in conn.execute(f"""
            SELECT steam_id, name, COUNT(*) n, MAX(seen_at) last FROM sightings
            WHERE steam_id IN ({marks}) AND name != '' GROUP BY steam_id, name
            ORDER BY last DESC, MAX(id) DESC""", ids):
        by[r["steam_id"]]["names"].append(dict(r))
    for r in conn.execute(f"""
            SELECT steam_id, ip, COUNT(*) n, MIN(seen_at) first, MAX(seen_at) last FROM sightings
            WHERE steam_id IN ({marks}) AND ip != '' GROUP BY steam_id, ip
            ORDER BY last DESC, MAX(id) DESC""", ids):
        by[r["steam_id"]]["ips"].append(dict(r))
    for r in conn.execute(f"""
            SELECT pt.steam_id, t.id, t.name, t.color FROM player_tags pt JOIN tags t ON t.id = pt.tag_id
            WHERE pt.steam_id IN ({marks}) ORDER BY t.name""", ids):
        by[r["steam_id"]]["tags"].append(dict(r))
    return players


def linked_accounts(conn, steam_id):
    rows = conn.execute(f"""
        SELECT s2.steam_id, group_concat(DISTINCT s2.ip) AS shared_ips, p.last_name, p.last_seen,
               p.vac_bans, p.game_bans
        FROM sightings s1
        JOIN sightings s2 ON s2.ip = s1.ip AND s2.steam_id != s1.steam_id
        JOIN players p ON p.steam_id = s2.steam_id
        WHERE s1.steam_id = ? AND s1.ip != '' AND s1.ip IN ({shared_ips_clause()})
        GROUP BY s2.steam_id ORDER BY p.last_seen DESC""", (steam_id,)).fetchall()
    return [dict(r) for r in rows]
