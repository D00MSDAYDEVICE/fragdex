"""VPN, Tor and proxy labels for recorded IP addresses.

Free lists of network ranges (the "base IPs") are downloaded first and matched locally. proxycheck.io, which needs a
key and has a daily limit, is only asked about addresses that no list and no earlier answer settles. When proxycheck
says an address is a VPN on a hosting network, the network range it gives is kept too, so other addresses in that
range are labelled without asking again.

Every source is downloaded on its own. One that can't be reached, or sends something unusable, keeps the ranges it
had: they are only replaced once a new list has downloaded and parsed completely.

Tables: ip_ranges (the ranges, by source), ip_flags (one row per recorded address with its label), ip_sources (each
source's last result).
"""
import dataclasses
import datetime as dt
import ipaddress
import json
import logging
import re
from urllib.parse import urlsplit

import httpx

from . import __version__, config, db

log = logging.getLogger("fragdex.vpn")

KINDS = ("tor", "proxy", "vpn", "host")     # when several ranges match, the first of these wins
ANON = ("tor", "proxy", "vpn")              # what counts as hiding behind something
LABELS = {"vpn": "VPN", "tor": "Tor", "proxy": "Proxy", "host": "Host"}
ANON_SQL = "('tor','proxy','vpn')"

MAX_BYTES = 50 * 1024 * 1024    # per download
TIMEOUT = 30                    # seconds without data before a download gives up
BASE_DAYS = 7                   # the base lists are downloaded again after this
CHECK_DAYS = 30                 # proxycheck answers, and the ranges they gave, are asked about again after this
SHRINK = 0.5                    # a list less than half its last size is set aside as broken
BATCH = 100                     # addresses per proxycheck request
DEFAULT_BUDGET = 900            # proxycheck lookups a day; the free key allows 1,000
RECENT_DAYS = 90                # addresses seen within this are looked up first
# a proxycheck range is applied to other addresses only when it's at least this narrow
MIN_PREFIX = {4: 20, 6: 40}
PROXYCHECK_URL = "https://proxycheck.io/v3/"


class SourceError(Exception):
    """A base list couldn't be used this time. The message is shown on the Settings page."""


# ---------------------------------------------------------------- parsers: bytes -> (network text, provider)

def _text(body):
    return body.decode("utf-8", errors="replace")


def parse_lines(body, provider=""):
    """One address or range per line. '#' starts a comment; a comment after an address names its provider."""
    for line in _text(body).splitlines():
        value, _, comment = line.partition("#")
        value = value.strip().split()[0] if value.strip() else ""
        if value:
            yield value, (comment.strip().title() if comment.strip() and not provider else provider)


def parse_csv_first(body, provider=""):
    """A geofeed (RFC 8805) or similar CSV: the range is the first column."""
    for line in _text(body).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            yield line.split(",", 1)[0].strip(), provider


def _json(body):
    try:
        return json.loads(body)
    except ValueError:
        raise SourceError("The answer wasn't valid JSON.") from None


def parse_mullvad(body, provider=""):
    data = _json(body)
    if not isinstance(data, list):
        raise SourceError("The answer wasn't a list of servers.")
    for r in data:
        if isinstance(r, dict) and r.get("type") == "wireguard":
            for k in ("ipv4_addr_in", "ipv6_addr_in"):
                if r.get(k):
                    yield str(r[k]), provider


def parse_nordvpn(body, provider=""):
    data = _json(body)
    if not isinstance(data, list):
        raise SourceError("The answer wasn't a list of servers.")
    for r in data:
        if isinstance(r, dict):
            for k in ("station", "ipv6_station"):
                if r.get(k):
                    yield str(r[k]), provider


def parse_aws(body, provider=""):
    data = _json(body)
    for r in data.get("prefixes", []) if isinstance(data, dict) else []:
        yield str(r.get("ip_prefix", "")), provider
    for r in data.get("ipv6_prefixes", []) if isinstance(data, dict) else []:
        yield str(r.get("ipv6_prefix", "")), provider


