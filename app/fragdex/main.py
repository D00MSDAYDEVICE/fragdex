import csv
import hmac
import io
import math
import os
import re
import secrets
import shutil
import time
import uuid
import zipfile
from collections import defaultdict
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from . import __version__, api, apikeys, auth, config, db, steam, worker
from .importers.common import (STEAM64_RE, Sighting, clean_name, file_kind, is_local_ip, normalize_discord_id,
                               normalize_ip, normalize_name, normalize_steam_id, parse_date)
from .importers.export import FORMAT_VERSION, ExportWriter
from .qlcolors import qlname
from .scanner_build import build_script

HERE = os.path.dirname(__file__)
DATE_RE = re.compile(r"^\d{4}-\d\d-\d\d$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PER_PAGE = (25, 50, 100, 250)
SOURCES = api.SOURCES
MAX_ZIP_BYTES = 8 << 30
MAX_ZIP_MEMBERS = 50000


@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    auth.bootstrap_admin()
    worker.start()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.secret_key(), session_cookie="fragdex_session",
                   max_age=config.SESSION_DAYS * 86400, same_site="lax", https_only=config.COOKIE_SECURE)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
app.include_router(api.router)
app.add_exception_handler(api.ApiError, api.api_error)

templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
templates.env.filters["qlname"] = qlname
templates.env.filters["clean"] = clean_name
templates.env.globals["steam_url"] = lambda sid: f"https://steamcommunity.com/profiles/{sid}"
# Discord profile link for a numeric user ID; usernames have no public URL
templates.env.globals["discord_url"] = lambda d: f"https://discord.com/users/{d}" if d and d.isdigit() else ""
templates.env.globals["has_role"] = auth.has_role
templates.env.globals["version"] = __version__


# ---------------------------------------------------------------- plumbing

class NeedLogin(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, msg="You don't have permission to do that."):
        super().__init__(msg)
        self.msg = msg


class NotFound(Exception):
    pass


@app.exception_handler(NeedLogin)
async def _need_login(request: Request, _exc):
    nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
    return RedirectResponse("/login?" + urlencode({"next": nxt}), status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, exc: Forbidden):
    return render(request, "error.html", get_user(request), status_code=403, title="Forbidden", message=exc.msg)


@app.exception_handler(NotFound)
async def _not_found(request: Request, _exc):
    return render(request, "error.html", get_user(request), status_code=404, title="Not found",
                  message="That page or player doesn't exist.")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > config.MAX_UPLOAD_MB * 1024 * 1024:
        return HTMLResponse(f"Upload too large (limit {config.MAX_UPLOAD_MB} MB)", status_code=413)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
                            "script-src 'self'; "
                            "frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
    return resp


def get_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=? AND disabled=0", (uid,)).fetchone()
    if not row or request.session.get("pwv") != row["password_hash"][-12:]:
        request.session.clear()
        return None
    return dict(row)


def require(request: Request, role="viewer"):
    user = get_user(request)
    if not user:
        raise NeedLogin()
    if not auth.has_role(user, role):
        raise Forbidden()
    return user


def csrf_token(request: Request):
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


async def checked_form(request: Request, **kw):
    form = await request.form(**kw)
    if not hmac.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "")):
        raise Forbidden("Your session expired or the form was stale. Reload the page and try again.")
    return form


def flash(request: Request, msg, level="ok"):
    request.session.setdefault("flash", []).append([level, msg])


def render(request: Request, name, user, status_code=200, **ctx):
    ctx.update(user=user, csrf=csrf_token(request), flashes=request.session.pop("flash", []),
               nav=ctx.get("nav", ""))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def back(url):
    return RedirectResponse(url, status_code=303)


def valid_sid(sid):
    if not STEAM64_RE.match(sid or ""):
        raise NotFound()
    return sid


def safe_next(nxt):
    if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return "/"


# ---------------------------------------------------------------- auth pages

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if get_user(request):
        return back(safe_next(next))
    return render(request, "login.html", None, next=safe_next(next))


@app.post("/login")
async def login(request: Request):
    form = await checked_form(request)
    ip = api.client_ip(request) or "?"
    nxt = safe_next(str(form.get("next", "/")))
    if auth.throttle.blocked(ip):
        return render(request, "login.html", None, status_code=429, next=nxt,
                      error="Too many failed attempts. Try again in 15 minutes.")
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        ok = await run_in_threadpool(auth.verify_password, password, row["password_hash"] if row else
                                     "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        if not row or not ok or row["disabled"]:
            auth.throttle.fail(ip)
            return render(request, "login.html", None, status_code=401, next=nxt, username=username,
                          error="Invalid username or password.")
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (db.now(), row["id"]))
    auth.throttle.reset(ip)
    request.session.clear()
    request.session.update(uid=row["id"], pwv=row["password_hash"][-12:], csrf=secrets.token_urlsafe(24))
    return back(nxt)


@app.post("/logout")
async def logout(request: Request):
    await checked_form(request)
    request.session.clear()
    return back("/login")


@app.get("/account", response_class=HTMLResponse)
def account(request: Request):
    user = require(request)
    return render(request, "account.html", user, nav="account")


