"""fragdex-scan: index Quake Live demos on your own PC into a small .fdx file
that you upload on Fragdex's Import page, instead of uploading gigabytes of demos.

  fragdex-scan                      scan the default folders: C:\\demos (Windows) or
                                    /home/demos (Linux), ~/demos, and the demo folders
                                    of every Steam library that has Quake Live
  fragdex-scan FOLDER [FOLDER ...]  scan these folders (subfolders and .zip archives too)

Options:
  -o, --output FILE   where to write the export (default: fragdex-<date>.fdx here)
  --all               include files already exported by an earlier run
  --logs              also index .log/.txt server and minqlx chat logs
  -j, --jobs N        parallel workers (default: number of CPUs)

Only player names, Steam IDs, (for logs) IPs, map/server names and file hashes are
written. Already-exported files are remembered in fragdex-scan-state.json next to the
output, so the next run only picks up new demos.
"""
import argparse
import concurrent.futures
import datetime as dt
import glob
import hashlib
import io
import json
import multiprocessing
import os
import re
import stat
import sys
import time
import zipfile

from . import __version__
from .importers.common import DEMO_EXT_RE, LOG_EXTS
from .importers.demo import parse_demo_stream
from .importers.export import ExportWriter
from .importers.logs import parse_log_text

STATE_FILE = "fragdex-scan-state.json"


# ---------------------------------------------------------------- finding demos

def _steam_roots():
    roots = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                roots.append(winreg.QueryValueEx(k, "SteamPath")[0])
        except OSError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(os.path.join(os.environ[env], "Steam"))
    else:
        roots += [os.path.expanduser(p) for p in ("~/.steam/steam", "~/.local/share/Steam",
                                                  "~/.var/app/com.valvesoftware.Steam/.local/share/Steam")]
    libraries = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        libraries.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                libraries += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
        except OSError:
            pass
    return libraries


def default_folders():
    candidates = [r"C:\demos"] if os.name == "nt" else ["/home/demos"]
    candidates.append(os.path.expanduser("~/demos"))
    for lib in _steam_roots():
        ql = os.path.join(lib, "steamapps", "common", "Quake Live")
        candidates += glob.glob(os.path.join(ql, "*", "baseq3", "demos"))
        candidates.append(os.path.join(ql, "baseq3", "demos"))
    seen, out = set(), []
    for c in candidates:
        key = os.path.normcase(os.path.abspath(c))
        if key not in seen and os.path.isdir(c):
            seen.add(key)
            out.append(c)
    return out


def _kind(name, logs):
    if DEMO_EXT_RE.search(name):
        return "demo"
    if logs and name.lower().endswith(LOG_EXTS):
        return "log"
    return None


