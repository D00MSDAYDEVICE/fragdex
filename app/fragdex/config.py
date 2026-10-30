import os
import secrets


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


DATA_DIR = os.environ.get("DATA_DIR", "/data")
WATCH_DIR = os.environ.get("WATCH_DIR", "/watch")
DB_PATH = os.path.join(DATA_DIR, "fragdex.sqlite3")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

TLS_MODE = os.environ.get("TLS_MODE", "auto").strip().lower()
COOKIE_SECURE = _bool("COOKIE_SECURE", TLS_MODE != "off")
SESSION_DAYS = _int("SESSION_DAYS", 7)

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

STEAM_API_KEY_ENV = os.environ.get("STEAM_API_KEY", "").strip()
STEAM_REFRESH_HOURS = _int("STEAM_REFRESH_HOURS", 24)

WATCH_INTERVAL = _int("WATCH_INTERVAL", 60)
WATCH_SETTLE_SECONDS = _int("WATCH_SETTLE_SECONDS", 30)
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 1024)
KEEP_UPLOADS = _bool("KEEP_UPLOADS", False)
# IPs shared by more accounts than this (VPNs, LAN cafes, CGNAT) are ignored
# when linking alt accounts. 0 disables the limit.
ALT_IP_MAX_ACCOUNTS = _int("ALT_IP_MAX_ACCOUNTS", 10)


def secret_key():
    env = os.environ.get("SECRET_KEY", "").strip()
    if env:
        return env
    path = os.path.join(DATA_DIR, "secret_key")
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = secrets.token_urlsafe(48)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key
