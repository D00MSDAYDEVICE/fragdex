import os
import struct

import pytest

from fragdex.importers.common import normalize_ip, normalize_name, normalize_steam_id, parse_date, search_key
from fragdex.importers.demo import parse_demo
from fragdex.importers.huffman_table import CODES
from fragdex.importers.logs import parse_line, parse_log_text
from fragdex.importers.spreadsheet import parse_csv, parse_xlsx

PLAYERS_XLSX = os.environ.get("PLAYERS_XLSX", "")  # optional: the original Players.xlsx


# ---------------------------------------------------------------- huffman / demo

def test_huffman_tree_is_complete_prefix_code():
    codes = list(CODES.values())
    assert len(codes) == 257
    assert sum(2.0 ** -len(c) for c in codes) == pytest.approx(1.0)
    for a in codes:
        for b in codes:
            assert a == b or not b.startswith(a)
    # byte 0 is by far the most frequent symbol in msg_hData
    assert len(CODES[0]) == min(len(c) for c in codes)


class BitWriter:
    """Mirror of MSG_WriteBits for a Huffman-coded (non-OOB) message."""

    def __init__(self):
        self.bits = []

    def write_bits(self, value, bits):
        value &= (1 << bits) - 1
        nbits = bits & 7
        for i in range(nbits):
            self.bits.append((value >> i) & 1)
        value >>= nbits
        for _ in range(0, bits - nbits, 8):
            self.bits.extend(int(b) for b in CODES[value & 0xFF])
            value >>= 8

    def byte(self, v):
        self.write_bits(v, 8)

    def short(self, v):
        self.write_bits(v, 16)

    def long(self, v):
        self.write_bits(v, 32)

    def string(self, s):
        for ch in s.encode():
            self.byte(ch)
        self.byte(0)

    def data(self):
        out = bytearray((len(self.bits) + 7) // 8)
        for i, b in enumerate(self.bits):
            out[i >> 3] |= b << (i & 7)
        return bytes(out)


def _demo_bytes():
    m1 = BitWriter()
    m1.long(0)                     # reliable ack
    m1.byte(2)                     # svc_gamestate
    m1.long(1)
    m1.byte(3); m1.short(0)        # svc_configstring CS_SERVERINFO
    m1.string("\\sv_hostname\\^1Test ^7Server\\mapname\\campgrounds\\g_levelStartTime\\1761766055")
    m1.byte(3); m1.short(529)      # first player
    m1.string("n\\^1Rail^7gun\\t\\1\\model\\sarge\\c\\CA\\st\\76561197960287930\\cn\\")
    m1.byte(3); m1.short(530)      # player without a steam id -> ignored
    m1.string("n\\UnnamedPlayer\\t\\3")
    m1.byte(4)                     # svc_baseline -> parser stops this message

    m2 = BitWriter()
    m2.long(5)
    m2.byte(5); m2.long(2)         # svc_serverCommand: a player joins mid-demo
    m2.string('cs 531 "n\\Latecomer\\t\\2\\st\\76561198000000001"')
    m2.byte(5); m2.long(3)         # a rename
    m2.string('cs 529 "n\\Railgun\\t\\1\\st\\76561197960287930"')
    m2.byte(5); m2.long(4)
    m2.string('chat "Railgun: hi 76561199999999999"')
    m2.byte(7)                     # svc_snapshot -> stop

    out = b""
    for seq, m in enumerate((m1, m2)):
        d = m.data()
        out += struct.pack("<ii", seq, len(d)) + d
    return out + struct.pack("<ii", -1, -1)


def test_demo_parser_reads_gamestate_and_server_commands(tmp_path):
    path = tmp_path / "test.dm_91"
    path.write_bytes(_demo_bytes())
    sightings, info = parse_demo(str(path))
    got = {(s.steam_id, s.name) for s in sightings}
    assert got == {
        ("76561197960287930", "^1Rail^7gun"),
        ("76561197960287930", "Railgun"),
        ("76561198000000001", "Latecomer"),
    }
    assert info["map"] == "campgrounds"
    assert info["server"] == "Test Server"
    assert all(s.seen_at == "2025-10-29 19:27:35" for s in sightings)
    assert all(s.ip == "" for s in sightings)


REAL_DEMO_DIR = os.environ.get("REAL_DEMO_DIR", "")


@pytest.mark.skipif(not os.path.isdir(REAL_DEMO_DIR), reason="set REAL_DEMO_DIR to a folder of .dm_91 demos")
def test_real_demos():
    import glob
    files = sorted(glob.glob(os.path.join(REAL_DEMO_DIR, "*.dm_91")), key=os.path.getmtime)[-10:]
    assert files
    for f in files:
        sightings, info = parse_demo(f)
        assert info["map"], f
        assert sightings, f
        assert all(len(s.steam_id) == 17 and s.steam_id.startswith("7656119") for s in sightings)
        assert all(s.seen_at[:2] == "20" for s in sightings)


def test_scanner_standalone_build_and_export(tmp_path):
    import gzip
    import json
    import subprocess
    import sys
    import zipfile

    from fragdex.importers.export import read_export
    from fragdex.scanner_build import build_script

    demos = tmp_path / "demos"
    (demos / "sub").mkdir(parents=True)
    (demos / "sub" / "a.dm_91").write_bytes(_demo_bytes())
    (demos / "notes.txt").write_text("ignored without --logs")
    with zipfile.ZipFile(demos / "old.zip", "w") as z:
        z.writestr("inner/b.dm_91", _demo_bytes()[:-8] + struct.pack("<ii", -1, -1) + b"x")
    script = tmp_path / "fragdex-scan.py"
    script.write_text(build_script(), encoding="utf-8")
    out = tmp_path / "out" / "x.fdx"
    out.parent.mkdir()

    run = [sys.executable, "-I", str(script), str(demos), "-o", str(out), "-j", "2"]
    r = subprocess.run(run, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "2 file(s)" in r.stdout
    with gzip.open(out, "rt", encoding="utf-8") as f:
        header = json.loads(f.readline())
    assert header["format"] == "fragdex-scan"
    entries = list(read_export(str(out)))
    assert {e["file"] for e in entries} == {"a.dm_91", "old.zip/b.dm_91"}
    assert all(len(e["sha256"]) == 64 for e in entries)
    assert {(s.steam_id, s.name) for s in entries[0]["sightings"]} >= {("76561197960287930", "Railgun")}

    # second run: nothing new, no file written
    r = subprocess.run(run[:-4] + ["-o", str(tmp_path / "out" / "y.fdx")], capture_output=True, text=True,
                       timeout=120)
    assert "Nothing new" in r.stdout and not (tmp_path / "out" / "y.fdx").exists()


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN_SCANNER = os.path.join(APP_DIR, "fragdex", "downloads", "fragdex-scan.exe")


def test_windows_scanner_huffman_table_in_sync():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_huffman_cs", os.path.join(APP_DIR, "tools", "gen_huffman_cs.py"))
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    with open(gen.OUT, encoding="utf-8") as f:
        assert f.read() == gen.render(), "run: python tools/gen_huffman_cs.py"


def _odd_names_demo():
    """One gamestate with names that need JSON escaping, UTF-8 and colour-code normalising."""
    m = BitWriter()
    m.long(0)
    m.byte(2); m.long(1)
    m.byte(3); m.short(0)
    m.string("\\sv_hostname\\^2Ünï  \"Quoted\"   Server\\mapname\\bloodrun\\g_levelStartTime\\0")
    for i, name in enumerate(('^7^7Dé"jà\tvu^7^1', "Ctrl\x01Char", "  spaced   out  ", "^7")):
        m.byte(3); m.short(529 + i)
        m.string(f"n\\{name}\\t\\1\\st\\7656119800000010{i}")
    m.byte(8)
    d = m.data()
    return struct.pack("<ii", 0, len(d)) + d + struct.pack("<ii", -1, -1)


def _folder_link(link, target):
    """A junction on Windows (no admin rights needed), a symlink elsewhere."""
    if os.name == "nt":
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def test_scanner_does_not_follow_folder_links(tmp_path):
    from fragdex.scanner import collect
    root, other = tmp_path / "root", tmp_path / "other"
    (root / "sub").mkdir(parents=True)
    other.mkdir()
    for p in (root / "a.dm_91", root / "sub" / "b.dm_91", other / "c.dm_91"):
        p.write_bytes(b"x")
    _folder_link(root / "sub" / "loop", root)    # would recurse forever
    _folder_link(root / "elsewhere", other)
    assert sorted(i["name"] for i in collect([str(root)])) == ["a.dm_91", "b.dm_91"]


@pytest.mark.skipif(os.name != "nt" or not os.path.isfile(WIN_SCANNER), reason="Windows scanner exe not built")
def test_windows_scanner_matches_python_scanner(tmp_path):
    import gzip
    import json
    import subprocess
    import zipfile

    from fragdex.scanner import run

    demos = tmp_path / "demos"
    (demos / "sub").mkdir(parents=True)
    (demos / "sub" / "a.dm_91").write_bytes(_demo_bytes())
    (demos / "odd.dm_91").write_bytes(_odd_names_demo())
    (demos / "junk.dm_91").write_bytes(os.urandom(5000))
    with zipfile.ZipFile(demos / "old.zip", "w") as z:
        z.writestr("inner/b.dm_91", _odd_names_demo() + b"x")
    _folder_link(demos / "sub" / "loop", demos)
    folders = [str(demos)] + ([REAL_DEMO_DIR] if os.path.isdir(REAL_DEMO_DIR) else [])

    def entries(path):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            assert json.loads(f.readline())["format"] == "fragdex-scan"
            return {e["file"]: e for e in map(json.loads, f)}

    (tmp_path / "py").mkdir()
    run(folders, str(tmp_path / "py" / "x.fdx"), include_all=True, jobs=1, quiet=True)
    r = subprocess.run([WIN_SCANNER, "--out", str(tmp_path / "win" / "x.fdx"), "--all"] + folders, timeout=600)
    assert r.returncode == 0
    py, win = entries(tmp_path / "py" / "x.fdx"), entries(tmp_path / "win" / "x.fdx")
    assert {"a.dm_91", "odd.dm_91", "junk.dm_91", "old.zip/b.dm_91"} <= set(win)
    assert win == py
    with open(tmp_path / "py" / "fragdex-scan-state.json", encoding="utf-8") as f1, \
            open(tmp_path / "win" / "fragdex-scan-state.json", encoding="utf-8") as f2:
        assert json.load(f1) == json.load(f2)   # either tool continues where the other left off


def test_export_reader_rejects_junk(tmp_path):
    import gzip
    import json

    from fragdex.importers.export import read_export
    p = tmp_path / "bad.fdx"
    with gzip.open(p, "wt") as f:
        f.write('{"format":"nope"}\n')
    with pytest.raises(ValueError):
        list(read_export(str(p)))
    with gzip.open(p, "wt") as f:
        f.write('{"format":"fragdex-scan","version":1}\n')
        f.write(json.dumps({"file": "x", "sha256": "zz", "s": [["123", "a", "", ""],
                                                                 ["76561197960287930", "^7ok^7", "1.2.3.4", "2026-01-01"],
                                                                 "junk"]}) + "\n")
    (e,) = list(read_export(str(p)))
    assert e["sha256"] == ""
    assert [(s.steam_id, s.name, s.ip) for s in e["sightings"]] == [("76561197960287930", "ok", "1.2.3.4")]


def test_demo_parser_survives_garbage(tmp_path):
    path = tmp_path / "junk.dm_91"
    path.write_bytes(os.urandom(5000))
    sightings, _ = parse_demo(str(path))
    assert isinstance(sightings, list)


# ---------------------------------------------------------------- logs

TS = "2000-01-01 00:00:00"


def test_minqlx_chatlog_connect_line():
    s = parse_line("[2026-10-29 19:27:35] Railgun:76561197960287930:203.0.113.45 connected.", TS)
    assert (s.steam_id, s.name, s.ip, s.seen_at) == (
        "76561197960287930", "Railgun", "203.0.113.45", "2026-10-29 19:27:35")


def test_chatlog_with_utf8_bom():
    out, _ = parse_log_text("﻿[2026-10-29 19:27:35] Railgun:76561197960287930:1.2.3.4 connected.\n", 0)
    assert (out[0].name, out[0].seen_at) == ("Railgun", "2026-10-29 19:27:35")


def test_chatlog_name_with_colon_and_colors():
    s = parse_line("[2026-10-29 19:27:35] ^1A:B^7c:76561197960287930:1.2.3.4 connected.", TS)
    assert s.name == "^1A:B^7c" and s.ip == "1.2.3.4"


def test_chat_message_cannot_spoof_connect_line():
    assert parse_line("[2026-10-29 19:27:35] Troll: Fake:76561197960287930:9.9.9.9 connected.", TS) is None
    assert parse_line(r"[2026-10-29 19:27:35] Troll: n\Fake\ip\9.9.9.9\st\76561197960287930", TS) is None
    assert parse_line("say: Troll: 76561197960287930 9.9.9.9", TS) is None


def test_userinfo_lines():
    s = parse_line(r"ClientUserinfoChanged: 3 n\Big Boss\t\0\model\keel\st\76561198000000002\c\US", TS)
    assert (s.steam_id, s.name, s.ip, s.seen_at) == ("76561198000000002", "Big Boss", "", TS)
    s = parse_line(r"Userinfo: \name\Foo\ip\5.6.7.8:27960\steam_id\76561198000000003", TS)
    assert (s.name, s.ip) == ("Foo", "5.6.7.8")


def test_generic_line_one_id_one_ip():
    s = parse_line("2026-01-02 03:04:05 kick 76561198000000004 from 8.8.4.4:27960 reason", TS)
    assert (s.steam_id, s.name, s.ip, s.seen_at) == ("76561198000000004", "", "8.8.4.4", "2026-01-02 03:04:05")
    assert parse_line("76561198000000004 and 76561198000000005 on 8.8.4.4", TS) is None


def test_log_text_skips_noise():
    out, stats = parse_log_text("hello\nworld\n[2026-10-29 19:27:35] X:76561197960287930:1.1.1.1 connected.\n",
                                0)
    assert stats["lines"] == 3 and len(out) == 1


# ---------------------------------------------------------------- normalisation

def test_steam_id_formats():
    assert normalize_steam_id("76561197960287930") == "76561197960287930"
    assert normalize_steam_id("STEAM_0:0:11101") == "76561197960287930"
    assert normalize_steam_id("[U:1:22202]") == "76561197960287930"
    assert normalize_steam_id("https://steamcommunity.com/profiles/76561197960287930/") == "76561197960287930"
    assert normalize_steam_id(7.65611980147868e16) is None
    assert normalize_steam_id("7.65611980147868E+16") is None
    assert normalize_steam_id("1234") is None


def test_ip_and_dates():
    assert normalize_ip("1.2.3.4:27960") == "1.2.3.4"
    assert normalize_ip("999.1.1.1") is None
    assert normalize_ip("0.0.0.0") is None
    assert parse_date(45607) == "2024-11-11 00:00:00"
    assert parse_date("10/29/2026") == "2026-10-29 00:00:00"
    assert search_key("^1Sl^7UG  x") == "slug x"
    assert normalize_name("^4Osseous^7") == "^4Osseous"
    assert normalize_name("bong^2™^7") == "bong^2™"
    assert normalize_name("LK^15^7") == "LK^15"
    assert normalize_name("^7CHEW^1x") == "CHEW^1x"
    assert normalize_name("^7") == ""


def test_csv_import(tmp_path):
    p = tmp_path / "p.csv"
    p.write_text("Name,SteamID,IP,Aliases\nfoo,76561198000000009,1.2.3.4 5.6.7.8,bar / baz\n"
                 "bad,7.65612E+16,1.1.1.1,\n", encoding="utf-8")
    out, stats = parse_csv(str(p))
    assert stats == {"rows": 2, "skipped_bad_id": 1, "skipped_no_id": 0, "bad_discord": 0, "discord": {},
                     "discord_names": {}}
    assert {(s.name, s.ip) for s in out} == {("foo", "1.2.3.4"), ("foo", "5.6.7.8"), ("bar", ""), ("baz", "")}


def test_csv_discord_column(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Player Name,Steam ID,Discord ID\na,76561198000000001,<@80351110224678912>\n"
                 "b,76561198000000002,@Frost.bite\nc,76561198000000003,8.03511102246789E+16\n"
                 "d,76561198000000004,\n", encoding="utf-8")
    _out, stats = parse_csv(str(p))
    assert stats["discord"] == {"76561198000000001": "80351110224678912", "76561198000000002": "frost.bite"}
    assert stats["bad_discord"] == 1


def test_csv_steam_to_discord_links(tmp_path):
    p = tmp_path / "links.csv"
    p.write_text("steamid,discordid,discord_name\n"
                 "76561198000000001,80351110224678912,\n"
                 "76561198000000002,80351110224678913,frost.bite\n"
                 "80351110224678914,76561198000000003,\n"       # columns swapped: no Steam ID in the Steam column
                 "80351110224678915,80351110224678915,\n", encoding="utf-8")
    out, stats = parse_csv(str(p))
    assert out == []                                            # no empty records for rows that only link accounts
    assert stats["rows"] == 4 and stats["skipped_bad_id"] == 2
    assert stats["discord"] == {"76561198000000001": "80351110224678912", "76561198000000002": "80351110224678913"}
    assert stats["discord_names"] == {"76561198000000002": "frost.bite"}


def test_csv_discord_username_column_alone(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("Name,Steam ID,Discord Username\na,76561198000000001,Frost.Bite\n", encoding="utf-8")
    out, stats = parse_csv(str(p))
    assert len(out) == 1 and stats["discord"] == {"76561198000000001": "frost.bite"} and stats["discord_names"] == {}


def test_discord_ids_and_local_ips():
    from fragdex.importers.common import is_local_ip, normalize_discord_id
    for good, want in (("80351110224678912", "80351110224678912"), ("<@!80351110224678912>", "80351110224678912"),
                       ("https://discord.com/users/80351110224678912", "80351110224678912"),
                       ("Some.User", "some.user"), ("@name_1", "name_1"), ("Old Name#1234", "Old Name#1234")):
        assert normalize_discord_id(good) == want
    for bad in ("", "x", "1234", "8.03511102246789E+16", 80351110224678912, 8.03e16, "has space", "a" * 33):
        assert normalize_discord_id(bad) is None, bad
    assert all(is_local_ip(x) for x in ("127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1"))
    assert not any(is_local_ip(x) for x in ("203.0.113.45", "10.0.0.1", "192.168.1.5", "2001:db8::1", "junk"))


@pytest.mark.skipif(not os.path.exists(PLAYERS_XLSX), reason="Players.xlsx not available")
def test_players_xlsx():
    out, stats = parse_xlsx(PLAYERS_XLSX)
    assert stats["skipped_bad_id"] == 268
    assert len({s.steam_id for s in out}) == 861
    assert all(len(s.steam_id) == 17 for s in out)
