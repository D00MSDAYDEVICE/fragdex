"""API keys: creation, validation of their settings, per-key rate limits and usage counters.

A key is shown once when it is created; the database keeps only its SHA-256. Everything else about a key
(name, access, allowed IPs, rate limit, expiry, disabled) is a column that can change without a new key.
"""
import datetime as dt
import hashlib
import ipaddress
import re
import secrets
import threading
import time

from . import db

PREFIX = "fdx_"
DEFAULT_RATE = 120
MAX_RATE = 100000
MAX_ALLOWED_IPS = 50
DATE_RE = re.compile(r"^\d{4}-\d\d-\d\d$")


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def new_key():
    """Returns (key, key_hash, prefix). The prefix is what the Settings page shows to tell keys apart."""
    key = PREFIX + secrets.token_urlsafe(32)
    return key, hash_key(key), key[:len(PREFIX) + 6]


def parse_name(value):
    name = re.sub(r"\s+", " ", str(value or "")).strip()[:60]
    return (name, None) if name else (None, "Give the key a name, such as the app or admin that will use it.")


def parse_allowed_ips(text):
    """IPs and CIDR ranges separated by spaces, commas or new lines. Returns (normalised, error)."""
    out = []
    for item in re.split(r"[\s,;]+", str(text or "").strip()):
        if not item:
            continue
        try:
            net = ipaddress.ip_network(item, strict=False)
        except ValueError:
            return None, f"'{item[:50]}' is not an IP address or range (such as 203.0.113.10 or 198.51.100.0/24)."
        addr = net.network_address
        if net.num_addresses == 1 and addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped   # requests are compared as IPv4, see ip_allowed
        s = str(net) if net.num_addresses > 1 else str(addr)
        if s not in out:
            out.append(s)
    if len(out) > MAX_ALLOWED_IPS:
        return None, f"A key can have at most {MAX_ALLOWED_IPS} allowed IP addresses or ranges."
    return " ".join(out), None


def parse_rate(value):
    s = str(value if value is not None else "").strip()
    if s == "":
        return DEFAULT_RATE, None
    if not s.isdigit() or int(s) > MAX_RATE:
        return None, f"Rate limit must be a whole number from 0 (unlimited) to {MAX_RATE:,} requests per minute."
    return int(s), None


def parse_expiry(value):
    s = str(value or "").strip()
    if not s:
        return "", None
    try:
        if DATE_RE.match(s):
            dt.date.fromisoformat(s)
            return s, None
    except ValueError:
        pass
    return None, "Expiry must be a date (YYYY-MM-DD), or empty for a key that never expires."


def today():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def state(key):
    """'active', 'disabled' or 'expired'. A key works through the whole of its expiry day (UTC)."""
    if key["disabled"]:
        return "disabled"
    if key["expires_at"] and key["expires_at"] < today():
        return "expired"
    return "active"


def ip_allowed(ip, allowed):
    if not allowed:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in ipaddress.ip_network(n, strict=False) for n in allowed.split())


class RateLimiter:
    """Requests per key per clock minute. In memory: the app runs as a single process."""

    def __init__(self):
        self._windows = {}
        self._lock = threading.Lock()

    def hit(self, key_id, limit):
        """Count one request. Returns (allowed, remaining, retry_after_seconds)."""
        if limit <= 0:
            return True, None, 0
        now = time.time()
        window = int(now // 60)
        with self._lock:
            w, n = self._windows.get(key_id, (window, 0))
            if w != window:
                n = 0
            if n >= limit:
                return False, 0, int(60 - now % 60) + 1
            self._windows[key_id] = (window, n + 1)
            return True, limit - n - 1, 0

    def reset(self, key_id):
        with self._lock:
            self._windows.pop(key_id, None)


class Usage:
    """Request counters kept in memory and written to the database by the worker every few seconds,
    so answering an API request never waits for the database write lock."""

    def __init__(self):
        self._used = {}
        self._denied = {}
        self._lock = threading.Lock()

    def used(self, key_id, ip):
        with self._lock:
            n = self._used.get(key_id, (0,))[0]
            self._used[key_id] = (n + 1, db.now(), ip)

    def denied(self, key_id, ip, reason):
        with self._lock:
            self._denied[key_id] = (db.now(), ip, reason)

    def flush(self, conn):
        with self._lock:
            used, denied = self._used, self._denied
            self._used, self._denied = {}, {}
        if used:
            conn.executemany("UPDATE api_keys SET requests = requests + ?, last_used_at = ?, last_used_ip = ? "
                             "WHERE id = ?", [(n, at, ip, kid) for kid, (n, at, ip) in used.items()])
        if denied:
            conn.executemany("UPDATE api_keys SET last_denied_at = ?, last_denied_ip = ?, last_denied_reason = ? "
                             "WHERE id = ?", [(at, ip, why, kid) for kid, (at, ip, why) in denied.items()])


limiter = RateLimiter()
usage = Usage()