@app.post("/account/password")
async def account_password(request: Request):
    user = require(request)
    form = await checked_form(request)
    if not auth.verify_password(str(form.get("current", "")), user["password_hash"]):
        flash(request, "Current password is incorrect.", "error")
        return back("/account")
    new = str(form.get("new", ""))
    problem = auth.password_problem(new) or (None if new == form.get("confirm") else "Passwords don't match.")
    if problem:
        flash(request, problem, "error")
        return back("/account")
    h = auth.hash_password(new)
    with db.get_db() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (h, user["id"]))
    request.session["pwv"] = h[-12:]
    flash(request, "Password changed.")
    return back("/account")


# ---------------------------------------------------------------- player list

def read_filters(request: Request):
    qp = request.query_params
    f = {k: qp.get(k, "").strip() for k in ("q", "tag", "source", "bans", "seen_from", "seen_to", "sort", "dir")}
    f["has_ip"] = qp.get("has_ip") == "1"
    f["has_alts"] = qp.get("has_alts") == "1"
    if not f["tag"].isdigit():
        f["tag"] = ""
    if f["source"] not in SOURCES:
        f["source"] = ""
    if f["bans"] not in ("vac", "any", "clean"):
        f["bans"] = ""
    for k in ("seen_from", "seen_to"):
        if not DATE_RE.match(f[k]):
            f[k] = ""
    if f["sort"] not in db.SORTS:
        f["sort"] = ""
    if f["dir"] not in ("asc", "desc"):
        f["dir"] = ""
    return f


def filter_qs(f, **override):
    d = {k: v for k, v in f.items() if v}
    d = {k: ("1" if v is True else v) for k, v in d.items()}
    d.update({k: v for k, v in override.items() if v not in (None, "")})
    for k, v in override.items():
        if v in (None, ""):
            d.pop(k, None)
    return urlencode(d)


@app.get("/", response_class=HTMLResponse)
def players(request: Request):
    user = require(request)
    f = read_filters(request)
    try:
        per = int(request.query_params.get("per", 50))
    except ValueError:
        per = 50
    per = per if per in PER_PAGE else 50
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    with db.get_db() as conn:
        total, rows = db.search_players(conn, f, limit=per, offset=(page - 1) * per)
        db.attach_details(conn, rows)
        tags = [dict(r) for r in conn.execute("SELECT * FROM tags ORDER BY name")]
        stats = {
            "players": conn.execute("SELECT COUNT(*) FROM players").fetchone()[0],
            "sightings": conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0],
            "active_imports": conn.execute(
                "SELECT COUNT(*) FROM imports WHERE status IN ('queued','running')").fetchone()[0],
        }
        steam_enabled = bool(db.steam_api_key(conn))
    pages = max(1, math.ceil(total / per))
    active = any(f[k] for k in f if k not in ("sort", "dir"))
    return render(request, "players.html", user, nav="players", players=rows, total=total, page=page,
                  pages=pages, per=per, per_options=PER_PAGE, f=f, tags=tags, sources=SOURCES, stats=stats,
                  filter_qs=filter_qs, filters_active=active, steam_enabled=steam_enabled)


