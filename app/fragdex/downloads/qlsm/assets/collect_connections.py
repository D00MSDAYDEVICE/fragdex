"""Runs on a game host, on Python 3.6 or later. Prints connection lines from minqlx chat logs as JSON.

Only "name:steamid:ip connected." lines are kept, so player chat never leaves the host. Oldest lines come first, so a
history longer than the limit carries on from where it stopped. Timestamps are turned from the host's local time into
UTC in the lines sent; "latest" stays in local time, because it is compared with the logs again next time.
"""
import json
import os
import re
import time

STAMP = '%Y-%m-%d %H:%M:%S'
LOG_NAME_RE = re.compile(r'^chat\.log(?:\.(\d+))?$')
CONNECT_RE = re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.+:7656119\d{10}:\S+ connected\.)$')
SPEAKER_RE = re.compile(r':7656119\d{10}> ')     # how the log writes who said something
MAX_LINE = 400


def log_files(folder):
    """The chat logs in a folder, oldest first: the highest-numbered rotated file down to chat.log."""
    found = []
    for name in os.listdir(folder):
        m = LOG_NAME_RE.match(name)
        if m:
            found.append((int(m.group(1) or 0), name))
    return [os.path.join(folder, name) for _n, name in sorted(found, reverse=True)]


def to_utc(stamp):
    return time.strftime(STAMP, time.gmtime(time.mktime(time.strptime(stamp, STAMP))))


def read_lines(path):
    with open(path, 'rb') as f:
        for raw in f:
            if len(raw) <= MAX_LINE:
                yield raw.decode('utf-8', 'replace').rstrip('\r\n')


def collect(folder, since, limit):
    if not os.path.isdir(folder):
        return {'found': False, 'lines': [], 'latest': '', 'truncated': False}
    matches = []
    for path in log_files(folder):
        for line in read_lines(path):
            m = CONNECT_RE.match(line)
            # the same second as "since" is read again: Fragdex ignores lines it already has
            if m and m.group(1) >= since and not SPEAKER_RE.search(line):
                matches.append((m.group(1), m.group(2)))
    matches.sort(key=lambda item: item[0])
    kept = matches[:limit]
    return {'found': True, 'lines': ['[%s] %s' % (to_utc(stamp), text) for stamp, text in kept],
            'latest': kept[-1][0] if kept else '', 'truncated': len(matches) > limit}


def main():
    limit = int(os.environ['FRAGDEX_LIMIT'])
    out = {}
    for target in json.loads(os.environ['FRAGDEX_TARGETS']):
        port = int(target['port'])
        out[str(port)] = collect('/home/ql/qlds-%d/chatlogs' % port, str(target.get('since') or ''), limit)
    print(json.dumps(out))


if __name__ == '__main__':
    main()