def parse_gcp(body, provider=""):
    data = _json(body)
    for r in data.get("prefixes", []) if isinstance(data, dict) else []:
        yield str(r.get("ipv4Prefix") or r.get("ipv6Prefix") or ""), provider


def parse_oracle(body, provider=""):
    data = _json(body)
    for region in data.get("regions", []) if isinstance(data, dict) else []:
        for k in ("cidrs", "ipv6_cidrs"):
            for r in region.get(k, []) or []:
                yield str(r.get("cidr", "")), provider


# ---------------------------------------------------------------- sources

@dataclasses.dataclass(frozen=True)
class Source:
    id: str
    name: str
    kind: str
    urls: tuple             # (url, parser, provider)
    default: bool
    home: str = ""          # page about the list, linked from Settings
    note: str = ""


X4B = "https://raw.githubusercontent.com/X4BNet/lists_vpn/main/output/"
SOURCES = (
    Source("x4b_vpn", "X4BNet VPN list", "vpn",
           ((X4B + "vpn/ipv4.txt", parse_lines, ""), (X4B + "vpn/ipv6.txt", parse_lines, "")), True,
           "https://github.com/X4BNet/lists_vpn", "Networks of known VPN providers. MIT licence."),
    Source("tor", "Tor exit list", "tor", (("https://check.torproject.org/torbulkexitlist", parse_lines, ""),), True,
           "https://check.torproject.org/", "The Tor Project's list of current exit relays."),
    Source("x4b_dc", "X4BNet datacenter list", "host",
           ((X4B + "datacenter/ipv4.txt", parse_lines, ""), (X4B + "datacenter/ipv6.txt", parse_lines, "")), True,
           "https://github.com/X4BNet/lists_vpn", "Hosting networks, where most VPNs run. MIT licence."),
    Source("mullvad", "Mullvad servers", "vpn",
           (("https://api.mullvad.net/www/relays/all/", parse_mullvad, "Mullvad"),), False,
           "https://mullvad.net/en/servers", "Mullvad's own server list. Not a documented API, so it may change."),
    Source("nordvpn", "NordVPN servers", "vpn",
           (("https://api.nordvpn.com/v1/servers?limit=0&fields%5Bservers.station%5D&fields%5Bservers.ipv6_station%5D",
             parse_nordvpn, "NordVPN"),), False,
           "https://nordvpn.com/servers/", "NordVPN's own server list. Not a documented API, so it may change."),
    Source("az0", "az0 VPN list", "vpn", (("http://az0-vpnip-public.oooninja.com/ip.txt", parse_lines, ""),), False,
           "https://github.com/az0/vpn_ip", "ProtonVPN, Windscribe and a few others. Downloaded over plain HTTP."),
    Source("cloud", "Cloud providers", "host",
           (("https://ip-ranges.amazonaws.com/ip-ranges.json", parse_aws, "AWS"),
            ("https://www.gstatic.com/ipranges/cloud.json", parse_gcp, "Google Cloud"),
            ("https://docs.oracle.com/en-us/iaas/tools/public_ip_ranges.json", parse_oracle, "Oracle Cloud"),
            ("https://digitalocean.com/geo/google.csv", parse_csv_first, "DigitalOcean"),
            ("https://geoip.linode.com/", parse_csv_first, "Linode")), False,
           "", "AWS, Google Cloud, Oracle, DigitalOcean and Linode's own lists. Mostly in the datacenter list already."),
)
BY_ID = {s.id: s for s in SOURCES}
UPLOAD = "upload:"          # uploaded lists are the sources upload:vpn, upload:host, ...


def source_name(source_id):
    if source_id == "proxycheck":
        return "proxycheck.io"
    if source_id.startswith(UPLOAD):
        return f"Uploaded {LABELS.get(source_id[len(UPLOAD):], '')} list"
    s = BY_ID.get(source_id)
    return s.name if s else source_id