def _csv_safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/export.csv")
def export_csv(request: Request):
    require(request)
    f = read_filters(request)

    def generate():
        with db.get_db() as conn:
            _total, rows = db.search_players(conn, f, limit=-1, offset=0)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["steam_id", "discord_id", "name", "name_raw", "last_ip", "first_seen", "last_seen", "aliases", "ips",
                        "linked_accounts", "tags", "notes", "steam_persona", "vac_bans", "game_bans",
                        "steam_profile"])
            for i in range(0, len(rows), 500):
                chunk = db.attach_details(conn, rows[i:i + 500])
                for p in chunk:
                    w.writerow([_csv_safe(x) for x in (
                        p["steam_id"], p["discord_id"], clean_name(p["last_name"]), p["last_name"], p["last_ip"],
                        p["first_seen"],
                        p["last_seen"], " | ".join(n["name"] for n in p["names"]),
                        " | ".join(x["ip"] for x in p["ips"]), p["alt_count"],
                        " | ".join(t["name"] for t in p["tags"]), p["notes"], p["steam_persona"], p["vac_bans"],
                        p["game_bans"], f"https://steamcommunity.com/profiles/{p['steam_id']}")])
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
            yield buf.getvalue()

    return StreamingResponse(generate(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="fragdex-players-{db.now()[:10]}.csv"'})


@app.get("/export.fdx")
def export_fdx(request: Request):
    """Every record of the filtered players, plus notes and tags, as a .fdx any Fragdex server re-imports."""
    require(request)
    f = read_filters(request)

    def generate():
        buf = io.BytesIO()
        w = ExportWriter(buf, f"fragdex {__version__}", db.now(), version=FORMAT_VERSION)
        where, params = db.build_filter(f)
        players_sql = f"SELECT p.steam_id FROM players p{where}"
        with db.get_db() as conn:
            key, rows = None, []
            for r in conn.execute(
                    "SELECT steam_id, name, ip, seen_at, source, source_ref, server FROM sightings"
                    f" WHERE steam_id IN ({players_sql}) ORDER BY source, source_ref, server, id", params):
                k = (r["source"], r["source_ref"], r["server"])
                if rows and (k != key or len(rows) >= 5000):
                    w.add_rows(key[1] or key[0], key[0], "", 0, rows, server=key[2])
                    rows = []
                key = k
                rows.append([r["steam_id"], r["name"], r["ip"], r["seen_at"]])
                if buf.tell() > 1 << 16:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
            if rows:
                w.add_rows(key[1] or key[0], key[0], "", 0, rows, server=key[2])
            tags = defaultdict(list)
            for r in conn.execute(f"""SELECT pt.steam_id, t.name, t.color FROM player_tags pt
                                      JOIN tags t ON t.id = pt.tag_id WHERE pt.steam_id IN ({players_sql})
                                      ORDER BY t.name""", params):
                tags[r["steam_id"]].append([r["name"], r["color"]])
            for r in conn.execute(f"SELECT p.steam_id, p.notes, p.discord_id FROM players p{where}", params):
                if r["notes"] or r["discord_id"] or r["steam_id"] in tags:
                    w.add_player(r["steam_id"], r["notes"], tags.get(r["steam_id"], []), r["discord_id"])
        w.close()
        yield buf.getvalue()

    return StreamingResponse(generate(), media_type="application/gzip",
                             headers={"Content-Disposition": f'attachment; filename="fragdex-{db.now()[:10]}.fdx"'})


# ---------------------------------------------------------------- player detail

@app.get("/player/{sid}", response_class=HTMLResponse)
def player(request: Request, sid: str):
    user = require(request)
    valid_sid(sid)
    with db.get_db() as conn:
        p = conn.execute("SELECT * FROM players WHERE steam_id=?", (sid,)).fetchone()
        if not p:
            raise NotFound()
        p = dict(p)
        names = [dict(r) for r in conn.execute("""
            SELECT name, COUNT(*) n, MIN(NULLIF(seen_at,'')) first, MAX(NULLIF(seen_at,'')) last,
                   group_concat(DISTINCT source) sources
            FROM sightings WHERE steam_id=? AND name != '' GROUP BY name ORDER BY last DESC, MAX(id) DESC""", (sid,))]
        ips = [dict(r) for r in conn.execute("""
            SELECT s.ip, COUNT(*) n, MIN(NULLIF(s.seen_at,'')) first, MAX(NULLIF(s.seen_at,'')) last,
                   group_concat(DISTINCT s.source) sources,
                   (SELECT COUNT(DISTINCT steam_id) FROM sightings o WHERE o.ip = s.ip AND o.steam_id != s.steam_id)
                       others
            FROM sightings s WHERE s.steam_id=? AND s.ip != '' GROUP BY s.ip
            ORDER BY last DESC, MAX(s.id) DESC""", (sid,))]
        sightings = [dict(r) for r in conn.execute("""
            SELECT * FROM sightings WHERE steam_id=? ORDER BY seen_at DESC, id DESC LIMIT 500""", (sid,))]
        sighting_total = conn.execute("SELECT COUNT(*) FROM sightings WHERE steam_id=?", (sid,)).fetchone()[0]
        tags = [dict(r) for r in conn.execute("""
            SELECT t.*, EXISTS(SELECT 1 FROM player_tags pt WHERE pt.tag_id=t.id AND pt.steam_id=?) AS on_player
            FROM tags t ORDER BY name""", (sid,))]
        linked = db.linked_accounts(conn, sid)
        steam_enabled = bool(db.steam_api_key(conn))
    return render(request, "player.html", user, nav="players", p=p, names=names, ips=ips, sightings=sightings,
                  sighting_total=sighting_total, tags=tags, linked=linked, steam_enabled=steam_enabled,
                  alt_limit=config.ALT_IP_MAX_ACCOUNTS)


@app.post("/player/{sid}/notes")
async def player_notes(request: Request, sid: str):
    require(request, "editor")
    valid_sid(sid)
    form = await checked_form(request)
    with db.get_db() as conn:
        conn.execute("UPDATE players SET notes=?, updated_at=? WHERE steam_id=?",
                     (str(form.get("notes", ""))[:20000], db.now(), sid))
    flash(request, "Notes saved.")
    return back(f"/player/{sid}")


@app.post("/player/{sid}/discord")
async def player_discord(request: Request, sid: str):
    require(request, "editor")
    valid_sid(sid)
    form = await checked_form(request)
    raw = str(form.get("discord_id", "")).strip()
    discord = normalize_discord_id(raw) if raw else ""
    if discord is None:
        flash(request, f"'{raw[:40]}' is not a Discord user ID (17-20 digits) or username.", "error")
        return back(f"/player/{sid}")
    with db.get_db() as conn:
        conn.execute("UPDATE players SET discord_id=?, updated_at=? WHERE steam_id=?", (discord, db.now(), sid))
    flash(request, "Discord ID saved." if discord else "Discord ID removed.")
    return back(f"/player/{sid}")


@app.post("/player/{sid}/tags")
async def player_tags(request: Request, sid: str):
    require(request, "editor")
    valid_sid(sid)
    form = await checked_form(request)
    wanted = {int(t) for t in form.getlist("tag") if str(t).isdigit()}
    new_tag = str(form.get("new_tag", "")).strip()[:40]
    with db.get_db() as conn, db.transaction(conn):
        if new_tag:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (new_tag,))
            wanted.add(conn.execute("SELECT id FROM tags WHERE name=?", (new_tag,)).fetchone()[0])
        conn.execute("DELETE FROM player_tags WHERE steam_id=?", (sid,))
        conn.executemany("INSERT OR IGNORE INTO player_tags(steam_id, tag_id) SELECT ?, id FROM tags WHERE id=?",
                         [(sid, t) for t in wanted])
    flash(request, "Tags updated.")
    return back(f"/player/{sid}")


@app.post("/player/{sid}/sightings")
async def player_add_sighting(request: Request, sid: str):
    user = require(request, "editor")
    valid_sid(sid)
    form = await checked_form(request)
    return _manual_add(request, user, sid, form, f"/player/{sid}")


@app.post("/sightings/{sighting_id}/delete")
async def sighting_delete(request: Request, sighting_id: int):
    require(request, "editor")
    await checked_form(request)
    with db.get_db() as conn, db.transaction(conn):
        row = conn.execute("SELECT steam_id FROM sightings WHERE id=?", (sighting_id,)).fetchone()
        if not row:
            raise NotFound()
        conn.execute("DELETE FROM sightings WHERE id=?", (sighting_id,))
        db.refresh_players(conn, [row["steam_id"]])
        db.recompute_alts(conn)
    flash(request, "Record deleted.")
    return back(f"/player/{row['steam_id']}")


@app.post("/player/{sid}/steam")
async def player_steam(request: Request, sid: str):
    require(request, "editor")
    valid_sid(sid)
    await checked_form(request)
    try:
        with db.get_db() as conn:
            await run_in_threadpool(steam.refresh, conn, [sid])
        flash(request, "Steam data refreshed.")
    except Exception as e:  # noqa: BLE001
        flash(request, f"Steam refresh failed: {e}", "error")
    return back(f"/player/{sid}")


@app.post("/player/{sid}/delete")
async def player_delete(request: Request, sid: str):
    require(request, "admin")
    valid_sid(sid)
    await checked_form(request)
    with db.get_db() as conn, db.transaction(conn):
        conn.execute("DELETE FROM players WHERE steam_id=?", (sid,))
        db.recompute_alts(conn)
    flash(request, f"Deleted player {sid} and all their records.")
    return back("/")


# ---------------------------------------------------------------- manual add

def _manual_add(request, user, sid, form, fail_url):
    name = normalize_name(form.get("name"))
    ip_raw = str(form.get("ip", "")).strip()
    ip = normalize_ip(ip_raw) if ip_raw else ""
    if ip is None:
        flash(request, f"'{ip_raw}' is not a valid IP address.", "error")
        return back(fail_url)
    if ip and is_local_ip(ip):
        flash(request, f"{ip} is a local address (the server itself), not a player's IP, so it isn't recorded.",
              "error")
        return back(fail_url)
    discord_raw = str(form.get("discord_id", "")).strip()
    discord = normalize_discord_id(discord_raw) if discord_raw else ""
    if discord is None:
        flash(request, f"'{discord_raw[:40]}' is not a Discord user ID (17-20 digits) or username.", "error")
        return back(fail_url)
    seen = parse_date(str(form.get("seen_at", "")).replace("T", " ")) if form.get("seen_at") else ""
    if not name and not ip and not discord:
        flash(request, "Enter at least a name, an IP address or a Discord ID." if "discord_id" in form
              else "Enter at least a name or an IP address.", "error")
        return back(fail_url)
    added = 0
    with db.get_db() as conn, db.transaction(conn):
        if name or ip:
            added, _ = db.add_sightings(conn, [Sighting(steam_id=sid, name=name, ip=ip, seen_at=seen)], "manual",
                                        f"added by {user['username']}")
        else:
            ts = db.now()
            conn.execute("INSERT OR IGNORE INTO players(steam_id, created_at, updated_at) VALUES(?,?,?)", (sid, ts, ts))
        if discord:
            conn.execute("UPDATE players SET discord_id=? WHERE steam_id=?", (discord, sid))
        notes = str(form.get("notes", "")).strip()
        if notes:
            conn.execute("UPDATE players SET notes = CASE WHEN notes = '' THEN ? ELSE notes || char(10) || ? END "
                         "WHERE steam_id=?", (notes, notes, sid))
        db.refresh_players(conn, [sid])
        db.recompute_alts(conn)
    if name or ip:
        msg = "Record added." if added else "That exact record already exists."
        flash(request, msg[:-1] + "; Discord ID saved." if discord else msg)
    else:
        flash(request, "Discord ID saved.")
    return back(f"/player/{sid}")


@app.get("/add", response_class=HTMLResponse)
def add_page(request: Request):
    user = require(request, "editor")
    return render(request, "add.html", user, nav="add")


@app.post("/add")
async def add_player(request: Request):
    user = require(request, "editor")
    form = await checked_form(request)
    sid = normalize_steam_id(form.get("steam_id"))
    if not sid:
        flash(request, "Enter a valid Steam ID (SteamID64, STEAM_0:X:Y, [U:1:Z] or a steamcommunity.com/profiles "
                       "link).", "error")
        return back("/add")
    return _manual_add(request, user, sid, form, "/add")


# ---------------------------------------------------------------- imports

def _save_upload(upload, user):
    """Store one uploaded file (or the members of a zip) and queue imports. Returns list of messages."""
    filename = os.path.basename((upload.filename or "upload").replace("\\", "/"))[:200]
    kind = file_kind(filename)
    if kind is None:
        return [f"Skipped {filename}: unsupported file type"]
    dest = os.path.join(config.UPLOAD_DIR, f"{uuid.uuid4().hex}_{re.sub(r'[^A-Za-z0-9._-]', '_', filename)}")
    with open(dest, "wb") as out:
        shutil.copyfileobj(upload.file, out, 1 << 20)
    msgs = []
    with db.get_db() as conn:
        if kind != "zip":
            worker.enqueue(conn, kind, filename, dest, "upload", user["username"], worker.sha256_file(dest))
            return msgs
        try:
            with zipfile.ZipFile(dest) as z:
                total = count = 0
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    base = os.path.basename(info.filename.replace("\\", "/"))
                    mkind = file_kind(base)
                    if mkind not in ("demo", "log", "sheet", "export"):
                        continue
                    total += info.file_size
                    count += 1
                    if total > MAX_ZIP_BYTES or count > MAX_ZIP_MEMBERS:
                        msgs.append(f"{filename}: stopped extracting, archive too large")
                        break
                    mdest = os.path.join(config.UPLOAD_DIR,
                                         f"{uuid.uuid4().hex}_{re.sub(r'[^A-Za-z0-9._-]', '_', base)}")
                    with z.open(info) as src, open(mdest, "wb") as out:
                        shutil.copyfileobj(src, out, 1 << 20)
                    worker.enqueue(conn, mkind, f"{filename}/{base}"[:250], mdest, "upload", user["username"],
                                   worker.sha256_file(mdest))
                if count == 0:
                    msgs.append(f"{filename}: no demos, logs, sheets or .fdx exports inside")
        except zipfile.BadZipFile:
            msgs.append(f"{filename}: not a valid zip file")
        finally:
            os.remove(dest)
    return msgs


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    user = require(request, "editor")
    with db.get_db() as conn:
        jobs = [dict(r) for r in conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 200")]
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM imports GROUP BY status")}
        watched = conn.execute("SELECT COUNT(*) FROM watch_files").fetchone()[0]
        cleanup = [dict(id=k, title=t["title"], desc=t["desc"], unit=t["unit"], count=t["count"](conn))
                   for k, t in CLEANUP_TOOLS.items()]
    return render(request, "import.html", user, nav="import", jobs=jobs, counts=counts, cleanup=cleanup,
                  active=counts.get("queued", 0) + counts.get("running", 0), watch_dir=config.WATCH_DIR,
                  watch_exists=os.path.isdir(config.WATCH_DIR), watched=watched, wstatus=worker.status,
                  max_mb=config.MAX_UPLOAD_MB, scanner_exe=os.path.isfile(SCANNER_EXE))


SCANNER_EXE = os.path.join(HERE, "downloads", "fragdex-scan.exe")


@app.get("/download/fragdex-scan.py")
def download_scanner_py(request: Request):
    require(request, "editor")
    return Response(build_script(), media_type="text/x-python; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="fragdex-scan.py"'})


@app.get("/download/fragdex-scan.exe")
def download_scanner_exe(request: Request):
    require(request, "editor")
    if not os.path.isfile(SCANNER_EXE):
        raise NotFound()
    return FileResponse(SCANNER_EXE, media_type="application/vnd.microsoft.portable-executable",
                        filename="fragdex-scan.exe")


@app.get("/import/status.json")
def import_status(request: Request):
    require(request, "editor")
    with db.get_db() as conn:
        active = conn.execute("SELECT COUNT(*) FROM imports WHERE status IN ('queued','running')").fetchone()[0]
        last = conn.execute("SELECT MAX(id) FROM imports").fetchone()[0]
    return JSONResponse({"active": active, "last": last})


@app.post("/import")
async def import_upload(request: Request):
    user = require(request, "editor")
    form = await checked_form(request, max_files=10000, max_fields=50)
    files = [f for f in form.getlist("files") if hasattr(f, "filename") and f.filename]
    if not files:
        flash(request, "Choose at least one file.", "error")
        return back("/import")
    msgs = []
    for upload in files:
        msgs += await run_in_threadpool(_save_upload, upload, user)
        await upload.close()
    for m in msgs[:20]:
        flash(request, m, "error")
    flash(request, f"Queued {len(files)} file(s) for import.")
    return back("/import")


@app.post("/import/{job_id}/retry")
async def import_retry(request: Request, job_id: int):
    require(request, "editor")
    await checked_form(request)
    with db.get_db() as conn:
        job = conn.execute("SELECT * FROM imports WHERE id=?", (job_id,)).fetchone()
        if job and job["status"] == "error" and job["stored_path"] and os.path.exists(job["stored_path"]):
            conn.execute("UPDATE imports SET status='queued', message='' WHERE id=?", (job_id,))
            worker.wake()
            flash(request, f"Import #{job_id} queued again.")
        else:
            flash(request, "That import can't be retried (file no longer available).", "error")
    return back("/import")


@app.post("/import/clear")
async def import_clear(request: Request):
    require(request, "editor")
    await checked_form(request)
    with db.get_db() as conn:
        n = conn.execute("DELETE FROM imports WHERE status IN ('done','duplicate')").rowcount
    flash(request, f"Cleared {n} finished entries from the history (imported data is kept).")
    return back("/import")


# Database cleanup tools on the Import page: count() says how much there is to fix, run() fixes it and
# returns (changed, players affected). Add new tools here; the page lists them in this order.
CLEANUP_TOOLS = {
    "local-ips": {
        "title": "Remove local IP addresses",
        "desc": "127.0.0.1 and ::1 are the server itself (or a proxy), not a player's address, and linked every "
                "player seen through it. New imports skip them; this removes them from records imported before.",
        "unit": "record",
        "count": db.count_local_ips,
        "run": db.remove_local_ips,
    },
}


@app.post("/import/cleanup/{tool}")
async def import_cleanup(request: Request, tool: str):
    require(request, "editor")
    await checked_form(request)
    t = CLEANUP_TOOLS.get(tool)
    if not t:
        raise NotFound()
    with db.get_db() as conn, db.transaction(conn):
        changed, players = t["run"](conn)
    flash(request, f"{t['title']}: fixed {changed:,} {t['unit']}{'s' if changed != 1 else ''} "
                   f"of {players:,} player{'s' if players != 1 else ''}." if changed else
                   f"{t['title']}: nothing to fix.")
    return back("/import")


# Every column the spreadsheet importer reads, showing each accepted format once
SAMPLE_ROWS = [
    ["Player Name", "Steam ID", "Discord ID", "Aliases", "IP Address", "Date"],
    ["^1Rail^7gun", "76561198000000001", "80351110224678912", "railgod, ^1R^7ailgun", "203.0.113.45",
     "2026-10-29 19:27:35"],
    ["Frostbite", "STEAM_0:1:19867137", "frostbite", "frosty", "198.51.100.7 198.51.100.8", "2026-10-28"],
    ["^3Halcyon", "[U:1:39734279]", "", "", "192.0.2.14", ""],
    ["Wraith", "https://steamcommunity.com/profiles/76561198000000017", "", "wr4ith | ^4wraith^7.cfg", "",
     "2026-09-14 21:05"],
]


@app.get("/download/sample-import.csv")
def sample_csv(request: Request):
    """A spreadsheet to start from: UTF-8 with a BOM so Excel keeps the colour-coded names intact."""
    require(request, "editor")
    buf = io.StringIO()
    csv.writer(buf).writerows(SAMPLE_ROWS)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="fragdex-import-sample.csv"'})


# ---------------------------------------------------------------- tags

@app.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request):
    user = require(request, "editor")
    with db.get_db() as conn:
        tags = [dict(r) for r in conn.execute("""
            SELECT t.*, (SELECT COUNT(*) FROM player_tags pt WHERE pt.tag_id = t.id) n FROM tags t ORDER BY name""")]
    return render(request, "tags.html", user, nav="tags", tags=tags)


@app.post("/tags")
async def tag_create(request: Request):
    require(request, "editor")
    form = await checked_form(request)
    name = str(form.get("name", "")).strip()[:40]
    color = str(form.get("color", "#e8562a"))
    if not name:
        flash(request, "Tag name is required.", "error")
        return back("/tags")
    with db.get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO tags(name, color) VALUES(?,?)",
                     (name, color if COLOR_RE.match(color) else "#e8562a"))
    return back("/tags")


@app.post("/tags/{tag_id}")
async def tag_update(request: Request, tag_id: int):
    require(request, "editor")
    form = await checked_form(request)
    name = str(form.get("name", "")).strip()[:40]
    color = str(form.get("color", ""))
    with db.get_db() as conn:
        if name:
            try:
                conn.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))
            except Exception:  # noqa: BLE001 - unique name clash
                flash(request, f"A tag named '{name}' already exists.", "error")
        if COLOR_RE.match(color):
            conn.execute("UPDATE tags SET color=? WHERE id=?", (color, tag_id))
    return back("/tags")


