"""End-to-end test of the web app: login, import, search, detail, export, roles."""
import os
import re
import tempfile
import time

import pytest

_tmp = tempfile.mkdtemp(prefix="fragdex-test-")
os.environ.update(DATA_DIR=_tmp, WATCH_DIR=os.path.join(_tmp, "watch"), TLS_MODE="off",
                  ADMIN_USERNAME="admin", ADMIN_PASSWORD="test-password-123", WATCH_INTERVAL="10")

from fastapi.testclient import TestClient  # noqa: E402

from fragdex import db  # noqa: E402
from fragdex.main import app  # noqa: E402

CHATLOG = (
    "[2026-10-29 19:27:35] Railgun:76561197960287930:203.0.113.45 connected.\n"
    "[2026-10-29 19:30:00] ^1Alt^7Guy:76561198000000001:203.0.113.45 connected.\n"
    "[2026-10-29 19:31:00] Railgun: gg\n"
)


def csrf_of(html):
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def wait_imports(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        with db.get_db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM imports WHERE status IN ('queued','running')").fetchone()[0]
        if n == 0:
            time.sleep(0.5)  # let the worker recompute linked accounts
            return
        time.sleep(0.2)
    raise AssertionError("imports did not finish")


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://testserver") as c:
        yield c


def login(c, user="admin", pw="test-password-123"):
    r = c.get("/login")
    return c.post("/login", data={"csrf": csrf_of(r.text), "username": user, "password": pw, "next": "/"},
                  follow_redirects=False)


def test_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_bad_login(client):
    r = login(client, pw="wrong")
    assert r.status_code == 401


def test_csrf_enforced(client):
    r = client.post("/login", data={"csrf": "nope", "username": "admin", "password": "test-password-123"})
    assert r.status_code == 403


def test_full_flow(client):
    assert login(client).status_code == 303
    page = client.get("/import")
    assert page.status_code == 200
    token = csrf_of(page.text)

    files = [("files", ("chat.log", CHATLOG.encode(), "text/plain"))]
    xlsx = os.environ.get("PLAYERS_XLSX", "")
    if xlsx and os.path.exists(xlsx):
        with open(xlsx, "rb") as f:
            files.append(("files", ("Players.xlsx", f.read(), "application/octet-stream")))
    r = client.post("/import", data={"csrf": token}, files=files)
    assert r.status_code == 200
    wait_imports()

    with db.get_db() as conn:
        jobs = [dict(j) for j in conn.execute("SELECT * FROM imports ORDER BY id")]
    assert all(j["status"] == "done" for j in jobs), jobs

    # list + search by name, alias, IP and CIDR
    r = client.get("/?q=railgun")
    assert "76561197960287930" in r.text
    r = client.get("/?q=203.0.113.0/24")
    assert "76561197960287930" in r.text and "76561198000000001" in r.text
    r = client.get("/?q=altguy")
    assert "76561198000000001" in r.text and "qc1" in r.text  # colour-coded name rendered
    r = client.get("/?has_alts=1")
    assert "76561197960287930" in r.text

    # player detail shows linked account
    r = client.get("/player/76561197960287930")
    assert r.status_code == 200
    assert "steamcommunity.com/profiles/76561197960287930" in r.text
    assert "76561198000000001" in r.text  # linked via shared IP

    # notes + tags
    token = csrf_of(r.text)
    client.post("/player/76561197960287930/notes", data={"csrf": token, "notes": "test note"})
    client.post("/player/76561197960287930/tags", data={"csrf": token, "new_tag": "admin"})
    r = client.get("/player/76561197960287930")
    assert "test note" in r.text and ">admin</span>" in r.text

    # manual add with STEAM_X id
    r = client.post("/add", data={"csrf": token, "steam_id": "STEAM_0:1:1", "name": "Manual", "ip": "9.9.9.9"},
                    follow_redirects=False)
    assert r.headers["location"] == "/player/76561197960265731"

    # csv export
    r = client.get("/export.csv?q=railgun")
    assert r.status_code == 200 and "76561197960287930" in r.text and "test note" in r.text

    if os.path.exists(xlsx):
        with db.get_db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        assert n >= 861


def test_fdx_import_and_dedupe(client, tmp_path):
    from fragdex.scanner import run
    from test_importers import _demo_bytes

    demos = tmp_path / "demos"
    demos.mkdir()
    (demos / "match.dm_91").write_bytes(_demo_bytes())
    out = tmp_path / "scan.fdx"
    summary = run([str(demos)], str(out), jobs=1, quiet=True)
    assert summary["players"] == 2

    token = csrf_of(client.get("/import").text)
    client.post("/import", data={"csrf": token}, files=[("files", ("scan.fdx", out.read_bytes(), "application/gzip"))])
    wait_imports()
    with db.get_db() as conn:
        job = dict(conn.execute("SELECT * FROM imports WHERE kind='export' ORDER BY id DESC").fetchone())
        assert job["status"] == "done", job
        assert "1 imported" in job["message"]
        assert conn.execute("SELECT COUNT(*) FROM sightings WHERE source_ref='match.dm_91'").fetchone()[0] == 3

    # the same demo uploaded directly is recognised as already imported
    client.post("/import", data={"csrf": token},
                files=[("files", ("renamed.dm_91", _demo_bytes(), "application/octet-stream"))])
    wait_imports()
    with db.get_db() as conn:
        job = dict(conn.execute("SELECT * FROM imports ORDER BY id DESC").fetchone())
    assert job["status"] == "duplicate" and "Already imported" in job["message"]

    # scanner downloads
    r = client.get("/download/fragdex-scan.py")
    assert r.status_code == 200 and "def main(" in r.text and "from ." not in r.text
    assert "Desktop scanner" in client.get("/import").text


def test_fdx_database_export_roundtrip(client, tmp_path):
    from fragdex.importers.export import read_export

    sid = "76561197960287930"
    r = client.get("/export.fdx")
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    backup = tmp_path / "backup.fdx"
    backup.write_bytes(r.content)
    entries = list(read_export(str(backup)))
    assert any(e["kind"] == "log" and any(s.ip == "203.0.113.45" for s in e["sightings"]) for e in entries)
    assert {"kind": "player", "steam_id": sid, "notes": "test note", "tags": [("admin", "#e8562a")],
            "discord_id": ""} in entries

    # filters apply to the export too
    only = tmp_path / "only.fdx"
    only.write_bytes(client.get("/export.fdx?q=altguy").content)
    assert {s.steam_id for e in read_export(str(only)) if e["kind"] != "player" for s in e["sightings"]} \
        == {"76561198000000001"}

    with db.get_db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM sightings WHERE steam_id=?", (sid,)).fetchone()[0]
    token = csrf_of(client.get(f"/player/{sid}").text)
    client.post(f"/player/{sid}/delete", data={"csrf": token})
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM players WHERE steam_id=?", (sid,)).fetchone()[0] == 0

    client.post("/import", data={"csrf": token}, files=[("files", ("backup.fdx", r.content, "application/gzip"))])
    wait_imports()
    with db.get_db() as conn:
        job = dict(conn.execute("SELECT * FROM imports ORDER BY id DESC").fetchone())
        assert job["status"] == "done" and "notes/tags" in job["message"], job
        assert conn.execute("SELECT COUNT(*) FROM sightings WHERE steam_id=?", (sid,)).fetchone()[0] == before
        p = conn.execute("SELECT * FROM players WHERE steam_id=?", (sid,)).fetchone()
        assert p["notes"] == "test note" and p["last_ip"] == "203.0.113.45"
        assert conn.execute("SELECT COUNT(*) FROM player_tags pt JOIN tags t ON t.id=pt.tag_id "
                            "WHERE pt.steam_id=? AND t.name='admin'", (sid,)).fetchone()[0] == 1


def test_alias_sort_and_steam_status(client):
    r = client.get("/")
    assert 'class="th-toggle"' in r.text and 'aria-pressed="false"' in r.text
    r = client.get("/?sort=aliases&dir=desc")
    assert r.status_code == 200 and 'aria-pressed="true"' in r.text
    s = client.get("/admin/settings/steam.json").json()
    assert s["state"] == "nokey" and s["total"] > 0 and s["done"] + s["pending"] == s["total"]
    assert "data-steam-progress" in client.get("/admin/settings").text


def test_discord_id(client, tmp_path):
    from fragdex.importers.export import read_export

    sid = "76561198000000042"
    r = client.get("/add")
    token = csrf_of(r.text)
    assert 'name="discord_id"' in r.text
    r = client.post("/add", data={"csrf": token, "steam_id": sid, "name": "^2Dc^7Guy",
                                  "discord_id": "<@80351110224678912>"}, follow_redirects=True)
    assert "Record added; Discord ID saved." in r.text
    with db.get_db() as conn:
        assert conn.execute("SELECT discord_id FROM players WHERE steam_id=?", (sid,)).fetchone()[0] \
            == "80351110224678912"
    # column between Steam ID and IP Address, linked to the Discord profile; searchable
    r = client.get("/")
    heads = re.findall(r"<th[^>]*>(?:<a[^>]*>)?([A-Za-z ]+)", r.text)
    assert heads[heads.index("Steam ID") + 1] == "Discord" and heads[heads.index("Discord") + 1] == "IP Address"
    assert "https://discord.com/users/80351110224678912" in r.text
    assert sid in client.get("/?q=80351110224678912").text
    # a Discord ID alone updates an existing player; bad values are refused
    client.post("/add", data={"csrf": token, "steam_id": sid, "discord_id": "@Frost.Bite"})
    r = client.post(f"/player/{sid}/discord", data={"csrf": token, "discord_id": "not valid!"}, follow_redirects=True)
    assert "is not a Discord user ID" in r.text
    with db.get_db() as conn:
        assert conn.execute("SELECT discord_id FROM players WHERE steam_id=?", (sid,)).fetchone()[0] == "frost.bite"
    # exports keep it
    assert "discord_id" in client.get("/export.csv").text.splitlines()[0]
    fdx = tmp_path / "d.fdx"
    fdx.write_bytes(client.get(f"/export.fdx?q={sid}").content)
    assert any(e.get("discord_id") == "frost.bite" for e in read_export(str(fdx)) if e["kind"] == "player")
    client.post(f"/player/{sid}/discord", data={"csrf": token, "discord_id": ""})
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM players WHERE discord_id != ''").fetchone()[0] == 0


def test_local_ips_and_cleanup(client):
    token = csrf_of(client.get("/add").text)
    sid, other = "76561198000000043", "76561198000000044"
    r = client.post("/add", data={"csrf": token, "steam_id": sid, "name": "Lh", "ip": "127.0.0.1"},
                    follow_redirects=True)
    assert "is a local address" in r.text
    # new imports never record 127.x / ::1; the name and date are kept
    log = (f"[2026-10-30 10:00:00] Lh:{sid}:127.0.0.1 connected.\n"
           f"[2026-10-30 10:05:00] Other:{other}:127.0.0.1 connected.\n")
    client.post("/import", data={"csrf": token}, files=[("files", ("local.log", log.encode(), "text/plain"))])
    wait_imports()
    with db.get_db() as conn:
        rows = conn.execute("SELECT steam_id, name, ip FROM sightings WHERE steam_id IN (?,?)", (sid, other))
        assert sorted(tuple(r) for r in rows) == [(sid, "Lh", ""), (other, "Other", "")]
        # records imported before the filter existed
        ts = db.now()
        conn.executemany(
            "INSERT INTO sightings(steam_id, name, name_clean, ip, seen_at, source, created_at) VALUES(?,?,?,?,?,?,?)",
            [(sid, "Lh", "lh", "127.0.0.1", "2026-10-30 10:00:00", "log", ts),     # twin of an existing record
             (sid, "Lh", "lh", "127.0.0.1", "2026-10-29 09:00:00", "log", ts),     # keeps its name and date
             (sid, "", "", "::1", "2026-10-28 09:00:00", "log", ts),               # nothing left: deleted
             (other, "Other", "other", "127.0.0.1", "2026-10-29 09:00:00", "log", ts)])
        db.refresh_players(conn, [sid, other])
        db.recompute_alts(conn)
        assert conn.execute("SELECT alt_count FROM players WHERE steam_id=?", (sid,)).fetchone()[0] == 1
    r = client.get("/import")
    assert "Database cleanup" in r.text and "4 records" in r.text and "Sample CSV" in r.text
    r = client.post("/import/cleanup/local-ips", data={"csrf": token}, follow_redirects=True)
    assert "fixed 4 records of 2 players" in r.text and "Clean" in r.text
    with db.get_db() as conn:
        assert db.count_local_ips(conn) == 0
        assert conn.execute("SELECT COUNT(*) FROM sightings WHERE steam_id=?", (sid,)).fetchone()[0] == 2
        p = conn.execute("SELECT last_ip, ip_count, alt_count FROM players WHERE steam_id=?", (sid,)).fetchone()
        assert tuple(p) == ("", 0, 0)
    assert client.post("/import/cleanup/nope", data={"csrf": token}).status_code == 404


def test_discord_links_import_and_usernames(client, monkeypatch):
    import httpx

    from fragdex import discord_users
    from fragdex.importers.common import Sighting

    known, other, unknown = "76561198000000701", "76561198000000702", "76561198000000703"
    id1, id2 = "80351110224670701", "80351110224670702"
    with db.get_db() as conn:
        db.add_sightings(conn, [Sighting(steam_id=known, name="Linked", ip="203.0.113.70"),
                                Sighting(steam_id=other, name="HasOne", ip="203.0.113.71")], "manual")
        db.refresh_players(conn, [known, other])
        conn.execute("UPDATE players SET discord_id='frost.bite' WHERE steam_id=?", (other,))
        before = conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    token = csrf_of(client.get("/import").text)
    body = f"steamid,discordid,discord_name\n{known},{id1},\n{other},{id2},\n{unknown},{id2},\n"
    client.post("/import", data={"csrf": token}, files=[("files", ("links.csv", body.encode(), "text/csv"))])
    wait_imports()
    with db.get_db() as conn:
        job = conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 1").fetchone()
        assert conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] == before
        assert conn.execute("SELECT 1 FROM players WHERE steam_id=?", (unknown,)).fetchone() is None
        assert conn.execute("SELECT discord_id FROM players WHERE steam_id=?", (known,)).fetchone()[0] == id1
        assert conn.execute("SELECT discord_id FROM players WHERE steam_id=?", (other,)).fetchone()[0] == "frost.bite"
    assert "1 Discord IDs added" in job["message"] and "1 players already had" in job["message"]
    assert "1 Steam IDs not in the database" in job["message"] and job["players"] == 2 and job["status"] == "done"

    # without a username the ID is shown, and clicking copies it
    assert f'data-copy="{id1}" title="Click to copy"><code>{id1}</code>' in client.get("/?q=Linked").text

    calls = []

    def discord_api(request):
        calls.append(request)
        did = request.url.path.rsplit("/", 1)[1]
        if did == id1:
            return httpx.Response(200, json={"id": did, "username": "railgod", "discriminator": "0"})
        return httpx.Response(404, json={"message": "Unknown User", "code": 10013})

    monkeypatch.setattr(discord_users, "PAUSE", 0)
    monkeypatch.setattr(discord_users, "_client", lambda: httpx.Client(
        transport=httpx.MockTransport(discord_api), headers={"User-Agent": discord_users.USER_AGENT}))
    with db.get_db() as conn:
        ids = discord_users.due(conn)
        assert id1 in ids and "frost.bite" not in ids       # usernames can't be looked up
        discord_users.refresh(conn, "t" * 60, [id1, "80351110224670799"])
        assert conn.execute("SELECT discord_name FROM players WHERE steam_id=?", (known,)).fetchone()[0] == "railgod"
        assert id1 not in discord_users.due(conn)
    assert calls[0].headers["Authorization"] == "Bot " + "t" * 60
    assert calls[0].headers["User-Agent"].startswith("DiscordBot (https://")

    # the list shows the username; hovering shows the ID and clicking copies it; both are searchable
    page = client.get("/?q=railgod").text
    assert ">railgod</span>" in page and f'data-copy="{id1}" title="Discord ID {id1}: click to copy"' in page
    assert client.get(f"/player/{known}").text.count(id1) >= 2

    # a different ID forgets the old username, the same ID keeps it
    token = csrf_of(client.get(f"/player/{known}").text)
    client.post(f"/player/{known}/discord", data={"csrf": token, "discord_id": id1})
    with db.get_db() as conn:
        assert conn.execute("SELECT discord_name FROM players WHERE steam_id=?", (known,)).fetchone()[0] == "railgod"
    client.post(f"/player/{known}/discord", data={"csrf": token, "discord_id": id2})
    with db.get_db() as conn:
        row = conn.execute("SELECT discord_name, discord_checked_at FROM players WHERE steam_id=?", (known,)).fetchone()
    assert row["discord_name"] == "" and row["discord_checked_at"] is None


