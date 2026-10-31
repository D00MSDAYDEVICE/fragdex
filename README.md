# Fragdex

**Documentation: <https://d00msdaydevice.github.io/fragdex/>**

**Fragdex is a self-hosted, login-protected player database built for Quake Live server administrators.** It consolidates player information from demos, server logs, chat logs, and spreadsheets into a centralized, searchable database. Search by name, alias, Steam ID, IP address, or CIDR range to investigate player histories, track past names and IPs, identify potential alternate accounts sharing an IP, and organize records with notes, tags, and ban status. Optional Steam Web API integration adds profile avatars, current Steam names, and VAC or game-ban information, while advanced filtering and CSV exports make player research and moderation easier.

Designed for flexibility and portability, Fragdex includes the `fragdex-scan` desktop tool for indexing large demo collections locally and uploading compact `.fdx` files instead of entire archives. The `.fdx` format preserves complete player histories, notes, and tags for backups and transfers between servers. Role-based permissions separate viewer, editor, and administrator access, while Docker deployment with automatic HTTPS simplifies self-hosting. **Fragdex gives server operators a single, reliable source of player intelligence**, reducing manual investigation time, helping identify linked accounts, and keeping player data under their control.

- Search by name, alias, Steam ID, IP or CIDR range (`1.2.3.0/24`), and filter by
  tag, source, ban status, "has IP", "has linked accounts" and last-seen date.
- Each row shows the latest name (in its Quake colours), Steam ID with a link to the
  Steam profile, latest IP, small icons that reveal past names and past IPs, and
  the last-seen date.
- Alt-account linking: other Steam IDs seen on the same IP. Local addresses (127.0.0.1, ::1)
  are never recorded, so they can't link everyone seen through the server itself.
- Discord ID per player (user ID or username), in its own column, searchable, settable on the
  Add page, the player page or from a spreadsheet's *Discord ID* column.
- Sort by most aliases with the toggle in the History column header.
- Optional Steam Web API enrichment: avatar, current Steam name, VAC/game bans, with
  live progress of the background refresh under Settings.
