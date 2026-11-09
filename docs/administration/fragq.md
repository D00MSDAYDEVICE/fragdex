# FragQ links

[FragQ](https://github.com/D00MSDAYDEVICE/fragq) is a self-hosted Discord pickup bot whose players link their Discord account to their Steam account. Fragdex can read those links, so players get their Discord ID without anyone typing it in. Several FragQ servers, each run by its own admin, can feed one Fragdex.

This is optional, and needs FragQ 1.1.0 or later.

## Set it up

1. In FragQ's web panel, open **Connectors** for your Discord server.
2. In the **Fragdex** panel, press **Create Fragdex connector**. It only needs a name, and it needs no queue. You can leave the rate limit at 60 requests per minute, which is plenty. Under **Allowed IPs**, you can enter the address Fragdex connects from.
3. Copy the key. FragQ shows it only once.
4. In Fragdex, open **Settings → FragQ links** and press **Add connection**. Give it a name, and enter the **FragQ address** shown in that panel and the key.
5. Tick **Trusted** if you trust the admin of that FragQ server. See [Several FragQ servers](#several-fragq-servers).
6. Press **Add connection**, then **Test** on the new entry.

!!! warning "Use the FragQ address"
    The Connectors page shows two addresses. Enter the **FragQ address** from the **Fragdex** panel, such as `https://fragq.example.com`, and not the **Connector API address** at the top of the page, which is for game servers.

Fragdex then checks every connection every 15 minutes (`FRAGQ_SYNC_MINUTES`), and **Sync now** does it straight away. A server that can't be reached shows its error on its own entry and doesn't hold up the others, and what it said before still counts. Each entry shows the result of its last sync and how many Discord IDs it has filled in since its key was added. A new key starts that count again.

Instead of the Settings page, you can set `FRAGQ_URL` and `FRAGQ_KEY` in `.env`. That adds one trusted connection, whose name and trust you can change on the Settings page. To remove it, remove those settings.

### Things to know

- A connector belongs to one Discord server, and FragQ only returns links for members of that server. To read several Discord servers on the same FragQ, add a connection for each.
- A change to a different Steam account appears in Fragdex once a moderator has approved it in FragQ.
- **Replace key** in FragQ stops Fragdex until you save the new key here. **Delete** or **Disable** stops it too. The links Fragdex already has are kept.

See [Connect Fragdex](https://d00msdaydevice.github.io/fragq/connectors/#connect-fragdex) in the FragQ documentation for the FragQ side.

## Claimed and verified

Each link from FragQ is labelled. FragQ's own **Linked players** table gives the same links a more specific name:

| Label in Fragdex | In FragQ | Meaning |
|---|---|---|
| **verified** | **Steam proven** | The player signed in through Steam on FragQ's `/verify` page. |
| **verified** | **In-game** | The player typed `!link` on a game server and confirmed it in a Discord direct message. |
| **claimed** | **Claimed** | The player typed the Steam ID with `!steam`, `/steam` or the pop-up form. Nothing proves the account is theirs. |

Links made before FragQ 1.1.0 are **claimed** until the player proves them. To do that, a player opens `/verify` on your FragQ address, such as `https://fragq.example.com/verify`, and signs in with Discord and then Steam. FragQ needs `PUBLIC_URL` set for this page to work. See [Linking Steam](https://d00msdaydevice.github.io/fragq/playing/steam/) and [The /verify page](https://d00msdaydevice.github.io/fragq/administration/steam/#the-verify-page) in the FragQ documentation.

When a player proves a Steam account that someone else had only claimed, FragQ gives the account to the player who proved it. The next sync updates Fragdex in the same way, unless the value there was entered by hand or imported.

The label is shown next to the Discord name on the Players page and the player's profile, and is in the CSV export and the [API](../integrations/api.md) as `discord_status`.

## What a sync does

| Situation | Result |
|---|---|
| The player has no Discord ID | Filled in from FragQ, with its label. |
| The current value came from FragQ and the links now point elsewhere | Updated, with its label. |
| FragQ has the same Discord account as Fragdex | The label and username are added. The value stays as it is. |
| The value was entered by hand or imported, and differs | Kept, and listed under **Conflicts** on the Settings page. |
| An admin removed the Discord ID by hand | Not filled in again. Enter one by hand to set it. |
| The Steam ID isn't in Fragdex yet | The player is added with their Steam name from FragQ. The source of that record is **FragQ**. |
| FragQ doesn't have a Steam name for it yet | Left out until a later sync. |
| A link is no longer in FragQ's list | Kept. The member may only have left the Discord server. |

A Discord username comes with each link, so usernames show up even without a [Discord bot token](discord.md).

## Several FragQ servers

Fragdex keeps each server's answer separately and works out a player's Discord ID from all of them, so the result doesn't depend on which server synced last.

A connection starts out **not trusted**. Everything it sends counts as **claimed**, even a link it says was proved, because each server is run by a different admin and a modified FragQ can send anything. Tick **Trusted** only for servers whose admin you trust. Changing it takes effect straight away for every player that connection has a link for.

| What the servers say | Result |
|---|---|
| A value was entered by hand or imported, or an admin removed it | That value stays. Any server that disagrees is listed under **Conflicts**. |
| A trusted server has a verified link, and others only claim | The verified link is used. |
| Two trusted servers have different verified links | Fragdex keeps what it has, which may be nothing, and lists a conflict. |
| Servers claim different Discord accounts | Fragdex keeps what it has, which may be nothing, and lists a conflict. |
| All the servers that have a link agree | That link is used, verified if any trusted server proved it. |

To settle a conflict, open the player and enter the right Discord ID by hand. A value entered by hand always wins.

**Removing a connection** forgets what that server said. Players keep the Discord IDs they have, as when a link leaves a server's list, and the remaining servers decide any player they have a link for. A **verified** label that no trusted server backs up any more becomes **claimed**.

## Verified Steam IDs

Separately from the label on the Discord link, a player gets a **verified** mark on their Steam ID when it comes from the game itself, because it can't be typed in by someone else:

- a demo or a server or minqlx log file, including demo and log records in a `.fdx` file;
- the [minqlx plugin](../integrations/minqlx-plugin.md), when its API key has **Game server** ticked. A `.fdx` export keeps this status.

Spreadsheets, manual records, other API keys and FragQ don't count. The **Verified Steam ID** filter on the Players page shows only these players.

A `.fdx` file is plain data that says where its records came from, so import only files you trust. One that claims demo records or game server records counts as verified on your server.

!!! note "The two are different"
    A verified Steam ID shows that the account is a real player. It doesn't show who owns it. A player seen in demos whose link is **claimed** could have been claimed by someone else, so only the **verified** label on the Discord link connects the two accounts.