def test_discord_sort_and_filter(client):
    from fragdex.importers.common import Sighting

    alpha, bravo, charlie, id_only, none = (f"7656119800000081{i}" for i in range(5))
    with db.get_db() as conn:
        db.add_sightings(conn, [Sighting(steam_id=s, name=f"DcSort{i}", ip="") for i, s in
                                enumerate((alpha, bravo, charlie, id_only, none))], "manual")
        db.refresh_players(conn, [alpha, bravo, charlie, id_only, none])
        for sid, did, name in ((alpha, "80351110224670811", "alpha.sort"), (bravo, "bravo.sort", ""),
                               (charlie, "80351110224670813", "Charlie.Sort"), (id_only, "80351110224670814", "")):
            conn.execute("UPDATE players SET discord_id=?, discord_name=? WHERE steam_id=?", (did, name, sid))

    def order(qs):
        page = client.get(f"/?q=DcSort&per=250&{qs}").text
        return re.findall(r"<code>(7656119800000081\d)</code>", page)

    assert order("sort=discord&dir=asc") == [alpha, bravo, charlie, id_only, none]
    assert order("sort=discord&dir=desc") == [charlie, bravo, alpha, id_only, none]
    assert order("sort=discord&dir=asc&has_discord=1") == [alpha, bravo, charlie, id_only]

    page = client.get("/?q=DcSort&has_discord=1").text
    assert 'name="has_discord" value="1" data-autosubmit checked' in page
    assert "has_discord=1" in re.search(r'href="\?([^"]*sort=discord[^"]*)"', page).group(1)   # the sort link keeps it
    # a first click on the heading sorts A to Z
    assert "dir=asc" in re.search(r'href="\?([^"]*sort=discord[^"]*)"', client.get("/?q=DcSort").text).group(1)