@app.post("/tags/{tag_id}/delete")
async def tag_delete(request: Request, tag_id: int):
    require(request, "editor")
    await checked_form(request)
    with db.get_db() as conn:
        conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    flash(request, "Tag deleted.")
    return back("/tags")


# ---------------------------------------------------------------- admin

@app.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request):
    user = require(request, "admin")
    with db.get_db() as conn:
        users = [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY username")]
    return render(request, "users.html", user, nav="users", users=users, roles=auth.ROLES)


@app.post("/admin/users")
async def user_create(request: Request):
    require(request, "admin")
    form = await checked_form(request)
    username = str(form.get("username", "")).strip()
    role = str(form.get("role", "viewer"))
    pw = str(form.get("password", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{2,40}", username):
        flash(request, "Username must be 2-40 characters: letters, numbers, _ . @ -", "error")
        return back("/admin/users")
    problem = auth.password_problem(pw)
    if problem or role not in auth.ROLES:
        flash(request, problem or "Invalid role.", "error")
        return back("/admin/users")
    with db.get_db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            flash(request, f"User '{username}' already exists.", "error")
            return back("/admin/users")
        conn.execute("INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)",
                     (username, auth.hash_password(pw), role, db.now()))
    flash(request, f"Created {role} '{username}'.")
    return back("/admin/users")


def _other_active_admins(conn, uid):
    return conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0 AND id != ?",
                        (uid,)).fetchone()[0]


@app.post("/admin/users/{uid}")
async def user_update(request: Request, uid: int):
    me = require(request, "admin")
    form = await checked_form(request)
    role = str(form.get("role", ""))
    disabled = 1 if form.get("disabled") == "1" else 0
    pw = str(form.get("password", ""))
    with db.get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise NotFound()
        if role in auth.ROLES and role != target["role"]:
            if target["role"] == "admin" and not _other_active_admins(conn, uid):
                flash(request, "You can't demote the last admin.", "error")
            else:
                conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
        if disabled != target["disabled"]:
            if uid == me["id"]:
                flash(request, "You can't disable your own account.", "error")
            elif disabled and target["role"] == "admin" and not _other_active_admins(conn, uid):
                flash(request, "You can't disable the last admin.", "error")
            else:
                conn.execute("UPDATE users SET disabled=? WHERE id=?", (disabled, uid))
        if pw:
            problem = auth.password_problem(pw)
            if problem:
                flash(request, problem, "error")
            else:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(pw), uid))
                flash(request, f"Password reset for '{target['username']}'.")
    return back("/admin/users")