def _order(source_id):
    """Ties between ranges of the same kind: proxycheck first, then the built-in lists in order, then uploads."""
    if source_id == "proxycheck":
        return 0
    s = BY_ID.get(source_id)
    return SOURCES.index(s) + 1 if s else 100


# ---------------------------------------------------------------- settings

def enabled(conn):
    return db.get_setting(conn, "vpn_enabled") == "1"


def proxycheck_key(conn):
    return config.PROXYCHECK_KEY_ENV or db.get_setting(conn, "proxycheck_key")


def budget(conn):
    try:
        return max(0, int(db.get_setting(conn, "vpn_budget", str(DEFAULT_BUDGET))))
    except ValueError:
        return DEFAULT_BUDGET


def skip_host(conn):
    return db.get_setting(conn, "vpn_skip_host") == "1"


def source_states(conn):
    """{source id: ip_sources row as a dict}."""
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM ip_sources")}


def source_enabled(source, states):
    row = states.get(source.id)
    return source.default if not row or row["enabled"] is None else bool(row["enabled"])


def enabled_sources(conn):
    states = source_states(conn)
    return [s for s in SOURCES if source_enabled(s, states)]


def set_source_enabled(conn, source_id, on):
    """Turn a built-in list on or off. Turning one off removes its ranges straight away."""
    conn.execute("INSERT INTO ip_sources(id, enabled) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled",
                 (source_id, int(on)))
    if not on and conn.execute("SELECT 1 FROM ip_ranges WHERE source=? LIMIT 1", (source_id,)).fetchone():
        with db.transaction(conn):
            conn.execute("DELETE FROM ip_ranges WHERE source=?", (source_id,))
            conn.execute("UPDATE ip_sources SET ranges=0, loaded_at='', error='', error_at='' WHERE id=?", (source_id,))
            conn.execute("UPDATE ip_flags SET matched=0")
        return True
    return False


def _record(conn, source_id, ranges=None, error=None):
    conn.execute("INSERT OR IGNORE INTO ip_sources(id) VALUES(?)", (source_id,))
    if error is None:
        conn.execute("UPDATE ip_sources SET ranges=?, loaded_at=?, error='', error_at='' WHERE id=?",
                     (ranges, db.now(), source_id))
    else:
        conn.execute("UPDATE ip_sources SET error=?, error_at=? WHERE id=?", (error[:300], db.now(), source_id))


def _days_ago(days):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def base_due(conn):
    last = db.get_setting(conn, "vpn_base_at")
    return not last or last < _days_ago(BASE_DAYS)


# ---------------------------------------------------------------- downloading the base lists

def _quiet(**_progress):
    """The default progress callback."""


def _client():
    return httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": f"Fragdex/{__version__}"})


def _host(url):
    return urlsplit(url).hostname or url


def download(client, url, progress=lambda done, total: None):
    """The body of url, up to MAX_BYTES. Raises SourceError with a reason an admin can read."""
    host = _host(url)
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                raise SourceError(f"{host} returned HTTP {r.status_code}.")
            size = r.headers.get("content-length", "")
            total = int(size) if size.isdigit() else None
            if total and total > MAX_BYTES:
                raise SourceError(f"{host} sent a list over {MAX_BYTES // 1048576} MB, so it was left out.")
            chunks, n = [], 0
            progress(0, total)
            for chunk in r.iter_bytes():
                n += len(chunk)
                if n > MAX_BYTES:
                    raise SourceError(f"{host} sent a list over {MAX_BYTES // 1048576} MB, so it was left out.")
                chunks.append(chunk)
                progress(r.num_bytes_downloaded, total)
            return b"".join(chunks)
    except httpx.TimeoutException:
        raise SourceError(f"Couldn't reach {host}: it timed out.") from None
    except httpx.HTTPError as e:
        raise SourceError(f"Couldn't reach {host}: {type(e).__name__}.") from None


