# Export CSV and .fdx

Both buttons sit at the top of the **Players** page and export **the players in the current search and filters**, or every player when nothing is filtered. Every role, viewers included, can export.

| | **Export CSV** | **Export .fdx** |
|---|---|---|
| For | spreadsheets, sharing a list | backups, moving or merging servers |
| Contents | one summary row per player | every record of every player, plus notes, tags and Discord IDs |
| History | aliases and IPs joined into one cell, undated | complete: each name and IP with its date, source and server |
| Re-import into Fragdex | as a spreadsheet: loses dates and sources | keeps every record, with its date, source and server |
| Size (1,714 players, 34,565 records) | 270 KB | **328 KB** |

## CSV

One row per player, ready for Excel, LibreOffice or Google Sheets. Columns:

`steam_id`, `discord_id`, `discord_name`, `name` (colour codes removed), `name_raw` (with colour codes), `last_ip`, `first_seen`, `last_seen`, `aliases`, `ips`, `linked_accounts`, `tags`, `notes`, `steam_persona`, `vac_bans`, `game_bans`, `steam_profile` (link), `steam_verified`, `discord_status`

!!! tip "Keep Steam IDs as text"
    Excel turns 17-digit Steam IDs into numbers such as `7.65611980147868E+16` and silently loses the last digits. When opening the CSV in Excel, import it with **Data → From Text/CSV** and set the `steam_id` and `discord_id` columns to **Text**. Discord user IDs are even longer than Steam IDs and are damaged the same way.

## .fdx

A compressed file holding every record of the exported players: each name, IP, date, source and server, plus their notes, tags and Discord IDs. It is the same format the [fragdex-scan](../importing/fragdex-scan.md) desktop tool writes, and any Fragdex server can import it.

Use it to:

- **Back up** the player data. See [Backup and restore](../administration/backup-restore.md).
- **Move** to a new server: export everything, import on the new one.
- **Merge** two Fragdex servers, or share a subset (for example everyone tagged *cheater*) with another community.

On import, records that already exist are skipped, tags are added, notes are appended only if they differ, and a Discord ID fills in only where the player has none. Importing the same export twice changes nothing.

See [The .fdx format](../importing/fdx-format.md) for what is inside.
