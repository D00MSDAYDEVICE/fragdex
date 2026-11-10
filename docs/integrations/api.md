# API

Other programs can search Fragdex and add records to it through a JSON API: a [minqlx plugin](minqlx-plugin.md) on your game servers, a Discord bot, a script, or another Fragdex server. Every request needs an **API key**, created by an admin on the Settings page.

## API keys

Open **Settings → Fragdex API**. The panel shows the **Base URL** to give to whoever sets up the other program, for example `https://fragdex.example.com/api/v1`, and lists the keys.

### Create a key

Press **Create key**, fill in the form and press **Create key** again. The new key (`fdx_…`) is shown **once**, with a copy button and a `curl` command to test it. Fragdex stores only a SHA-256 hash of it, so a key that wasn't copied can't be shown again: rotate it instead.

Create one key per program or admin. You can then see which program made which requests, change one key's access and revoke it without breaking the others.

### Settings of a key

All of these can be changed at any time with **Edit**, without a new key. Changes apply from the key's next request.

| Setting | |
|---|---|
| **Name** | Which program or admin uses the key, for example *minqlx on CA #1*. |
| **Rate limit** | Requests per minute, `120` by default. `0` means unlimited. |
| **Expires** | The key works through the end of this day (UTC). Empty: never expires. An expired key is kept; set a later date and it works again. |
| **Allowed IPs** | IP addresses and ranges (CIDR) the key may be used from, one per line, for example `203.0.113.10` or `198.51.100.0/24`. Empty: any address. Requests from anywhere else are refused. |
| **See IP addresses** | Include IP addresses in replies and allow searching by IP. Off by default. |
| **See notes** | Include player notes in replies and allow searching them. Off by default. |
| **Add records** | Allow `POST /records`, which adds records to the database. Needed by the minqlx plugin. |
| **Game server** | Records added with this key count as [verified Steam IDs](../administration/fragq.md#verified-steam-ids). Only for a game server's plugin. |

Every key can read players: names, aliases, Steam IDs, tags, Discord IDs, Steam bans and linked accounts. A key without **See IP addresses** still sees *which* accounts are linked, but not the shared addresses.

### Other actions

| Button | |
|---|---|
| **Disable / Enable** | Pause a key without deleting it. |
| **Rotate** | Replace the key with a new one and keep all its settings, for example after it leaked. The old key stops working immediately. |
| **Delete** | Remove the key. Programs using it can no longer connect. |

Each key shows when it was created and by whom, when it was last used and from which address, how many requests it has made, and the last request that was refused and why (wrong address, expired, disabled). The last one is the first place to look when a program stops working.

!!! note "Behind another proxy"
    Allowed IPs and the login lockout use the caller's real address, which Caddy passes to Fragdex. If Fragdex runs with `TLS_MODE=off` behind another reverse proxy, every request comes from that proxy. Set `TRUSTED_PROXIES` in `.env` to the proxy's address or range (for example `10.0.0.5` or `private_ranges`) so Caddy uses the address the proxy reports instead. See [Settings and .env reference](../administration/settings.md#site-and-https).

## Making requests

Send the key in the `Authorization` header (or as `X-API-Key: <key>`). Never put it in the URL.

```bash
curl -H "Authorization: Bearer fdx_..." https://fragdex.example.com/api/v1/status
```

Every reply is JSON. Errors look like `{"error": "This API key is expired."}`:

| Status | Meaning |
|---|---|
| `400` | A parameter or the request body is invalid; the message says which. |
| `401` | The key is missing or wrong. |
| `403` | The key is disabled or expired, is used from an address it isn't allowed from, or lacks the permission for this request. |
| `404` | No such player. |
| `429` | Over the rate limit. `Retry-After` says how many seconds to wait. |

Replies to a key with a rate limit carry `X-RateLimit-Limit` and `X-RateLimit-Remaining` headers. Dates are UTC, formatted `YYYY-MM-DD HH:MM:SS`.

## Endpoints

All paths are relative to the base URL.

### `GET /status`

The server's version and size, and what the key may do. Use it to test a key.

```json
{"app": "fragdex", "version": "1.2.0", "players": 1714, "records": 34565,
 "key": {"name": "Discord bot", "ips": false, "notes": false, "write": false, "game_server": false, "rate_limit": 120, "expires": null}}
```

### `GET /players`

Search and filter players, like the Players page. Parameters, all optional:

| Parameter | |
|---|---|
| `q` | Name, alias, Steam name, Steam ID, Discord ID; also IP address or CIDR range (with **See IP addresses**) and notes (with **See notes**). |
| `tag` | Tag name. |
| `source` | `demo`, `log`, `xlsx`, `csv`, `manual`, `api` or `fragq`. |
| `bans` | `vac`, `any` or `clean`. |
| `has_ip`, `has_discord`, `verified`, `linked` | `1` for only players with an IP address / with a Discord ID / with a verified Steam ID / with linked accounts. |
| `seen_from`, `seen_to` | Last-seen date range, `YYYY-MM-DD`. |
| `sort`, `dir` | `last_seen` (default), `first_seen`, `name`, `steam_id`, `discord`, `aliases`, `ips` or `alts`; `desc` (default) or `asc`. |
| `limit`, `offset` | Page size (1–500, default 50) and position. |

```json
{"total": 2, "offset": 0, "limit": 50, "players": [
  {"steam_id": "76561197960287930", "name": "Railgun", "name_raw": "^1Rail^7gun",
   "first_seen": "2026-09-05 10:55:58", "last_seen": "2026-10-29 19:27:35", "name_count": 2,
   "linked_accounts": 1, "discord_id": "80351110224678912", "discord_name": "railgod", "discord_status": "verified",
   "steam_verified": true, "tags": ["cheater"],
   "steam": {"persona": "Railgun", "avatar": "https://avatars.steamstatic.com/…", "vac_bans": 1, "game_bans": 0,
             "community_banned": false, "days_since_last_ban": 120, "checked_at": "2026-10-30 08:00:00"},
   "profile_url": "https://steamcommunity.com/profiles/76561197960287930",
   "aliases": ["Railgun", "railgod"]}]}
```

`steam` is `null` until the player has been checked with the [Steam Web API](../administration/steam.md). With **See IP addresses** each player also has `last_ip` and `ip_count`; with **See notes**, `notes`.

### `GET /players/{steam_id}`

One player in full: the fields above, plus every name (`names`, with record counts, first and last seen and sources), the number of `records`, and `linked` accounts with their tags and bans. With **See IP addresses**, also every IP (`ips`, with how many other accounts used it) and each linked account's `shared_ips`. The Steam ID may be in any format Fragdex accepts (SteamID64, `STEAM_0:X:Y`, `[U:1:Z]`).

### `GET /players/{steam_id}/linked`

Only the linked accounts.

### `POST /players/lookup`

Up to 100 players at once, for example everyone on a game server.

```bash
curl -H "Authorization: Bearer fdx_..." -H "Content-Type: application/json" \
     -d '{"steam_ids": ["76561197960287930", "76561198000000002"]}' \
     https://fragdex.example.com/api/v1/players/lookup
```

```json
{"players": [
  {"known": true, "steam_id": "76561197960287930", "name": "Railgun", "tags": ["cheater"], "…": "…",
   "linked": [{"steam_id": "76561198000000001", "name": "AltGuy", "last_seen": "2026-10-29 19:30:00",
               "vac_bans": 0, "game_bans": 0, "tags": []}]},
  {"known": false, "steam_id": "76561198000000002"}],
 "invalid": []}
```

### `GET /ips/{ip}`

Every player seen on an IP address or CIDR range (`/ips/198.51.100.0/24`), with the matching addresses, record counts and tags. Up to 500 players. Needs **See IP addresses**.

### `GET /discord/{id}`

The players with a Discord ID: a user ID, a mention (`<@80351110224678912>`) or a username. A username also matches names [looked up from Discord](../administration/discord.md), in upper or lower case.

```json
{"query": "railgod", "discord_id": null,
 "players": [{"steam_id": "76561197960287930", "name": "Railgun",
              "discord_id": "80351110224678912", "discord_name": "railgod", "…": "…"}]}
```

`query` is what was searched for. The top-level `discord_id` is that same value for a numeric ID and `null` for a username. Each player has their own `discord_id` and `discord_name`. A player's `discord_id` can hold a username instead of a number if that is what was entered for them, and `discord_name` is `null` until a username has been looked up.

`steam_verified` is `true` when the Steam ID came from a demo, a log or a game server. `discord_status` is `verified` or `claimed` for a [link from FragQ](../administration/fragq.md#claimed-and-verified), otherwise `null`.

### `POST /logs`

Add connection lines from a minqlx chat log, as the [QLSM addon](qlsm-addon.md) does. Needs **Add records** and **Game server**, because these become **log** records, whose Steam IDs count as verified.

```json
{"server": "Thunderdome CA #1",
 "lines": ["[2026-10-31 19:27:35] Railgun:76561197960287930:203.0.113.45 connected."]}
```

Up to 5000 lines per request, with times in UTC. Only complete connection lines are stored. The answer says how many were `added`, `skipped` as already known and `ignored` as not being connections.

### `POST /records`

Add records. Needs **Add records**.

```json
{"server": "Thunderdome CA #1",
 "records": [{"steam_id": "76561197960287930", "name": "^1Rail^7gun", "ip": "203.0.113.45",
              "seen_at": "2026-10-31 19:27:35"}]}
```

| Field | |
|---|---|
| `server` | Optional. Stored with every record; colour codes are removed. |
| `steam_id` | Required, any accepted format. |
| `name`, `ip` | At least one of them. Local addresses (`127.0.0.1`, `::1`) are dropped, as for every source. |
| `seen_at` | UTC, as `YYYY-MM-DD HH:MM:SS`, ISO 8601 or a Unix time. Default: now. |

Up to 1,000 records per request. The reply counts what happened:

```json
{"added": 1, "skipped": 0, "rejected": [], "players": 1}
```

`skipped` records already existed. `rejected` lists invalid ones by position, with the reason. The records appear in Fragdex at once with the source **API** and the key's name; linked accounts are recalculated within a minute.

## Security

- Give each program its own key with only the access it needs. A Discord bot that shows names and bans needs neither IP addresses nor notes.
- Restrict keys used from fixed addresses, such as game servers, to those addresses.
- Store keys like passwords: in the program's configuration, not in chat or in shared code.
- If a key may have leaked, **Rotate** it.
- The API sends no CORS headers, so web pages on other sites can't use a key from a visitor's browser.
