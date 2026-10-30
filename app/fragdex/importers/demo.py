"""Extract player names and Steam IDs from Quake Live demos (.dm_91).

A demo is a sequence of recorded server->client messages:
    int32 sequence, int32 length, <length bytes Huffman-coded bitstream>
We only read what we need from each message:
  * the initial gamestate's configstrings (server info + one per player), and
  * reliable server commands ("cs <index> ...") that update configstrings when
    players join or rename mid-demo.
Server commands are always written before the snapshot in a message, so we
stop decoding a message as soon as a snapshot or baseline appears and never
have to understand entity/playerstate deltas.
"""
import datetime as dt
import os
import re
import struct

from .common import STEAM64_ANY_RE, STEAM64_RE, Sighting, clean_name, fmt_dt, normalize_name
from .huffman_table import CODES

SVC_NOP, SVC_GAMESTATE, SVC_CONFIGSTRING, SVC_BASELINE = 1, 2, 3, 4
SVC_SERVERCOMMAND, SVC_DOWNLOAD, SVC_SNAPSHOT, SVC_EOF = 5, 6, 7, 8

MAX_MSG_LEN = 1 << 20

# Lookup table indexed by the next PEEK bits of the stream (first stream bit
# = least significant bit), giving (symbol, code length).
_PEEK = max(len(c) for c in CODES.values())
_MASK = (1 << _PEEK) - 1
_TABLE = [None] * (1 << _PEEK)
for _sym, _code in CODES.items():
    _k = len(_code)
    _low = sum(1 << i for i, b in enumerate(_code) if b == "1")
    for _high in range(1 << (_PEEK - _k)):
        _TABLE[_low | (_high << _k)] = (_sym if _sym < 256 else 0, _k)


class EndOfMessage(Exception):
    pass


class BitReader:
    def __init__(self, data: bytes):
        self.size_bits = len(data) * 8
        self.data = data + b"\x00\x00\x00\x00"
        self.bit = 0

    def _raw_bit(self):
        if self.bit >= self.size_bits:
            raise EndOfMessage
        b = (self.data[self.bit >> 3] >> (self.bit & 7)) & 1
        self.bit += 1
        return b

    def _huff_byte(self):
        pos = self.bit
        if pos >= self.size_bits:
            raise EndOfMessage
        i = pos >> 3
        window = (self.data[i] | (self.data[i + 1] << 8) | (self.data[i + 2] << 16)) >> (pos & 7)
        sym, k = _TABLE[window & _MASK]
        if pos + k > self.size_bits:
            raise EndOfMessage
        self.bit = pos + k
        return sym

    def read_bits(self, bits):
        """MSG_ReadBits for a non-OOB message: low bits raw, then Huffman bytes."""
        value = 0
        nbits = bits & 7
        for i in range(nbits):
            value |= self._raw_bit() << i
        for i in range(0, bits - nbits, 8):
            value |= self._huff_byte() << (i + nbits)
        return value

    def read_byte(self):
        return self.read_bits(8)

    def read_short(self):
        v = self.read_bits(16)
        return v - 0x10000 if v & 0x8000 else v

    def read_long(self):
        v = self.read_bits(32)
        return v - 0x100000000 if v & 0x80000000 else v

    def read_string(self, limit=8192):
        out = bytearray()
        while True:
            try:
                c = self.read_byte()
            except EndOfMessage:
                break
            if c == 0:
                break
            if len(out) < limit:
                out.append(c)
        return out.decode("utf-8", errors="replace")


def parse_info(s: str) -> dict:
    """Parse a Quake info string: "\\key\\value\\key\\value" or "key\\value..."."""
    parts = s[1:].split("\\") if s.startswith("\\") else s.split("\\")
    return {parts[i]: parts[i + 1] for i in range(0, len(parts) - 1, 2)}


