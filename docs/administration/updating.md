# Updating

Your data is not in the project folder: the database and certificates live in Docker volumes (`fragdex_fragdex_data`, `fragdex_caddy_data`, `fragdex_caddy_config`), and your settings in `.env`. Updating replaces the program and keeps all of that.

## Update

=== "With git"

    ```bash
    cd fragdex
    git pull
    docker compose pull caddy && docker compose up -d --build
    ```

=== "From a .zip"

    Extract the new release over the old folder, or into a new folder and copy `.env` and `certs/` across. Then, in that folder:

    ```bash
    docker compose pull caddy && docker compose up -d --build
    ```

=== "With the installer"

    Re-run `./deployfragdex.sh` or `deployfragdex.ps1` and answer **N** when asked whether to overwrite `.env`. It rebuilds and restarts with your existing settings.

The app is unavailable for the few seconds it takes to restart.

!!! danger "Never delete the volumes"
    `docker compose down -v` and `docker volume prune` (while Fragdex is stopped) **delete the database**. A normal `docker compose down` is safe.

## Upgrading from an early version

Early versions did not fix the Docker Compose project name, so the volumes were named after the folder, such as `fragdex-main_fragdex_data`. Current versions always use `fragdex_…`.

The installers handle this: they find the old volumes, copy the data into the new names once and print the command to remove the originals when you are happy. If several old installs are found, they ask which one to keep. If you update by hand instead, run the installer once, or copy the volumes yourself:

```bash
docker compose down
docker volume create --label com.docker.compose.project=fragdex --label com.docker.compose.volume=fragdex_data fragdex_fragdex_data
docker run --rm -v fragdex-main_fragdex_data:/from:ro -v fragdex_fragdex_data:/to python:3.12-slim cp -a /from/. /to/
docker compose up -d
```

(and the same for `caddy_data` and `caddy_config`, or let Caddy request new certificates).

## Before a big update

Take a backup first. See [Backup and restore](backup-restore.md).