def networks(items):
    """(network text, provider) pairs -> sorted, non-overlapping (network, provider), and the number left out.
    Two CIDR ranges are either apart or one inside the other; a range inside another is dropped."""
    nets, bad = [], 0
    for value, provider in items:
        try:
            net = ipaddress.ip_network(value.strip(), strict=False)
        except (ValueError, AttributeError):
            bad += 1
            continue
        # never a whole chunk of the internet, loopback, link-local, multicast or an unspecified address
        if (net.prefixlen < (8 if net.version == 4 else 16) or net.is_loopback or net.is_link_local
                or net.is_unspecified or net.is_multicast):
            bad += 1
            continue
        nets.append((net, provider[:80]))
    nets.sort(key=lambda x: (db.ip_bytes(x[0].network_address), x[0].prefixlen))
    out, end = [], None
    for net, provider in nets:
        first = db.ip_bytes(net.network_address)
        if end is not None and first <= end:
            continue    # inside the range before it
        out.append((net, provider))
        end = db.ip_bytes(net.broadcast_address)
    return out, bad


def _store(conn, source_id, kind, nets, report=_quiet, name=""):
    """Replace a source's ranges with nets, in one transaction, and have every address matched again."""
    ts = db.now()
    rows = [(source_id, kind, db.ip_bytes(n.network_address), db.ip_bytes(n.broadcast_address), str(n), p, ts)
            for n, p in nets]
    with db.transaction(conn):
        conn.execute("DELETE FROM ip_ranges WHERE source=?", (source_id,))
        for i in range(0, len(rows), 5000):
            conn.executemany("INSERT INTO ip_ranges(source, kind, first, last, cidr, provider, added_at)"
                             " VALUES(?,?,?,?,?,?,?)", rows[i:i + 5000])
            report(stage="save", source=name, done=min(i + 5000, len(rows)), total=len(rows))
        _record(conn, source_id, len(rows))
        conn.execute("UPDATE ip_flags SET matched=0")


def refresh_base(conn, report=_quiet, only=None):
    """Download every turned-on base list, or those in `only`, and replace each one's ranges. A list that fails keeps
    the ranges it had. Returns {source id: None or the error}."""
    sources = [s for s in enabled_sources(conn) if only is None or s.id in only]
    states = source_states(conn)
    results = {}
    with _client() as client:
        for number, src in enumerate(sources, 1):
            def progress(done, total, src=src, number=number):
                report(stage="download", source=src.name, number=number, count=len(sources), done=done, total=total)
            progress(0, None)
            try:
                items = []
                for url, parser, provider in src.urls:
                    body = download(client, url, progress)
                    try:
                        items.extend(parser(body, provider))
                    except (AttributeError, TypeError):
                        raise SourceError(f"{_host(url)} sent a list in a form Fragdex doesn't recognise.") from None
                nets, _bad = networks(items)
                if not nets:
                    raise SourceError("The list was empty.")
                before = (states.get(src.id) or {}).get("ranges") or 0
                if before and len(nets) < before * SHRINK:
                    raise SourceError(f"The list had {len(nets):,} ranges, under half of the {before:,} it had last"
                                      " time, so it was set aside.")
                _store(conn, src.id, src.kind, nets,
                       lambda **p: report(**p, number=number, count=len(sources)), src.name)
                results[src.id] = None
            except SourceError as e:
                log.warning("VPN list %s: %s", src.id, e)
                _record(conn, src.id, error=str(e))
                results[src.id] = str(e)
    if only is None:    # the weekly schedule counts from the last time every list was downloaded
        db.set_setting(conn, "vpn_base_at", db.now())
    return results


