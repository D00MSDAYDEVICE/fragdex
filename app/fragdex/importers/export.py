"""The .fdx format written by fragdex-scan (and by Fragdex's own export) and read by the server.

A .fdx file is gzip-compressed JSON Lines:
  line 1   {"format": "fragdex-scan", "version": 1, "created": "...", "tool": "1.0.0"}
  line 2+  one object per scanned file:
           {"file": "CA-campgrounds-....dm_91", "kind": "demo", "sha256": "...", "size": 1593344,
            "map": "campgrounds", "server": "House of Blud",
            "s": [[steam_id, name, ip, seen_at], ...]}
Version 2 (a server export: Players > Export .fdx) adds:
  - sighting groups of any source (kind "log", "csv", "xlsx" or "manual"; "file" is the
    original file name, "sha256" empty)
  - {"kind": "player", "steam_id": "...", "notes": "...", "tags": [[name, "#rrggbb"], ...]}
Everything read back is re-validated; the file is never trusted.
"""
import gzip
import json
import re

from .common import (Sighting, clean_name, normalize_ip, normalize_name, normalize_steam_id,
                     parse_date)

FORMAT = "fragdex-scan"
FORMAT_VERSION = 2          # newest version this code can read
SCAN_VERSION = 1            # what fragdex-scan writes, so older servers still accept it
SIGHTING_KINDS = ("demo", "log", "csv", "xlsx", "manual")
MAX_LINE = 8 << 20
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ExportWriter:
    """Write a .fdx to a path, or to a binary file object (for streaming downloads)."""

    def __init__(self, target, tool_version, created, version=SCAN_VERSION):
        if isinstance(target, str):
            self._f = gzip.open(target, "wb", compresslevel=9)
        else:
            self._f = gzip.GzipFile(fileobj=target, mode="wb", compresslevel=9, mtime=0)
        self._write({"format": FORMAT, "version": version, "created": created, "tool": tool_version})
        self.entries = 0

    def _write(self, obj):
        self._f.write((json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))

    def add(self, file, kind, sha256, size, sightings, map_name="", server=""):
        self.add_rows(file, kind, sha256, size, [[s.steam_id, s.name, s.ip, s.seen_at] for s in sightings],
                      map_name, server)

    def add_rows(self, file, kind, sha256, size, rows, map_name="", server=""):
        self._write({"file": file, "kind": kind, "sha256": sha256, "size": size, "map": map_name,
                     "server": server, "s": rows})
        self.entries += 1

    def add_player(self, steam_id, notes, tags):
        self._write({"kind": "player", "steam_id": steam_id, "notes": notes, "tags": tags})
        self.entries += 1

    def close(self):
        self._f.close()


def read_export(path):
    """Yield validated entries: dicts with file, kind, sha256, map, server and a sightings list."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = json.loads(f.readline(MAX_LINE) or "{}")
        if header.get("format") != FORMAT:
            raise ValueError("Not a fragdex-scan export (.fdx) file")
        if int(header.get("version", 0)) > FORMAT_VERSION:
            raise ValueError("This export was made by a newer fragdex-scan; update Fragdex")
        while True:
            line = f.readline(MAX_LINE)
            if not line:
                break
            if not line.endswith("\n") and len(line) >= MAX_LINE:
                raise ValueError("Export file has an oversized entry")
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if not isinstance(e, dict):
                continue
            if e.get("kind") == "player":
                sid = normalize_steam_id(e["steam_id"] if isinstance(e.get("steam_id"), str) else None)
                if not sid:
                    continue
                tags = []
                for t in e.get("tags") or []:
                    if isinstance(t, list) and len(t) == 2 and isinstance(t[0], str) and t[0].strip():
                        color = t[1] if isinstance(t[1], str) and COLOR_RE.match(t[1]) else "#e8562a"
                        tags.append((t[0].strip()[:40], color))
                yield {"kind": "player", "steam_id": sid, "notes": str(e.get("notes") or "")[:20000].strip(),
                       "tags": tags[:50]}
                continue
            kind = e.get("kind") if e.get("kind") in SIGHTING_KINDS else "demo"
            server = clean_name(str(e.get("server") or ""))[:80]
            sightings = []
            for row in e.get("s") or []:
                if not isinstance(row, list) or len(row) < 4:
                    continue
                sid = normalize_steam_id(row[0] if isinstance(row[0], str) else None)
                if not sid:
                    continue
                ip = normalize_ip(row[2]) if row[2] else ""
                sightings.append(Sighting(steam_id=sid, name=normalize_name(row[1]), ip=ip or "",
                                          seen_at=parse_date(row[3]), server=server))
            sha = str(e.get("sha256") or "").lower()
            yield {
                "file": str(e.get("file") or "unknown")[:200],
                "kind": kind,
                "sha256": sha if SHA_RE.match(sha) else "",
                "map": str(e.get("map") or "")[:64],
                "server": server,
                "sightings": sightings,
            }
