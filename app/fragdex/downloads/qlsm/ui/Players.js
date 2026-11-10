// The Fragdex Players dialog: who is connected, their Discord and bans, and icons that copy the values.
// Hand-written against QLSM's window.__qlsm runtime, so the addon needs no build step.
const REFRESH_MS = 30000;
const COPIED_MS = 1200;

const QL_COLORS = {
  0: '#000000', 1: '#ff0000', 2: '#00ff00', 3: '#ffff00', 4: '#3b82f6',
  5: '#00ffff', 6: '#ff00ff', 7: '#ffffff', 8: '#ff7f00', 9: '#7f7f7f',
};

// QLSM's icon list is fixed and has none of these.
const ICONS = {
  user: [['path', { d: 'M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2' }], ['circle', { cx: 12, cy: 7, r: 4 }]],
  hash: [['line', { x1: 4, x2: 20, y1: 9, y2: 9 }], ['line', { x1: 4, x2: 20, y1: 15, y2: 15 }],
    ['line', { x1: 10, x2: 8, y1: 3, y2: 21 }], ['line', { x1: 16, x2: 14, y1: 3, y2: 21 }]],
  steam: [['path', { d: 'M16.5 5a4.5 4.5 0 1 1 -.653 8.953l-4.347 3.009l0 .038a3 3 0 0 1 -2.824 2.995l-.176 .005a3 3 0 0 1 -2.94 -2.402l-2.56 -1.098v-3.5l3.51 1.755a2.989 2.989 0 0 1 2.834 -.635l2.727 -3.818a4.5 4.5 0 0 1 4.429 -5.302z' }],
    ['circle', { cx: 16.5, cy: 9.5, r: 1, fill: 'currentColor' }]],
  check: [['path', { d: 'M20 6 9 17l-5-5' }]],
  x: [['path', { d: 'M18 6 6 18' }], ['path', { d: 'm6 6 12 12' }]],
};

const h = (...args) => window.__qlsm.react.createElement(...args);

function Glyph({ name }) {
  return h('svg', {
    width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true,
  }, ICONS[name].map(([tag, attrs], i) => h(tag, { key: i, ...attrs })));
}

function PlayerName({ text }) {
  let color;
  const spans = [];
  text.split(/(\^[0-9])/).forEach((part) => {
    if (/^\^[0-9]$/.test(part)) {
      color = QL_COLORS[part[1]];
    } else if (part) {
      spans.push(h('span', { key: spans.length, style: color ? { color } : undefined }, part));
    }
  });
  return h('span', { className: 'fragdex-name', title: text.replace(/\^[0-9]/g, '') }, spans);
}

function copyText(text, anchor) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  // Plain HTTP. The textarea goes inside the dialog, which would take focus back from anything outside it.
  const area = document.createElement('textarea');
  area.value = text;
  area.className = 'fragdex-copy-area';
  anchor.appendChild(area);
  area.focus();
  area.select();
  try {
    return document.execCommand('copy') ? Promise.resolve() : Promise.reject(new Error('Copy failed'));
  } catch (err) {
    return Promise.reject(err);
  } finally {
    anchor.removeChild(area);
  }
}

function CopyButton({ icon, label, emptyLabel, value }) {
  const { useEffect, useState } = window.__qlsm.react;
  const [result, setResult] = useState('');

  useEffect(() => {
    if (!result) return undefined;
    const timer = setTimeout(() => setResult(''), COPIED_MS);
    return () => clearTimeout(timer);
  }, [result]);

  const onClick = (event) => {
    if (!value) return;
    copyText(value, event.currentTarget.parentNode).then(() => setResult('copied'), () => setResult('failed'));
  };
  const title = !value ? emptyLabel : result === 'copied' ? 'Copied' : result === 'failed' ? "Couldn't copy" : label;
  const state = !value ? ' is-empty' : result ? ` is-${result}` : '';
  return h('button', {
    type: 'button', className: `fragdex-copy${state}`, title, 'aria-label': title, 'aria-disabled': !value, onClick,
  }, h(Glyph, { name: result === 'copied' ? 'check' : result === 'failed' ? 'x' : icon }));
}