def store_upload(conn, kind, body):
    """An uploaded list of addresses and ranges, one per line, labelled kind. Replaces the last upload of that kind.
    Returns (ranges kept, lines left out)."""
    if kind not in LABELS:
        raise SourceError("Choose what the list is: VPN, Tor, proxy or hosting.")
    nets, bad = networks(parse_lines(body))
    if not nets:
        raise SourceError("The file had no addresses or ranges in it.")
    _store(conn, UPLOAD + kind, kind, nets)
    conn.execute("UPDATE ip_sources SET enabled=1 WHERE id=?", (UPLOAD + kind,))
    return len(nets), bad


def remove_upload(conn, source_id):
    with db.transaction(conn):
        conn.execute("DELETE FROM ip_ranges WHERE source=?", (source_id,))
        conn.execute("DELETE FROM ip_sources WHERE id=?", (source_id,))
        conn.execute("UPDATE ip_flags SET matched=0")


# ---------------------------------------------------------------- matching recorded addresses

def add_new_addresses(conn):
    """Give every recorded address a row in ip_flags. Only records added since the last call are read."""
    mark = int(db.get_setting(conn, "vpn_sightings_mark", "0") or 0)
    top = conn.execute("SELECT COALESCE(MAX(id), 0) FROM sightings").fetchone()[0]
    if top <= mark:
        return 0
    added = conn.execute("""INSERT OR IGNORE INTO ip_flags(ip, bin)
        SELECT ip, ip_bin(ip) FROM (SELECT DISTINCT ip FROM sightings WHERE id > ? AND id <= ? AND ip != '')
        WHERE ip_bin(ip) IS NOT NULL""", (mark, top)).rowcount
    db.set_setting(conn, "vpn_sightings_mark", str(top))
    return added


def _best(conn, sources, b):
    best = None
    for source in sources:
        r = conn.execute("SELECT source, kind, provider, last FROM ip_ranges WHERE source=? AND first <= ?"
                         " ORDER BY first DESC LIMIT 1", (source, b)).fetchone()
        if r and r["last"] >= b:
            rank = (KINDS.index(r["kind"]), r["provider"] == "", _order(r["source"]))
            if best is None or rank < best[0]:
                best = (rank, r)
    return best[1] if best else None


def match(conn, report=_quiet):
    """Label every address not matched against the current ranges yet. proxycheck's own answer for an address wins
    over any range. Returns how many addresses started or stopped counting as VPN, Tor or proxy."""
    total = conn.execute("SELECT COUNT(*) FROM ip_flags WHERE matched=0").fetchone()[0]
    if not total:
        return 0
    sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM ip_ranges")]
    done = changed = 0
    while True:
        rows = conn.execute("SELECT ip, bin, kind, pc_kind, pc_provider FROM ip_flags WHERE matched=0 LIMIT 2000").fetchall()
        if not rows:
            break
        updates = []
        for row in rows:
            if row["pc_kind"] is not None:
                kind, provider, via = row["pc_kind"], row["pc_provider"], "proxycheck"
            else:
                r = _best(conn, sources, row["bin"])
                kind, provider, via = (r["kind"], r["provider"], r["source"]) if r else ("", "", "")
            if (kind in ANON) != (row["kind"] in ANON):
                changed += 1
            updates.append((kind, provider, via, row["ip"]))
        with db.transaction(conn):
            conn.executemany("UPDATE ip_flags SET kind=?, provider=?, via=?, matched=1 WHERE ip=?", updates)
        done += len(rows)
        report(stage="match", done=min(done, total), total=total)
    return changed


def expire_ranges(conn):
    """Ranges proxycheck gave more than CHECK_DAYS ago are dropped; asking again about their addresses brings back
    the ones still in use."""
    old = conn.execute("SELECT first, last FROM ip_ranges WHERE source='proxycheck' AND added_at < ?",
                       (_days_ago(CHECK_DAYS),)).fetchall()
    if not old:
        return 0
    with db.transaction(conn):
        for r in old:
            conn.execute("UPDATE ip_flags SET matched=0 WHERE bin BETWEEN ? AND ?", (r["first"], r["last"]))
        conn.execute("DELETE FROM ip_ranges WHERE source='proxycheck' AND added_at < ?", (_days_ago(CHECK_DAYS),))
    return len(old)