def test_discord_rate_limit_and_bad_token(monkeypatch):
    import httpx

    from fragdex import discord_users

    def run(response):
        monkeypatch.setattr(discord_users, "_client", lambda: httpx.Client(transport=httpx.MockTransport(response)))
        with db.get_db() as conn:
            discord_users.refresh(conn, "t" * 60, ["80351110224670800"])

    with pytest.raises(discord_users.RateLimited) as e:
        run(lambda r: httpx.Response(429, headers={"Retry-After": "65"}, json={"retry_after": 64.5}))
    assert e.value.retry_after == 65
    with pytest.raises(RuntimeError, match="check the Discord bot token") as e:
        run(lambda r: httpx.Response(401, json={"message": "401: Unauthorized"}))
    assert "t" * 20 not in str(e.value)


def test_discord_bot_token_setting(client):
    page = client.get("/admin/settings").text
    assert 'id="discord"' in page and "Add a token to start" in page
    token = csrf_of(page)
    r = client.post("/admin/settings", data={"csrf": token, "action": "discord_token", "discord_bot_token": "short"},
                    follow_redirects=True)
    assert "look like a Discord bot token" in r.text
    secret = "not-a-real-discord-token-" + "x" * 30 + "6789"   # built up so secret scanners don't flag it
    r = client.post("/admin/settings", data={"csrf": token, "action": "discord_token", "discord_bot_token": secret},
                    follow_redirects=True)
    assert "Discord bot token saved." in r.text and secret not in r.text and "…6789" in r.text
    client.post("/admin/settings", data={"csrf": token, "action": "discord_token", "discord_bot_token": ""})
    with db.get_db() as conn:
        assert db.discord_bot_token(conn) == ""


def test_sample_csv_imports_every_column(client, tmp_path):
    from fragdex.importers.spreadsheet import parse_csv

    r = client.get("/download/sample-import.csv")
    assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf")
    assert r.content.decode("utf-8-sig").splitlines()[0] == "Player Name,Steam ID,Discord ID,Aliases,IP Address,Date"
    p = tmp_path / "sample.csv"
    p.write_bytes(r.content)
    out, stats = parse_csv(str(p))
    assert stats["rows"] == 4 and stats["skipped_bad_id"] == stats["skipped_no_id"] == stats["bad_discord"] == 0
    assert len(stats["discord"]) == 2 and {s.steam_id for s in out} == {
        "76561198000000001", "76561198000000003", "76561198000000007", "76561198000000017"}
    assert {s.name for s in out if s.steam_id == "76561198000000017"} == {"Wraith", "wr4ith", "^4wraith^7.cfg"}


def test_discord_column_added_to_old_database(tmp_path, monkeypatch):
    import sqlite3

    from fragdex import config
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    # the players table as released before Discord IDs existed
    create = re.search(r"CREATE TABLE IF NOT EXISTS players\(.*?\);", db.SCHEMA, re.S).group(0)
    old.execute(create.replace("    discord_id TEXT NOT NULL DEFAULT '',\n", "")
                .replace("    discord_name TEXT NOT NULL DEFAULT '',\n", "")
                .replace("    discord_checked_at TEXT,\n", "")
                .replace("    discord_source TEXT NOT NULL DEFAULT '',  -- manual, import or fragq; '' = set before this was recorded\n", "")
                .replace("    discord_status TEXT NOT NULL DEFAULT '',  -- from FragQ: claimed or verified\n", "")
                .replace("    steam_verified INTEGER NOT NULL DEFAULT 0,\n", ""))
    assert "discord_id" not in [r[1] for r in old.execute("PRAGMA table_info(players)")]
    old.execute("INSERT INTO players(steam_id, last_name, notes, created_at, updated_at)"
                " VALUES('76561198000000001', 'Keep', 'my note', 'x', 'y')")
    old.commit()
    old.close()
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(config, "DB_PATH", str(path))
    db.init_db()
    db.init_db()   # idempotent
    with db.get_db() as conn:
        row = dict(conn.execute("SELECT * FROM players").fetchone())
    assert row["discord_id"] == "" and row["last_name"] == "Keep" and row["notes"] == "my note"
    assert row["discord_name"] == "" and row["discord_checked_at"] is None
    assert row["discord_source"] == row["discord_status"] == "" and row["steam_verified"] == 0


