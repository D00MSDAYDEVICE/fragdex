# Changelog

All notable changes to Fragdex. Versions follow [Semantic Versioning](https://semver.org/); dates are UTC.

To update, see [Updating](https://d00msdaydevice.github.io/fragdex/administration/updating/). Existing databases are upgraded automatically when the new version starts.

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