def forget_unrecorded(conn):
    """Addresses no longer in any record (deleted players or records)."""
    conn.execute("DELETE FROM ip_flags WHERE ip NOT IN (SELECT ip FROM sightings WHERE ip != '')")


# ---------------------------------------------------------------- proxycheck.io

class LookupFailed(Exception):
    """proxycheck refused or couldn't be reached. retry: seconds to wait."""
    def __init__(self, msg, retry):
        super().__init__(msg)
        self.retry = retry


def _day():
    """proxycheck's day, which starts at midnight UTC-7."""
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=7)).strftime("%Y-%m-%d")


def seconds_to_reset():
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=7)
    tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds()) + 60


def used_today(conn):
    day, _, n = db.get_setting(conn, "vpn_used").partition(":")
    return int(n) if day == _day() and n.isdigit() else 0


def _count_used(conn, n):
    db.set_setting(conn, "vpn_used", f"{_day()}:{used_today(conn) + n}")


def lookup_paused(conn):
    """Lookups wait for the base lists, so none are spent on addresses a list would have covered. Returns the reason
    they're waiting, or ''."""
    if db.get_setting(conn, "vpn_lookup_anyway") == "1":
        return ""
    sources = enabled_sources(conn)
    if not sources:
        return ""
    if not db.get_setting(conn, "vpn_base_at"):
        return "waiting"     # the first download hasn't run yet
    states = source_states(conn)
    if not any((states.get(s.id) or {}).get("loaded_at") for s in sources):
        return "no_lists"
    return ""


def queue(conn, limit=BATCH):
    """The addresses to ask proxycheck about next, at most one per /24 (IPv6: /48) network, most useful first:
    never asked before, seen recently, shared by several accounts, then the rest; hosting addresses and answers due
    to be checked again come last."""
    stale = _days_ago(CHECK_DAYS)
    recent = _days_ago(RECENT_DAYS)
    host = " AND NOT (f.pc_at IS NULL AND f.kind = 'host')" if skip_host(conn) else ""
    rows = conn.execute(f"""
        SELECT f.ip, f.bin, f.kind, f.pc_at,
               (SELECT MAX(seen_at) FROM sightings s WHERE s.ip = f.ip) AS last,
               (SELECT COUNT(DISTINCT steam_id) FROM sightings s WHERE s.ip = f.ip) AS accounts
        FROM ip_flags f
        WHERE f.matched = 1 AND (f.pc_at IS NULL OR f.pc_at < ?)
          AND NOT (f.pc_at IS NULL AND f.kind IN {ANON_SQL}){host}
        ORDER BY f.pc_at IS NOT NULL, f.pc_at, f.kind = 'host', COALESCE(last, '') >= ? DESC, accounts > 1 DESC,
                 last DESC
        LIMIT ?""", (stale, recent, limit * 20)).fetchall()
    out, nets = [], set()
    for r in rows:
        net = r["bin"][:15] if r["bin"][:12] == db.IPV4_PREFIX else r["bin"][:6]
        if net in nets:
            continue
        nets.add(net)
        out.append(r["ip"])
        if len(out) >= limit:
            break
    return out


def _kind(d):
    det = d.get("detections") or {}
    if det.get("tor"):
        return "tor"
    if det.get("proxy") or det.get("compromised"):
        return "proxy"
    if det.get("vpn"):
        return "vpn"
    if det.get("hosting"):
        return "host"
    return ""


