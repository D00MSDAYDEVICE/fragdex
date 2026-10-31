# What is Fragdex?

Fragdex keeps a history of every Quake Live player your servers have seen: each name they used, each Steam ID, each IP address and when it was seen. It builds that history from files you already have (demos, server logs, minqlx chat logs and spreadsheets) and makes it searchable from a browser, behind a login.

It is meant for server administrators and moderators who need to answer questions like:

- *Who is this player, and what names have they used before?*
- *Which other Steam accounts have connected from the same IP?*
- *Has this account been VAC or game banned?*
- *Who was on the server on that date, and from which address?*

## Features

| Area | What you get |
|---|---|
| Search | Name, alias, current Steam name, Steam ID, Discord ID, IP address, CIDR range such as `203.0.113.0/24`, and notes. |
| Filters | Tag, source, Steam ban status, *has IP*, *has linked accounts*, last-seen date range. |
| Player list | Latest name in its Quake colours, Steam ID with a link to the Steam profile, Discord ID, latest IP, past names and IPs behind small icons, tags, last seen. Sort by name, Steam ID, last seen or most aliases. |
| Player profile | Every name and IP with first/last seen dates and sources, linked accounts, all raw records, Steam details, Discord ID, tags and notes. |
| Linked accounts | Other Steam IDs seen on the same IP. IPs shared by many accounts (VPNs, LAN centres) are ignored. |
| Steam Web API | Optional: avatars, current Steam names, VAC and game bans, refreshed in the background. |
| Importing | Upload files or `.zip` archives, or drop them into a watched folder. Duplicates are never imported twice; local addresses such as 127.0.0.1 are never recorded. A sample CSV shows every spreadsheet column. Database cleanup tools repair older data. |
| fragdex-scan | Desktop tool that turns gigabytes of demos into a few hundred kilobytes to upload. |
| Export | CSV for spreadsheets, `.fdx` for full-fidelity backups and transfers between servers. |
| Privacy | *Hide IPs* toggle for screen-sharing and streaming. Everything stays on your own server. |
| Accounts | Viewer, editor and admin roles. |
| Deployment | Docker Compose with Caddy: automatic HTTPS for a domain or a bare public IP, on any ports. |

## How it fits together

```text
 demos (.dm_91)        server / chat logs        spreadsheets (.xlsx/.csv)
       │                       │                           │
       ├── fragdex-scan ──► .fdx file                      │
       │      (on your PC)     │                           │
       ▼                       ▼                           ▼
   ┌────────────── Import page  or  watched folder ──────────────┐
   │                     Fragdex (FastAPI + SQLite)              │
   │   players · names · IPs · linked accounts · tags · notes    │
   └──────────────────────────────┬──────────────────────────────┘
                                  │  Caddy (HTTPS)
                                  ▼
                       your browser, after login
```

Fragdex runs as two containers: the app itself (Python, FastAPI, SQLite) and Caddy, which handles HTTPS certificates. The database lives in a Docker volume, so updates and rebuilds keep your data.

## What the sources contain

Each source carries different information, so combining them gives the fullest picture:

- **Demos** have names and Steam IDs, but **no IP addresses**.
- **minqlx chat logs** have name, Steam ID, IP and time for every connection.
- **Server logs** have names, Steam IDs and sometimes IPs.
- **Spreadsheets** are whatever you have been keeping by hand.

See [Supported sources](../importing/sources.md) for the details.

## Where to go next

- [Installation](installation.md): get it running.
- [First login and import](first-steps.md): set up users and load data.
- [Search and filter players](../guide/search.md): day-to-day use.
