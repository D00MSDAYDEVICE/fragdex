# Security

Fragdex stores IP addresses linked to player identities. In many countries (including the EU under the GDPR) that is personal data, so treat the database with care.

## What Fragdex does

| Area | |
|---|---|
| Passwords | Hashed with scrypt. Minimum 10 characters. |
| Logins | Rate limited per IP address: after 8 failed attempts, logins from that address are refused for 15 minutes. |
| Sessions | Signed cookies, `HttpOnly`, `SameSite=Lax`, and `Secure` whenever HTTPS is on. They expire after `SESSION_DAYS`. Disabling a user ends their sessions. |
| Forms | Every form carries a CSRF token. |
| Headers | Strict Content Security Policy, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. |
| Network | The app container is not published on the host; it is reachable only through Caddy. |
| Imports | Every file, including `.fdx`, is validated on import and never trusted. Players cannot inject records by typing Steam IDs or IPs into chat. |
| Steam key | Shown only by its last characters, never included in error messages. |
| API keys | Shown once when created; only a SHA-256 hash is stored. Per key: access to IP addresses, notes and adding records, allowed IPs, rate limit and expiry. Admins only. See [API](../integrations/api.md). |
| Watched folder | Mounted read-only. |
| Third parties | No analytics, no CDNs: fonts and scripts are served by Fragdex itself. Steam avatars load from Steam's image servers. |

## What you should do

- **Change the first admin password** and remove `ADMIN_PASSWORD` from `.env`.
- **Keep accounts few** and use the lowest role that works. Disable accounts of people who leave.
- **Use HTTPS.** Leave `TLS_MODE=auto` unless you have a reason not to; with `off`, put Fragdex behind a proxy that does TLS.
- **Protect `.env`.** It may contain the Steam key and the first admin password. The Linux installer sets it to mode `600`.
- **Protect exports and backups.** A `.fdx` export or SQLite backup contains every IP address in the database. Store and share them accordingly.
- **Use a dedicated Discord bot** for [username lookups](discord.md), not one with permissions in your servers. Its token is stored in the database like the Steam key, or set `DISCORD_BOT_TOKEN` in `.env` instead.
- **Limit FragQ's connector key.** A Fragdex connector's key can read every Steam and Discord link of that Discord server's members. Create it with **Create Fragdex connector** so it can do nothing else, and enter the address Fragdex connects from under **Allowed IPs**. It is stored in the database like the Steam key, or set `FRAGQ_KEY` in `.env` instead.
- **Trust a FragQ connection only if you trust its admin.** A trusted connection can mark any Steam account as verified, and a modified FragQ can send anything. New connections start out untrusted, so everything they send counts as claimed.
- **Tick Game server only on a key for a game server or the [QLSM addon](../integrations/qlsm-addon.md).** Records from that key count as [verified Steam IDs](fragq.md#verified-steam-ids), and a script with it could add any Steam ID. If several admins send logs, give each their own key.
- **Give each program its own API key** with only the access it needs, restricted to its address where possible. Rotate a key that may have leaked.
- **Use Hide IPs** when screen-sharing or streaming.
- **Keep Docker and Fragdex updated.** See [Updating](updating.md).

## Reporting a vulnerability

Please report security problems privately via the repository's **Security → Report a vulnerability** page on GitHub rather than in a public issue.
