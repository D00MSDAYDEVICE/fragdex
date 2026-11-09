# Discord usernames

A Discord ID is only a number. With a Discord bot token, Fragdex looks up the username for every Discord ID in the database and shows it on the Players page and the player's profile. This is optional: without a token, Fragdex shows the ID.

## Set it up

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create an application. Any name will do.
2. Open its **Bot** page and press **Reset Token** to get the token. Copy it.
3. In Fragdex, open **Settings → Discord usernames**, paste the token and press **Save token**.

The bot doesn't need to join a server, needs no permissions or privileged intents, and never comes online. Fragdex only uses the token to ask Discord for a user's public name.

Instead of the Settings page, you can set `DISCORD_BOT_TOKEN` in `.env`.

!!! warning "Treat the token like a password"
    Anyone with the token can act as that bot. If it leaks, reset it in the Developer Portal and save the new one. Use a bot made for this purpose rather than one that sits in your servers with permissions.

## What happens

- Fragdex looks up one ID about every second in the background, so 240 IDs take about four minutes. A new ID, from an import or typed in by hand, is looked up within moments.
- Each username is checked again every 7 days (`DISCORD_REFRESH_DAYS`), because people can change them. **Look up all usernames again** does it now.
- Fragdex follows Discord's rate limits and waits when asked to.
- If Discord no longer knows a user, the last username seen is kept.
- Only numeric IDs can be looked up. If a player's Discord entry is a username, it is shown as it is.

## Where usernames appear

- **Players page:** the **Discord** column shows the username. Hover over it to see the ID, and click to copy the ID. A player with no username yet shows the ID.
- **Player profile:** the username and the ID, with a copy button.
- **Search** finds players by username.
- **CSV export** and the [API](../integrations/api.md) include `discord_name`.

## Importing Steam-to-Discord links

A spreadsheet or CSV with a Steam ID column and a Discord ID column fills in the Discord IDs of players already in the database. See [Link Discord accounts from a file](../importing/sources.md#link-discord-accounts-from-a-file). They can also be read automatically from [FragQ](fragq.md).
