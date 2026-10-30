# Settings and .env reference

## The Settings page

**Settings** (admins only) has two panels.

**Steam Web API**: the key and live refresh progress. See [Steam Web API](steam.md).

**System**: the number of players and records, database size, TLS mode, watched folder, linked-account IP limit and whether uploads are kept. These values come from `.env` and are shown for reference.

**Recalculate summaries & linked accounts** rebuilds every player's summary (latest name and IP, counts, first and last seen) and all linked accounts from the raw records. Fragdex keeps these up to date by itself; use the button after changing `ALT_IP_MAX_ACCOUNTS`.

## The .env file

Everything else is configured in `.env` in the Fragdex folder. The installer writes it from `.env.example`, where every setting is documented too. After changing it, apply the change with:

```bash
docker compose up -d
```

### Site and HTTPS

| Setting | Default | |
|---|---|---|
| `SITE_HOST` | none | Domain name or IP address people use to reach the site. Several, comma separated: `203.0.113.10,192.168.1.50`. |
| `HTTP_PORT` | `80` | Port published on the host for HTTP (redirects to HTTPS). |
| `HTTPS_PORT` | `443` | Port published on the host for HTTPS. |
| `TLS_MODE` | `auto` | `auto`, `internal`, `custom` or `off`. See [HTTPS and ports](https.md). |
| `ACME_EMAIL` | none | Optional e-mail for Let's Encrypt expiry and problem notices. |

### Accounts

| Setting | Default | |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | First admin account, created on the very first start only. |
| `ADMIN_PASSWORD` | none | Its password. Empty: a random one is printed in `docker compose logs app`. Remove it after the first login. |
| `SESSION_DAYS` | `7` | How long a login lasts. |

### Steam

| Setting | Default | |
|---|---|---|
| `STEAM_API_KEY` | none | Optional Steam Web API key. Overrides a key saved on the Settings page. |
| `STEAM_REFRESH_HOURS` | `24` | How often each player's Steam data is refreshed. |

### Importing

| Setting | Default | |
|---|---|---|
| `HOST_WATCH_DIR` | `/home/demos` (Linux), `C:\demos` (Windows) | Host folder scanned for new files, mounted read-only. |
| `WATCH_INTERVAL` | `60` | Seconds between scans of the watched folder. |
| `MAX_UPLOAD_MB` | `1024` | Maximum size of one upload, in MB. |
| `KEEP_UPLOADS` | `false` | Keep uploaded files in the data volume after importing them. |

### Linked accounts

| Setting | Default | |
|---|---|---|
| `ALT_IP_MAX_ACCOUNTS` | `10` | IPs used by more Steam IDs than this (VPNs, LAN centres, carrier NAT) are not used to link accounts. `0` means no limit. See [Linked accounts](../guide/linked-accounts.md). |

## Where things are stored

| What | Where |
|---|---|
| Database (`fragdex.sqlite3`), kept uploads | Docker volume `fragdex_fragdex_data`, mounted at `/data` in the app container |
| HTTPS certificates and keys | Docker volumes `fragdex_caddy_data` and `fragdex_caddy_config` |
| Settings | `.env` in the Fragdex folder |
| Your own certificate (`TLS_MODE=custom`) | `certs/cert.pem` and `certs/key.pem` in the Fragdex folder |

The volume names are fixed by `name: fragdex` in `docker-compose.yml`, whatever the folder is called. See [Updating](updating.md) and [Backup and restore](backup-restore.md).
