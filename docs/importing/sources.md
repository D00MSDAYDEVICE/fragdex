# Supported sources

| Source | Names | Steam IDs | IPs | Dates |
|---|:-:|:-:|:-:|:-:|
| Demos `.dm_91` | :material-check: | :material-check: | :material-close: | match start |
| minqlx chat logs | :material-check: | :material-check: | :material-check: | each line |
| Server console logs | :material-check: | :material-check: | sometimes | each line, or file date |
| Spreadsheets `.xlsx` / `.csv` | :material-check: | :material-check: | if there is a column | if there is a column |
| `.fdx` files | :material-check: | :material-check: | as the original source | as the original source |

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

Steam IDs can be a SteamID64 (`76561197960287930`), `STEAM_0:0:11101`, `[U:1:22202]` or a `steamcommunity.com/profiles/…` link.

!!! warning "Steam IDs saved as numbers"
    Excel stores long numbers with only 15 significant digits, so a Steam ID typed or pasted into a *General* or *Number* cell becomes something like `7.65611980147868E+16` and its last digits are lost for good. Fragdex skips such rows and reports how many it skipped. Format the Steam ID column as **Text** before entering IDs.

## .fdx files

Two programs write `.fdx` files:

- [fragdex-scan](fragdex-scan.md): everything it extracted from demos (and optionally logs) on your PC.
- **Players → Export .fdx** on any Fragdex server: every record with its source, date and server, plus notes and tags.

Everything in a `.fdx` file is validated again on import; the file is never trusted. See [The .fdx format](fdx-format.md).
