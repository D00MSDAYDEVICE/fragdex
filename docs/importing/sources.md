# Supported sources

| Source | Names | Steam IDs | IPs | Dates |
|---|:-:|:-:|:-:|:-:|
| Demos `.dm_91` | :material-check: | :material-check: | :material-close: | match start |
| minqlx chat logs | :material-check: | :material-check: | :material-check: | each line |
| Server console logs | :material-check: | :material-check: | sometimes | each line, or file date |
| Spreadsheets `.xlsx` / `.csv` | :material-check: | :material-check: | if there is a column | if there is a column |
| `.fdx` files | :material-check: | :material-check: | as the original source | as the original source |
| [API](../integrations/api.md), e.g. the [minqlx plugin](../integrations/minqlx-plugin.md) | :material-check: | :material-check: | :material-check: | each connection |

## Demos

Fragdex reads Quake Live demos (`.dm_91`, or any `.dm_<number>`) directly: it decodes the demo's network stream and reads the player information from the gamestate.

- **Name and Steam ID** of every player in the demo, including players who join or rename mid-match.
- **Map and server name**, shown in each record's *From* column.
- **Date**: the match start time (`g_levelStartTime`, UTC).
- **No IP addresses.** Demos do not contain them.

Point the [watched folder](overview.md#watched-folder) at your Quake Live `baseq3/demos` folder to index every demo you record, or use [fragdex-scan](fragdex-scan.md) to index a big collection on your own PC.

## minqlx chat logs

Connection lines from minqlx chat logs give the most complete records: name, Steam ID, IP and time.

```text
[2026-10-29 19:27:35] ^1Rail^7gun:76561197960287930:203.0.113.45 connected.
```

!!! tip "Live instead of logs"
    The [fragdex minqlx plugin](../integrations/minqlx-plugin.md) sends the same information to Fragdex the moment a player connects, so you don't need to collect chat logs at all. Its records have the source **API**.

## Server console logs

From Quake Live server and minqlx console logs (`.log` or `.txt`), Fragdex picks:

1. **Info strings** carrying a Steam ID, such as
    `ClientUserinfoChanged: 3 n\Railgun\t\0\…\st\76561197960287930`
    or `Userinfo: \name\Railgun\ip\203.0.113.45:27960\steam_id\7656…`.
2. **Any other line with exactly one SteamID64 and exactly one IP address**, recorded without a name.

!!! info "Chat cannot inject records"
    Rules 1 and 2 are skipped for chat lines (`say:`, `say_team:`, `tell:` and so on), so players cannot type fake Steam IDs or IPs into chat and have them recorded.

Lines without a timestamp are dated with the file's modification time. Log files in the watched folder are **followed**: only lines added since the last scan are read.

## Spreadsheets

`.xlsx`, `.xlsm` and `.csv` files with a header row. Columns are matched by header name, case-insensitively, in any order; only the Steam ID column is required.

| Field | Accepted headers |
|---|---|
| Steam ID | Steam ID, SteamID, Steam_ID, SteamID64, Steam64 |
| Name | Player Name, Name, Player, Nick, Nickname |
| Aliases | Aliases, Alias, AKA, Other Names, Alt Names (split on `,` `;` `|` and ` / `) |
| IP | IP Address, IP, IPs, IP Addresses, Address (several per cell allowed) |
| Date | Date, Seen, Last Seen, Date Seen, Timestamp, Time, Seen At |
| Discord ID | Discord ID, Discord, DiscordID, Discord User, Discord Username, Discord Name |

Steam IDs can be a SteamID64 (`76561197960287930`), `STEAM_0:0:11101`, `[U:1:22202]` or a `steamcommunity.com/profiles/…` link. Discord IDs can be a user ID, `<@mention>`, `discord.com/users/…` link or username (see [Discord ID](../guide/player-profile.md#discord-id)). A Discord ID from a spreadsheet fills in only where the player has none yet; invalid ones are ignored and counted in the import's details.

The easiest start is the **Sample CSV** button on the Import page. It downloads a file with every column above and one example row for each accepted format:

```csv
Player Name,Steam ID,Discord ID,Aliases,IP Address,Date
^1Rail^7gun,76561198000000001,80351110224678912,"railgod, ^1R^7ailgun",203.0.113.45,2026-10-29 19:27:35
Frostbite,STEAM_0:1:19867137,frostbite,frosty,198.51.100.7 198.51.100.8,2026-10-28
^3Halcyon,[U:1:39734279],,,192.0.2.14,
Wraith,https://steamcommunity.com/profiles/76561198000000017,,wr4ith | ^4wraith^7.cfg,,2026-09-14 21:05
```

!!! warning "Steam and Discord IDs saved as numbers"
    Excel stores long numbers with only 15 significant digits, so a Steam ID (17 digits) or Discord ID (17–20 digits) typed or pasted into a *General* or *Number* cell becomes something like `7.65611980147868E+16` and its last digits are lost for good. Fragdex skips such rows (or ignores such Discord IDs) and reports how many. Format both columns as **Text** before entering IDs, and open the sample CSV with **Data → From Text/CSV**, setting those columns to Text.

## .fdx files

Two programs write `.fdx` files:

- [fragdex-scan](fragdex-scan.md): everything it extracted from demos (and optionally logs) on your PC.
- **Players → Export .fdx** on any Fragdex server: every record with its source, date and server, plus notes, tags and Discord IDs.

## Local addresses

Whatever the source, `127.0.0.1` (any `127.x`) and `::1` are never recorded as a player's IP. They appear when a player connects from the server machine itself or through a proxy on it, so they say nothing about the player and would [link](../guide/linked-accounts.md) everyone seen through them. The rest of the record is kept. To remove such addresses from data imported before, use [Database cleanup](overview.md#database-cleanup).

Everything in a `.fdx` file is validated again on import; the file is never trusted. See [The .fdx format](fdx-format.md).
