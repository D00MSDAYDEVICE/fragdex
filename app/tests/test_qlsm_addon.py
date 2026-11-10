"""The QLSM addon: its host-side log reader, and its backend against this Fragdex through a stand-in for QLSM."""
import importlib.util
import json
import os
import sys
import time
import types

import pytest
from test_app import _create_key, _edit_key, app, db, login
from fastapi.testclient import TestClient

ADDON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fragdex", "downloads", "qlsm")
SID = "76561197960287930"


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ADDON, *rel.split("/")))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load("assets/collect_connections.py", "fragdex_addon_collect")


def write(folder, name, *lines):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- reading the logs on the host

def test_collect_keeps_only_connections_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    d = str(tmp_path / "chatlogs")
    write(d, "chat.log.2", f"[2026-01-01 10:00:00] Oldest:{SID}:203.0.113.1 connected.")
    write(d, "chat.log.1", f"[2026-01-02 10:00:00] Middle:{SID}:203.0.113.2 connected.")
    write(d, "chat.log",
          f"[2026-01-03 10:00:00] Newest:{SID}:203.0.113.3 connected.",
          f"[2026-01-03 10:00:01] <Bob:{SID}> hello there",
          f"[2026-01-03 10:00:02] <Bob:{SID}> x:76561198000000001:198.51.100.9 connected.",
          f"[2026-01-03 10:00:03] Bob:{SID} disconnected.",
          f"[2026-01-03 10:00:04] [CMD] <Bob:{SID}> kick",
          "[2026-01-03 10:00:05] [MAP] campgrounds (ca)")
    write(d, "notes.txt", f"[2026-01-04 10:00:00] Ignored:{SID}:203.0.113.4 connected.")

    r = collector.collect(d, "", 100)
    assert r["found"] and not r["truncated"] and r["latest"] == "2026-01-03 10:00:00"
    assert [x.split("] ")[1].split(":")[0] for x in r["lines"]] == ["Oldest", "Middle", "Newest"]
    assert not any("hello" in x or "198.51.100.9" in x for x in r["lines"])

    assert [x.split("] ")[1].split(":")[0] for x in collector.collect(d, "2026-01-02 10:00:00", 100)["lines"]] == ["Middle", "Newest"]

    first = collector.collect(d, "", 2)
    assert first["truncated"] and first["latest"] == "2026-01-02 10:00:00" and len(first["lines"]) == 2
    rest = collector.collect(d, first["latest"], 2)
    assert not rest["truncated"] and rest["latest"] == "2026-01-03 10:00:00"

    assert collector.collect(str(tmp_path / "missing"), "", 100) == {"found": False, "lines": [], "latest": "", "truncated": False}


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs a settable time zone")
def test_collect_converts_local_time_to_utc(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "EST5")
    time.tzset()
    try:
        d = str(tmp_path / "chatlogs")
        write(d, "chat.log", f"[2026-01-01 10:00:00] A:{SID}:203.0.113.1 connected.")
        r = collector.collect(d, "", 10)
        assert r["lines"][0].startswith("[2026-01-01 15:00:00]") and r["latest"] == "2026-01-01 10:00:00"
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()


# ---------------------------------------------------------------- the backend, with QLSM stood in for

class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Query:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)

    def get(self, row_id):
        return next((r for r in self.rows if r.id == row_id), None)

    def filter_by(self, host_id):
        return Query([r for r in self.rows if r.host_id == host_id])


class Settings:
    def __init__(self, manifest):
        self.manifest, self.values, self.on = manifest, {}, set()

    def get(self, scope="global", scope_id=0):
        out = {}
        for f in self.manifest["settings"][scope]:
            out[f["key"]] = f.get("default", False if f["type"] == "bool" else 0 if f["type"] == "number" else "")
        out.update(self.values.get((scope, scope_id), {}))
        return out

    def set(self, scope="global", scope_id=0, values=None):
        self.values.setdefault((scope, scope_id), {}).update(values)

    def is_enabled(self, scope="global", scope_id=0):
        if scope == "instance":
            return ("instance", scope_id) in self.on
        return (scope, scope_id) in self.on


