# Add records manually

Not everything arrives in a file. A player reported in Discord, an IP from a ban appeal, a name from a screenshot: editors can add those by hand.

## Add a player or record

Open **Add** in the top navigation.

| Field | |
|---|---|
| **Steam ID** | Required. Any of: `76561197960287930`, `STEAM_0:0:11101`, `[U:1:22202]`, or a `https://steamcommunity.com/profiles/…` link. |
| **Name** | Quake colour codes are allowed, for example `^1Rail^7gun`. |
| **IP address** | Optional. IPv4 or IPv6. |
| **Seen at** | Optional date and time. Leave it empty for an undated record. |
| **Notes** | Optional. Appended to the player's notes. |

Enter at least a name or an IP. If the Steam ID is new, the player is created; otherwise the name and IP are added to their history. Adding the exact same record twice does nothing.

## Add to an existing player

On a [player profile](player-profile.md), the **Add record** panel does the same without retyping the Steam ID.

## How manual records appear

Manual records have the source **manual** and show *added by &lt;username&gt;* in the **All records** table, so you can always tell them apart from imported data. Filter the Players page by **Any source → Manual** to list every player with a manual record. They count for names, IPs and [linked accounts](linked-accounts.md) like any other record.

A wrong record can be removed with the bin icon in **All records**.
