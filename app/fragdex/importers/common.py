"""Normalisation helpers shared by every importer."""
import datetime as dt
import ipaddress
import re
from dataclasses import dataclass

STEAM64_BASE = 76561197960265728
STEAM64_RE = re.compile(r"^7656119\d{10}$")
STEAM64_ANY_RE = re.compile(r"(?<!\d)7656119\d{10}(?!\d)")
STEAM2_RE = re.compile(r"^STEAM_[0-5]:([01]):(\d+)$", re.I)
STEAM3_RE = re.compile(r"^\[?U:1:(\d+)\]?$", re.I)
PROFILE_URL_RE = re.compile(r"steamcommunity\.com/profiles/(7656119\d{10})(?!\d)")
COLOR_RE = re.compile(r"\^[0-9A-Za-z]")

DEMO_EXT_RE = re.compile(r"\.dm_\d+$", re.I)
LOG_EXTS = (".log", ".txt")
SHEET_EXTS = (".xlsx", ".xlsm", ".csv")
EXPORT_EXT = ".fdx"  # fragdex-scan output


@dataclass(frozen=True)
class Sighting:
    steam_id: str
    name: str = ""
    ip: str = ""
    seen_at: str = ""  # "YYYY-MM-DD HH:MM:SS", or "" when unknown
    server: str = ""


def file_kind(filename: str):
    low = filename.lower()
    if DEMO_EXT_RE.search(low):
        return "demo"
    if low.endswith(LOG_EXTS):
        return "log"
    if low.endswith(SHEET_EXTS):
        return "sheet"
    if low.endswith(".zip"):
        return "zip"
    if low.endswith(EXPORT_EXT):
        return "export"
    return None


def normalize_steam_id(value):
    """Return a 17-digit SteamID64 string, or None.

    Floats are always rejected: a SteamID64 does not fit in a double, so a
    spreadsheet cell stored as a number has already lost its last digits.
    """
    if value is None or isinstance(value, (float, bool)):
        return None
    s = str(value).strip()
    if not s:
        return None
    if STEAM64_RE.match(s):
        return s
    m = STEAM2_RE.match(s)
    if m:
        return str(STEAM64_BASE + int(m.group(2)) * 2 + int(m.group(1)))
    m = STEAM3_RE.match(s)
    if m:
        return str(STEAM64_BASE + int(m.group(1)))
    m = PROFILE_URL_RE.search(s)
    if m:
        return m.group(1)
    return None


def normalize_ip(value):
    if value is None:
        return None
    s = str(value).strip().strip("[]")
    if not s:
        return None
    # drop a :port suffix on IPv4 ("1.2.3.4:27960")
    if s.count(":") == 1 and "." in s:
        s = s.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return None
    if ip.is_unspecified:
        return None
    return str(ip)


def clean_name(name: str) -> str:
    """Strip Quake colour codes and collapse whitespace."""
    return " ".join(COLOR_RE.sub("", name or "").split())


def search_key(name: str) -> str:
    return clean_name(name).casefold()


TRAILING_COLOR_RE = re.compile(r"(\^[0-9A-Za-z])+$")


def normalize_name(name) -> str:
    if name is None:
        return ""
    # QL often appends a meaningless ^7 ("^4Osseous^7") and some names start with
    # one (white is the default); drop those so the same alias isn't stored twice.
    s = TRAILING_COLOR_RE.sub("", " ".join(str(name).split())).strip()
    while s.startswith("^7") and len(s) > 2:
        s = s[2:]
    # QL names are short; anything huge is not a name
    return s[:64]


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y",
)


def fmt_dt(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def parse_date(value) -> str:
    """Best-effort date parsing; returns "" when unknown."""
    if value is None or value == "":
        return ""
    if isinstance(value, dt.datetime):
        return fmt_dt(value)
    if isinstance(value, dt.date):
        return fmt_dt(dt.datetime(value.year, value.month, value.day))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Excel serial date (1900 system)
        if 20000 < value < 80000:
            return fmt_dt(dt.datetime(1899, 12, 30) + dt.timedelta(days=float(value)))
        # unix timestamp
        if 946684800 < value < 4102444800:
            return fmt_dt(dt.datetime.fromtimestamp(value, dt.timezone.utc).replace(tzinfo=None))
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return parse_date(float(s))
    s2 = s.replace("Z", "").split("+")[0].split(".")[0]
    for f in _DATE_FORMATS:
        try:
            return fmt_dt(dt.datetime.strptime(s2, f))
        except ValueError:
            pass
    return ""