class Job:
    def __init__(self):
        self.queued = []

    def queue(self, *args):
        self.queued.append(args)


@pytest.fixture()
def addon(monkeypatch):
    """backend.py loaded against stand-ins for Flask, requests and QLSM's models."""
    flask = types.ModuleType("flask")

    class Blueprint:
        def __init__(self, *a):
            pass

        def route(self, *a, **k):
            return lambda f: f

    flask.Blueprint, flask.jsonify = Blueprint, lambda obj: obj
    flask.request = types.SimpleNamespace(args={})
    status = {}
    flask.current_app = types.SimpleNamespace(extensions={"redis": types.SimpleNamespace(get=status.get)})
    jwt = types.ModuleType("flask_jwt_extended")
    jwt.jwt_required = lambda: (lambda f: f)
    req = types.ModuleType("requests")
    req.RequestException = OSError
    hooks = types.ModuleType("ui.addons.hooks")
    hooks.HOOK_SCOPES = {"poller.tick": "global"}
    models = types.ModuleType("ui.models")
    host = Row(id=1, name="host-1")
    instances = [Row(id=10, host_id=1, port=27960, name="CA one", qlx_plugins="log.py,ban.py"),
                 Row(id=11, host_id=1, port=27961, name="Off server", qlx_plugins=""),
                 Row(id=12, host_id=1, port=27962, name="Plugin server", qlx_plugins="fragdex, log")]
    models.Host, models.QLInstance = Row(query=Query([host])), Row(query=Query(instances))
    for name, mod in (("flask", flask), ("flask_jwt_extended", jwt), ("requests", req), ("ui", types.ModuleType("ui")),
                      ("ui.addons", types.ModuleType("ui.addons")), ("ui.addons.hooks", hooks), ("ui.models", models)):
        monkeypatch.setitem(sys.modules, name, mod)
    backend = load("backend.py", "fragdex_addon_backend")

    with open(os.path.join(ADDON, "qlsm-addon.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    job = Job()
    collected = {}

    def run_playbook(h, playbook, extravars=None):
        ctx.playbook_calls.append((playbook, extravars))
        with open(extravars["fragdex_dest"], "w", encoding="utf-8") as f:
            json.dump(collected, f)
        return True, "", ""

    ctx = types.SimpleNamespace(settings=Settings(manifest), tasks={"fragdex_sync_host": job}, playbook_calls=[], notes=[],
                                run_playbook=run_playbook, logger=types.SimpleNamespace(warning=lambda *a: None),
                                notify=lambda sev, title, **k: ctx.notes.append((title, k.get("body"))))
    backend._ctx = ctx
    ctx.settings.on |= {("global", 0), ("host", 1), ("instance", 10), ("instance", 12)}
    return types.SimpleNamespace(backend=backend, ctx=ctx, job=job, collected=collected, requests=req, request=flask.request,
                                 status=status, flask=flask)


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://testserver") as c:
        yield c


def connect(addon, client, key):
    """Send the addon's requests to this Fragdex, and remember what it asked for."""
    addon.ctx.settings.set("global", 0, {"fragdex_url": "http://testserver/api/v1/", "api_key": key})

    def request(method, url, json=None, timeout=None, headers=None, verify=None):
        addon.sent.append((url, json))
        return client.request(method, url, json=json, headers=headers)

    addon.sent = []
    addon.requests.request = request


def connect_line(day, name, sid, ip="203.0.113.7"):
    return f"[2026-02-{day:02d} 12:00:00] {name}:{sid}:{ip} connected."


def test_addon_sends_only_enabled_servers_and_remembers_position(addon, client):
    assert login(client).status_code == 303
    key, _ = _create_key(client, name="QLSM addon", can_write="1", game_server="1")
    connect(addon, client, key)
    sid = "76561198000000990"
    addon.collected.update({"27960": {"found": True, "lines": [connect_line(1, "AddonOne", sid)], "latest": "2026-02-01 07:00:00", "truncated": False},
                            "27962": {"found": False, "lines": [], "latest": "", "truncated": False}})

    assert addon.backend.sync_host(1) is False
    targets = addon.ctx.playbook_calls[0][1]["fragdex_targets"]
    assert targets == [{"port": 27960, "since": ""}, {"port": 27962, "since": ""}]     # the switched-off server isn't read
    settings = addon.ctx.settings.get("instance", 10)
    assert settings["sent_up_to"] == "2026-02-01 07:00:00" and settings["history_sent"] is True
    with db.get_db() as conn:
        row = conn.execute("SELECT name, ip, source, source_ref, server, seen_at FROM sightings WHERE steam_id=?", (sid,)).fetchone()
        assert tuple(row) == ("AddonOne", "203.0.113.7", "log", "qlsm: CA one", "CA one", "2026-02-01 12:00:00")
        assert conn.execute("SELECT steam_verified FROM players WHERE steam_id=?", (sid,)).fetchone()[0] == 1

    # the next run starts from there, and repeating lines adds nothing
    addon.backend.sync_host(1)
    assert addon.ctx.playbook_calls[1][1]["fragdex_targets"][0] == {"port": 27960, "since": "2026-02-01 07:00:00"}
    with db.get_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sightings WHERE steam_id=?", (sid,)).fetchone()[0] == 1

    # a server's own option, and a server running the plugin once its history is in
    addon.ctx.settings.set("instance", 10, {"sync_logs": False})
    addon.ctx.settings.set("instance", 12, {"sent_up_to": "2026-02-01 07:00:00", "history_sent": True})
    calls = len(addon.ctx.playbook_calls)
    assert addon.backend.sync_host(1) is False and len(addon.ctx.playbook_calls) == calls      # nothing to read


def test_addon_long_history_continues_and_failure_keeps_position(addon, client):
    assert login(client).status_code == 303
    key, _ = _create_key(client, name="QLSM addon two", can_write="1", game_server="1")
    connect(addon, client, key)
    addon.ctx.settings.on.discard(("instance", 12))
    sid = "76561198000000991"
    lines = [connect_line(2, f"Bulk{i}", sid) for i in range(1500)]
    addon.collected["27960"] = {"found": True, "lines": lines, "latest": "2026-02-02 07:00:00", "truncated": True}
    assert addon.backend.sync_host(1) is True
    assert len([u for u, _b in addon.sent if u.endswith("/logs")]) == 2       # sent in batches
    settings = addon.ctx.settings.get("instance", 10)
    assert settings["sent_up_to"] == "2026-02-02 07:00:00" and settings["history_sent"] is False

    # Fragdex refuses: the position doesn't move, and the reason reaches the admin without the key
    addon.ctx.settings.set("global", 0, {"api_key": "fdx_not-a-real-key"})
    addon.collected["27960"] = {"found": True, "lines": lines[:1], "latest": "2026-02-09 07:00:00", "truncated": False}
    with pytest.raises(addon.backend.FragdexError) as err:
        addon.backend.sync_host(1)
    assert "HTTP 401" in str(err.value) and "fdx_not-a-real-key" not in str(err.value)
    assert addon.ctx.settings.get("instance", 10)["sent_up_to"] == "2026-02-02 07:00:00"


def test_addon_tick_queues_on_the_chosen_interval(addon):
    addon.ctx.settings.set("global", 0, {"fragdex_url": "http://testserver", "api_key": "k", "interval_minutes": 15})
    addon.backend._last_run = None      # monotonic time can be smaller than the interval, as on a host that just started
    addon.backend._tick()
    assert addon.job.queued == [(1,)]
    addon.backend._tick()        # 15 seconds later: not yet
    assert addon.job.queued == [(1,)]
    addon.backend._last_run -= 15 * 60 + 1
    addon.backend._tick()
    assert addon.job.queued == [(1,), (1,)]
    addon.ctx.settings.on.discard(("host", 1))
    addon.backend._last_run -= 15 * 60 + 1
    addon.backend._tick()
    assert addon.job.queued == [(1,), (1,)]      # a host that is switched off is left alone


def test_addon_live_column(addon, client):
    assert login(client).status_code == 303
    key, kid = _create_key(client, name="QLSM addon three", can_write="1", game_server="1")
    connect(addon, client, key)
    from fragdex.importers.common import Sighting
    tagged, clean, unknown = "76561198000000992", "76561198000000993", "76561198000000994"
    with db.get_db() as conn:
        with db.transaction(conn):
            db.add_sightings(conn, [Sighting(steam_id=s, name="Col") for s in (tagged, clean)], "demo")
            db.refresh_players(conn, [tagged, clean])
        conn.execute("INSERT OR IGNORE INTO tags(name, color) VALUES('cheater', '#ff0000')")
        conn.execute("INSERT OR IGNORE INTO player_tags(steam_id, tag_id) SELECT ?, id FROM tags WHERE name='cheater'", (tagged,))
        conn.execute("UPDATE players SET vac_bans=1, game_bans=0, steam_checked_at='x' WHERE steam_id=?", (tagged,))
    addon.request.args = {"steam_ids": f"{tagged},{clean},{unknown},junk"}
    r = addon.backend.players(10)
    assert r["configured"] is True
    assert r["data"][tagged]["display"] == "cheater, VAC" and r["data"][tagged]["color"] == "magenta"
    assert "Verified Steam ID" in r["data"][tagged]["title"] and "1 VAC" in r["data"][tagged]["title"]
    assert r["data"][clean]["display"] == "clean" and r["data"][unknown]["display"] == "–"

    assert addon.backend.players(11)["configured"] is False        # switched off for this server
    addon.ctx.settings.set("instance", 10, {"show_column": False})
    assert addon.backend.players(10)["configured"] is False

    # the settings badge says what's wrong with a key
    assert addon.backend.connection()["data"]["ok"] is True
    _edit_key(client, kid, name="QLSM addon three", can_write="1")
    assert "needs Add records and Game server" in addon.backend.connection()["data"]["label"]


def test_addon_players_panel(addon, client):
    assert login(client).status_code == 303
    key, _ = _create_key(client, name="QLSM addon four", can_write="1", game_server="1")
    connect(addon, client, key)
    from fragdex.importers.common import Sighting
    linked, typed, banned, unchecked, unknown = (f"7656119800000100{i}" for i in range(5))
    with db.get_db() as conn:
        with db.transaction(conn):
            db.add_sightings(conn, [Sighting(steam_id=s, name="Panel") for s in (linked, typed, banned)], "demo")
            db.add_sightings(conn, [Sighting(steam_id=unchecked, name="Panel")], "manual")
            db.refresh_players(conn, [linked, typed, banned, unchecked])
        conn.execute("UPDATE players SET discord_id='80351110224678912', discord_name='railgod', discord_status='verified',"
                     " vac_bans=1, game_bans=2, days_since_last_ban=120, steam_checked_at='x' WHERE steam_id=?", (linked,))
        conn.execute("UPDATE players SET discord_id='typedname', vac_bans=0, game_bans=0, steam_checked_at='x' WHERE steam_id=?", (typed,))
        conn.execute("UPDATE players SET vac_bans=0, game_bans=1, days_since_last_ban=1, steam_checked_at='x' WHERE steam_id=?", (banned,))

    key_name = "server:status:1:10"
    assert addon.backend.roster(10)["data"]["message"] == "This server is offline."
    addon.status[key_name] = json.dumps({"players": []})
    assert addon.backend.roster(10)["data"]["message"] == "No players online."
    addon.status[key_name] = json.dumps({"players": [
        {"name": "^1Zed", "steam": linked}, {"name": "^4Amy", "steamid": typed}, {"name": "bob", "steam_id": banned},
        {"name": "Carl", "steam": unchecked}, {"name": "Dee", "steam": unknown}, {"name": "Bot", "steam": "90071996842377"}]})

    answer = addon.backend.roster(10)["data"]
    assert not answer["warning"]
    rows = {r["name"]: r for r in answer["players"]}
    assert [r["name"] for r in answer["players"]] == ["^4Amy", "bob", "Bot", "Carl", "Dee", "^1Zed"]     # stays put between updates

    zed = rows["^1Zed"]
    assert (zed["discord_name"], zed["discord_id"], zed["discord_status"]) == ("railgod", "80351110224678912", "verified")
    assert zed["bans"] == {"display": "VAC 1", "title": "1 VAC ban and 2 game bans, last one 120 days ago", "color": "red"}
    assert (rows["^4Amy"]["discord_name"], rows["^4Amy"]["discord_id"]) == ("typedname", "")    # never copied as an ID
    assert rows["^4Amy"]["bans"]["display"] == "Clean"
    assert rows["bob"]["bans"] == {"display": "BAN 1", "title": "1 game ban, last one 1 day ago", "color": "yellow"}
    assert rows["bob"]["discord_note"] == "Not linked"
    assert rows["Carl"]["bans"]["display"] == "–" and rows["Carl"]["steam_id"] == unchecked
    assert rows["Dee"]["discord_note"] == "Not in Fragdex yet"
    assert rows["Bot"]["steam_id"] == ""
    assert (zed["steam_status"]["display"], zed["steam_status"]["color"]) == ("Verified", "green")
    assert (rows["Carl"]["steam_status"]["display"], rows["Carl"]["steam_status"]["color"]) == ("Claimed", "yellow")
    assert rows["Dee"]["steam_status"]["display"] == "–"

    # switched off for this server
    assert "off for this server" in addon.backend.roster(11)["data"]["message"]

    # Fragdex refuses: the players still list, without claiming anything about them
    addon.ctx.settings.set("global", 0, {"api_key": "fdx_not-a-real-key"})
    answer = addon.backend.roster(10)["data"]
    assert "HTTP 401" in answer["warning"] and "fdx_not-a-real-key" not in answer["warning"]
    assert len(answer["players"]) == 6 and {r["discord_note"] for r in answer["players"]} == {"–"}
    assert {r["steam_status"]["display"] for r in answer["players"]} == {"–"}

    # a QLSM that keeps its status somewhere else says so
    addon.flask.current_app.extensions.clear()
    assert "Couldn't read QLSM's live status" in addon.backend.roster(10)["data"]["message"]


def test_addon_download(client):
    import io
    import zipfile
    from fragdex import main
    assert login(client).status_code == 303
    assert 'href="/download/qlsm/fragdex-qlsm-addon.zip"' in client.get("/admin/settings").text
    r = client.get("/download/qlsm/fragdex-qlsm-addon.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with open(os.path.join(ADDON, "qlsm-addon.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert f'filename="fragdex-qlsm-addon-{manifest["version"]}.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.namelist() == list(main.QLSM_ADDON_FILES)
        assert json.loads(z.read("qlsm-addon.json")) == manifest
    menu = manifest["ui"]["instance_menu"]
    assert [m["label"] for m in menu] == ["Fragdex Settings", "Fragdex Players"]
    assert "status" not in menu[0] and manifest["ui"]["panels"]["instance_form"]["status"]["load"]      # the label needs the row to itself
    assert "ui/" + menu[1]["component"] in main.QLSM_ADDON_FILES
