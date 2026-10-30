# Linked accounts

Fragdex links Steam IDs that have been seen on the **same IP address**. It is the quickest way to spot a player's alternate accounts, ban evasion, or simply people who share a house.

## How linking works

1. Every record with an IP address is grouped by IP.
2. An IP seen with **two or more** Steam IDs links all of them to each other.
3. An IP seen with **more** Steam IDs than `ALT_IP_MAX_ACCOUNTS` (10 by default) is ignored. Such addresses are almost always VPN exit nodes, LAN centres, universities or mobile carriers' shared NAT, and linking everyone on them would be noise.

Linked accounts are recalculated after every import, manual record and deletion.

!!! note "Only sources with IPs count"
    Demos do not contain IP addresses, so players known only from demos are never linked. minqlx chat logs, server logs and spreadsheets with an IP column provide the addresses.

## Where you see them

- **Players page**: tick **Has linked accounts** to list only players who have at least one.
- **Player profile → Linked accounts**: the other Steam IDs, how many IPs they share with this player, and when they were last seen.
- **Player profile → IP addresses → Other accounts**: per IP, how many other accounts used it. Click the number to search for everyone on that IP. Greyed-out counts are over the limit and ignored for linking.

## Reading the results

A link means *these accounts used the same public IP address at some point*. It is strong evidence when:

- the IP is shared by only two or three accounts,
- they share several IPs over time, or
- the dates line up (one account stops, the other starts).

It is weak evidence when the IP is a known VPN or hosting range, or the accounts were seen months apart on a dynamic residential IP that has since been reassigned. Check the dates in **All records** before acting on a link.

## Changing the limit

Set `ALT_IP_MAX_ACCOUNTS` in `.env` and run `docker compose up -d`. `0` means no limit. Then press **Settings → Recalculate summaries & linked accounts** to apply it to the existing data. See [Settings and .env reference](../administration/settings.md).
