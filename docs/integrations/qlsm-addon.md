# QLSM addon

[QLSM](https://github.com/dngrtech/qlsm) is a web dashboard for managing Quake Live servers. The Fragdex addon for QLSM connects it to Fragdex, with three features you choose per server:

- **Chat log collection.** Player connections from each server's minqlx chat log are sent to Fragdex, including the history still on the server.
- **A Fragdex column in Live Status.** Each connected player's tags and bans appear next to their name, with linked accounts, Discord and verified status in the tooltip.
- **A Players panel.** Everyone connected, with their Discord, bans and buttons to copy their IDs.

It needs QLSM 1.54 or later and Fragdex 1.4 or later. Nothing else on the game servers has to change: QLSM's default presets already run minqlx's `log` plugin, which writes the chat log.

## Set it up

1. In Fragdex, open **Settings → Fragdex API** and create a key for QLSM, with **Add records** and **Game server** ticked. Tick nothing else. The column doesn't need IP addresses or notes.
2. On the same panel, download **QLSM addon (fragdex-qlsm-addon-….zip)**. In QLSM, open **Settings → Addons**, press **Install** and choose the file. Restart QLSM when it asks.
3. Open **Settings → Fragdex**. Tick **Enabled**, enter Fragdex's address and the key, and press **Save**. The badge says whether it connected.
4. Open **Fragdex** in the menu of each **host** you want and tick **Enabled**.
5. Open **Fragdex Settings** in the menu of each **server** you want and tick **Enabled**.

!!! warning "Addons have full access to QLSM"
    QLSM addons aren't sandboxed: an addon can reach QLSM's database, SSH keys and game hosts. Install this one only from a source you trust.

## Choosing servers

A server is only touched when QLSM's addon switch is on for all three levels: the addon, its host and the server. Each server then has two options:

| Option | What it does |
|---|---|
| **Send chat log connections to Fragdex** | Collects this server's connections. |
| **Show the Fragdex column in Live Status** | Shows the column for this server. |

## Chat log collection

The addon checks every 15 minutes by default, reading only what is new since the last check. The first check sends the history still on the server, oldest first. A long history is sent in parts, one after another.

- **Only connection lines leave the server.** Player chat stays on the host.
- Records appear in Fragdex with the source **log**, under the server's name, and their Steam IDs count as [verified](../administration/fragq.md#verified-steam-ids).
- Times are converted from the host's clock to UTC.
- A server running the [fragdex minqlx plugin](minqlx-plugin.md) only sends its history, once. The plugin reports new connections itself, and sending both would record each one twice. The history can overlap the time the plugin has been running.
- If a check fails, QLSM's notification bell says why, and the next check sends the same lines again.

To send a server's history again, open its **Fragdex Settings** menu entry, clear **Sent up to** and press **Save**. Records Fragdex already has are skipped.

## The Live Status column

| Shown | Meaning |
|---|---|
| A tag, such as `cheater` | The player's tags in Fragdex, up to two. |
| `VAC` or `BAN` | VAC or game bans on the account. |
| `linked` | Nothing on this account, but a linked account is tagged or banned. |
| `clean` | In Fragdex, with nothing recorded against them. |
| `–` | Not in Fragdex yet. |

Hover over a cell for the details.

## The Players panel

Open **Fragdex Players** in a server's menu to see who is connected. The list updates every 30 seconds.

Each player has three small buttons. Hover over one to see what it does:

| Button | Copies |
|---|---|
| Person | The Discord name. |
| `#` | The Discord ID. |
| Steam logo | The Steam ID. |

A button is greyed out when there is nothing to copy. A Discord value that was typed in as a username is copied as a name, never as an ID. **verified** or **claimed** next to a Discord name says [how the link is known](../administration/fragq.md#claimed-and-verified).

| Steam | Meaning |
|---|---|
| `Verified` | Fragdex has this Steam ID from a demo, a log or a game server. |
| `Claimed` | Fragdex only has this Steam ID from an entry by hand, an import or FragQ. |
| `–` | Not in Fragdex yet. |

| VAC/BAN | Meaning |
|---|---|
| `VAC 1` | VAC bans on the account, and how many. Game bans are in the tooltip. |
| `BAN 2` | Game bans on the account, and how many. |
| `Clean` | Steam has been asked and reports no bans. |
| `–` | Steam hasn't been asked yet. This needs a [Steam Web API key](../administration/steam.md) in Fragdex. |

Everyone who can log in to QLSM can see this panel, including the Discord IDs.

## Updating or removing it

Each Fragdex update includes the matching addon. After updating Fragdex, download the addon again from **Settings → Fragdex API**, install it in the same place and restart QLSM. Removing the addon stops all its features and leaves everything Fragdex has already received. QLSM keeps the addon's settings, so reinstalling it carries on where it stopped.