@app.post("/admin/users/{uid}/delete")
async def user_delete(request: Request, uid: int):
    me = require(request, "admin")
    await checked_form(request)
    with db.get_db() as conn:
        target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not target:
            raise NotFound()
        if uid == me["id"]:
            flash(request, "You can't delete your own account.", "error")
        elif target["role"] == "admin" and not _other_active_admins(conn, uid):
            flash(request, "You can't delete the last admin.", "error")
        else:
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
            flash(request, f"Deleted '{target['username']}'.")
    return back("/admin/users")


@app.get("/admin/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    user = require(request, "admin")
    return _settings(request, user)


def _settings(request, user, new_key=None):
    """The Settings page. new_key = {"name", "key"} shows a just-created key, once: it is never stored."""
    with db.get_db() as conn:
        key = db.steam_api_key(conn)
        info = {
            "players": conn.execute("SELECT COUNT(*) FROM players").fetchone()[0],
            "sightings": conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0],
            "steam_error": db.get_setting(conn, "steam_last_error"),
        }
        steam_status = _steam_status(conn)
        apikeys.usage.flush(conn)
        keys = [dict(r, state=apikeys.state(r)) for r in conn.execute("SELECT * FROM api_keys ORDER BY name, id")]
    try:
        info["db_mb"] = round(os.path.getsize(config.DB_PATH) / 1048576, 1)
    except OSError:
        info["db_mb"] = 0
    return render(request, "settings.html", user, nav="settings", key_set=bool(key),
                  key_from_env=bool(config.STEAM_API_KEY_ENV), key_hint=(key[:4] + "…" + key[-4:]) if key else "",
                  info=info, cfg=config, wstatus=worker.status, sp=steam_status, api_keys=keys, new_key=new_key,
                  api_url=str(request.base_url).rstrip("/") + api.router.prefix, default_rate=apikeys.DEFAULT_RATE,
                  max_rate=apikeys.MAX_RATE, plugin=os.path.isfile(MINQLX_PLUGIN))


