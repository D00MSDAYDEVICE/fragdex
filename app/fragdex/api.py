"""JSON API under /api/v1 for other apps (minqlx plugins, Discord bots, other Fragdex servers).

Every request needs an API key from Settings, sent as "Authorization: Bearer <key>" (or "X-API-Key: <key>").
What a key may see or change is set per key: IP addresses, notes and adding records are separate permissions.
"""
import ipaddress

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import __version__, apikeys, db, worker
from .importers.common import (Sighting, clean_name, normalize_discord_id, normalize_ip, normalize_name,
                               normalize_steam_id, parse_date)

router = APIRouter(prefix="/api/v1")

MAX_PAGE = 500
MAX_LOOKUP = 100
MAX_RECORDS = 1000
BANS = ("vac", "any", "clean")
SOURCES = ("demo", "log", "xlsx", "csv", "manual", "api", "fragq")


class ApiError(Exception):
    def __init__(self, status, msg, headers=None):
        super().__init__(msg)
        self.status, self.msg, self.headers = status, msg, headers or {}


async def api_error(_request: Request, exc: ApiError):
    return JSONResponse({"error": exc.msg}, status_code=exc.status, headers=exc.headers)


def client_ip(request: Request):
    """The caller's address. Caddy, the only way in, always sets X-Fragdex-Client-IP itself (replacing anything
    the client sent): the address it connected from, or what a proxy listed in TRUSTED_PROXIES reported."""
    return request.headers.get("x-fragdex-client-ip") or (request.client.host if request.client else "")


def api_key(request: Request, response: Response):
    """Dependency: the calling key, after its state, IP allow list and rate limit are checked."""
    auth = request.headers.get("authorization", "")
    raw = auth[7:].strip() if auth[:7].lower() == "bearer " else request.headers.get("x-api-key", "").strip()
    if not raw:
        raise ApiError(401, "Missing API key. Send it in the header 'Authorization: Bearer <key>'.")
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE key_hash=?", (apikeys.hash_key(raw),)).fetchone()
    if not row:
        raise ApiError(401, "Invalid API key.")
    key = dict(row)
    ip = client_ip(request)
    st = apikeys.state(key)
    if st != "active":
        apikeys.usage.denied(key["id"], ip, st)
        raise ApiError(403, f"This API key is {st}.")
    if not apikeys.ip_allowed(ip, key["allowed_ips"]):
        apikeys.usage.denied(key["id"], ip, "ip")
        raise ApiError(403, f"This API key can't be used from {ip}.")
    ok, remaining, retry = apikeys.limiter.hit(key["id"], key["rate_limit"])
    if not ok:
        raise ApiError(429, f"Rate limit of {key['rate_limit']} requests per minute reached. "
                            f"Try again in {retry} seconds.",
                       {"Retry-After": str(retry), "X-RateLimit-Limit": str(key["rate_limit"]),
                        "X-RateLimit-Remaining": "0"})
    if key["rate_limit"]:
        response.headers["X-RateLimit-Limit"] = str(key["rate_limit"])
        response.headers["X-RateLimit-Remaining"] = str(remaining)
    apikeys.usage.used(key["id"], ip)
    return key


def _need(key, perm, what):
    if not key[perm]:
        raise ApiError(403, f"This API key isn't allowed to {what}. An admin can change that under Settings → Fragdex API.")


def _sid(value):
    sid = normalize_steam_id(value if isinstance(value, str) else None)
    if not sid:
        raise ApiError(400, f"'{str(value)[:40]}' is not a Steam ID.")
    return sid


def _steam(p):
    if not p.get("steam_checked_at"):
        return None
    return {"persona": p["steam_persona"], "avatar": p["steam_avatar"], "vac_bans": p["vac_bans"],
            "game_bans": p["game_bans"], "community_banned": bool(p["community_banned"]),
            "days_since_last_ban": p["days_since_last_ban"], "checked_at": p["steam_checked_at"]}