def ask(client, key, ips):
    """proxycheck's answers for ips: {ip: dict}. Raises LookupFailed."""
    try:
        r = client.post(PROXYCHECK_URL, params={"key": key, "tag": "0"}, data={"ips": ",".join(ips)})
    except httpx.HTTPError as e:
        raise LookupFailed(f"Couldn't reach proxycheck.io: {type(e).__name__}.", 1800) from None
    try:
        data = r.json()
    except ValueError:
        data = None
    message = str((data or {}).get("message") or "")[:200] if isinstance(data, dict) else ""
    status = (data or {}).get("status") if isinstance(data, dict) else None
    if r.status_code == 429 or status == "denied" or r.status_code in (401, 403):
        limit = r.status_code == 429 or re.search(r"limit|exceed|quer(y|ies)", message, re.I)
        raise LookupFailed(" ".join(x for x in (f"proxycheck.io refused the lookup (HTTP {r.status_code}).", message) if x),
                           seconds_to_reset() if limit else 3600)
    if r.status_code != 200 or not isinstance(data, dict) or status not in ("ok", "warning"):
        raise LookupFailed(" ".join(x for x in (f"proxycheck.io answered HTTP {r.status_code}.", message) if x), 3600)
    return {ip: data[ip] for ip in ips if isinstance(data.get(ip), dict)}


def save_answers(conn, answers):
    """Store proxycheck's answers, and the network range of each VPN on a hosting network. Returns addresses saved."""
    ts = db.now()
    with db.transaction(conn):
        for ip, d in answers.items():
            kind = _kind(d)
            network = d.get("network") or {}
            operator = (d.get("operator") or {}).get("name") if isinstance(d.get("operator"), dict) else None
            provider = str(operator or (network.get("provider") if kind else "") or "")[:80]
            risk = d.get("risk") if isinstance(d.get("risk"), int) else None
            conn.execute("UPDATE ip_flags SET pc_kind=?, pc_provider=?, pc_risk=?, pc_at=?, matched=0 WHERE ip=?",
                         (kind, provider, risk, ts, ip))
            if kind == "vpn" and str(network.get("type") or "").lower() == "hosting" and network.get("range"):
                _save_range(conn, str(network["range"]), provider, ts)
    return len(answers)


def _save_range(conn, cidr, provider, ts):
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return
    if net.prefixlen < MIN_PREFIX[net.version]:
        return      # too wide to say everyone in it is on a VPN
    first, last = db.ip_bytes(net.network_address), db.ip_bytes(net.broadcast_address)
    if conn.execute("SELECT 1 FROM ip_ranges WHERE source='proxycheck' AND first <= ? AND last >= ?",
                    (first, last)).fetchone():
        return      # already inside one
    conn.execute("DELETE FROM ip_ranges WHERE source='proxycheck' AND first >= ? AND last <= ?", (first, last))
    conn.execute("INSERT INTO ip_ranges(source, kind, first, last, cidr, provider, added_at) VALUES('proxycheck','vpn',?,?,?,?,?)",
                 (first, last, str(net), provider, ts))
    conn.execute("UPDATE ip_flags SET matched=0 WHERE bin BETWEEN ? AND ?", (first, last))


def lookup(conn, client=None):
    """Ask proxycheck about the next batch, within the day's budget. Returns (addresses asked, state); state is
    nokey, paused, budget, done or ok. Raises LookupFailed."""
    key = proxycheck_key(conn)
    if not key:
        return 0, "nokey"
    if lookup_paused(conn):
        return 0, "paused"
    left = budget(conn) - used_today(conn)
    if left <= 0:
        return 0, "budget"
    ips = queue(conn, min(BATCH, left))
    if not ips:
        return 0, "done"
    own = client is None
    client = client or httpx.Client(timeout=30, headers={"User-Agent": f"Fragdex/{__version__}"})
    try:
        answers = ask(client, key, ips)
    finally:
        if own:
            client.close()
    _count_used(conn, len(ips))
    save_answers(conn, answers)
    db.set_setting(conn, "vpn_lookup_error", "")
    return len(ips), "ok"


# ---------------------------------------------------------------- shown on pages

