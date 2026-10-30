"""Parse Quake Live server console logs and minqlx chat logs.

Recognised lines (in order of preference):
  1. minqlx chat log connects:
       [2026-10-29 19:27:35] Railgun:76561197960287930:203.0.113.45 connected.
  2. Lines carrying an info string with a Steam ID, e.g.
       ClientUserinfoChanged: 3 n\\Railgun\\t\\0\\...\\st\\76561197960287930
       Userinfo: \\name\\Railgun\\ip\\1.2.3.4:27960\\steam_id\\7656...
  3. Any other line containing exactly one SteamID64 and exactly one IP
     (recorded without a name).
Rules 2 and 3 are skipped for chat-log lines and "say:"-style chat lines,
since players could type them. Lines without a timestamp are dated with the
file's modification time.
"""
import datetime as dt
import ipaddress
import re

from .common import (STEAM64_ANY_RE, STEAM64_RE, Sighting, clean_name, fmt_dt,
                     normalize_ip, normalize_name, parse_date)

TS_RE = re.compile(r"^\[?(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)(?:[.,]\d+)?\]?\s*")
CHATLOG_RE = re.compile(r"^(?P<name>.+):(?P<sid>7656119\d{10}):(?P<ip>[0-9A-Fa-f.:]+)(?:\s+(?P<rest>.*))?$")
CHAT_PREFIX_RE = re.compile(r"^\s*(say|say_team|sayteam|tell|chat|tchat|print|broadcast)\b", re.I)
IPV4_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?(?![\d.])")
INFO_NAME_KEYS = ("n", "name")
INFO_SID_KEYS = ("st", "steam_id", "steamid", "steam", "sid")


def _parse_info_tail(line: str) -> dict:
    idx = line.find("\\")
    start = line.rfind(" ", 0, idx) + 1
    info = line[start:]
    parts = info[1:].split("\\") if info.startswith("\\") else info.split("\\")
    return {parts[i].lower(): parts[i + 1] for i in range(0, len(parts) - 1, 2)}


def parse_line(line: str, default_ts: str):
    """Return a Sighting or None."""
    line = line.lstrip("﻿").rstrip("\r\n")
    if "7656119" not in line:
        return None
    ts = default_ts
    body = line
    m = TS_RE.match(line)
    if m:
        ts = parse_date(m.group(1)) or default_ts
        body = line[m.end():]

    m = CHATLOG_RE.match(body)
    if m:
        name = m.group("name").strip()
        ip = normalize_ip(m.group("ip"))
        # A chat message ("[ts] Name: text") can't be mistaken for a connect line.
        if ip and ": " not in name and len(clean_name(name)) <= 40:
            return Sighting(steam_id=m.group("sid"), name=normalize_name(name), ip=ip, seen_at=ts)

    # Everything below could be typed by a player in chat, so never trust it in
    # chat logs ("[timestamp] ...") or on server-log chat lines.
    if line.startswith("[") or CHAT_PREFIX_RE.match(body):
        return None

    if "\\" in body:
        info = _parse_info_tail(body)
        sid = next((info[k] for k in INFO_SID_KEYS if STEAM64_RE.match(info.get(k, ""))), None)
        if sid is None:
            sid = next((v for v in info.values() if STEAM64_RE.match(v)), None)
        if sid:
            name = next((info[k] for k in INFO_NAME_KEYS if info.get(k)), "")
            ip = normalize_ip(info.get("ip")) or ""
            if name or ip:
                return Sighting(steam_id=sid, name=normalize_name(name), ip=ip, seen_at=ts)

    sids = set(STEAM64_ANY_RE.findall(body))
    if len(sids) == 1:
        ips = set()
        for cand in IPV4_RE.findall(body):
            try:
                ipaddress.IPv4Address(cand)
                ips.add(cand)
            except ValueError:
                pass
        if len(ips) == 1:
            ip = normalize_ip(ips.pop())
            if ip:
                return Sighting(steam_id=sids.pop(), ip=ip, seen_at=ts)
    return None


def parse_log_text(text: str, mtime: float):
    default_ts = fmt_dt(dt.datetime.fromtimestamp(mtime, dt.timezone.utc).replace(tzinfo=None))
    out = []
    lines = 0
    for line in text.splitlines():
        lines += 1
        s = parse_line(line, default_ts)
        if s:
            out.append(s)
    return out, {"lines": lines}