def test_steam_verified_filled_in_for_an_old_database(tmp_path, monkeypatch):
    import sqlite3
    from fragdex import config
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    # tables as released before verified Steam IDs existed
    for table in ("players", "sightings"):
        create = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\(.*?\n\);", db.SCHEMA, re.S).group(0)
        old.execute(re.sub(r"\n    (steam_verified|verified) INTEGER[^\n]*", "", create))
    for sid in ("76561198000000001", "76561198000000002"):
        old.execute("INSERT INTO players(steam_id, created_at, updated_at) VALUES(?, 'x', 'y')", (sid,))
    old.execute("INSERT INTO sightings(steam_id, name, source, created_at) VALUES('76561198000000001', 'A', 'demo', 'x')")
    old.execute("INSERT INTO sightings(steam_id, name, source, created_at) VALUES('76561198000000002', 'B', 'csv', 'x')")
    old.commit()
    old.close()
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(config, "DB_PATH", str(path))
    db.init_db()
    db.init_db()   # idempotent
    with db.get_db() as conn:
        rows = dict(conn.execute("SELECT steam_id, steam_verified FROM players").fetchall())
    assert rows == {"76561198000000001": 1, "76561198000000002": 0}


def test_steam_errors_never_show_the_key():
    from fragdex import steam

    class Forbidden:
        status_code = 403

    class Client:
        def get(self, url, params):
            return Forbidden()

    with pytest.raises(RuntimeError) as err:
        steam._get(Client(), "ISteamUser/GetPlayerSummaries/v2/", {"key": "s3cr3tkey", "steamids": "1"})
    assert "s3cr3tkey" not in str(err.value) and "HTTP 403" in str(err.value)


# ---------------------------------------------------------------- API

def _key_settings(**over):
    form = {"name": "bot", "rate_limit": "120", "expires_at": "", "allowed_ips": ""}
    form.update(over)
    return form


def _create_key(client, **over):
    page = client.get("/admin/settings")
    r = client.post("/admin/api-keys", data={"csrf": csrf_of(page.text), **_key_settings(**over)})
    assert r.status_code == 200
    key = max(re.findall(r"fdx_[A-Za-z0-9_-]+", r.text), key=len)
    with db.get_db() as conn:
        kid = conn.execute("SELECT id FROM api_keys WHERE key_hash=?", (apikeys_hash(key),)).fetchone()[0]
    return key, kid


def apikeys_hash(key):
    from fragdex import apikeys
    return apikeys.hash_key(key)


def _edit_key(client, kid, **over):
    page = client.get("/admin/settings")
    r = client.post(f"/admin/api-keys/{kid}", data={"csrf": csrf_of(page.text), **_key_settings(**over)})
    assert r.status_code == 200 and "Saved the settings" in r.text


def _api(client, key, path, method="GET", **kw):
    return client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {key}"}, **kw)


def test_api_keys_and_permissions(client):
    assert login(client).status_code == 303
    page = client.get("/admin/settings")
    assert "Base URL" in page.text and "http://testserver/api/v1" in page.text
    key, kid = _create_key(client, name="Discord bot")
    with db.get_db() as conn:
        row = dict(conn.execute("SELECT * FROM api_keys WHERE id=?", (kid,)).fetchone())
    assert key not in str(row.values()) and row["key_hash"] == apikeys_hash(key)
    assert key not in client.get("/admin/settings").text      # shown once only

    assert client.get("/api/v1/status").status_code == 401
    assert _api(client, "fdx_wrong", "/status").status_code == 401
    r = client.get("/api/v1/status", headers={"X-API-Key": key})
    assert r.status_code == 200 and r.json()["key"] == {"name": "Discord bot", "ips": False, "notes": False,
                                                        "write": False, "rate_limit": 120, "expires": None}
    assert r.headers["x-ratelimit-limit"] == "120"

    # read-only key: no writes, no IP addresses anywhere, IP search doesn't match
    rec = {"records": [{"steam_id": "76561198000000555", "name": "^2Api^7Guy", "ip": "192.0.2.55"}]}
    assert _api(client, key, "/records", "POST", json=rec).status_code == 403
    _edit_key(client, kid, name="Discord bot", can_write="1")
    r = _api(client, key, "/records", "POST", json={"server": "^1CA ^7#1", "records": rec["records"] + [
        {"steam_id": "76561198000000555", "name": "Local", "ip": "127.0.0.1", "seen_at": "2026-10-30 10:00:00"},
        {"steam_id": "nope", "name": "x"}, {"steam_id": "76561198000000555"}]})
    assert r.status_code == 200
    assert r.json() == {"added": 2, "skipped": 0, "players": 1, "rejected": [
        {"index": 2, "error": "invalid steam_id"}, {"index": 3, "error": "needs a name or an ip"}]}
    with db.get_db() as conn:
        rows = conn.execute("SELECT name, ip, source, server FROM sightings WHERE steam_id='76561198000000555'"
                            " ORDER BY name").fetchall()
    assert [tuple(r) for r in rows] == [("Local", "", "api", "CA #1"), ("^2Api^7Guy", "192.0.2.55", "api", "CA #1")]

    p = _api(client, key, "/players/76561198000000555").json()
    assert p["name"] == "ApiGuy" and "ips" not in p and "last_ip" not in p and "notes" not in p
    assert _api(client, key, "/players?q=192.0.2.55").json()["total"] == 0
    assert _api(client, key, "/ips/192.0.2.55").status_code == 403

    _edit_key(client, kid, name="Discord bot", can_write="1", can_ips="1")
    p = _api(client, key, "/players/76561198000000555").json()
    assert p["last_ip"] == "192.0.2.55" and p["ips"][0]["ip"] == "192.0.2.55"
    assert _api(client, key, "/players?q=192.0.2.55").json()["players"][0]["steam_id"] == "76561198000000555"
    r = _api(client, key, "/ips/192.0.2.0/24").json()
    assert r["query"] == "192.0.2.0/24" and r["players"][0]["ips"] == ["192.0.2.55"]

    # lookup: known / unknown / invalid, linked accounts with their tags
    with db.get_db() as conn:
        conn.execute("UPDATE players SET discord_id='80351110224678912' WHERE steam_id='76561198000000555'")
    r = _api(client, key, "/players/lookup", "POST",
             json={"steam_ids": ["76561198000000555", "[U:1:123456789]", "bad"]}).json()
    assert [p["known"] for p in r["players"]] == [True, False] and r["invalid"] == ["bad"]
    assert r["players"][0]["discord_id"] == "80351110224678912"
    assert _api(client, key, "/discord/<@80351110224678912>").json()["players"][0]["steam_id"] == "76561198000000555"
    linked = _api(client, key, "/players/76561197960287930/linked").json()["linked"]
    assert linked and all("shared_ips" in a for a in linked)
    assert _api(client, key, "/players/76561198999999999").status_code == 404
    assert _api(client, key, "/players?bans=nonsense").status_code == 400

    # usage reaches the Settings page
    page = client.get("/admin/settings").text
    assert "Discord bot" in page and "Never" not in page.split("Discord bot", 1)[1].split("Last used", 1)[1][:80]


