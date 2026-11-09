# The .fdx format

`.fdx` is Fragdex's exchange format, written by [fragdex-scan](fragdex-scan.md) and by **Players → Export .fdx**. It is a **gzip-compressed [JSON Lines](https://jsonlines.org/) file**: one JSON object per line, UTF-8, compressed with gzip. Any tool that reads gzip and JSON can read it:

```bash
gzip -dc fragdex-20261030-101500.fdx | head -3
```

## Structure

The first line is a header:

```json
{"format": "fragdex-scan", "version": 1, "created": "2026-10-30 10:15:00", "tool": "1.0.0"}
```

| Field | |
|---|---|
| `format` | Always `fragdex-scan`. |
| `version` | `1` from fragdex-scan, `2` from a server export. A server reads every version up to its own. |
| `created` | UTC time the file was written. |
| `tool` | Version of the program that wrote it. |

Every following line is an entry. A **file entry** holds the records from one scanned file (or, in a server export, one group of records):

```json
{"file": "CA-campgrounds-2026_09_05-10_55_58.dm_91", "kind": "demo",
 "sha256": "9f2c…", "size": 1593344, "map": "campgrounds", "server": "Thunderdome CA #1",
 "s": [["76561197960287930", "^1Rail^7gun", "", "2026-09-05 10:55:58"], …]}
```

| Field | |
|---|---|
| `file` | Original file name. |
| `kind` | `demo` or `log` from fragdex-scan; also `csv`, `xlsx`, `manual`, `api` or `fragq` in a server export. An entry without a known kind is imported as `manual`. Only `demo` and `log` count as [verified Steam IDs](../administration/fragq.md#verified-steam-ids). |
| `verified` | `true` on an `api` group added with a [Game server](../administration/fragq.md#verified-steam-ids) key, in a server export. Those records then count as verified on the server that imports the file. Ignored on every other kind. |
| `sha256` | SHA-256 of the original file, used to skip files the server already has. Empty for groups of records in a server export. |
| `size` | Size of the original file in bytes. |
| `map`, `server` | From the demo, if known. |
| `s` | The records: `[steam_id, name, ip, seen_at]`. Name keeps its Quake colour codes; `ip` is empty for demos; `seen_at` is `YYYY-MM-DD HH:MM:SS` or empty (demo dates are UTC; log dates are as the log wrote them). |

A server export (version 2) also has a **player entry** for each exported player with notes, tags or a Discord ID:

```json
{"kind": "player", "steam_id": "76561197960287930", "notes": "Banned 2026-09 for aimbot", "tags": [["cheater", "#e8562a"]], "discord_id": "80351110224678912"}
```

`discord_id` is present only when the player has one. Servers from before Discord IDs existed ignore it, so newer exports still import there.

## Import rules

- Every value is validated again on import; malformed entries are skipped and counted. A `.fdx` file is never trusted.
- A file entry whose `sha256` the server has already imported (from any source) is skipped.
- Records that already exist (same Steam ID, name, IP, date and source) are skipped.
- Tags are created if missing and added to the player. Notes are appended only if they are not already there. A Discord ID is used only if the player has none.
- Local addresses (`127.0.0.1`, `::1`) are dropped from records, as for every other source.

## Size

gzip compresses the repetitive player data very well. Measured on real data:

| Data | Uncompressed source | `.fdx` |
|---|---|---|
| 1,916 demos | 8.3 GB of demos | ~500 KB |
| 1,714 players, 34,565 records | 4.7 MB as a full CSV | 328 KB |