def _api_key_form(form):
    """The editable settings of an API key from the Settings form. Returns (values, error)."""
    name, err = apikeys.parse_name(form.get("name"))
    allowed, err2 = apikeys.parse_allowed_ips(form.get("allowed_ips"))
    rate, err3 = apikeys.parse_rate(form.get("rate_limit"))
    expires, err4 = apikeys.parse_expiry(form.get("expires_at"))
    error = err or err2 or err3 or err4
    if error:
        return None, error
    return {"name": name, "allowed_ips": allowed, "rate_limit": rate, "expires_at": expires,
            "can_ips": int(form.get("can_ips") == "1"), "can_notes": int(form.get("can_notes") == "1"),
            "can_write": int(form.get("can_write") == "1")}, None


def _api_key_row(conn, key_id):
    row = conn.execute("SELECT * FROM api_keys WHERE id=?", (key_id,)).fetchone()
    if not row:
        raise NotFound()
    return row


@app.post("/admin/api-keys", response_class=HTMLResponse)
async def api_key_create(request: Request):
    user = require(request, "admin")
    form = await checked_form(request)
    values, error = _api_key_form(form)
    if error:
        flash(request, error, "error")
        return back("/admin/settings#api")
    key, key_hash, prefix = apikeys.new_key()
    with db.get_db() as conn:
        conn.execute("""INSERT INTO api_keys(name, prefix, key_hash, can_ips, can_notes, can_write, allowed_ips,
                        rate_limit, expires_at, created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                     (values["name"], prefix, key_hash, values["can_ips"], values["can_notes"], values["can_write"],
                      values["allowed_ips"], values["rate_limit"], values["expires_at"], user["username"], db.now()))
    # rendered straight away rather than redirected, so the key never passes through the session cookie
    return _settings(request, user, new_key={"name": values["name"], "key": key})


@app.post("/admin/api-keys/{key_id}")
async def api_key_update(request: Request, key_id: int):
    require(request, "admin")
    form = await checked_form(request)
    values, error = _api_key_form(form)
    with db.get_db() as conn:
        _api_key_row(conn, key_id)
        if error:
            flash(request, error, "error")
            return back("/admin/settings#api")
        conn.execute("""UPDATE api_keys SET name=?, can_ips=?, can_notes=?, can_write=?, allowed_ips=?, rate_limit=?,
                        expires_at=? WHERE id=?""",
                     (values["name"], values["can_ips"], values["can_notes"], values["can_write"],
                      values["allowed_ips"], values["rate_limit"], values["expires_at"], key_id))
    apikeys.limiter.reset(key_id)
    flash(request, f"Saved the settings of API key '{values['name']}'. They apply from its next request.")
    return back("/admin/settings#api")


@app.post("/admin/api-keys/{key_id}/rotate", response_class=HTMLResponse)
async def api_key_rotate(request: Request, key_id: int):
    user = require(request, "admin")
    await checked_form(request)
    key, key_hash, prefix = apikeys.new_key()
    with db.get_db() as conn:
        row = _api_key_row(conn, key_id)
        conn.execute("UPDATE api_keys SET key_hash=?, prefix=? WHERE id=?", (key_hash, prefix, key_id))
    return _settings(request, user, new_key={"name": row["name"], "key": key, "rotated": True})


@app.post("/admin/api-keys/{key_id}/toggle")
async def api_key_toggle(request: Request, key_id: int):
    require(request, "admin")
    await checked_form(request)
    with db.get_db() as conn:
        row = _api_key_row(conn, key_id)
        conn.execute("UPDATE api_keys SET disabled=? WHERE id=?", (0 if row["disabled"] else 1, key_id))
    flash(request, f"API key '{row['name']}' {'enabled' if row['disabled'] else 'disabled'}.")
    return back("/admin/settings#api")


@app.post("/admin/api-keys/{key_id}/delete")
async def api_key_delete(request: Request, key_id: int):
    require(request, "admin")
    await checked_form(request)
    with db.get_db() as conn:
        row = _api_key_row(conn, key_id)
        conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    flash(request, f"Deleted API key '{row['name']}'. Apps using it can no longer connect.")
    return back("/admin/settings#api")


MINQLX_PLUGIN = os.path.join(HERE, "downloads", "minqlx", "fragdex.py")


@app.get("/download/minqlx/fragdex.py")
def download_minqlx_plugin(request: Request):
    require(request, "admin")
    if not os.path.isfile(MINQLX_PLUGIN):
        raise NotFound()
    return FileResponse(MINQLX_PLUGIN, media_type="text/x-python; charset=utf-8", filename="fragdex.py")


def _steam_status(conn):
    """Progress of the background Steam (avatar / ban) refresh, for the Settings page."""
    st = worker.status
    p = steam.progress(conn)
    state = st["steam_state"] if db.steam_api_key(conn) else "nokey"
    if state == "running" and not p["pending"]:
        state = "idle"
    eta = None
    if state == "running" and st["steam_batch_secs"]:
        eta = int(math.ceil(p["pending"] / 100) * st["steam_batch_secs"])
    next_in = None
    if st["steam_next_run"] and state in ("idle", "error"):
        next_in = max(0, int(st["steam_next_run"] - time.time()))
    p.update(state=state, eta=eta, next_in=next_in, last_run=st["steam_last_run"],
             percent=round(100 * p["done"] / p["total"], 1) if p["total"] else 100.0,
             error=db.get_setting(conn, "steam_last_error") if state == "error" else "")
    return p


@app.get("/admin/settings/steam.json")
def settings_steam_status(request: Request):
    require(request, "admin")
    with db.get_db() as conn:
        return JSONResponse(_steam_status(conn))


@app.post("/admin/settings")
async def settings_save(request: Request):
    require(request, "admin")
    form = await checked_form(request)
    action = form.get("action")
    with db.get_db() as conn:
        if action == "steam_key" and not config.STEAM_API_KEY_ENV:
            key = str(form.get("steam_api_key", "")).strip()
            if key and not re.fullmatch(r"[A-Fa-f0-9]{32}", key):
                flash(request, "A Steam Web API key is 32 hexadecimal characters.", "error")
                return back("/admin/settings")
            db.set_setting(conn, "steam_api_key", key)
            worker.wake_steam()
            flash(request, "Steam API key saved." if key else "Steam API key removed.")
        elif action == "steam_refresh_all":
            conn.execute("UPDATE players SET steam_checked_at=NULL")
            worker.wake_steam()
            flash(request, "All players will be refreshed from Steam in the background.")
        elif action == "recompute_alts":
            with db.transaction(conn):
                db.refresh_players(conn, [r[0] for r in conn.execute("SELECT steam_id FROM players")])
                db.recompute_alts(conn)
            flash(request, "Recalculated player summaries and linked accounts.")
    return back("/admin/settings")