def test_api_discord_lookup(client):
    from fragdex.importers.common import Sighting

    looked_up, old_style, sheet, typed = (f"7656119800000092{i}" for i in range(4))
    sids = [looked_up, old_style, sheet, typed]
    with db.get_db() as conn:
        db.add_sightings(conn, [Sighting(steam_id=s, name=f"ApiDc{i}", ip="") for i, s in enumerate(sids)], "manual")
        db.refresh_players(conn, sids)
        for sid, did, name in ((looked_up, "80351110224670921", "apigod"), (old_style, "80351110224670922", "Old.Style#1234"),
                               (sheet, "80351110224670923", "Mixed.Case"), (typed, "typed.username", "")):
            conn.execute("UPDATE players SET discord_id=?, discord_name=? WHERE steam_id=?", (did, name, sid))
    assert login(client).status_code == 303
    key, _ = _create_key(client, name="Discord lookup")

    def lookup(q):
        return _api(client, key, f"/discord/{q}").json()

    r = lookup("80351110224670921")
    assert r["query"] == r["discord_id"] == "80351110224670921"
    assert (r["players"][0]["discord_id"], r["players"][0]["discord_name"]) == ("80351110224670921", "apigod")

    # a username search doesn't report the username as an ID; the player still has both
    r = lookup("ApiGod")
    assert r["query"] == "apigod" and r["discord_id"] is None
    assert (r["players"][0]["discord_id"], r["players"][0]["discord_name"]) == ("80351110224670921", "apigod")

    # usernames match whatever their case
    for q, sid in (("old.style%231234", old_style), ("OLD.STYLE%231234", old_style), ("Old.Style%231234", old_style),
                   ("mixed.case", sheet), ("Mixed.Case", sheet), ("TYPED.username", typed)):
        assert [p["steam_id"] for p in lookup(q)["players"]] == [sid], q
    p = lookup("typed.username")["players"][0]
    assert p["discord_id"] == "typed.username" and p["discord_name"] is None


def test_verified_steam_ids(client, tmp_path):
    from fragdex.importers.common import Sighting
    from fragdex.importers.export import ExportWriter, read_export

    demo, log, sheet, manual, plugin, script = (f"7656119800000093{i}" for i in range(6))
    with db.get_db() as conn, db.transaction(conn):
        for sid, source in ((demo, "demo"), (log, "log"), (sheet, "csv"), (manual, "manual")):
            db.add_sightings(conn, [Sighting(steam_id=sid, name=f"VerTest{source}")], source)
        db.refresh_players(conn, [demo, log, sheet, manual])

    def verified():
        with db.get_db() as conn:
            return {r[0] for r in conn.execute(
                "SELECT steam_id FROM players WHERE steam_id LIKE '7656119800000093%' AND steam_verified=1")}

    assert verified() == {demo, log}

    # records from a game server's key count; another key with Add records can't vouch for a Steam ID
    assert login(client).status_code == 303
    game, game_id = _create_key(client, name="CA 1", can_write="1")
    other, _ = _create_key(client, name="Script", can_write="1")
    body = lambda sid: {"records": [{"steam_id": sid, "name": "VerTestapi"}]}
    assert _api(client, game, "/records", "POST", json=body(plugin)).json()["added"] == 1
    assert _api(client, other, "/records", "POST", json=body(script)).json()["added"] == 1
    assert verified() == {demo, log}
    _edit_key(client, game_id, name="CA 1", can_write="1", game_server="1")      # earlier records count too
    assert verified() == {demo, log, plugin}
    page = client.get("/admin/settings").text
    assert 'name="game_server" value="1"  checked' in page or 'name="game_server" value="1" checked' in page
    _edit_key(client, game_id, name="CA 1", can_write="1")
    assert verified() == {demo, log}
    _edit_key(client, game_id, name="CA 1", can_write="1", game_server="1")
    assert verified() == {demo, log, plugin}

    # shown, filtered and in the API
    page = client.get("/?q=VerTest&verified=1&per=250").text
    assert demo in page and plugin in page and sheet not in page and script not in page
    assert 'name="verified" value="1" data-autosubmit checked' in page and "verified-mark" in page
    assert "Verified Steam ID" in client.get(f"/player/{demo}").text
    assert "Verified Steam ID" not in client.get(f"/player/{sheet}").text.replace('>Verified Steam ID</label>', '')
    r = _api(client, other, "/players?q=VerTest&verified=1").json()
    assert r["total"] == 3 and all(p["steam_verified"] for p in r["players"])
    assert _api(client, other, f"/players/{sheet}").json()["steam_verified"] is False

    # a .fdx keeps the status of a game server's records, and nobody else's
    from fragdex import worker
    export = tmp_path / "restore.fdx"
    export.write_bytes(client.get("/export.fdx?q=VerTest").content)
    groups = {e["file"]: e["verified"] for e in read_export(str(export)) if e["kind"] == "api"}
    assert groups == {"api: CA 1": True, "api: Script": False}
    with db.get_db() as conn:
        conn.execute("DELETE FROM players WHERE steam_id IN (?, ?)", (plugin, script))
        assert verified() == {demo, log}
        worker.process_export(conn, {"stored_path": str(export), "id": None, "origin": "watch"})
    assert verified() == {demo, log, plugin}
    with db.get_db() as conn:
        assert {r[0] for r in conn.execute("SELECT DISTINCT source FROM sightings WHERE steam_id=?", (plugin,))} == {"api"}

    # only what a .fdx says is a demo or log counts; other sources keep their type when read back
    path = tmp_path / "kinds.fdx"
    w = ExportWriter(str(path), "test", "2026-01-01", version=2)
    row = [[demo, "A", "", "2026-01-01 00:00:00"]]
    w.add_rows("a.dm_91", "demo", "", 0, row)
    w._write({"file": "no-type", "s": row})
    w.add_rows("x", "api", "", 0, row)
    w._write({"file": "only-api-can-claim-this", "kind": "csv", "verified": True, "s": row})
    w.close()
    entries = list(read_export(str(path)))
    assert [e["kind"] for e in entries] == ["demo", "manual", "api", "csv"]
    assert not any(e["verified"] for e in entries)


class _FakeFragq:
    def __init__(self, status, body):
        self.status_code, self.body, self.seen = status, body, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers):
        self.seen = {"url": url, "headers": headers}
        return self

    def json(self):
        return self.body


def test_fragq_fetch(monkeypatch):
    from fragdex import fragq

    fake = _FakeFragq(200, {"ok": True, "links": [{"steam_id": "x"}]})
    monkeypatch.setattr(fragq, "_client", lambda: fake)
    assert fragq.fetch("https://fragq.test", "fq_secret") == [{"steam_id": "x"}]
    assert fake.seen == {"url": "https://fragq.test/api/connect/v1/steam/links",
                         "headers": {"Authorization": "Bearer fq_secret"}}
    for status, body, expected in ((404, {}, "Update FragQ"), (401, {"error": "Invalid connector key."}, "Invalid connector key"),
                                   (403, {"error": "Not allowed."}, "Read linked accounts"), (200, {"nope": 1}, "wasn't a list"),
                                   (200, None, "wasn't a list")):
        monkeypatch.setattr(fragq, "_client", lambda: _FakeFragq(status, body))
        with pytest.raises(RuntimeError) as err:
            fragq.fetch("https://fragq.test", "fq_secret")
        assert expected in str(err.value) and "fq_secret" not in str(err.value)
    assert fragq.parse_url(" https://fragq.test/ ") == ("https://fragq.test", None)
    assert fragq.parse_url("") == ("", None)
    assert fragq.parse_url("javascript:alert(1)")[0] is None


def _fq_players(*sids):
    from fragdex.importers.common import Sighting
    with db.get_db() as conn, db.transaction(conn):
        db.add_sightings(conn, [Sighting(steam_id=s, name=f"FqPlayer{i}") for i, s in enumerate(sids)], "demo")
        db.refresh_players(conn, sids)


def _fq_did(n):
    return f"8035111022467{n:04d}"


def _fq_link(sid, n, by="", name=None, persona="P"):
    return {"steam_id": sid, "discord_id": _fq_did(n), "discord_name": name, "persona": persona, "verified_by": by}


def _fq_state(conn, sid):
    r = conn.execute("SELECT discord_id, discord_name, discord_source, discord_status FROM players WHERE steam_id=?", (sid,)).fetchone()
    return tuple(r) if r else None


