"""Import players from .xlsx / .csv sheets.

Columns are matched by header name (case-insensitive), so the original
Players.xlsx layout and simpler CSVs both work:
  name    : Player Name, Name, Player, Nick
  steam   : Steam ID, SteamID, SteamID64, Steam_ID
  aliases : Aliases, Alias, AKA, Other Names   (split on , ; | and " / ")
  ip      : IP Address, IP, IPs
  date    : Date, Seen, Last Seen, Timestamp
  discord : Discord ID, Discord, Discord Username   (one per player; kept with the player)
  discord_name : Discord Name, Discord Username   (the username, when the sheet also has a Discord ID column)
A row with only a Steam ID and a Discord ID fills in that player's Discord ID and adds no record.
Rows whose Steam ID was stored as a number (e.g. 7.65611980147868E+16) have
lost digits and are skipped.
"""
import csv
import io
import re

from .common import (Sighting, normalize_discord_id, normalize_ip, normalize_name, normalize_steam_id,
                     parse_date)

HEADERS = {
    "name": {"player name", "name", "player", "nick", "nickname", "playername"},
    "steam": {"steam id", "steamid", "steam_id", "steamid64", "steam id64", "steam64", "steam id 64"},
    "aliases": {"aliases", "alias", "aka", "other names", "alt names"},
    "ip": {"ip address", "ip", "ips", "ip addr", "ip addresses", "address"},
    "date": {"date", "seen", "last seen", "date seen", "timestamp", "time", "seen at"},
    "discord": {"discord id", "discord", "discordid", "discord userid", "discord user id"},
    "discord_name": {"discord name", "discord username", "discord user"},
}
ALIAS_SPLIT_RE = re.compile(r"\s*(?:,|;|\||\s/\s)\s*")
IP_SPLIT_RE = re.compile(r"[\s,;|]+")


def _map_header(row):
    cols = {}
    for i, cell in enumerate(row):
        if cell is None:
            continue
        key = " ".join(str(cell).strip().lower().replace("_", " ").split())
        for field, names in HEADERS.items():
            if field not in cols and (key in names or key.replace(" ", "") in names):
                cols[field] = i
    if "discord" not in cols and "discord_name" in cols:     # a username is all this sheet has
        cols["discord"] = cols.pop("discord_name")
    return cols if "steam" in cols else None


def _rows_to_sightings(rows):
    stats = {"rows": 0, "skipped_bad_id": 0, "skipped_no_id": 0, "bad_discord": 0, "discord": {}, "discord_names": {}}
    out = []
    cols = None
    for row in rows:
        row = list(row)
        if cols is None:
            cols = _map_header(row)
            continue
        if not any(c not in (None, "") for c in row):
            continue
        stats["rows"] += 1

        def cell(field):
            i = cols.get(field)
            return row[i] if i is not None and i < len(row) else None

        raw_sid = cell("steam")
        sid = normalize_steam_id(raw_sid)
        if not sid:
            if raw_sid not in (None, ""):
                stats["skipped_bad_id"] += 1
            else:
                stats["skipped_no_id"] += 1
            continue
        name = normalize_name(cell("name"))
        seen = parse_date(cell("date"))
        ips = [ip for ip in (normalize_ip(p) for p in IP_SPLIT_RE.split(str(cell("ip") or ""))) if ip]
        discord = None
        raw_discord = cell("discord")
        if raw_discord not in (None, ""):
            discord = normalize_discord_id(raw_discord)
            if discord:
                stats["discord"][sid] = discord
                discord_name = str(cell("discord_name") or "").strip()[:40]
                if discord_name:
                    stats["discord_names"][sid] = discord_name
            else:
                stats["bad_discord"] += 1
        if name or ips or not discord:     # else there is no sighting here, only a link to a Discord account
            for ip in ips or [""]:
                out.append(Sighting(steam_id=sid, name=name, ip=ip, seen_at=seen))
        aliases = cell("aliases")
        if aliases not in (None, ""):
            for alias in ALIAS_SPLIT_RE.split(str(aliases)):
                alias = normalize_name(alias)
                if alias and alias != name:
                    out.append(Sighting(steam_id=sid, name=alias))
    if cols is None:
        raise ValueError("No header row with a Steam ID column was found")
    return out, stats


def parse_xlsx(path):
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        all_out, total = [], {"rows": 0, "skipped_bad_id": 0, "skipped_no_id": 0, "bad_discord": 0, "discord": {},
                              "discord_names": {}}
        found = False
        for ws in wb.worksheets:
            try:
                out, stats = _rows_to_sightings(ws.iter_rows(values_only=True))
            except ValueError:
                continue
            found = True
            all_out.extend(out)
            for k in total:
                if isinstance(total[k], dict):
                    total[k].update(stats[k])
                else:
                    total[k] += stats[k]
        if not found:
            raise ValueError("No sheet has a header row with a Steam ID column")
        return all_out, total
    finally:
        wb.close()


def parse_csv(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return _rows_to_sightings(csv.reader(io.StringIO(text), dialect))


def parse_sheet(path, filename):
    if filename.lower().endswith(".csv"):
        return parse_csv(path)
    return parse_xlsx(path)