def describe(kind, provider="", via="", risk=None):
    """A label for templates and the API, or None."""
    if not kind:
        return None
    label = LABELS.get(kind, kind)
    title = label + (f": {provider}" if provider else "") + f" · {source_name(via)}" + (
        f", risk {risk}" if risk is not None and via == "proxycheck" else "")
    return {"kind": kind, "label": label, "provider": provider or None, "source": source_name(via), "title": title}


def attach(conn, players):
    """Add p["vpn"] (the latest IP's label, or None) and p["vpn_other"] (how many other addresses of theirs are VPN,
    Tor or proxy) to player dicts. Returns the list."""
    by = {p["steam_id"]: p for p in players}
    for p in players:
        p["vpn"], p["vpn_other"] = None, 0
    if not players or not enabled(conn):
        return players      # labels are only shown while VPN detection is on
    ids = list(by)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(f"""
                SELECT s.steam_id, f.ip, f.kind, f.provider, f.via, f.pc_risk
                FROM (SELECT DISTINCT steam_id, ip FROM sightings WHERE steam_id IN ({",".join("?" * len(chunk))})
                      AND ip != '') s JOIN ip_flags f ON f.ip = s.ip WHERE f.kind != ''""", chunk):
            p = by[r["steam_id"]]
            if r["ip"] == p["last_ip"]:
                p["vpn"] = describe(r["kind"], r["provider"], r["via"], r["pc_risk"])
            elif r["kind"] in ANON:
                p["vpn_other"] += 1
    return players


def panel(conn):
    """Everything the VPN detection panel on the Settings page shows."""
    states = source_states(conn)
    sources = []
    for s in SOURCES:
        st = states.get(s.id) or {}
        sources.append(dict(id=s.id, name=s.name, label=LABELS[s.kind], kind=s.kind, on=source_enabled(s, states),
                            default=s.default, ranges=st.get("ranges") or 0, loaded_at=st.get("loaded_at") or "",
                            error=st.get("error") or "", error_at=st.get("error_at") or "", home=s.home, note=s.note,
                            upload=False))
    for sid, st in sorted(states.items()):
        if sid.startswith(UPLOAD):
            kind = sid[len(UPLOAD):]
            sources.append(dict(id=sid, name=source_name(sid), label=LABELS.get(kind, kind), kind=kind, on=True,
                                default=False, ranges=st["ranges"], loaded_at=st["loaded_at"], error="", error_at="",
                                home="", note="Uploaded on the Settings page.", upload=True))
    c = conn.execute(f"""SELECT COUNT(*) total,
                         SUM(kind IN {ANON_SQL}) anon,
                         SUM(kind = 'host') host,
                         SUM(pc_at IS NOT NULL) checked,
                         SUM(matched = 0) unmatched,
                         SUM(pc_at IS NULL AND kind = '') left_other,
                         SUM(pc_at IS NULL AND kind = 'host') left_host
                         FROM ip_flags""").fetchone()
    key = proxycheck_key(conn)
    daily = budget(conn)
    # hosting addresses are looked up last, or not at all with "Don't look up hosting addresses"
    left_host = 0 if skip_host(conn) else c["left_host"] or 0
    left = (c["left_other"] or 0) + left_host
    return dict(on=enabled(conn), sources=sources, key_set=bool(key), key_from_env=bool(config.PROXYCHECK_KEY_ENV),
                key_hint="…" + key[-4:] if key else "", budget=daily, used=used_today(conn), skip_host=skip_host(conn),
                no_links=db.get_setting(conn, "vpn_no_links") == "1", base_at=db.get_setting(conn, "vpn_base_at"),
                paused=lookup_paused(conn), lookup_error=db.get_setting(conn, "vpn_lookup_error"),
                total=c["total"] or 0, anon=c["anon"] or 0, host=c["host"] or 0, checked=c["checked"] or 0,
                unmatched=c["unmatched"] or 0, left=left, left_other=c["left_other"] or 0, left_host=left_host, days=-(-left // daily) if daily and left else 0,
                labels=LABELS)
