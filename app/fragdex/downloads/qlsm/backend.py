"""Fragdex addon for QLSM.

Features, chosen per server with QLSM's own host and instance switches:
  - sends the connection lines of each server's minqlx chat log to Fragdex;
  - adds a Fragdex column (tags, bans, linked accounts) to the Live Status player list;
  - adds a Players panel, with Discord details and bans for whoever is connected.

Everything goes through `ctx`, except what the addon API has no call for: listing hosts and instances, kept in
_models(), and the connected players, kept in _live_players(). A QLSM that changes either reports a clear error.

"Sent up to" is the server's own clock as written in the log, so comparing it needs no time zone. Lines are turned
into UTC on the host before they are sent. Fragdex ignores a line it already has, so resending is harmless.
"""
import json
import os
import re
import shutil
import tempfile
import time

import requests
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from ui.addons.hooks import HOOK_SCOPES

bp = Blueprint('fragdex_addon', __name__)

LOOKUP_MAX = 100         # Fragdex's limit for one lookup
RUN_LIMIT = 5000         # lines read per server per run; a longer history continues in the next run
BATCH = 1000             # lines per request to Fragdex
CURSOR_RE = re.compile(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$')
STEAM_RE = re.compile(r'^7656119\d{10}$')

_ctx = None
_last_run = None         # monotonic time of the last run; None until the first, which is at once


class FragdexError(Exception):
    """A failure to show an admin. Never contains the API key."""


def _config():
    return _ctx.settings.get('global')


def _configured(cfg):
    return bool(cfg['fragdex_url'] and cfg['api_key'])


def _base_url(cfg):
    """Fragdex's address. The API panel shows it with /api/v1, so either form works."""
    return re.sub(r'/api/v1/*$', '', cfg['fragdex_url'].strip().rstrip('/'))


def _api(cfg, method, path, payload=None, timeout=15):
    try:
        r = requests.request(method, f'{_base_url(cfg)}/api/v1{path}', json=payload, timeout=timeout,
                             headers={'Authorization': f"Bearer {cfg['api_key']}"}, verify=bool(cfg['verify_tls']))
    except requests.RequestException as e:
        raise FragdexError(f"Couldn't reach Fragdex ({type(e).__name__}).") from None
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200:
        reason = str(body.get('error') or '')[:200] if isinstance(body, dict) else ''
        raise FragdexError(' '.join(x for x in (f'Fragdex returned HTTP {r.status_code}.', reason) if x))
    return body


def _models():
    """QLSM's Host and QLInstance. Not part of the addon API, so a QLSM that changes them is reported, not guessed at."""
    try:
        from ui.models import Host, QLInstance
    except ImportError as e:
        raise FragdexError(f"This QLSM version doesn't list servers the way the addon expects: {e}") from None
    return Host, QLInstance


def _notify(severity, title, body, key):
    if hasattr(_ctx, 'notify'):
        _ctx.notify(severity, title, body=body, link='/addons/fragdex', key=key)


def _resolve(key):
    if hasattr(_ctx, 'resolve'):
        _ctx.resolve(key)


def _runs_plugin(instance):
    """Is the fragdex minqlx plugin on this server? It reports new connections itself."""
    names = (p.strip().lower() for p in (instance.qlx_plugins or '').split(','))
    return any(n in ('fragdex', 'fragdex.py') for n in names)


# ---------------------------------------------------------------- sending chat logs

def _targets(host_id):
    """{port: (instance, its settings)} for the servers on this host that have something to send."""
    _host, instances = _models()
    out = {}
    for instance in instances.query.filter_by(host_id=host_id).all():
        if not _ctx.settings.is_enabled('instance', instance.id):
            continue
        settings = _ctx.settings.get('instance', instance.id)
        if not settings['sync_logs']:
            continue
        if settings['sent_up_to'] and settings['history_sent'] and _runs_plugin(instance):
            continue        # the history is in, and the plugin has it from here on
        out[int(instance.port)] = (instance, settings)
    return out


def _collect(host, targets):
    """Read the connection lines of these servers. Returns {port as a string: result}."""
    work = tempfile.mkdtemp(prefix='fragdex-addon-')
    dest = os.path.join(work, 'collected.json')
    try:
        ok, _out, _err = _ctx.run_playbook(host, 'collect_connections.yml', extravars={
            'fragdex_targets': [{'port': port, 'since': s['sent_up_to'] if CURSOR_RE.match(s['sent_up_to'] or '') else ''}
                                for port, (_i, s) in targets.items()],
            'fragdex_limit': RUN_LIMIT,
            'fragdex_dest': dest,
        })
        if not ok:
            raise FragdexError(f"Couldn't read the chat logs on {host.name}. The QLSM logs have the details.")
        with open(dest, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise FragdexError(f"The chat logs from {host.name} couldn't be read back: {type(e).__name__}.") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def sync_host(host_id):
    """Send what is new on one host. Returns True if a long history has more to send."""
    cfg = _config()
    if not _configured(cfg) or not _ctx.settings.is_enabled('host', host_id):
        return False
    hosts, _instance = _models()
    host = hosts.query.get(host_id)
    if host is None:
        return False
    targets = _targets(host_id)
    if not targets:
        return False

    collected = _collect(host, targets)
    more = False
    for port, (instance, settings) in targets.items():
        result = collected.get(str(port)) or {}
        if not result.get('found'):
            continue        # no chat log: this server doesn't run minqlx's log plugin
        lines = result.get('lines') or []
        for i in range(0, len(lines), BATCH):
            _api(cfg, 'POST', '/logs', {'server': instance.name, 'lines': lines[i:i + BATCH]}, timeout=60)
        truncated = bool(result.get('truncated'))
        # saved only after Fragdex has everything, so a failure sends the same lines again
        values = {'history_sent': settings['history_sent'] or not truncated}
        if result.get('latest'):
            values['sent_up_to'] = result['latest']
        _ctx.settings.set('instance', instance.id, values)
        more = more or truncated
    return more


def _tick():
    """Runs every 15 seconds in QLSM's status poller, so it only queues the work."""
    global _last_run
    cfg = _config()
    now = time.monotonic()
    if not _configured(cfg) or (_last_run is not None and now - _last_run < max(1, int(cfg['interval_minutes'])) * 60):
        return
    _last_run = now
    try:
        hosts, _instance = _models()
        wanted = [h.id for h in hosts.query.all() if _ctx.settings.is_enabled('host', h.id)]
    except (FragdexError, AttributeError) as e:
        _notify('critical', 'Fragdex addon needs an update', f"QLSM's server list has changed: {e}", 'qlsm-version')
        return
    _resolve('qlsm-version')
    for host_id in wanted:
        _ctx.tasks['fragdex_sync_host'].queue(host_id)


# ---------------------------------------------------------------- Live Status column

def _cell(player):
    """One player's cell. Tags and bans are what an admin needs at a glance; the rest is in the tooltip."""
    if not player.get('known'):
        return {'display': '–', 'title': 'Not in Fragdex yet'}
    steam = player.get('steam') or {}
    tags = player.get('tags') or []
    vac, game = steam.get('vac_bans') or 0, steam.get('game_bans') or 0
    flagged = [a for a in player.get('linked') or [] if a.get('tags') or a.get('vac_bans') or a.get('game_bans')]

    badges = list(tags[:2])
    if vac or game:
        badges.append('VAC' if vac else 'BAN')
    if not badges and flagged:
        badges.append('linked')

    details = []
    if tags:
        details.append('Tags: ' + ', '.join(tags))
    if vac or game:
        details.append(f'{vac} VAC, {game} game bans')
    if player.get('linked_accounts'):
        details.append(f"{player['linked_accounts']} linked accounts, {len(flagged)} flagged")
    if player.get('discord_name') or player.get('discord_id'):
        link = player.get('discord_status')
        details.append(f"Discord: {player.get('discord_name') or player.get('discord_id')}" + (f' ({link})' if link else ''))
    if player.get('steam_verified'):
        details.append('Verified Steam ID')

    cell = {'display': ', '.join(badges) or 'clean', 'title': ' · '.join(details) or 'Nothing recorded against this player'}
    if tags:
        cell['color'] = 'magenta'
    elif badges:
        cell['color'] = 'yellow'
    return cell


@bp.route('/instances/<int:instance_id>/players', methods=['GET'])
@jwt_required()
def players(instance_id):
    cfg = _config()
    if (not _configured(cfg) or not _ctx.settings.is_enabled('instance', instance_id)
            or not _ctx.settings.get('instance', instance_id)['show_column']):
        return jsonify({'data': {}, 'configured': False})
    ids = [s for s in request.args.get('steam_ids', '').split(',') if STEAM_RE.match(s)][:LOOKUP_MAX]
    if not ids:
        return jsonify({'data': {}, 'configured': True})
    try:
        found = _api(cfg, 'POST', '/players/lookup', {'steam_ids': ids}, timeout=5)
    except FragdexError as e:       # QLSM keeps showing the last values and asks again
        return jsonify({'error': {'message': str(e)}}), 502
    return jsonify({'data': {p['steam_id']: _cell(p) for p in found.get('players', [])}, 'configured': True})


# ---------------------------------------------------------------- Players panel

def _live_players(instance_id):
    """Who QLSM's status poller last saw on this server, or None if it has nothing. Not part of the addon API."""
    _host, instances = _models()
    try:
        instance = instances.query.get(instance_id)
        raw = instance and current_app.extensions['redis'].get(f'server:status:{instance.host_id}:{instance_id}')
        status = json.loads(raw) if raw else None
    except Exception as e:      # a Redis failure, or a QLSM that keeps this somewhere else
        raise FragdexError(f"Couldn't read QLSM's live status ({type(e).__name__}).") from None
    if not isinstance(status, dict):
        return None
    players = status.get('players')
    return [p for p in players if isinstance(p, dict)] if isinstance(players, list) else []


def _live_steam_id(player):
    """The same fields, in the same order, as QLSM's own players table."""
    for key in ('steam', 'steamid', 'steam_id'):
        value = str(player.get(key) or '').strip()
        if value:
            return value
    return ''


def _bans(player):
    """The VAC/BAN cell. Clean only means something once Steam has been asked."""
    steam = player.get('steam')
    if not steam:
        return {'display': '–', 'title': 'Not checked with Steam yet', 'color': ''}
    vac, game = steam.get('vac_bans') or 0, steam.get('game_bans') or 0
    if not vac and not game:
        return {'display': 'Clean', 'title': 'No VAC or game bans', 'color': ''}
    counts = [f"{n} {kind} ban{'' if n == 1 else 's'}" for n, kind in ((vac, 'VAC'), (game, 'game')) if n]
    days = steam.get('days_since_last_ban')
    when = ('' if days is None else ', last one today' if days == 0
            else f", last one {days} day{'' if days == 1 else 's'} ago")
    return {'display': f'VAC {vac}' if vac else f'BAN {game}', 'title': ' and '.join(counts) + when,
            'color': 'red' if vac else 'yellow'}


def _steam_status(player):
    """The Steam cell: how Fragdex knows this Steam ID, not whether the player is connected with it."""
    if not player.get('known'):
        return {'display': '–', 'title': 'Not in Fragdex yet', 'color': ''}
    if player.get('steam_verified'):
        return {'display': 'Verified', 'title': 'Steam ID seen in a demo, a log or on a game server', 'color': 'green'}
    return {'display': 'Claimed', 'title': 'Steam ID only added by hand, by import or from FragQ', 'color': 'yellow'}


def _roster_row(name, steam_id, player):
    """One player of the panel. A Discord "ID" that isn't a number is a username someone typed in."""
    discord = str(player.get('discord_id') or '')
    numeric = discord.isdigit()
    return {'name': name, 'steam_id': steam_id if STEAM_RE.match(steam_id) else '',
            'discord_name': player.get('discord_name') or ('' if numeric else discord),
            'discord_id': discord if numeric else '',
            'discord_status': player.get('discord_status') or '',
            'discord_note': 'Not linked' if player.get('known') else 'Not in Fragdex yet',
            'steam_status': _steam_status(player),
            'bans': _bans(player)}


@bp.route('/instances/<int:instance_id>/roster', methods=['GET'])
@jwt_required()
def roster(instance_id):
    """The Players panel. `message` is shown instead of the list when there is nobody to list."""
    cfg = _config()
    if not _configured(cfg):
        return jsonify({'data': {'players': [], 'message': 'Enter the address and API key in Settings → Fragdex.'}})
    if not _ctx.settings.is_enabled('instance', instance_id):
        return jsonify({'data': {'players': [], 'message': 'Fragdex is off for this server. Turn it on in Fragdex Settings.'}})
    try:
        live = _live_players(instance_id)
    except FragdexError as e:
        return jsonify({'data': {'players': [], 'message': str(e)}})
    if live is None:
        return jsonify({'data': {'players': [], 'message': 'This server is offline.'}})
    if not live:
        return jsonify({'data': {'players': [], 'message': 'No players online.'}})

    people = [(str(p.get('name') or 'Unknown'), _live_steam_id(p)) for p in live]
    ids = sorted({sid for _name, sid in people if STEAM_RE.match(sid)})[:LOOKUP_MAX]
    found, warning = {}, ''
    if ids:
        try:
            found = {p['steam_id']: p for p in _api(cfg, 'POST', '/players/lookup', {'steam_ids': ids}, timeout=5).get('players', [])}
        except FragdexError as e:
            warning = str(e)
    rows = []
    for name, sid in sorted(people, key=lambda item: re.sub(r'\^\d', '', item[0]).lower()):
        row = _roster_row(name, sid, found.get(sid) or {})
        if warning:     # nothing is known about anyone, which isn't the same as "not in Fragdex"
            blank = {'display': '–', 'title': "Fragdex couldn't be reached", 'color': ''}
            row.update(discord_note='–', steam_status=blank, bans=blank)
        rows.append(row)
    return jsonify({'data': {'players': rows, 'warning': warning}})


# ---------------------------------------------------------------- status

@bp.route('/connection', methods=['GET'])
@jwt_required()
def connection():
    """The badge on the settings form."""
    cfg = _config()
    if not _configured(cfg):
        return jsonify({'data': {'ok': False, 'label': 'Enter the address and API key'}})
    try:
        key = _api(cfg, 'GET', '/status', timeout=8).get('key') or {}
    except FragdexError as e:
        return jsonify({'data': {'ok': False, 'label': str(e)}})
    if not key.get('write') or not key.get('game_server'):
        return jsonify({'data': {'ok': False, 'label': 'Connected, but the key needs Add records and Game server'}})
    return jsonify({'data': {'ok': True, 'label': f"Connected as {key.get('name')}"}})


@bp.route('/instances/<int:instance_id>/status', methods=['GET'])
@jwt_required()
def instance_status(instance_id):
    """The badge on the server's settings form."""
    if not _ctx.settings.is_enabled('instance', instance_id):
        return jsonify({'data': {'ok': False, 'label': 'Off'}})
    settings = _ctx.settings.get('instance', instance_id)
    if not settings['sync_logs']:
        return jsonify({'data': {'ok': True, 'label': 'Column only'}})
    if settings['sent_up_to']:
        return jsonify({'data': {'ok': True, 'label': f"Sent up to {settings['sent_up_to']}"}})
    return jsonify({'data': {'ok': False, 'label': 'Waiting for first sync'}})


def register(ctx):
    global _ctx
    _ctx = ctx
    ctx.blueprint(bp)

    @ctx.task(timeout=600)
    def fragdex_sync_host(host_id):
        key = f'sync-{host_id}'
        try:
            more = sync_host(host_id)
        except FragdexError as e:
            ctx.logger.warning('Fragdex sync of host %s failed: %s', host_id, e)
            _notify('warning', 'Fragdex sync failed', str(e), key)
            return False
        except AttributeError as e:     # a QLSM whose models no longer have what the addon reads
            _notify('critical', 'Fragdex addon needs an update', f"QLSM's server list has changed: {e}", 'qlsm-version')
            return False
        _resolve(key)
        if more:
            ctx.tasks['fragdex_sync_host'].queue(host_id)
        return True

    if 'poller.tick' in HOOK_SCOPES:    # missing on an older QLSM, where ctx.on would mark the addon broken
        ctx.on('poller.tick')(_tick)