def test_fragq_link_rules():
    from fragdex import fragq

    empty, old, imported, typed, conflict, cleared, new, unnamed, proven = (f"7656119800000094{i}" for i in range(9))
    _fq_players(empty, old, imported, typed, conflict, cleared, proven)
    did = _fq_did
    with db.get_db() as conn:
        for sid, discord, source, name in ((old, did(1), "fragq", "old.name"), (imported, did(2), "import", ""),
                                           (typed, "typed.name", "", ""), (conflict, did(3), "manual", ""),
                                           (cleared, "", "manual", "")):
            conn.execute("UPDATE players SET discord_id=?, discord_source=?, discord_name=? WHERE steam_id=?",
                         (discord, source, name, sid))
    links = [
        _fq_link(empty, 10, "", "filled.in"), _fq_link(old, 11, "steam", "new.name"), _fq_link(imported, 2, "game", "same.one"),
        _fq_link(typed, 13, "", "Typed.Name"), _fq_link(conflict, 14, "steam", "other"), _fq_link(cleared, 15, "", "nope"),
        _fq_link(new, 16, "", "new.player", "^1Steam^7Name"), _fq_link(unnamed, 17, "", None, None), _fq_link(proven, 18, "steam"),
        {"steam_id": "nope", "discord_id": did(19)}, {"steam_id": empty, "discord_id": 80351110224670000}, "junk",
    ]
    with db.get_db() as conn:
        conn.execute("DELETE FROM fragq_connections")
        cid = fragq.add_connection(conn, "A", "https://a.test", "key-a", trusted=True)
        assert fragq.apply(conn, cid, links) == dict(links=9, filled=3, updated=1, relabelled=2, new_players=1, conflicts=1,
                                                     kept_empty=1, waiting=1, bad=3)
        assert fragq.apply(conn, cid, links) == dict(links=9, filled=0, updated=0, relabelled=0, new_players=0, conflicts=1,
                                                     kept_empty=1, waiting=1, bad=3)     # repeating changes nothing
        assert _fq_state(conn, empty) == (did(10), "filled.in", "fragq", "claimed")
        assert _fq_state(conn, old) == (did(11), "new.name", "fragq", "verified")
        assert _fq_state(conn, imported) == (did(2), "same.one", "import", "verified")     # the same account gets the label
        assert _fq_state(conn, typed) == (did(13), "Typed.Name", "", "claimed")            # a typed username becomes the ID
        assert _fq_state(conn, conflict) == (did(3), "", "manual", "")
        assert _fq_state(conn, cleared) == ("", "", "manual", "")
        assert _fq_state(conn, new) == (did(16), "new.player", "fragq", "claimed")
        assert _fq_state(conn, proven) == (did(18), "", "fragq", "verified")
        assert _fq_state(conn, unnamed) is None
        assert fragq.conflicts(conn) == [{"steam_id": conflict, "name": "FqPlayer4", "fragdex": did(3), "links": [
            {"connection_id": cid, "connection": "A", "fragq": did(14), "label": "verified"}]}]

        # a player known only from FragQ has a name, but no verified Steam ID and no last-seen date
        row = conn.execute("SELECT last_name, steam_verified, last_seen FROM players WHERE steam_id=?", (new,)).fetchone()
        assert tuple(row) == ("^1Steam^7Name", 0, "")
        assert conn.execute("SELECT source FROM sightings WHERE steam_id=?", (new,)).fetchone()[0] == "fragq"

        # editing by hand: saving the same ID changes nothing, a new one takes over, clearing it sticks
        db.set_discord(conn, empty, did(10))
        assert _fq_state(conn, empty)[2:] == ("fragq", "claimed")
        db.set_discord(conn, empty, did(99))
        assert _fq_state(conn, empty)[2:] == ("manual", "")
        assert fragq.apply(conn, cid, links[:1])["conflicts"] == 1
        db.set_discord(conn, empty, "")
        assert fragq.apply(conn, cid, links[:1])["kept_empty"] == 1
        conn.execute("DELETE FROM fragq_connections")


def test_fragq_several_connections_settle():
    from fragdex import fragq

    beats, proofs, claims, hand, cleared, only_a, tie, only_c = (f"7656119800000097{i}" for i in range(8))
    _fq_players(beats, proofs, claims, hand, cleared, only_a, tie, only_c)
    did = _fq_did
    with db.get_db() as conn:
        conn.execute("DELETE FROM fragq_connections")
        conn.execute("UPDATE players SET discord_id=?, discord_source='fragq', discord_status='claimed' WHERE steam_id=?", (did(30), claims))
        conn.execute("UPDATE players SET discord_id=?, discord_source='manual' WHERE steam_id=?", (did(9), hand))
        conn.execute("UPDATE players SET discord_source='manual' WHERE steam_id=?", (cleared,))
        a = fragq.add_connection(conn, "A", "https://a.test", "key-aaaa", trusted=True)
        b = fragq.add_connection(conn, "B", "https://b.test", "key-bbbb", trusted=True)
        c = fragq.add_connection(conn, "C", "https://c.test", "key-cccc")      # untrusted unless said otherwise
        assert fragq.connection(conn, c)["trusted"] == 0
        lists = {
            a: [_fq_link(beats, 10), _fq_link(proofs, 20, "steam"), _fq_link(claims, 30), _fq_link(hand, 40, "steam"),
                _fq_link(cleared, 50, "steam"), _fq_link(only_a, 60, "steam"), _fq_link(tie, 70)],
            b: [_fq_link(beats, 11, "steam"), _fq_link(proofs, 21, "steam"), _fq_link(claims, 31)],
            c: [_fq_link(only_c, 80, "steam"), _fq_link(tie, 71, "steam")],     # an untrusted server can't verify anything
        }
        for order in ((a, b, c), (c, b, a), (b, a, c), (a, c, b)):
            for cid in order:
                fragq.apply(conn, cid, lists[cid])

        def check():
            assert _fq_state(conn, beats)[0] == did(11) and _fq_state(conn, beats)[3] == "verified"    # proof beats a claim
            assert _fq_state(conn, proofs)[0] == did(20)       # two proofs: the current value stays, an admin decides
            assert _fq_state(conn, claims)[0] == did(30) and _fq_state(conn, claims)[3] == "claimed"   # two claims: stays
            assert _fq_state(conn, hand)[0] == did(9) and _fq_state(conn, hand)[2] == "manual"         # by hand always wins
            assert _fq_state(conn, cleared) == ("", "", "manual", "")
            assert _fq_state(conn, only_a)[0] == did(60) and _fq_state(conn, only_a)[3] == "verified"
            assert _fq_state(conn, only_c)[0] == did(80) and _fq_state(conn, only_c)[3] == "claimed"
            assert _fq_state(conn, tie)[0] == did(70)          # an untrusted "proof" is only a claim

        check()
        assert {x["steam_id"] for x in fragq.conflicts(conn)} == {proofs, claims, hand, tie}
        for order in ((a, b, c), (c, a, b)):     # nothing flips, however often and in whatever order they sync
            for cid in order:
                s = fragq.apply(conn, cid, lists[cid])
                assert (s["filled"], s["updated"], s["relabelled"]) == (0, 0, 0), cid
        check()

        # a link missing from a list stays
        fragq.apply(conn, a, lists[a][1:])
        assert conn.execute("SELECT COUNT(*) FROM fragq_links WHERE connection_id=? AND steam_id=?", (a, beats)).fetchone()[0] == 1
        check()

        # trusting a server re-decides its players; so does taking the trust away
        row = fragq.connection(conn, c)
        fragq.update_connection(conn, row, "C", row["url"], "", True)
        assert fragq.connection(conn, c)["key"] == "key-cccc"      # an empty key keeps the saved one
        assert _fq_state(conn, only_c)[3] == "verified"
        assert _fq_state(conn, tie)[0] == did(71) and _fq_state(conn, tie)[3] == "verified"
        fragq.update_connection(conn, fragq.connection(conn, c), "C", row["url"], "", False)
        assert _fq_state(conn, only_c)[3] == "claimed"
        assert _fq_state(conn, tie)[0] == did(71) and _fq_state(conn, tie)[3] == "claimed"

        # removing a server forgets what it said: IDs stay, but a label nothing backs up any more is lowered
        fragq.delete_connection(conn, fragq.connection(conn, a))
        assert conn.execute("SELECT COUNT(*) FROM fragq_links WHERE connection_id=?", (a,)).fetchone()[0] == 0
        assert _fq_state(conn, only_a)[0] == did(60) and _fq_state(conn, only_a)[3] == "claimed"
        assert _fq_state(conn, proofs)[0] == did(21) and _fq_state(conn, proofs)[3] == "verified"  # the tie is gone
        conn.execute("DELETE FROM fragq_connections")