def _summary(p, key, tags=None):
    d = {"steam_id": p["steam_id"], "name": clean_name(p["last_name"]), "name_raw": p["last_name"],
         "first_seen": p["first_seen"] or None, "last_seen": p["last_seen"] or None,
         "name_count": p["name_count"], "linked_accounts": p["alt_count"], "discord_id": p["discord_id"] or None,
         "discord_name": p["discord_name"] or None, "discord_status": p["discord_status"] or None,
         "steam_verified": bool(p["steam_verified"]),
         "tags": tags if tags is not None else [], "steam": _steam(p),
         "profile_url": f"https://steamcommunity.com/profiles/{p['steam_id']}"}
    if key["can_ips"]:
        d.update(last_ip=p["last_ip"] or None, ip_count=p["ip_count"])
    if key["can_notes"]:
        d["notes"] = p["notes"]
    return d


def _tags(conn, sids):
    out = {sid: [] for sid in sids}
    for i in range(0, len(sids), 500):
        chunk = sids[i:i + 500]
        for r in conn.execute(f"""SELECT pt.steam_id, t.name FROM player_tags pt JOIN tags t ON t.id = pt.tag_id
                                  WHERE pt.steam_id IN ({",".join("?" * len(chunk))}) ORDER BY t.name""", chunk):
            out[r["steam_id"]].append(r["name"])
    return out


def _linked(conn, sid, key):
    rows = db.linked_accounts(conn, sid)
    tags = _tags(conn, [r["steam_id"] for r in rows])
    out = []
    for r in rows:
        d = {"steam_id": r["steam_id"], "name": clean_name(r["last_name"]), "last_seen": r["last_seen"] or None,
             "vac_bans": r["vac_bans"], "game_bans": r["game_bans"], "tags": tags[r["steam_id"]]}
        if key["can_ips"]:
            d["shared_ips"] = sorted((r["shared_ips"] or "").split(","))
        out.append(d)
    return out


def _player_row(conn, sid):
    row = conn.execute("SELECT * FROM players WHERE steam_id=?", (sid,)).fetchone()
    if not row:
        raise ApiError(404, f"No player with Steam ID {sid}.")
    return dict(row)


# ---------------------------------------------------------------- read

@router.get("/status")
def status(key=Depends(api_key)):
    with db.get_db() as conn:
        players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        records = conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    return {"app": "fragdex", "version": __version__, "players": players, "records": records,
            "key": {"name": key["name"], "ips": bool(key["can_ips"]), "notes": bool(key["can_notes"]),
                    "write": bool(key["can_write"]), "rate_limit": key["rate_limit"] or None,
                    "expires": key["expires_at"] or None}}


@router.get("/players")
def players(request: Request, key=Depends(api_key)):
    qp = request.query_params
    f = {"q": qp.get("q", "").strip()[:200], "source": qp.get("source", ""), "bans": qp.get("bans", ""),
         "seen_from": qp.get("seen_from", ""), "seen_to": qp.get("seen_to", ""),
         "sort": qp.get("sort", "last_seen"), "dir": qp.get("dir", "desc"),
         "has_ip": qp.get("has_ip") in ("1", "true"), "has_discord": qp.get("has_discord") in ("1", "true"),
         "verified": qp.get("verified") in ("1", "true"),
         "has_alts": qp.get("linked") in ("1", "true")}
    if f["source"] and f["source"] not in SOURCES:
        raise ApiError(400, f"source must be one of: {', '.join(SOURCES)}.")
    if f["bans"] and f["bans"] not in BANS:
        raise ApiError(400, f"bans must be one of: {', '.join(BANS)}.")
    for k in ("seen_from", "seen_to"):
        if f[k] and not apikeys.DATE_RE.match(f[k]):
            raise ApiError(400, f"{k} must be a date (YYYY-MM-DD).")
    if f["sort"] not in db.SORTS or f["dir"] not in ("asc", "desc"):
        raise ApiError(400, f"sort must be one of: {', '.join(db.SORTS)}; dir asc or desc.")
    try:
        limit = min(MAX_PAGE, max(1, int(qp.get("limit", 50))))
        offset = max(0, int(qp.get("offset", 0)))
    except ValueError:
        raise ApiError(400, "limit and offset must be numbers.") from None
    with db.get_db() as conn:
        tag = qp.get("tag", "").strip()
        if tag:
            row = conn.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()
            if not row:
                return {"total": 0, "offset": offset, "limit": limit, "players": []}
            f["tag"] = row["id"]
        total, rows = db.search_players(conn, f, limit=limit, offset=offset,
                                        ips=bool(key["can_ips"]), notes=bool(key["can_notes"]))
        db.attach_details(conn, rows)
    out = []
    for p in rows:
        d = _summary(p, key, [t["name"] for t in p["tags"]])
        d["aliases"] = [clean_name(n["name"]) for n in p["names"]]
        out.append(d)
    return {"total": total, "offset": offset, "limit": limit, "players": out}


