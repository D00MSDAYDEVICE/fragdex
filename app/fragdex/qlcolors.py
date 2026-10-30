import re

from markupsafe import Markup, escape

_SPLIT_RE = re.compile(r"(\^[0-9A-Za-z])")


def qlname(name):
    """Render a Quake name with ^N colour codes as coloured spans."""
    if not name:
        return Markup('<span class="noname">(no name)</span>')
    color = 7
    out = []
    for part in _SPLIT_RE.split(str(name)):
        if not part:
            continue
        if _SPLIT_RE.fullmatch(part):
            c = part[1]
            color = int(c) % 8 if c.isdigit() else 7
            continue
        out.append(f'<span class="qc{color}">{escape(part)}</span>')
    if not out:
        return Markup('<span class="noname">(no name)</span>')
    return Markup('<span class="qlname">' + "".join(out) + "</span>")