def player_from_configstring(s: str):
    """Return (steam_id, name) if a configstring looks like a player's, else None."""
    if "\\" not in s or "7656119" not in s:
        return None
    info = parse_info(s)
    name = info.get("n")
    if name is None:
        return None
    sid = info.get("st", "")
    if not STEAM64_RE.match(sid):
        sid = next((v for v in info.values() if STEAM64_RE.match(v)), None)
        if sid is None:
            m = STEAM64_ANY_RE.search(s)
            sid = m.group(0) if m else None
    if not sid:
        return None
    return sid, name


_CS_CMD_RE = re.compile(r'^(cs|bcs0|bcs1|bcs2)\s+(\d+)\s+"(.*)"\s*$', re.S)


class DemoParser:
    def __init__(self):
        self.configstrings = {}
        self.players = {}  # (steam_id, name) -> None, insertion ordered
        self._bcs = {}
        self.messages = 0

    def _set_cs(self, index, value):
        self.configstrings[index] = value
        p = player_from_configstring(value)
        if p:
            self.players.setdefault(p, None)

    def _server_command(self, text):
        m = _CS_CMD_RE.match(text)
        if not m:
            return
        cmd, idx, val = m.group(1), int(m.group(2)), m.group(3)
        if cmd == "cs":
            self._set_cs(idx, val)
        elif cmd == "bcs0":
            self._bcs[idx] = val
        elif cmd == "bcs1":
            self._bcs[idx] = self._bcs.get(idx, "") + val
        else:
            self._set_cs(idx, self._bcs.pop(idx, "") + val)

    def parse_message(self, data: bytes):
        r = BitReader(data)
        try:
            r.read_long()  # reliable acknowledge
            while True:
                cmd = r.read_byte()
                if cmd == SVC_EOF:
                    return
                if cmd == SVC_NOP:
                    continue
                if cmd == SVC_SERVERCOMMAND:
                    r.read_long()
                    self._server_command(r.read_string(1024))
                elif cmd == SVC_GAMESTATE:
                    r.read_long()
                    while True:
                        op = r.read_byte()
                        if op != SVC_CONFIGSTRING:
                            return  # baseline / EOF: nothing else we need
                        idx = r.read_short()
                        self._set_cs(idx, r.read_string(8192))
                else:
                    return  # snapshot, download or unknown
        except EndOfMessage:
            return

    def parse_file(self, fh):
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            _seq, length = struct.unpack("<ii", hdr)
            if length == -1 or length < 0 or length > MAX_MSG_LEN:
                break
            data = fh.read(length)
            if len(data) < length:
                break
            self.messages += 1
            self.parse_message(data)

    @property
    def serverinfo(self):
        return parse_info(self.configstrings.get(0, ""))


def parse_demo(path: str):
    """Returns (sightings, info dict)."""
    with open(path, "rb") as fh:
        return parse_demo_stream(fh, os.path.getmtime(path))


def parse_demo_stream(fh, mtime: float):
    """Parse a demo from a binary file object; mtime dates it if the demo has no start time."""
    p = DemoParser()
    p.parse_file(fh)
    si = p.serverinfo
    seen_at = ""
    try:
        start = int(si.get("g_levelStartTime", "0"))
        if 946684800 < start < 4102444800:
            seen_at = fmt_dt(dt.datetime.fromtimestamp(start, dt.timezone.utc).replace(tzinfo=None))
    except ValueError:
        pass
    if not seen_at:
        seen_at = fmt_dt(dt.datetime.fromtimestamp(mtime, dt.timezone.utc).replace(tzinfo=None))
    server = clean_name(si.get("sv_hostname", ""))[:80]
    sightings = list(dict.fromkeys(
        Sighting(steam_id=sid, name=normalize_name(name), seen_at=seen_at, server=server) for sid, name in p.players))
    info = {
        "messages": p.messages,
        "map": si.get("mapname", ""),
        "server": server,
        "players": len({sid for sid, _ in p.players}),
    }
    return sightings, info
