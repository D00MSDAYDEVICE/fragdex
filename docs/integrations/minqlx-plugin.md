# minqlx plugin

The **fragdex** plugin connects your Quake Live servers to Fragdex:

- **Live records.** Every player who connects (Steam ID, name, IP address and time) and every name change is sent to Fragdex within seconds. You no longer need to import chat logs or rely on the watched folder.
- **Join warnings.** When a player joins who is tagged (by default `cheater` or `banned`), has VAC or game bans, or is linked to an account that is, admins on the server are told in game.
- **Lookups in game.** `!fragdex check <id>` shows what Fragdex knows about a player on the server, by client id or SteamID64.

The same file works on **minqlx** and **minqlxtended**, and needs nothing beyond the Python standard library.

## Install

1. In Fragdex, open **Settings → Fragdex API** and create a key for the game server, with **Add records** and **Game server** ticked. **Game server** makes the Steam IDs it sends count as [verified](../administration/fragq.md#verified-steam-ids). Tick nothing else: the plugin needs neither IP addresses nor notes. Under **Allowed IPs**, enter the game server's public address.
2. On the same panel, download **minqlx plugin (fragdex.py)** and copy it into the server's plugin folder (`qlx_pluginsPath`, usually `minqlx-plugins`).
3. Add `fragdex` to `qlx_plugins` in `server.cfg`, or load it now from the server console: `qlx !load fragdex`.
4. Point it at Fragdex from the **server console or rcon**:

    ```text
    qlx !fragdex url https://fragdex.example.com
    qlx !fragdex key fdx_...
    qlx !fragdex test
    ```

    `!fragdex test` should answer *connected to Fragdex … with key '…'*.

The URL and key are saved in the bot's database (Redis), so they survive restarts. Several game servers can share one key, but a key per server shows in Fragdex which server sent what and lets you restrict each key to its server's address.

### Supplying the Discord bot token to another plugin

If another minqlx plugin needs the Discord bot token stored in Fragdex, create a separate API key with **Read Discord bot token** enabled, no other optional permissions, and the game server's public egress IP under **Allowed IPs**. That plugin can use `GET /api/v1/discord/token` with `Authorization: Bearer <API key>`. Do not reuse the record-upload key: the token-read permission is separate.

!!! warning "Never type the key in chat"
    The plugin refuses `!fragdex key` from chat, because the line has already been sent to the server as a chat message by then. If it happens, **Rotate** the key in Fragdex and set the new one from the console.

Instead of the commands, you can set the cvars `qlx_fragdexUrl` and `qlx_fragdexKey` in `server.cfg`. Values set with the commands take precedence.

## QL Mover Discord token integration

The `discord_ql_mover` plugin can fetch Fragdex's configured Discord bot token at load time and refresh it in the background. In Fragdex, create a separate API key for QL Mover. Enable **Read Discord bot token**, leave the other optional permissions off, and set **Allowed IPs** to the game server's public egress IP. The key authenticates with `Authorization: Bearer`; do not use the admin login credentials or your record-upload key.

Set these cvars in `server.cfg`:

```text
set qlx_fragdex_api_url "https://fragdex.example.com/api/v1"
set qlx_fragdex_api_key "<Fragdex API key with Read Discord bot token permission>"
set qlx_fragdex_token_refresh_interval "3600"
```

The API URL defaults to `https://fragdex.kr3m.one/api/v1`, the API key defaults to empty, and the refresh interval defaults to 3600 seconds. Set the interval to `0` to fetch only at plugin load. The token is cached in memory and is not logged; a failed fetch leaves the last successfully cached token in place. If no token is obtained yet, QL Mover will skip Discord moves but the game server remains available.

## Commands

| Command | Permission | |
|---|---|---|
| `!fragdex` | `qlx_fragdexPermission` (5) | Status: server, key (first characters), records waiting, last upload. |
| `!fragdex url <address>` | 5 | Set the Fragdex server. `fragdex.example.com`, `https://fragdex.example.com:8443` and the full base URL all work. |
| `!fragdex key <key>` | 5, console only | Set the API key. |
| `!fragdex test` | 5 | Check the connection and the key. |
| `!fragdex check <id>` | `qlx_fragdexPermission` (5) | Look up a player on the server by client id (the number shown by `/players`), or anyone by SteamID64: last seen, tags, bans, linked accounts, Discord ID. |

## Cvars

| Cvar | Default | |
|---|---|---|
| `qlx_fragdexUrl` | | Fragdex address, if not set with `!fragdex url`. |
| `qlx_fragdexKey` | | API key, if not set with `!fragdex key`. |
| `qlx_fragdexServerName` | `sv_hostname` | Name stored with each record. |
| `qlx_fragdexRecord` | `1` | `0` stops sending connections. |
| `qlx_fragdexJoinAlerts` | `1` | `0` stops join warnings. |
| `qlx_fragdexAlertTags` | `cheater,banned` | Tags that trigger a warning, comma separated, case-insensitive. `*` means any tag. |
| `qlx_fragdexAlertBans` | `1` | `0` ignores VAC and game bans. |
| `qlx_fragdexPermission` | `5` | Permission level for `!fragdex` and `!fragdex check`. `url`, `key` and `test` always need 5. |
| `qlx_fragdexAlertPermission` | `2` | Permission level that receives join warnings. |
| `qlx_fragdexInterval` | `15` | Seconds between uploads. |
| `qlx_fragdexVerifyTls` | `1` | `0` accepts a self-signed certificate, for a Fragdex server on a LAN or with `TLS_MODE=internal`. |

Cvars are re-read on every map change.

## How it behaves

- Records are queued in memory and sent in batches every `qlx_fragdexInterval` seconds by a background thread, so the game never waits for Fragdex. If Fragdex can't be reached, up to 5,000 records wait and are sent once it is back. When the plugin is unloaded or reloaded, it sends what is left first.
- A refused key (wrong, expired, disabled, not allowed from this address, or without **Add records**) drops the batch rather than retrying it forever. `!fragdex` shows the reason, and Fragdex shows it under the key as *Last refused*.
- Records arrive in Fragdex with the source **API**, the key's name and the server name. Bots and local addresses are skipped.
- Join checks for players who reconnect within two minutes are skipped, and each player triggers at most one warning per 15 minutes.
- Warnings go privately to the admins on the server and to the server log.

## Troubleshooting

| `!fragdex test` says | |
|---|---|
| `HTTP 401: Invalid API key.` | The key is wrong or was rotated or deleted. Set the current one. |
| `HTTP 403: This API key can't be used from …` | Add that address to the key's **Allowed IPs**, or clear the list. |
| `HTTP 403: This API key is expired.` / `disabled.` | Change the expiry date or enable the key in Fragdex. |
| `CERTIFICATE_VERIFY_FAILED` | Fragdex uses a self-signed certificate. Fix HTTPS (see [HTTPS and ports](../administration/https.md)) or set `qlx_fragdexVerifyTls 0`. |
| `timed out`, `Connection refused`, `Name or service not known` | The game server can't reach Fragdex: check the address, port and firewalls. |
| *This key can't add records* | Tick **Add records** for the key in Fragdex. |
