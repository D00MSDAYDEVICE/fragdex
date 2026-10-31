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
    assert heads[heads.index("Steam ID") + 1] == "Discord ID" and heads[heads.index("Discord ID") + 1] == "IP Address"
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
    old.execute(create.replace("    discord_id TEXT NOT NULL DEFAULT '',\n", ""))
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
    r = client.get("/player/76561197960287930")
    assert r.status_code == 200 and "/notes" not in r.text


def test_security_headers(client):
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