def _is_link(path):
    """Junction or symbolic link. Not followed: a junction back to a parent folder would loop forever."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) == 0xA0000003  # mount point


def collect(folders, logs=False):
    """List scannable items: dicts with path, member (inside a zip), name, kind, size, mtime, key."""
    items = []

    def add_file(path):
        name = os.path.basename(path)
        try:
            st = os.stat(path)
        except OSError:
            return
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(path) as z:
                    for info in z.infolist():
                        base = os.path.basename(info.filename.replace("\\", "/"))
                        kind = _kind(base, logs)
                        if kind and not info.is_dir():
                            mtime = time.mktime(info.date_time + (0, 0, -1))
                            items.append({"path": path, "member": info.filename, "name": f"{name}/{base}",
                                          "kind": kind, "size": info.file_size, "mtime": mtime,
                                          "key": f"{os.path.abspath(path)}|{info.filename}|{info.file_size}"})
            except (zipfile.BadZipFile, OSError):
                pass
            return
        kind = _kind(name, logs)
        if kind:
            items.append({"path": path, "member": None, "name": name, "kind": kind, "size": st.st_size,
                          "mtime": st.st_mtime, "key": f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"})

    for folder in folders:
        if os.path.isfile(folder):
            add_file(folder)
            continue
        for dirpath, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not _is_link(os.path.join(dirpath, d))]
            for fn in sorted(files):
                add_file(os.path.join(dirpath, fn))
    return items


# ---------------------------------------------------------------- scanning

def scan_item(item):
    """Runs in a worker process. Returns the item plus sha256 / sightings, or an error."""
    try:
        if item["member"]:
            with zipfile.ZipFile(item["path"]) as z:
                data = z.read(item["member"])
        else:
            with open(item["path"], "rb") as f:
                data = f.read()
        out = dict(item, sha256=hashlib.sha256(data).hexdigest(), map="", server="")
        if item["kind"] == "demo":
            sightings, info = parse_demo_stream(io.BytesIO(data), item["mtime"])
            out.update(map=info["map"], server=info["server"])
        else:
            sightings, _ = parse_log_text(data.decode("utf-8", errors="replace"), item["mtime"])
        out["sightings"] = sightings
        return out
    except Exception as e:  # noqa: BLE001 - report and keep going
        return dict(item, error=f"{type(e).__name__}: {e}")


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def _fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def run(folders, output=None, include_all=False, logs=False, jobs=None, quiet=False):
    """Scan folders and write an .fdx export. Returns a summary dict."""
    say = (lambda *a, **k: None) if quiet else (lambda *a, **k: print(*a, **k, flush=True))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    output = output or f"fragdex-{stamp}.fdx"
    state_path = os.path.join(os.path.dirname(os.path.abspath(output)), STATE_FILE)
    state = {} if include_all else _load_state(state_path)

    items = collect(folders, logs)
    todo = [i for i in items if i["key"] not in state]
    say(f"Found {len(items)} file(s); {len(todo)} new.")
    summary = {"found": len(items), "scanned": 0, "errors": [], "players": 0, "output": None,
               "bytes_in": 0, "bytes_out": 0}
    if not todo:
        say("Nothing new to export.")
        return summary

    created = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    part = output + ".part"
    writer = ExportWriter(part, __version__, created)
    players = set()
    jobs = max(1, jobs or os.cpu_count() or 1)
    t0 = time.time()

    def handle(res, n):
        if "error" in res:
            summary["errors"].append(f"{res['name']}: {res['error']}")
        else:
            writer.add(res["name"], res["kind"], res["sha256"], res["size"], res["sightings"],
                       res["map"], res["server"])
            players.update(s.steam_id for s in res["sightings"])
            state[res["key"]] = res["sha256"]
            summary["bytes_in"] += res["size"]
        summary["scanned"] += 1
        if not quiet and (n % 25 == 0 or n == len(todo)):
            rate = n / max(0.001, time.time() - t0)
            sys.stdout.write(f"\r  {n}/{len(todo)} files  {len(players)} players  {rate:.0f} files/s   ")
            sys.stdout.flush()

    try:
        if jobs == 1:
            for n, item in enumerate(todo, 1):
                handle(scan_item(item), n)
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as ex:
                for n, res in enumerate(ex.map(scan_item, todo, chunksize=8), 1):
                    handle(res, n)
    finally:
        writer.close()
    say("")
    os.replace(part, output)
    _save_state(state_path, state)
    summary.update(players=len(players), output=output, bytes_out=os.path.getsize(output))
    say(f"Wrote {output}: {summary['scanned'] - len(summary['errors'])} file(s), {len(players)} players, "
        f"{_fmt_bytes(summary['bytes_in'])} of demos -> {_fmt_bytes(summary['bytes_out'])}.")
    for e in summary["errors"][:20]:
        say(f"  skipped {e}")
    say("Upload it on Fragdex's Import page, or drop it into the server's watched folder.")
    return summary


def main(argv=None):
    multiprocessing.freeze_support()
    ap = argparse.ArgumentParser(prog="fragdex-scan", description="Index Quake Live demos into a .fdx file "
                                 "for Fragdex.", epilog="Run without folders to scan the default demo folders.")
    ap.add_argument("folders", nargs="*", help="folders, demos or .zip archives to scan")
    ap.add_argument("-o", "--output", help="output .fdx file")
    ap.add_argument("--all", action="store_true", help="include files exported by an earlier run")
    ap.add_argument("--logs", action="store_true", help="also index .log/.txt server and chat logs")
    ap.add_argument("-j", "--jobs", type=int, default=0, help="parallel workers (default: CPU count)")
    ap.add_argument("--version", action="version", version=f"fragdex-scan {__version__}")
    args = ap.parse_args(argv)

    interactive = not args.folders and getattr(sys, "frozen", False)  # double-clicked .exe
    folders = args.folders or default_folders()
    print(f"fragdex-scan {__version__}")
    code = 0
    if not folders:
        print("No demo folders found. Pass one, e.g.:  fragdex-scan \"D:\\path\\to\\demos\"")
        code = 2
    else:
        for f in folders:
            print(f"  scanning {f}")
        output = args.output
        if output and not output.lower().endswith(".fdx"):
            output += ".fdx"
        try:
            run(folders, output, args.all, args.logs, args.jobs or None)
        except KeyboardInterrupt:
            print("\nCancelled.")
            code = 130
    if interactive:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
