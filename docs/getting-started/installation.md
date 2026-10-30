# Installation

Fragdex runs in Docker. An interactive installer sets everything up: it asks a few questions, writes the `.env` settings file, starts the containers and prints the admin login.

## Requirements

| | Linux | Windows |
|---|---|---|
| Docker | Docker Engine with the **Compose plugin** (`docker compose`) | **Docker Desktop**, running (WSL 2 backend recommended) |
| Ports | 80 and 443 free, or any other two ports you choose | same |
| Internet | Containers must reach the internet to build the image, get Let's Encrypt certificates and query Steam | same |
| Resources | Very modest: a small VPS is plenty. A database of ~1,700 players and ~35,000 records is a few MB. | same |

!!! tip "Domain or IP?"
    A domain name is nicest, but not required. With `TLS_MODE=auto` Fragdex gets a Let's Encrypt certificate for a **bare public IP** too, and falls back to a self-signed certificate on a LAN. See [HTTPS and ports](../administration/https.md).

## Install with the installer

=== "Linux"

    ```bash
    git clone https://github.com/D00MSDAYDEVICE/fragdex.git && cd fragdex
    ./deployfragdex.sh
    ```

=== "Windows"

    Start Docker Desktop first, then in PowerShell:

    ```powershell
    git clone https://github.com/D00MSDAYDEVICE/fragdex.git; cd fragdex
    powershell -ExecutionPolicy Bypass -File .\deployfragdex.ps1
    ```

No git? Download the repository as a `.zip` from GitHub (**Code → Download ZIP**), extract it and run the installer from that folder.

The installer:

1. **Checks container networking.** It starts a throwaway container and tries to reach `pypi.org`. If that fails it explains why and offers fixes (see [Troubleshooting](../help/troubleshooting.md)).
2. **Asks for your settings:**

    | Question | Default | Notes |
    |---|---|---|
    | Domain or IP | your LAN IP | What people type in the browser. Several, comma separated, are allowed. |
    | HTTP port / HTTPS port | `80` / `443` | Any free ports work. |
    | TLS mode | `auto` | `auto`, `internal`, `custom` or `off`. See [HTTPS and ports](../administration/https.md). |
    | E-mail for Let's Encrypt | empty | Optional, only asked for `auto`. |
    | Admin username | `admin` | The password is generated for you. |
    | Steam Web API key | empty | Optional, can be added later under **Settings**. |
    | Folder to watch | `/home/demos` (Linux), `C:\demos` (Windows) | Files dropped here are imported automatically. Created if missing. |

3. **Writes `.env`** from `.env.example` (on Linux with permissions `600`).
4. **Builds and starts** the containers with `docker compose up -d --build`.
5. **Prints the address and the admin login.** Write the password down.

```text
  Fragdex is starting at:  https://fragdex.example.com

  Username: admin
  Password: ••••••••••••••••••••
```

If `.env` already exists, the installer asks before overwriting it. Answer **N** to keep your settings and just rebuild and restart, which is also how you [update](../administration/updating.md).

## Install by hand

```bash
cp .env.example .env      # then edit SITE_HOST, ports, TLS_MODE, ...
docker compose up -d --build
docker compose logs app   # shows the generated admin password if ADMIN_PASSWORD was empty
```

Every setting is documented in `.env.example` and in the [Settings and .env reference](../administration/settings.md).

## Check that it is running

```bash
docker compose ps          # both "app" and "caddy" should be running
docker compose logs -f     # follow the logs; Ctrl+C to stop following
```

Open `https://<your domain or IP>` (add `:<port>` if you chose a port other than 443). With a self-signed certificate your browser warns once; that is expected on a LAN or without a public address.

## Uninstall

```bash
docker compose down        # stops and removes the containers; keeps your data
docker compose down -v     # also DELETES the database and certificates
```

!!! danger
    `docker compose down -v` and `docker volume prune` delete the database volume. [Make a backup](../administration/backup-restore.md) first.

Next: [First login and import](first-steps.md).