def test_fragq_undecided_player_stays_empty():
    from fragdex import fragq

    sid = "76561198000000985"
    _fq_players(sid)
    with db.get_db() as conn:
        conn.execute("DELETE FROM fragq_connections")
        a = fragq.add_connection(conn, "A", "https://a.test", "key-aaaa", trusted=True)
        b = fragq.add_connection(conn, "B", "https://b.test", "key-bbbb", trusted=True)
        # both answers are in before the player is decided, as after a connection's trust changes
        for cid, n in ((a, 1), (b, 2)):
            conn.execute("INSERT INTO fragq_links(connection_id, steam_id, discord_id, verified, updated_at) VALUES(?,?,?,1,'x')",
                         (cid, sid, _fq_did(n)))
        assert fragq.apply(conn, a, [_fq_link(sid, 1, "steam")])["conflicts"] == 1
        assert _fq_state(conn, sid)[0] == ""
        assert [x["steam_id"] for x in fragq.conflicts(conn)] == [sid]
        conn.execute("DELETE FROM fragq_connections")


def test_fragq_one_connection_failing_leaves_the_others(monkeypatch):
    from fragdex import fragq

    good, stays = "76561198000000980", "76561198000000981"
    _fq_players(good, stays)

    def fetch(url, key):
        if "down" in url:
            raise RuntimeError("Couldn't reach FragQ")
        return [_fq_link(good, 1, "steam")]

    with db.get_db() as conn:
        conn.execute("DELETE FROM fragq_connections")
        down = fragq.add_connection(conn, "Down", "https://down.test", "key-down", trusted=True)
        fragq.apply(conn, down, [_fq_link(stays, 2, "steam")])
        up = fragq.add_connection(conn, "Up", "https://up.test", "key-up", trusted=True)
        monkeypatch.setattr(fragq, "fetch", fetch)
        assert fragq.sync_all(conn) == {"discord": True, "players": False}
        assert "Couldn't reach" in fragq.connection(conn, down)["last_error"]
        assert fragq.connection(conn, up)["last_error"] == "" and fragq.connection(conn, up)["last_result"]
        assert _fq_state(conn, good)[3] == "verified"
        assert _fq_state(conn, stays)[3] == "verified"      # what it said before still counts
        assert conn.execute("SELECT COUNT(*) FROM fragq_links WHERE connection_id=?", (down,)).fetchone()[0] == 1
        conn.execute("DELETE FROM fragq_connections")


def test_fragq_filled_total_per_key(monkeypatch):
    from fragdex import fragq

    one, two, three = (f"7656119800000096{i}" for i in range(3))
    _fq_players(one, two, three)
    links = []
    monkeypatch.setattr(fragq, "fetch", lambda url, key: list(links))
    with db.get_db() as conn:
        conn.execute("DELETE FROM fragq_connections")
        cid = fragq.add_connection(conn, "A", "https://a.test", "key-a", trusted=True)

        def run(key):
            return fragq.sync(conn, fragq.connection(conn, cid), "https://a.test", key)["filled_total"]

        links.append(_fq_link(one, 1))
        assert run("key-a") == 1
        links.append(_fq_link(two, 2))
        assert run("key-a") == 2
        assert run("key-a") == 2    # nothing new
        links.append(_fq_link(three, 3))
        assert run("key-b") == 1    # a new key starts again
        assert fragq.connection(conn, cid)["filled_total"] == 1
        conn.execute("DELETE FROM fragq_connections")


def test_fragq_single_connection_becomes_the_first_entry(tmp_path, monkeypatch):
    from fragdex import config, fragq

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "old.sqlite3"))
    db.init_db()
    with db.get_db() as conn:
        for k, v in (("fragq_url", "https://old.test"), ("fragq_key", "fq_oldkey"), ("fragq_last_result", '{"at": "x", "links": 5}'),
                     ("fragq_filled_key", db.key_mark("fq_oldkey")), ("fragq_filled_total", "7"), ("fragq_conflicts", "[]")):
            db.set_setting(conn, k, v)
    db.init_db()
    db.init_db()     # idempotent
    with db.get_db() as conn:
        rows = fragq.connections(conn)
        assert len(rows) == 1
        c = rows[0]
        assert (c["url"], c["key"], c["trusted"], c["filled_total"], c["last_result"]) == (
            "https://old.test", "fq_oldkey", 1, 7, '{"at": "x", "links": 5}')
        assert c["filled_key"] == db.key_mark("fq_oldkey")
        assert conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'fragq_%'").fetchone()[0] == 0

    # a connection set in .env is added once, trusted, and takes its address and key from there
    monkeypatch.setattr(config, "FRAGQ_URL_ENV", "https://env.test")
    monkeypatch.setattr(config, "FRAGQ_KEY_ENV", "fq_envkey")
    db.init_db()
    db.init_db()
    with db.get_db() as conn:
        rows = fragq.connections(conn)
        assert len(rows) == 2 and rows[1]["from_env"] == 1 and rows[1]["trusted"] == 1 and rows[1]["key"] == ""
        assert fragq.address(rows[1]) == ("https://env.test", "fq_envkey")