- Notes and tags per player. Export any search as **CSV** (a summary row per player,
  for spreadsheets) or **.fdx** (the full history plus notes and tags, compressed, which
  any Fragdex server re-imports; see [Backups and moving servers](#backups-and-moving-servers)).
- One-click **hide IPs** toggle (eye icon in the IP Address column) for screen-sharing
  or streaming. Remembered per browser; it only masks the display.
- **fragdex-scan** desktop tool: index thousands of demos locally and upload one small
  `.fdx` file instead (8.3 GB of demos → about 500 KB in our test). On Windows it is a
  69 KB app with a window; elsewhere a single Python script.
- **JSON API** for other programs (Discord bots, scripts, other Fragdex servers), with named
  keys whose access, allowed IPs, rate limit and expiry can be changed without a new key.
- **fragdex minqlx plugin** (minqlx and minqlxtended): every connection is recorded live,
  and admins are warned in game when a tagged or banned player, or one linked to them, joins.
- Users with roles: **viewer** (search/view/export), **editor** (+ import, edit),
  **admin** (+ users, settings, API keys, delete players).
- Docker deploy with automatic HTTPS: Let's Encrypt for a domain **or a bare public
  IP**, self-signed when neither works, on whatever ports you choose.

## Quick start

**Linux** server with Docker and the Compose plugin:

```bash
git clone <this repo> fragdex && cd fragdex
./deployfragdex.sh
```

**Windows** host with Docker Desktop running:

```powershell
powershell -ExecutionPolicy Bypass -File .\deployfragdex.ps1
```

The installer asks for your domain or IP, ports and TLS mode, writes `.env`,
creates the watched demo folder, starts the containers and prints the admin login.
The watched folder defaults to **`/home/demos`** on Linux and **`C:\demos`** on
Windows. Or do it by hand:

```bash
cp .env.example .env    # edit SITE_HOST, ports, etc.
docker compose up -d --build
docker compose logs app # shows the generated admin password if ADMIN_PASSWORD was empty
```

Then open `https://<SITE_HOST>[:HTTPS_PORT]`, log in, change your password under
**Account**, and import your data under **Import**.

## HTTPS and ports

| `TLS_MODE` | What happens |
|---|---|
| `auto` (default) | **Domain** → Let's Encrypt certificate. **Public IP** → Let's Encrypt IP-address certificate (6-day "shortlived" profile, renewed automatically). **Private IP / LAN name** → self-signed. If Let's Encrypt fails for any reason it falls back to self-signed and keeps retrying in the background. |
| `internal` | Always self-signed (Caddy's local CA). Browsers warn once. |
| `custom` | Uses `./certs/cert.pem` and `./certs/key.pem`. |
| `off` | Plain HTTP on `HTTP_PORT`, for when another proxy already does TLS. |

- `HTTP_PORT` / `HTTPS_PORT` can be any free ports (for example `8080` / `8443`).
  HTTP redirects to HTTPS on the right port.
- Let's Encrypt validates through **public port 80 or 443**. If you use other ports,
  forward 80→`HTTP_PORT` or 443→`HTTPS_PORT` on your router/firewall, or you get
  the self-signed fallback.
- `SITE_HOST` can list several names, for example `203.0.113.10,192.168.1.50`. Each
  gets its own certificate.
- To trust the self-signed CA on your PCs, copy Caddy's root certificate out and
  install it as a trusted root:
  `docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./fragdex-root.crt`

## Importing data

Upload files on the **Import** page (multiple files or `.zip` archives), or drop them
into the watched folder (`HOST_WATCH_DIR`: `/home/demos` on Linux, `C:\demos` on
Windows, mounted read-only). Imports run in the background. The same file is never
imported twice, and identical records are never duplicated.

### fragdex-scan (desktop scanner)

For big demo collections, scan on your own PC and upload only the result. Download
it from **Import → Desktop scanner**.

**Windows app** (`fragdex-scan.exe`, 69 KB, no install; needs the .NET Framework 4.6.2+
that every Windows 10/11 PC has). It opens with your Steam libraries' Quake Live demo
folders already listed. **Add folders…** adds more, several at once, or drag them onto
the list. Pick the **Export to** folder at the bottom and press **Process**. A progress
bar shows the scan; when it finishes, **Open folder** shows the new `.fdx`. Your folder
list is remembered for next time. For scripts: `fragdex-scan.exe --out x.fdx [--all] FOLDER…`.

**Python script** (`fragdex-scan.py`, any OS, Python 3.8+, no packages):

```bash
fragdex-scan                         # scans C:\demos or /home/demos, ~/demos, and every
                                     # Steam library's Quake Live demo folders
fragdex-scan "D:\QL\demos" old.zip   # specific folders, demos or .zip archives
fragdex-scan --logs D:\logs          # also index server / minqlx chat logs
```

It writes `fragdex-<date>.fdx`, gzip-compressed JSON Lines holding each file's
SHA-256, map, server and the player names and Steam IDs. Upload it on the Import page
or drop it in the watched folder. The tool remembers what it already exported
(`fragdex-scan-state.json` next to the output), so later runs only export new demos.
Both versions share that file. The server skips demos it already has, whichever way
they arrived.

Benchmark on a real collection: 1,916 demos (8.3 GB, including a .zip) became a
~500 KB `.fdx` in 7 s with the Windows app (36 s with the Python script on a cold
disk), and the server imported it in about 2 s.

The Windows app is C# (`app/scanner-win/`), a port of the Python parser, built with
the compiler that ships with .NET Framework: run `app\tools\build_scanner_exe.ps1`
after changing the parser. A test checks that both scanners produce identical exports.
The Python download is generated from the app's parser modules at runtime, so it is
always in sync.

| Source | What is extracted |
|---|---|
| `.fdx` | From fragdex-scan: everything it extracted from many demos/logs. From a Fragdex **Export .fdx**: every record with its source, date and server, plus notes and tags. Re-validated on import. |
| Demos `.dm_91` | Name (`n`) + Steam ID (`st`) of every player in the gamestate configstrings, plus players who join or rename mid-demo. Dated by the match start (`g_levelStartTime`, UTC). Demos contain **no IPs**. Point `HOST_WATCH_DIR` at your Quake Live `baseq3/demos` folder to index every demo you record. |
| minqlx chat logs | `[2026-10-29 19:27:35] Railgun:76561197960287930:203.0.113.45 connected.` → name, Steam ID, IP, time. |
| Server logs `.log`/`.txt` | Info-string lines (`n\Name\…\st\7656…`, `\ip\…`), and lines containing exactly one Steam ID and one IP. Chat lines are ignored for these rules so players can't inject fake entries. Lines without a timestamp use the file's modified time. |
| Spreadsheets `.xlsx`/`.csv` | Columns matched by header: Player Name, Steam ID, Discord ID, Aliases, IP Address, Date. Also accepts `STEAM_0:X:Y`, `[U:1:Z]` and profile URLs. **Import → Sample CSV** downloads a template with every column. |

On a Windows host using Docker Desktop, `C:\demos` is shared with the container
automatically (WSL 2 backend). If you use the Hyper-V backend, add it under
Settings → Resources → File sharing.

Log files in the watched folder are **followed**: only new lines are read on each
scan, so you can point `HOST_WATCH_DIR` straight at a live server's log folder.

### Database cleanup

`127.0.0.1` / `::1` are never recorded as a player's IP: they are the server itself or a proxy,
and would link every account seen through them. Records imported before this rule can be
fixed under **Import → Database cleanup → Remove local IP addresses**, which shows how many
records it will change before you press **Fix**. Further maintenance tools go in the same panel.

### About Players.xlsx

268 of its Steam IDs were saved by Excel as numbers (`7.65611980147868E+16`), which
drops the last digits, so they can't be recovered from the file. The import skips
them and reports the count. The Steam Profile links in that sheet were built from the
rounded numbers, so they point to the wrong accounts too. To keep IDs intact in
future, format the Steam ID column as **Text** before pasting IDs.

## API and minqlx plugin

Admins create API keys under **Settings → Fragdex API**, which also shows the base URL
(`https://<your host>/api/v1`) to give to whoever sets up the other program. A key is shown
once; Fragdex stores only its SHA-256. Per key you choose whether it may see IP addresses,
see notes and add records, from which IPs it may be used, its rate limit (requests per
minute) and an expiry date. All of these can be changed later without a new key, and
**Rotate** issues a new key with the same settings.

```bash
curl -H "Authorization: Bearer fdx_..." https://fragdex.example.com/api/v1/players?q=railgun
```

Endpoints: `GET /status`, `GET /players` (search and filters), `GET /players/{steam_id}`,
`GET /players/{steam_id}/linked`, `POST /players/lookup` (up to 100 Steam IDs),
`GET /ips/{ip or CIDR}`, `GET /discord/{id}` and `POST /records`. See the
[API docs](https://d00msdaydevice.github.io/fragdex/integrations/api/).

The **fragdex** plugin for minqlx and minqlxtended (download it from the API panel) sends
every connection and name change to Fragdex in the background and warns admins in game about
players tagged `cheater` or `banned`, with VAC/game bans, or linked to such an account.
Set it up from the server console:

```text
qlx !fragdex url https://fragdex.example.com
qlx !fragdex key fdx_...
qlx !fragdex test
```

See the [plugin docs](https://d00msdaydevice.github.io/fragdex/integrations/minqlx-plugin/).

With `TLS_MODE=off` behind another reverse proxy, set `TRUSTED_PROXIES` to that proxy's
address so API key IP restrictions and the login lockout see the real client address.

## Troubleshooting

### Build fails: `Temporary failure in name resolution` / `No matching distribution found`

`pip` inside the image build can't resolve `pypi.org`: **containers on the host have no
working DNS** (the host itself usually does, and image pulls still work because the
Docker daemon uses the host's resolver). The installers test for this before building
and offer fixes. To fix it by hand on Linux:

```bash
# confirm: the host resolves, a container doesn't
getent hosts pypi.org
docker run --rm python:3.12-slim python -c "import socket; print(socket.gethostbyname('pypi.org'))"
```

1. **Give Docker real DNS servers.** Typical when `/etc/resolv.conf` points at a local stub
   such as systemd-resolved's `127.0.0.53`. Use your network's upstream DNS
   (`resolvectl status` lists it) or public resolvers, in `/etc/docker/daemon.json`:
   ```json
   { "dns": ["1.1.1.1", "8.8.8.8"] }
   ```
   then `sudo systemctl restart docker`.
2. **Restart Docker after firewall changes.** Reloading ufw/firewalld/iptables after Docker
   started can drop Docker's NAT rules: `sudo systemctl restart docker`.
3. **Outbound DNS blocked.** Some hosts and firewalls block port 53 to public resolvers.
   Use the provider's DNS servers in `daemon.json` instead.
4. **One-off workaround:** build with the host's network, then start normally:
   ```bash
   docker build --network host -t fragdex:latest app && docker compose up -d
   ```
   Let's Encrypt and Steam lookups still need container DNS, so fix 1–3 for those.

On **Windows / Docker Desktop**: disconnect VPN/proxy software or allow Docker through it,
add `"dns": ["1.1.1.1", "8.8.8.8"]` under Settings → Docker Engine, or run
`wsl --shutdown` and restart Docker Desktop.

## Operations

### Updating

The database and certificates live in Docker volumes (`fragdex_fragdex_data`,
`fragdex_caddy_data`), not in the project folder, and `.env` / `certs/` are not part
of a release. So: extract the new release over the old folder (or into any folder,
then copy `.env` and `certs/` across) and run

```bash
docker compose pull caddy && docker compose up -d --build
```

or re-run `./deployfragdex.sh` / `deployfragdex.ps1` and keep the existing `.env`.
Never use `docker compose down -v` or `docker volume prune` while the stack is stopped:
both delete the volumes.

Installs from before the project name was fixed (`name: fragdex` in
`docker-compose.yml`) used volumes named after their folder, such as
`fragdex-main_fragdex_data`. The installers copy those into the new names once and
leave the originals in place, printing the command to remove them.

```bash
docker compose logs -f                 # follow logs
# backup (the SQLite database lives in the fragdex_fragdex_data volume)
docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/data/fragdex.sqlite3'); s.backup(sqlite3.connect('/data/backup.sqlite3'))"
docker compose cp app:/data/backup.sqlite3 ./fragdex-backup.sqlite3
```

Other `.env` settings: `STEAM_API_KEY`, `STEAM_REFRESH_HOURS`, `WATCH_INTERVAL`,
`MAX_UPLOAD_MB`, `KEEP_UPLOADS`, `ALT_IP_MAX_ACCOUNTS` (IPs shared by more accounts
than this, such as VPNs, are ignored for alt linking), `SESSION_DAYS`. See
`.env.example`.

### Backups and moving servers

**Players → Export .fdx** downloads every record of the players in the current search
(all of them when nothing is filtered): each name, IP, date, source and server, plus
notes and tags. Import it on the **Import** page of any Fragdex server to restore or
merge. Records that already exist are skipped; notes are appended only if different.

Measured on 1,714 players / 34,565 records:

| Export | Size | Keeps full history | Re-imports losslessly |
|---|---|---|---|
| CSV (summary row per player) | 270 KB | no: aliases and IPs joined into one cell, undated | no |
| CSV with every record | 4.7 MB | yes | no (spreadsheet import) |
| **.fdx** | **328 KB** | yes, plus notes and tags | yes |

The SQLite backup above also keeps users, settings and the import history; `.fdx`
covers the player data and is easier to move or share.

## Security notes

- Passwords are hashed with scrypt; logins are rate limited per IP; every form has a
  CSRF token; cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` whenever TLS is on.
- The app container is only reachable through Caddy. The database contains personal
  data (IP addresses), so keep accounts to people who need them.
- API keys are stored as SHA-256 hashes and shown once. Give each program its own key with
  only the access it needs; IP addresses and notes are off unless you allow them.

## Repository layout

```
├── deployfragdex.sh          Linux installer (writes .env, starts Docker)
├── deployfragdex.ps1         Windows installer (Docker Desktop)
├── docker-compose.yml        app + Caddy (TLS) services
├── .env.example              all settings, documented
├── caddy/entrypoint.sh       builds the Caddyfile from TLS_MODE / SITE_HOST / ports
├── certs/                    your own cert.pem + key.pem for TLS_MODE=custom
├── docs/  mkdocs.yml         documentation site (GitHub Pages, built by .github/workflows/docs.yml)
├── app/
│   ├── Dockerfile
│   ├── requirements.txt      runtime deps (requirements-dev.txt adds pytest)
│   ├── fragdex/              the FastAPI application
│   │   ├── main.py           routes, auth, CSRF, uploads, downloads, API key settings
│   │   ├── api.py  apikeys.py    the /api/v1 JSON API; API keys, rate limits, usage
│   │   ├── db.py             SQLite schema, search, alt-account linking
│   │   ├── worker.py         import queue, watched folder, Steam refresher
│   │   ├── importers/        demo / log / spreadsheet / .fdx parsers
│   │   ├── scanner.py        fragdex-scan CLI (also bundled as a standalone script)
│   │   ├── downloads/        prebuilt fragdex-scan.exe and the minqlx plugin (minqlx/fragdex.py)
│   │   ├── templates/  static/
│   ├── scanner-win/          the Windows fragdex-scan app (C#, WinForms)
│   ├── tests/
│   └── tools/                Huffman table generators, Windows scanner build script
└── .github/workflows/ci.yml  tests, Python 3.8 scanner check, Docker + Caddyfile validation,
                              Windows scanner build + parity test
```

## Development

```bash
pip install -r app/requirements-dev.txt
python -m pytest app/tests        # PLAYERS_XLSX=... / REAL_DEMO_DIR=... enable the real-data tests
cd app && DATA_DIR=./.dev TLS_MODE=off uvicorn fragdex.main:app --reload
python tools/gen_huffman.py       # (in app/) regenerates the Quake 3 Huffman table (already committed)
python tools/gen_huffman_cs.py    # (in app/) copies it into the Windows scanner
python -m fragdex.scanner_build   # (in app/) writes the standalone fragdex-scan.py
powershell -File app\tools\build_scanner_exe.ps1   # rebuilds the Windows scanner (Windows only)
```

The demo parser decodes the Quake 3 network Huffman code. Its table is built from
ioquake3's `msg_hData` frequencies with a port of ioquake3's adaptive Huffman code
(GPLv2).

## License

Fragdex is free software, licensed under the **GNU General Public License v2.0 or
later** (`GPL-2.0-or-later`). See [LICENSE](LICENSE). This is required by the
ioquake3-derived Huffman code above, and it covers the fragdex-scan downloads too.

Third-party components:

| Component | License |
|---|---|
| Quake III Arena / ioquake3 Huffman table and algorithm (`app/tools/gen_huffman.py`, `importers/huffman_table.py`, `scanner-win/HuffmanTable.cs`) | GPL-2.0-or-later, © id Software |
| Chakra Petch font | SIL Open Font License 1.1, [OFL-ChakraPetch.txt](app/fragdex/static/fonts/OFL-ChakraPetch.txt) |
| JetBrains Mono font | SIL Open Font License 1.1, [OFL-JetBrainsMono.txt](app/fragdex/static/fonts/OFL-JetBrainsMono.txt) |

Quake Live and Steam are trademarks of their respective owners. Fragdex isn't
affiliated with id Software, Bethesda or Valve.