@router.get("/players/{steam_id}")
def player(steam_id: str, key=Depends(api_key)):
    sid = _sid(steam_id)
    with db.get_db() as conn:
        p = _player_row(conn, sid)
        d = _summary(p, key, _tags(conn, [sid])[sid])
        d["names"] = [{"name": clean_name(r["name"]), "name_raw": r["name"], "records": r["n"],
                       "first_seen": r["first"], "last_seen": r["last"], "sources": (r["sources"] or "").split(",")}
                      for r in conn.execute("""
            SELECT name, COUNT(*) n, MIN(NULLIF(seen_at,'')) first, MAX(NULLIF(seen_at,'')) last,
                   group_concat(DISTINCT source) sources
            FROM sightings WHERE steam_id=? AND name != '' GROUP BY name ORDER BY last DESC, MAX(id) DESC""", (sid,))]
        if key["can_ips"]:
            d["ips"] = [{"ip": r["ip"], "records": r["n"], "first_seen": r["first"], "last_seen": r["last"],
                         "other_accounts": r["others"]} for r in conn.execute("""
                SELECT s.ip, COUNT(*) n, MIN(NULLIF(s.seen_at,'')) first, MAX(NULLIF(s.seen_at,'')) last,
                       (SELECT COUNT(DISTINCT steam_id) FROM sightings o WHERE o.ip = s.ip AND o.steam_id != s.steam_id)
                           others
                FROM sightings s WHERE s.steam_id=? AND s.ip != '' GROUP BY s.ip
                ORDER BY last DESC, MAX(s.id) DESC""", (sid,))]
        d["records"] = conn.execute("SELECT COUNT(*) FROM sightings WHERE steam_id=?", (sid,)).fetchone()[0]
        d["linked"] = _linked(conn, sid, key)
    return d


@router.get("/players/{steam_id}/linked")
def player_linked(steam_id: str, key=Depends(api_key)):
    sid = _sid(steam_id)
    with db.get_db() as conn:
        _player_row(conn, sid)
        return {"steam_id": sid, "linked": _linked(conn, sid, key)}


@router.post("/players/lookup")
async def lookup(request: Request, key=Depends(api_key)):
    """Many Steam IDs at once, e.g. everyone on a game server. Linked accounts are listed with their tags
    and bans, so a plugin can warn about a player whose other account is a known cheater."""
    body = await _json(request)
    ids = body.get("steam_ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not ids:
        raise ApiError(400, "Send {\"steam_ids\": [...]} with 1 to %d Steam IDs." % MAX_LOOKUP)
    if len(ids) > MAX_LOOKUP:
        raise ApiError(400, f"At most {MAX_LOOKUP} Steam IDs per request.")
    sids, invalid = [], []
    for v in ids:
        sid = normalize_steam_id(v if isinstance(v, str) else None)
        if sid and sid not in sids:
            sids.append(sid)
        elif not sid:
            invalid.append(str(v)[:40])

    def run():
        with db.get_db() as conn:
            rows = {r["steam_id"]: dict(r) for r in conn.execute(
                f"SELECT * FROM players WHERE steam_id IN ({','.join('?' * len(sids))})", sids)} if sids else {}
            tags = _tags(conn, list(rows))
            out = []
            for sid in sids:
                p = rows.get(sid)
                if not p:
                    out.append({"steam_id": sid, "known": False})
                    continue
                d = {"known": True, **_summary(p, key, tags[sid])}
                d["linked"] = _linked(conn, sid, key) if p["alt_count"] else []
                out.append(d)
            return out

    return {"players": await run_in_threadpool(run), "invalid": invalid}


@router.get("/ips/{ip:path}")
def ip_lookup(ip: str, key=Depends(api_key)):
    _need(key, "can_ips", "see IP addresses")
    try:
        net = ipaddress.ip_network(ip.strip(), strict=False)
    except ValueError:
        raise ApiError(400, f"'{ip[:50]}' is not an IP address or CIDR range.") from None
    if net.num_addresses == 1:
        where, arg = "ip = ?", normalize_ip(str(net.network_address))
    else:
        where, arg = "ip != '' AND ip_in(ip, ?)", str(net)
    with db.get_db() as conn:
        rows = [dict(r) for r in conn.execute(f"""
            SELECT s.steam_id, group_concat(DISTINCT s.ip) ips, COUNT(*) n, MIN(NULLIF(s.seen_at,'')) first,
                   MAX(NULLIF(s.seen_at,'')) last, p.last_name
            FROM sightings s JOIN players p ON p.steam_id = s.steam_id
            WHERE {where} GROUP BY s.steam_id ORDER BY last DESC LIMIT {MAX_PAGE}""", (arg,))]
        tags = _tags(conn, [r["steam_id"] for r in rows])
    return {"query": str(net) if net.num_addresses > 1 else arg,
            "players": [{"steam_id": r["steam_id"], "name": clean_name(r["last_name"]),
                         "ips": sorted(r["ips"].split(",")), "records": r["n"], "first_seen": r["first"],
                         "last_seen": r["last"], "tags": tags[r["steam_id"]]} for r in rows]}


@router.get("/discord/{discord_id}")
def discord_lookup(discord_id: str, key=Depends(api_key)):
    did = normalize_discord_id(discord_id)
    if not did:
        raise ApiError(400, f"'{discord_id[:40]}' is not a Discord user ID or username.")
    with db.get_db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM players WHERE discord_id=? COLLATE NOCASE"
                                              " OR discord_name=? COLLATE NOCASE"
                                              " ORDER BY last_seen DESC", (did, did))]
        tags = _tags(conn, [p["steam_id"] for p in rows])
    return {"query": did, "discord_id": did if did.isdigit() else None,
            "players": [_summary(p, key, tags[p["steam_id"]]) for p in rows]}


