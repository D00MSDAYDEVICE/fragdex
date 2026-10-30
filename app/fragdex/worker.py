"""Background threads: import queue, watched folder scanner, Steam refresher."""
import hashlib
import logging
import os
import re
import threading
import time
import traceback

from . import config, db, steam
from .importers.common import file_kind
from .importers.demo import parse_demo
from .importers.export import read_export
from .importers.logs import parse_log_text
from .importers.spreadsheet import parse_sheet

log = logging.getLogger("fragdex.worker")
_wake = threading.Event()
_steam_wake = threading.Event()
_started = False
# steam_state: idle | running | error | nokey;  steam_batch_secs: smoothed time per 100-player batch
status = {"watch_last_scan": None, "watch_error": None, "steam_last_run": None, "steam_state": "idle",
          "steam_batch_secs": None, "steam_next_run": None}


def wake():
    _wake.set()


def wake_steam():
    _steam_wake.set()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def enqueue(conn, kind, filename, stored_path, origin, created_by="", sha=""):
    """Queue an import. Identical demo/sheet files that were already imported are marked duplicate."""
    ts = db.now()
    if sha and kind in ("demo", "sheet", "export"):
        dup = conn.execute("SELECT id FROM imports WHERE sha256=? AND status IN ('done','queued','running') LIMIT 1",
                           (sha,)).fetchone()
        msg = f"Same file as import #{dup['id']}" if dup else ""
        if not dup and kind == "demo":
            # demos imported earlier through a fragdex-scan export
            dup = conn.execute("SELECT filename, import_id FROM imported_files WHERE sha256=?", (sha,)).fetchone()
            msg = f"Already imported via import #{dup['import_id']}" if dup else ""
        if dup:
            conn.execute("INSERT INTO imports(kind, filename, stored_path, sha256, origin, status, message,"
                         " created_by, created_at, finished_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (kind, filename, "", sha, origin, "duplicate", msg, created_by, ts, ts))
            if origin == "upload":
                _remove(stored_path)
            return None
    cur = conn.execute("INSERT INTO imports(kind, filename, stored_path, sha256, origin, status, created_by,"
                       " created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (kind, filename, stored_path, sha, origin, "queued", created_by, ts))
    wake()
    return cur.lastrowid


def _remove(path):
    try:
        if path and os.path.abspath(path).startswith(os.path.abspath(config.UPLOAD_DIR)):
            os.remove(path)
    except OSError:
        pass


def _read_log(conn, job):
    path = job["stored_path"]
    if job["origin"] != "watch":
        with open(path, "rb") as f:
            raw = f.read()
        return raw, None
    row = conn.execute("SELECT log_offset FROM watch_files WHERE path=?", (path,)).fetchone()
    offset = row["log_offset"] if row else 0
    size = os.path.getsize(path)
    if size < offset:
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read(size - offset)
    cut = raw.rfind(b"\n")
    raw = raw[:cut + 1] if cut >= 0 else b""
    return raw, offset + len(raw)


def _mark_imported(conn, sha, filename, import_id):
    if sha:
        conn.execute("INSERT OR IGNORE INTO imported_files(sha256, filename, import_id, created_at) VALUES(?,?,?,?)",
                     (sha, filename, import_id, db.now()))


def _apply_player_meta(conn, e):
    """Notes and tags from a Fragdex export: notes are appended when new, tags are added."""
    sid = e["steam_id"]
    ts = db.now()
    conn.execute("INSERT OR IGNORE INTO players(steam_id, created_at, updated_at) VALUES(?,?,?)", (sid, ts, ts))
    if e["notes"]:
        old = conn.execute("SELECT notes FROM players WHERE steam_id=?", (sid,)).fetchone()["notes"]
        if e["notes"] not in old:
            conn.execute("UPDATE players SET notes=?, updated_at=? WHERE steam_id=?",
                         ((old + "\n\n" + e["notes"]) if old else e["notes"], ts, sid))
    for name, color in e["tags"]:
        conn.execute("INSERT OR IGNORE INTO tags(name, color) VALUES(?,?)", (name, color))
        conn.execute("INSERT OR IGNORE INTO player_tags(steam_id, tag_id) SELECT ?, id FROM tags WHERE name=?",
                     (sid, name))


def process_export(conn, job):
    """Import a .fdx file (fragdex-scan output or a Fragdex export): many files' worth of sightings."""
    path = job["stored_path"]
    files = new = dup = groups = added = 0
    all_sids, seen, batch, meta = set(), set(), [], []

    def flush():
        nonlocal added
        with db.transaction(conn):
            for e in batch:
                a, sids = db.add_sightings(conn, e["sightings"], e["kind"], e["file"], job["id"])
                added += a
                all_sids.update(sids)
                _mark_imported(conn, e["sha256"], e["file"], job["id"])
        batch.clear()

    for e in read_export(path):
        if e["kind"] == "player":
            meta.append(e)
            continue
        sha = e["sha256"]
        if not sha:  # a record group from a Fragdex export: duplicate rows are ignored on insert
            groups += 1
        else:
            files += 1
            if sha in seen or conn.execute("SELECT 1 FROM imported_files WHERE sha256=?", (sha,)).fetchone():
                dup += 1
                continue
            seen.add(sha)
            new += 1
        batch.append(e)
        if len(batch) >= 200:
            flush()
    flush()
    parts = []
    if files:
        parts.append(f"{files} files in export: {new} imported" + (f", {dup} already imported earlier" if dup else ""))
    if groups:
        parts.append(f"{groups} record groups from a Fragdex export")
    if meta:
        parts.append(f"notes/tags for {len(meta)} players")
    with db.transaction(conn):
        for e in meta:
            _apply_player_meta(conn, e)
            all_sids.add(e["steam_id"])
        db.refresh_players(conn, all_sids)
        conn.execute("UPDATE imports SET status='done', players=?, sightings=?, skipped=?, message=?, finished_at=?"
                     " WHERE id=?", (len(all_sids), added, dup, "; ".join(parts) or "Empty export", db.now(),
                                     job["id"]))
    if job["origin"] == "upload" and not config.KEEP_UPLOADS:
        _remove(path)
    return added


def process(conn, job):
    kind, path, name = job["kind"], job["stored_path"], job["filename"]
    skipped = 0
    new_offset = None
    if kind == "export":
        return process_export(conn, job)
    if kind == "demo":
        sightings, info = parse_demo(path)
        source = "demo"
        msg = f"{info['map'] or 'unknown map'}"
        if info["server"]:
            msg += f" on {info['server']}"
        msg += f" - {info['players']} players with Steam IDs"
    elif kind == "sheet":
        sightings, stats = parse_sheet(path, name)
        source = "csv" if name.lower().endswith(".csv") else "xlsx"
        skipped = stats["skipped_bad_id"] + stats["skipped_no_id"]
        msg = f"{stats['rows']} rows"
        if stats["skipped_bad_id"]:
            msg += f"; skipped {stats['skipped_bad_id']} with damaged/invalid Steam IDs"
        if stats["skipped_no_id"]:
            msg += f"; skipped {stats['skipped_no_id']} without a Steam ID"
    elif kind == "log":
        raw, new_offset = _read_log(conn, job)
        sightings, stats = parse_log_text(raw.decode("utf-8", errors="replace"), os.path.getmtime(path))
        source = "log"
        msg = f"{stats['lines']} lines read"
    else:
        raise ValueError(f"Unsupported import type: {kind}")

    with db.transaction(conn):
        added, sids = db.add_sightings(conn, sightings, source, name, job["id"])
        db.refresh_players(conn, sids)
        if kind == "demo":
            _mark_imported(conn, job["sha256"], name, job["id"])
        if new_offset is not None:
            conn.execute("UPDATE watch_files SET log_offset=? WHERE path=?", (new_offset, path))
        if job["origin"] == "watch" and kind == "log" and added == 0:
            conn.execute("DELETE FROM imports WHERE id=?", (job["id"],))
        else:
            conn.execute("UPDATE imports SET status='done', players=?, sightings=?, skipped=?, message=?,"
                         " finished_at=? WHERE id=?", (len(sids), added, skipped, msg, db.now(), job["id"]))
    if job["origin"] == "upload" and not config.KEEP_UPLOADS:
        _remove(path)
    return added


def import_worker():
    alts_dirty = True
    while True:
        try:
            with db.get_db() as conn:
                job = conn.execute("SELECT * FROM imports WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                if job is None:
                    if alts_dirty:
                        with db.transaction(conn):
                            db.recompute_alts(conn)
                        alts_dirty = False
                        wake_steam()  # fetch avatars for the new players now rather than on the next cycle
                    _wake.wait(5)
                    _wake.clear()
                    continue
                conn.execute("UPDATE imports SET status='running' WHERE id=?", (job["id"],))
                try:
                    process(conn, job)
                    alts_dirty = True
                except Exception as e:  # noqa: BLE001 - record any parser failure on the job
                    log.warning("import %s failed: %s", job["id"], traceback.format_exc())
                    conn.execute("UPDATE imports SET status='error', message=?, finished_at=? WHERE id=?",
                                 (f"{type(e).__name__}: {e}"[:500], db.now(), job["id"]))
        except Exception:  # noqa: BLE001 - keep the worker alive
            log.error("import worker error: %s", traceback.format_exc())
            time.sleep(5)


def scan_watch_dir():
    root = config.WATCH_DIR
    if not os.path.isdir(root):
        return
    now = time.time()
    with db.get_db() as conn:
        known = {r["path"]: r for r in conn.execute("SELECT * FROM watch_files")}
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                kind = file_kind(fn)
                if kind not in ("demo", "log", "sheet", "export"):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                row = known.get(path)
                rel = os.path.relpath(path, root)
                if kind == "log":
                    offset = row["log_offset"] if row else 0
                    if st.st_size < offset:
                        offset = 0
                    conn.execute("INSERT INTO watch_files(path, size, mtime, log_offset) VALUES(?,?,?,?) "
                                 "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
                                 "log_offset=excluded.log_offset", (path, st.st_size, st.st_mtime, offset))
                    pending = conn.execute("SELECT 1 FROM imports WHERE stored_path=? AND status IN "
                                           "('queued','running')", (path,)).fetchone()
                    if st.st_size > offset and not pending:
                        enqueue(conn, "log", rel, path, "watch", "watch folder")
                    continue
                if row and row["size"] == st.st_size and row["mtime"] == st.st_mtime:
                    continue
                if now - st.st_mtime < config.WATCH_SETTLE_SECONDS:
                    continue  # probably still being written
                enqueue(conn, kind, rel, path, "watch", "watch folder", sha256_file(path))
                conn.execute("INSERT INTO watch_files(path, size, mtime) VALUES(?,?,?) ON CONFLICT(path) "
                             "DO UPDATE SET size=excluded.size, mtime=excluded.mtime", (path, st.st_size, st.st_mtime))


def watcher():
    while True:
        try:
            scan_watch_dir()
            status["watch_last_scan"] = db.now()
            status["watch_error"] = None
        except Exception as e:  # noqa: BLE001
            status["watch_error"] = str(e)
            log.error("watch scan failed: %s", traceback.format_exc())
        time.sleep(max(10, config.WATCH_INTERVAL))


def steam_refresher():
    last_batch = None
    while True:
        delay = 600
        try:
            with db.get_db() as conn:
                if db.steam_api_key(conn):
                    ids = [r[0] for r in conn.execute(
                        "SELECT steam_id FROM players WHERE steam_checked_at IS NULL OR steam_checked_at < ? "
                        "ORDER BY steam_checked_at IS NOT NULL, steam_checked_at LIMIT 100", (steam.cutoff(),))]
                    if ids:
                        status["steam_state"] = "running"
                        t = time.monotonic()
                        if last_batch and t - last_batch < 120:
                            prev = status["steam_batch_secs"]
                            gap = t - last_batch
                            status["steam_batch_secs"] = gap if prev is None else prev * 0.7 + gap * 0.3
                        last_batch = t
                        steam.refresh(conn, ids)
                        delay = 3 if len(ids) == 100 else 600
                    if delay > 3:
                        status["steam_state"] = "idle"
                        last_batch = None
                    status["steam_last_run"] = db.now()
                    db.set_setting(conn, "steam_last_error", "")
                else:
                    status["steam_state"] = "nokey"
        except Exception as e:  # noqa: BLE001
            msg = re.sub(r"key=[^&\s']+", "key=***", str(e))   # never log or show the API key
            log.warning("steam refresh failed: %s", msg)
            status["steam_state"] = "error"
            last_batch = None
            try:
                with db.get_db() as conn:
                    db.set_setting(conn, "steam_last_error", f"{db.now()}: {msg}"[:300])
            except Exception:  # noqa: BLE001
                pass
            delay = 900
        status["steam_next_run"] = time.time() + delay
        _steam_wake.wait(delay)
        _steam_wake.clear()


def start():
    global _started
    if _started:
        return
    _started = True
    for target in (import_worker, watcher, steam_refresher):
        threading.Thread(target=target, name=target.__name__, daemon=True).start()
