# Backup and restore

There are two kinds of backup. Use the one that fits; doing both does no harm.

| | `.fdx` export | SQLite backup |
|---|---|---|
| Made from | the web page, any role | the server's command line |
| Contains | every player record, verified Steam IDs, notes, tags and Discord IDs | everything: players, records, notes, tags, **users, settings, import history** |
| Restore into | any Fragdex server, merging with what is there | a Fragdex server, **replacing** its database |
| Good for | regular backups, moving servers, sharing, merging | complete disaster recovery |

## .fdx export

On the **Players** page, with no search or filters, press **Export .fdx**. That downloads every record of every player, with notes, tags and Discord IDs, in one compressed file: about 330 KB for 1,700 players and 35,000 records.

**Restore** by uploading it on the **Import** page of the same or another Fragdex server. Records that already exist are skipped, tags are added and notes are appended only if different, so restoring into a server that still has some of the data is safe.

A `.fdx` doesn't hold Discord usernames or [FragQ's claimed and verified labels](fragq.md#claimed-and-verified). Discord IDs come back as imported, and the next FragQ sync restores the labels of links it still lists. After that, a change in FragQ to one of those IDs shows up as a conflict instead of updating it. To keep all of that, use a SQLite backup.

See [Export CSV and .fdx](../guide/export.md).

## SQLite backup

Run these in the Fragdex folder. The first command makes a consistent copy of the live database (safe while Fragdex is running); the second copies it out of the container.

```bash
docker compose exec app python -c "import sqlite3; s=sqlite3.connect('/data/fragdex.sqlite3'); s.backup(sqlite3.connect('/data/backup.sqlite3'))"
docker compose cp app:/data/backup.sqlite3 ./fragdex-backup.sqlite3
```

Keep `.env` (and `certs/` if you use `TLS_MODE=custom`) with it.

### Restore a SQLite backup

```bash
docker compose cp ./fragdex-backup.sqlite3 app:/data/restore.sqlite3
docker compose exec app python -c "import sqlite3; sqlite3.connect('/data/restore.sqlite3').backup(sqlite3.connect('/data/fragdex.sqlite3'))"
docker compose restart app
```

This replaces the whole database, users included, with the backup.

## Scheduled backups

On Linux, a cron job can take a SQLite backup every night and keep the last two weeks:

```bash
# crontab -e   (adjust the path to your Fragdex folder)
30 4 * * * cd /opt/fragdex && docker compose exec -T app python -c "import sqlite3; s=sqlite3.connect('/data/fragdex.sqlite3'); s.backup(sqlite3.connect('/data/backup.sqlite3'))" && docker compose cp app:/data/backup.sqlite3 backups/fragdex-$(date +\%F).sqlite3 && find backups -name 'fragdex-*.sqlite3' -mtime +14 -delete
```

Create the `backups` folder first. Copy the backups off the server too: a backup on the same disk does not survive the disk.

## Moving to a new server

1. On the old server: **Export .fdx** (for the player data) or take a SQLite backup (for everything).
2. On the new server: [install Fragdex](../getting-started/installation.md).
3. Import the `.fdx` on the Import page, or restore the SQLite backup as above.
4. Point your DNS at the new server. With `TLS_MODE=auto` the new server gets its own certificate.
