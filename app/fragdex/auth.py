import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from . import config, db

ROLES = ("viewer", "editor", "admin")
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        expect = base64.b64decode(dk)
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(expect))
        return hmac.compare_digest(got, expect)
    except (ValueError, TypeError):
        return False


def password_problem(pw: str):
    if len(pw) < 10:
        return "Password must be at least 10 characters."
    if len(pw) > 256:
        return "Password is too long."
    return None


def has_role(user, role) -> bool:
    return bool(user) and ROLES.index(user["role"]) >= ROLES.index(role)


def bootstrap_admin():
    """Create the first admin account if there are no users yet."""
    with db.get_db() as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        pw = config.ADMIN_PASSWORD
        generated = not pw
        if generated:
            pw = secrets.token_urlsafe(12)
        conn.execute("INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)",
                     (config.ADMIN_USERNAME, hash_password(pw), "admin", db.now()))
    bar = "=" * 64
    if generated:
        print(f"{bar}\n Created admin account '{config.ADMIN_USERNAME}' with password: {pw}\n"
              f" Log in and change it under Account.\n{bar}", flush=True)
    else:
        print(f"{bar}\n Created admin account '{config.ADMIN_USERNAME}' from ADMIN_PASSWORD.\n"
              f" You can remove ADMIN_PASSWORD from .env now.\n{bar}", flush=True)


class LoginThrottle:
    """Per-IP failed login limiter: MAX failures per WINDOW seconds."""
    MAX, WINDOW = 8, 900

    def __init__(self):
        self._fails = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, ip):
        q = self._fails[ip]
        cutoff = time.monotonic() - self.WINDOW
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def blocked(self, ip) -> bool:
        with self._lock:
            return len(self._prune(ip)) >= self.MAX

    def fail(self, ip):
        with self._lock:
            self._prune(ip).append(time.monotonic())

    def reset(self, ip):
        with self._lock:
            self._fails.pop(ip, None)


throttle = LoginThrottle()