# ---------------------------------------------------------------- write

async def _json(request):
    try:
        return await request.json()
    except ValueError:
        raise ApiError(400, "The request body must be JSON.") from None


@router.post("/records")
async def add_records(request: Request, key=Depends(api_key)):
    """Add records (name / IP / time a player was seen), e.g. live from a minqlx plugin.
    Body: {"server": "...", "records": [{"steam_id", "name", "ip", "seen_at"}, ...]}.
    seen_at is UTC ("YYYY-MM-DD HH:MM:SS", ISO 8601 or a unix time); it defaults to now."""
    _need(key, "can_write", "add records")
    body = await _json(request)
    recs = body.get("records") if isinstance(body, dict) else None
    if not isinstance(recs, list) or not recs:
        raise ApiError(400, "Send {\"records\": [...]} with 1 to %d records." % MAX_RECORDS)
    if len(recs) > MAX_RECORDS:
        raise ApiError(400, f"At most {MAX_RECORDS} records per request.")
    server = clean_name(str(body.get("server") or ""))[:80]
    now = db.now()
    good, rejected = [], []
    for i, r in enumerate(recs):
        if not isinstance(r, dict):
            rejected.append({"index": i, "error": "not an object"})
            continue
        sid = normalize_steam_id(r.get("steam_id") if isinstance(r.get("steam_id"), str) else None)
        name = normalize_name(r.get("name")) if isinstance(r.get("name"), str) else ""
        ip_raw = r.get("ip")
        ip = normalize_ip(ip_raw) if isinstance(ip_raw, str) and ip_raw.strip() else ""
        seen_raw = r.get("seen_at")
        seen = parse_date(seen_raw) if seen_raw not in (None, "") and not isinstance(seen_raw, bool) else now
        error = ("invalid steam_id" if not sid else "invalid ip" if ip is None else
                 "invalid seen_at" if not seen else "needs a name or an ip" if not name and not ip else None)
        if error:
            rejected.append({"index": i, "error": error})
        else:
            good.append(Sighting(steam_id=sid, name=name, ip=ip, seen_at=seen, server=server))

    def run():
        with db.get_db() as conn, db.transaction(conn):
            added, sids = db.add_sightings(conn, good, "api", f"api: {key['name']}", verified=bool(key["game_server"]))
            db.refresh_players(conn, sids)
        return added, sids

    added, sids = await run_in_threadpool(run) if good else (0, set())
    if added:
        worker.api_records_added()
    return {"added": added, "skipped": len(good) - added, "rejected": rejected, "players": len(sids)}