def test_fragq_settings_and_labels(client, monkeypatch):
    from fragdex import fragq

    assert login(client).status_code == 303
    monkeypatch.setattr(fragq, "fetch", lambda url, key: [{"a": 1}, {"b": 2}])
    with db.get_db() as conn:
        conn.execute("DELETE FROM fragq_connections")

    def post(path, **form):
        return client.post(path, data={"csrf": csrf_of(client.get("/admin/settings").text), **form})

    key = "fq_abcdefghijklmnop1234"
    good = dict(label="Community A", url="https://a.test/", key=key)
    assert "Give the connection a name" in post("/admin/fragq", **{**good, "label": " "}).text
    assert "address, such as https://fragq.example.com" in post("/admin/fragq", **{**good, "url": "not a url"}).text
    assert "connector key" in post("/admin/fragq", **{**good, "key": "short"}).text
    assert "Enter the key" in post("/admin/fragq", **{**good, "key": ""}).text
    page = post("/admin/fragq", **good).text
    assert "Added FragQ connection" in page and key not in page and "Not trusted" in page
    assert re.search(r'name="key" placeholder=" ".*?<strong>Saved</strong>: …1234 \(enter a new key', page, re.S)
    assert all('autocomplete="new-password"' in m for m in re.findall(r'<input type="password"[^>]*>', page))
    assert re.findall(r'<input type="password"[^>]*>', page) and 'autocomplete="current-password"' not in page
    with db.get_db() as conn:
        a = fragq.connections(conn)[0]
    assert (a["url"], a["key"], a["trusted"]) == ("https://a.test", key, 0)

    assert "2 links available" in post(f"/admin/fragq/{a['id']}/test").text
    page = post(f"/admin/fragq/{a['id']}", label="Renamed", url="https://other.test", key="", trusted="1").text   # an empty key keeps it
    assert "Saved FragQ connection" in page and ">Trusted<" in page
    with db.get_db() as conn:
        a = fragq.connection(conn, a["id"])
    assert (a["name"], a["url"], a["key"], a["trusted"]) == ("Renamed", "https://other.test", key, 1)

    def fail(url, k):
        raise RuntimeError("FragQ returned HTTP 403.")

    monkeypatch.setattr(fragq, "fetch", fail)
    assert "failed: FragQ returned HTTP 403." in post(f"/admin/fragq/{a['id']}/test").text
    monkeypatch.setattr(fragq, "fetch", lambda url, k: [])

    # the labels show on the pages, and in the CSV and API
    sid, other = "76561198000000950", "76561198000000951"
    with db.get_db() as conn:
        b = fragq.add_connection(conn, "Second", "https://b.test", "key-bbbbbbbbbbbbbbbb", trusted=True)
        for s, name in ((sid, "FqLabel"), (other, "<b>X</b>")):
            conn.execute("INSERT OR IGNORE INTO players(steam_id, last_name, created_at, updated_at) VALUES(?, ?, 'x', 'y')", (s, name))
        conn.execute("UPDATE players SET discord_id='80351110224670950', discord_status='claimed', discord_source='fragq' WHERE steam_id=?", (sid,))
        fragq.apply(conn, a["id"], [_fq_link(other, 1, "steam")])
        fragq.apply(conn, b, [_fq_link(other, 2, "steam")])
        conn.execute("UPDATE fragq_connections SET last_result=?, filled_total=1234 WHERE id=?",
                     ('{"at": "x", "links": 1, "filled": 0, "updated": 0, "new_players": 0, "waiting": 0, "bad": 0}', b))
    assert "link-claimed" in client.get(f"/?q={sid}").text
    assert "link-claimed" in client.get(f"/player/{sid}").text
    assert "claimed" in client.get(f"/export.csv?q={sid}").text.splitlines()[1]
    page = client.get("/admin/settings").text
    assert "Conflicts (1)" in page and "&lt;b&gt;X&lt;/b&gt;" in page and "<b>X</b>" not in page
    assert "Filled since this key was added: 1,234." in page and "Second:" in page

    assert "Removed FragQ connection" in post(f"/admin/fragq/{a['id']}/delete").text
    assert "Removed FragQ connection" in post(f"/admin/fragq/{b}/delete").text
    assert client.post("/admin/fragq/999999/delete", data={"csrf": csrf_of(client.get("/admin/settings").text)}).status_code == 404
    with db.get_db() as conn:
        assert fragq.connections(conn) == [] and conn.execute("SELECT COUNT(*) FROM fragq_links").fetchone()[0] == 0


def test_api_key_limits_change_without_new_key(client):
    assert login(client).status_code == 303
    key, kid = _create_key(client, name="limited", rate_limit="3")
    codes = [_api(client, key, "/status").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = _api(client, key, "/status")
    assert int(r.headers["retry-after"]) > 0 and "Rate limit of 3" in r.json()["error"]
    _edit_key(client, kid, name="limited", rate_limit="0")            # unlimited, same key
    assert all(_api(client, key, "/status").status_code == 200 for _ in range(5))

    _edit_key(client, kid, name="limited", allowed_ips="198.51.100.0/24\n203.0.113.9")
    r = _api(client, key, "/status")
    assert r.status_code == 403 and "can't be used from" in r.json()["error"]
    # the address Caddy reports for the caller (X-Fragdex-Client-IP) is the one checked
    ok = client.get("/api/v1/status", headers={"Authorization": f"Bearer {key}",
                                               "X-Fragdex-Client-IP": "198.51.100.77"})
    assert ok.status_code == 200
    with db.get_db() as conn:
        from fragdex import apikeys
        apikeys.usage.flush(conn)
        row = conn.execute("SELECT allowed_ips, last_denied_reason FROM api_keys WHERE id=?", (kid,)).fetchone()
    assert tuple(row) == ("198.51.100.0/24 203.0.113.9", "ip")
    _edit_key(client, kid, name="limited", allowed_ips="")
    assert _api(client, key, "/status").status_code == 200

    _edit_key(client, kid, name="limited", expires_at="2020-01-01")
    assert _api(client, key, "/status").json()["error"] == "This API key is expired."
    _edit_key(client, kid, name="limited", expires_at="2099-12-31")
    assert _api(client, key, "/status").json()["key"]["expires"] == "2099-12-31"

    page = client.get("/admin/settings")
    client.post(f"/admin/api-keys/{kid}/toggle", data={"csrf": csrf_of(page.text)})
    assert _api(client, key, "/status").json()["error"] == "This API key is disabled."
    client.post(f"/admin/api-keys/{kid}/toggle", data={"csrf": csrf_of(page.text)})

    r = client.post(f"/admin/api-keys/{kid}/rotate", data={"csrf": csrf_of(page.text)})
    new = max(re.findall(r"fdx_[A-Za-z0-9_-]+", r.text), key=len)
    assert new != key and _api(client, key, "/status").status_code == 401
    assert _api(client, new, "/status").json()["key"]["expires"] == "2099-12-31"   # settings kept

    page = client.get("/admin/settings")
    r = client.post(f"/admin/api-keys/{kid}", data={"csrf": csrf_of(page.text),
                                                    **_key_settings(name="limited", allowed_ips="not-an-ip")})
    assert "is not an IP address" in r.text
    client.post(f"/admin/api-keys/{kid}/delete", data={"csrf": csrf_of(page.text)})
    assert _api(client, new, "/status").status_code == 401


def test_api_settings_parsing():
    from fragdex import apikeys
    assert apikeys.parse_allowed_ips("203.0.113.9, 198.51.100.7/24\n::ffff:192.0.2.1 203.0.113.9") == \
        ("203.0.113.9 198.51.100.0/24 192.0.2.1", None)
    assert apikeys.ip_allowed("198.51.100.200", "198.51.100.0/24")
    assert apikeys.ip_allowed("::ffff:198.51.100.200", "198.51.100.0/24")
    assert not apikeys.ip_allowed("203.0.113.10", "198.51.100.0/24 203.0.113.9")
    assert apikeys.ip_allowed("anything", "")
    assert apikeys.parse_rate("") == (120, None) and apikeys.parse_rate("-1")[0] is None
    assert apikeys.parse_expiry("2026-02-30")[0] is None and apikeys.parse_expiry("") == ("", None)
    # a search from a key without notes access never matches note text
    with db.get_db() as conn:
        conn.execute("UPDATE players SET notes='secret-note-xyz' WHERE steam_id='76561198000000555'")
        assert db.search_players(conn, {"q": "secret-note-xyz"})[0] == 1
        assert db.search_players(conn, {"q": "secret-note-xyz"}, notes=False)[0] == 0


def test_viewer_role(client):
    r = client.get("/admin/users")
    token = csrf_of(r.text)
    client.post("/admin/users", data={"csrf": token, "username": "viewer1", "password": "viewer-pass-123",
                                      "role": "viewer"})
    client.post("/logout", data={"csrf": token})
    assert login(client, "viewer1", "viewer-pass-123").status_code == 303
    assert client.get("/").status_code == 200
    assert client.get("/import").status_code == 403
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/settings").status_code == 403
    assert client.post("/admin/api-keys", data={"csrf": token, "name": "x"}).status_code == 403
    assert client.get("/download/minqlx/fragdex.py").status_code == 403
    r = client.get("/player/76561197960287930")
    assert r.status_code == 200 and "/notes" not in r.text


def test_security_headers(client):
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
