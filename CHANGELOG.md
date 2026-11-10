# Changelog

All notable changes to Fragdex. Versions follow [Semantic Versioning](https://semver.org/); dates are UTC.

To update, see [Updating](https://d00msdaydevice.github.io/fragdex/administration/updating/). Existing databases are upgraded automatically when the new version starts.

## 1.4.0 (2026-11-09)

### Added

- **QLSM addon**, downloaded from the **Fragdex API** panel and installed from QLSM's **Settings → Addons**. For each server you choose, it sends the connections in its chat logs to Fragdex and shows Fragdex tags and bans in the Live Status player list. A **Fragdex Players** panel lists who is connected, with whether their Steam ID is verified, their Discord, VAC and game bans, and buttons to copy their Discord name, Discord ID and Steam ID.
- API: `POST /logs` adds connection lines from a chat log, for keys with **Add records** and **Game server**. `GET /status` now reports a key's `game_server` option.

### Fixed

- **Security: forged records from minqlx chat logs.** A player could type a chat message or vote, or use a specially made name, that Fragdex read as another player's connection, which recorded a false Steam ID and IP. Log records count as verified Steam IDs, so these did too. Only complete connection lines are read now.

## 1.3.0 (2026-11-08)

### Added

- **Discord usernames.** With an optional Discord bot token (**Settings → Discord usernames** or `DISCORD_BOT_TOKEN`), Fragdex looks up the username for each Discord ID in the background and checks it again every 7 days.
    - The Players page shows the username. Hover to see the ID, click to copy it.
    - The player profile shows both, and search finds players by username.
    - CSV exports and the API include `discord_name`, and `GET /discord/{id}` also matches looked-up usernames.
- The Players page can be sorted by **Discord**, and has a **Has Discord** filter. The API has `sort=discord` and `has_discord=1`.
- Spreadsheets and CSV files can have a separate `Discord Name` column next to the Discord ID.
- **Verified Steam IDs.** A Steam ID from a demo, a log file or a game server's plugin is marked verified, because it can't be typed in by someone else. There is a **Verified Steam ID** filter, and `steam_verified` and `verified=1` in the API.
    - API keys have a **Game server** option. Only records from a key with it count, and turning it on also counts the key's earlier records.
- **FragQ links** (**Settings → FragQ links**, or `FRAGQ_URL` and `FRAGQ_KEY`). Fragdex reads the Discord account each player linked in FragQ every 15 minutes and labels it **verified** or **claimed**. Needs FragQ 1.1.0 or later, with a connector made with **Create Fragdex connector**.
    - Several FragQ servers can feed one Fragdex, each with its own name, key, last result and **Trusted** setting. A new connection starts untrusted, and only a trusted one can mark a link verified.
    - Each server's answer is kept separately and a player's value is worked out from all of them, so servers that disagree don't overwrite each other. Disagreements that can't be settled are listed as conflicts.
    - Values entered by hand or imported are never overwritten. Differences are listed as conflicts on the Settings page.
    - Players only FragQ knows about are added with their Steam name, under the new source **FragQ**.

### Changed

- A spreadsheet or CSV row with only a Steam ID and a Discord ID now just fills in the Discord ID of a player already in the database. It no longer adds an empty record or a nameless player. The import's message says how many IDs were added, kept or left out.
- The **Discord ID** column on the Players page is now **Discord**.
- A `.fdx` entry with an unknown type is imported as `manual` instead of `demo`, and API records in a `.fdx` keep the source `api` instead of becoming `demo`. Records from a **Game server** key stay verified.
- API: `GET /discord/{id}` matches usernames in upper or lower case. Its response has a new `query` field with what was searched for, and the top-level `discord_id` is now `null` for a username search instead of repeating the username.

## 1.2.0 (2026-10-31)

### Added

- **API** under `/api/v1` for other programs, such as Discord bots, scripts and game servers:
    - status, player search with the same filters as the Players page, full player profiles, linked accounts;
    - lookup of up to 100 Steam IDs at once;
    - search by IP address or CIDR range, and by Discord ID;
    - `POST /records` to add records, which appear with the source **API**.
- **API keys** on the Settings page, in a new **Fragdex API** panel that also shows the base URL to give to other admins.
    - A key is shown once; only its hash is stored.
    - Each key has its own allowed IPs, rate limit, expiry date and permissions (see IP addresses, see notes, add records). All of them can be changed at any time without a new key.
    - Keys can be disabled, rotated (new key, same settings) and deleted.
    - Each key shows when and from where it was last used, its request count, and the last refused request with the reason.
- **fragdex plugin for minqlx and minqlxtended**, downloadable from the Fragdex API panel. One file works on both.
    - Sends every connection and name change to Fragdex as it happens, in batches from a background thread.
    - Warns admins in game when a player joins who is tagged (`cheater` or `banned` by default), has VAC or game bans, or is linked to such an account.
    - `!fragdex url`, `key`, `test` and `check <client id or SteamID64>` commands. The key can only be set from the server console or rcon. `!fragdex` and `!fragdex check` need permission 5 by default (`qlx_fragdexPermission`).
- `TRUSTED_PROXIES` setting for running Fragdex with `TLS_MODE=off` behind another reverse proxy, so the real client address is used.
- Documentation: new Integrations section with the API and minqlx plugin pages.

### Changed

- Caddy passes the client's address to the app. API key IP restrictions and the login lockout both use it.
- Settings page: the System panel is the same height as the Steam Web API panel.

## 1.1.0 (2026-10-31)

### Added

- **Discord ID** per player:
    - a column on the Players page, a field on the player profile and the Add page;
    - included in search;
    - a `discord` column in CSV and spreadsheet imports, and in CSV and `.fdx` exports. The `.fdx` format is unchanged, so older files still import.
- **Database cleanup** panel on the Import page, with a tool that removes local IP addresses already in the database.
- Sample CSV download showing every column that can be imported.

### Changed

- Local addresses (`127.0.0.0/8`, `::1`, `::ffff:127.*`) are never recorded, from any source.
- Import page panels are laid out in an even 2×2 grid.
- `fragdex-scan.exe` rebuilt as 1.1.0.

## 1.0.0 (2026-10-30)

First release.

- Login-protected web app (FastAPI and SQLite) behind Caddy with automatic HTTPS, deployed with Docker Compose by `deployfragdex.sh` or `deployfragdex.ps1`.
- Builds a history of Quake Live players (names, aliases, Steam IDs, IP addresses) from demos, server and minqlx chat logs, spreadsheets, CSV and `.fdx` files, by upload or from a watched folder.
- Search by name, Steam ID, IP address or CIDR range, with filters, linked accounts, tags and notes.
- Optional Steam Web API lookups for avatars, Steam names and VAC and game bans.
- CSV and `.fdx` export.
- Admin and viewer roles.
- `fragdex-scan` desktop tool (Windows app and Python script) that indexes demo collections into small `.fdx` files.
- Documentation site on GitHub Pages.