function Cell({ cell }) {
  return h('span', { className: `fragdex-cell is-${cell.color || 'none'}`, title: cell.title }, cell.display);
}

function PlayerRow({ player }) {
  const discord = player.discord_name || player.discord_id;
  return h('div', { className: 'fragdex-row' },
    h(PlayerName, { text: player.name }),
    h(Cell, { cell: player.steam_status }),
    discord
      ? h('span', { className: 'fragdex-discord' },
        h('span', { className: 'fragdex-discord-name', title: discord }, discord),
        player.discord_status && h('span', { className: `fragdex-tag is-${player.discord_status}` }, player.discord_status))
      : h('span', { className: 'fragdex-muted' }, player.discord_note),
    h(Cell, { cell: player.bans }),
    h('div', { className: 'fragdex-copies' },
      h(CopyButton, { icon: 'user', label: 'Copy Discord Name', emptyLabel: 'No Discord name', value: player.discord_name }),
      h(CopyButton, { icon: 'hash', label: 'Copy Discord ID', emptyLabel: 'No Discord ID', value: player.discord_id }),
      h(CopyButton, { icon: 'steam', label: 'Copy Steam ID', emptyLabel: 'No Steam ID', value: player.steam_id })));
}

export default function Players({ ctx }) {
  const { useEffect, useState } = window.__qlsm.react;
  const { Modal, Button, Icon } = window.__qlsm.ui;
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const route = `instances/${ctx.scope.id}/roster`;

  useEffect(() => {
    let stale = false;
    const load = () => ctx.api('GET', route).then(
      (answer) => { if (!stale) { setData(answer || { players: [] }); setFailed(false); } },
      () => { if (!stale) setFailed(true); },
    );
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => { stale = true; clearInterval(timer); };
  }, [route]);

  const players = (data && data.players) || [];
  const entity = ctx.modal.entity || {};
  const where = [ctx.modal.subtitle, entity.port && `Port ${entity.port}`, players.length && `${players.length} connected`]
    .filter(Boolean).join(' · ');
  const warning = failed ? "Couldn't update the player list." : data && data.warning;

  let body;
  if (players.length) {
    body = h('div', null,
      h('div', { className: 'fragdex-row fragdex-head' },
        h('span', null, 'Name'), h('span', null, 'Steam'), h('span', null, 'Discord'), h('span', null, 'VAC/BAN'),
        h('span', { className: 'fragdex-head-copy' }, 'Copy')),
      players.map((player) => h(PlayerRow, { key: player.steam_id || player.name, player })));
  } else {
    let message = 'Loading players…';
    if (data) message = data.message;
    else if (failed) message = "Couldn't load the player list.";
    body = h('p', { className: 'fragdex-message' }, message);
  }

  return h(Modal, {
    isOpen: ctx.modal.isOpen,
    onClose: ctx.modal.onClose,
    size: 'xl',
    height: 'auto',         // puts the list under the header, using the dialog's full width
    icon: h(Icon, { name: 'database', className: 'text-theme-muted mt-1' }),
    title: h('span', null, 'Fragdex Players', h('span', { className: 'fragdex-where' }, where)),
    headerActions: h('button', { type: 'button', className: 'fragdex-close', title: 'Close', 'aria-label': 'Close', onClick: ctx.modal.onClose },
      h(Glyph, { name: 'x' })),
    footer: [
      h('span', { key: 'note', className: 'fragdex-footnote' }, 'Updates every 30 seconds'),
      h(Button, { key: 'close', variant: 'primary', onClick: ctx.modal.onClose }, 'Close'),
    ],
  }, warning ? h('p', { className: 'fragdex-warning' }, warning) : null, body);
}
