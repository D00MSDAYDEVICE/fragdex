# Troubleshooting

Start with the logs. In the Fragdex folder:

```bash
docker compose ps              # are both containers running?
docker compose logs -f app     # the app: imports, Steam refresh, errors
docker compose logs -f caddy   # HTTPS certificates
```

## Build fails: `Temporary failure in name resolution` or `No matching distribution found`

`pip` inside the image build cannot resolve `pypi.org`: **containers on this host have no working DNS**. The host itself usually does, and image pulls still work, because the Docker daemon uses the host's resolver. The installers test for this before building and offer fixes.

Confirm it:

```bash
getent hosts pypi.org      # the host resolves
docker run --rm python:3.12-slim python -c "import socket; print(socket.gethostbyname('pypi.org'))"   # a container doesn't
```

Fixes on Linux, most likely first:

1. **Give Docker real DNS servers.** Typical when `/etc/resolv.conf` points at a local stub such as systemd-resolved's `127.0.0.53`. Use your network's upstream DNS (`resolvectl status` lists it) or public resolvers in `/etc/docker/daemon.json`:

    ```json
    { "dns": ["1.1.1.1", "8.8.8.8"] }
    ```

    then `sudo systemctl restart docker`.

2. **Restart Docker after firewall changes.** Reloading ufw, firewalld or iptables after Docker started can drop Docker's NAT rules: `sudo systemctl restart docker`.
3. **Outbound DNS blocked.** Some hosts and firewalls block port 53 to public resolvers. Use the provider's DNS servers in `daemon.json` instead.
4. **One-off workaround:** build with the host's network, then start normally:

    ```bash
    docker build --network host -t fragdex:latest app && docker compose up -d
    ```

    Let's Encrypt and Steam lookups still need container DNS, so fix 1–3 for those.

On **Windows / Docker Desktop**: disconnect VPN or proxy software (or allow Docker through it), add `"dns": ["1.1.1.1", "8.8.8.8"]` under **Settings → Docker Engine**, or run `wsl --shutdown` and start Docker Desktop again.

## The browser warns about the certificate

Fragdex is using a self-signed certificate. That is expected with `TLS_MODE=internal`, on a LAN, or when Let's Encrypt failed. Check `docker compose logs caddy` for the reason; the usual ones:

- **Ports:** Let's Encrypt must reach public port **80 or 443**. Forward them if you use other ports. See [HTTPS and ports](../administration/https.md).
- **DNS:** the domain must point at this server's public IP.
- **Firewall:** the cloud provider's firewall or security group must allow 80 and 443.

Fragdex keeps retrying Let's Encrypt in the background, so once the cause is fixed the real certificate arrives by itself. To make the warning go away on a LAN, [trust the self-signed root](../administration/https.md#trust-the-self-signed-certificate).

## Port already in use

`Bind for 0.0.0.0:80 failed: port is already allocated` means another program (often a web server) uses the port. Choose other ports with `HTTP_PORT` and `HTTPS_PORT` in `.env` and run `docker compose up -d`.

## I lost the admin password

See [Users and roles → Forgotten admin password](../administration/users.md#forgotten-admin-password).

## Files in the watched folder are not imported

- **Import → Watched folder** should say **Active**. *Not mounted* means `HOST_WATCH_DIR` does not exist or is not shared with Docker.
- On Windows with the Hyper-V backend of Docker Desktop, share the folder under **Settings → Resources → File sharing**.
- New files are picked up every `WATCH_INTERVAL` seconds (60 by default).
- Files that were imported before are skipped on purpose, even under a new name.
- Check the **History** table for errors on that file.

## A spreadsheet import skipped rows

The **Details** column in the import history says why. Most often the Steam IDs were saved by Excel as numbers (`7.65611980147868E+16`), which loses their last digits. See [Supported sources → Spreadsheets](../importing/sources.md#spreadsheets).

## Steam data does not appear

- Check **Settings → Steam Web API** for an error message. `HTTP 403` usually means an invalid key.
- The container needs DNS and internet access; see the first section.
- Avatars and names are filled in batch by batch; the progress bar on the Settings page shows how far the refresh has got.

## Windows says "Windows protected your PC" for fragdex-scan.exe

The scanner is not code-signed. Click **More info → Run anyway**. See [fragdex-scan](../importing/fragdex-scan.md).

## Still stuck?

Open an issue at <https://github.com/D00MSDAYDEVICE/fragdex/issues> with what you did, what happened and the relevant lines from `docker compose logs`. Remove IP addresses, Steam keys and passwords from logs before posting them.
