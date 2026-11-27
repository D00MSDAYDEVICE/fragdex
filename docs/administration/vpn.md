# VPN detection

Fragdex can label IP addresses that are VPNs, Tor exits, proxies or hosting networks. The label shows in a **VPN** column on the Players page for each player's latest IP, and after **Sources** for every address on a player's profile. The **Uses VPN** filter on the Players page finds players with any such address, latest or not. It is separate from [tags](../guide/tags-and-notes.md), and Fragdex sets it by itself.

| Label | Means |
|---|---|
| **VPN** | A VPN service's network. |
| **Tor** | A Tor exit relay. |
| **Proxy** | An open or compromised proxy, such as a hacked home router. Only [proxycheck.io](#proxycheckio-lookups) finds these. |
| **Host** (dimmer) | A hosting network, such as a cloud provider. Most VPNs run on one, but so do game servers and seedboxes, so it isn't proof of anything on its own. |

Hover a label to see where it came from, such as *VPN: Mullvad · Mullvad servers*.

This is optional and off until you turn it on.

## Turn it on

1. Open **Settings → VPN detection**.
2. Tick **Turn on VPN detection**, choose the base lists to use, and press **Save**.

Fragdex downloads the lists straight away and matches every recorded address against them. A progress bar shows each list being downloaded, from the data actually received, then the ranges saved and the addresses matched.

The base lists only download public lists to your server, and the matching happens there. Nothing about your players is sent anywhere unless you also add a [proxycheck.io key](#proxycheckio-lookups).

## Base lists

| List | Label | On by default |
|---|---|---|
| [X4BNet VPN list](https://github.com/X4BNet/lists_vpn) | VPN | Yes |
| [Tor exit list](https://check.torproject.org/) | Tor | Yes |
| [X4BNet datacenter list](https://github.com/X4BNet/lists_vpn) | Host | Yes |
| Mullvad servers | VPN | No. Mullvad's own server list, but not a documented API, so it may change. |
| NordVPN servers | VPN | No. Same as Mullvad. |
| [az0 VPN list](https://github.com/az0/vpn_ip) | VPN | No. ProtonVPN, Windscribe and a few others. Downloaded over plain HTTP. |
| Cloud providers | Host | No. AWS, Google Cloud, Oracle, DigitalOcean and Linode's own lists, mostly in the datacenter list already. |

The lists are downloaded again every 7 days. **Refresh base IPs** downloads them now, and turning a list on downloads just that one. Turning a list off removes its ranges.

When an address is on more than one list, the strongest label wins: Tor, then Proxy, then VPN, then Host.

### When a list can't be downloaded

Each list is downloaded on its own, so one that fails doesn't stop the others. A list that fails keeps the ranges it had, and its row shows why in amber, such as:

> Couldn't reach raw.githubusercontent.com: it timed out. Still using the list from 2026-11-20.

A list is also set aside, and the old one kept, when:

- it is empty, or under half the size it was last time, which usually means a broken or cut-off download
- it is over 50 MB
- it isn't in the form Fragdex expects

The next refresh tries again.

### Upload a list

For a server that can't reach the internet, or for a list of your own, press **Upload a list**. Choose its label, then a text file with one address or range per line, such as `198.51.100.0/24`. `#` starts a comment. An upload replaces the last one with the same label, and its row has a button to remove it.

## proxycheck.io lookups

The base lists can't settle every address. An ordinary home connection isn't on any list, and a hacked home router acting as a proxy looks like one. With a key from [proxycheck.io](https://proxycheck.io/), Fragdex looks up the addresses the lists don't settle.

This sends those addresses to proxycheck.io. Each request asks proxycheck.io not to log it in your dashboard.

1. Create a free account at proxycheck.io and copy your API key.
2. Paste it under **proxycheck.io** in the VPN detection panel and press **Save key**. Or set `PROXYCHECK_KEY` in `.env`.

A free key allows 1,000 lookups a day, and each address counts as one. Paid plans allow more. **Lookups a day** (900 by default) is how many Fragdex uses, which leaves some for players who join later the same day. proxycheck.io's day starts at 07:00 UTC.

### Making the lookups go further

- **Lookups wait for the base lists**, so none are spent on an address a list would have covered. If no list can be loaded at all, lookups pause, and **Look up anyway** starts them without the lists.
- **An address is only looked up once.** Its answer is checked again after 30 days.
- **A whole network from one answer.** When proxycheck.io says an address is a VPN on a hosting network, Fragdex keeps the network range it gives and labels every other address in it, without looking them up. Ranges wider than a /20 (IPv6: /40) aren't used this way, and neither are home or mobile networks.
- **One address per network first.** Fragdex looks up one address from each /24 (IPv6: /48), so the range from its answer can settle the others before they are sent.
- **The most useful first:** addresses seen in the last 90 days, then those shared by several accounts, then older history. Addresses labelled Host by a list come last.
- **Don't look up hosting addresses** skips those completely. They keep their Host label; a lookup would only say whether one is a VPN.

The panel shows how far it has got:

| Line | Shows |
|---|---|
| Addresses | Every address in the records. |
| Found by the lists | How many are VPN, Tor or proxy, and how many are hosting. |
| Looked up | Addresses proxycheck.io has answered for, and today's lookups out of the daily limit. |
| Left to look up | Addresses no list or answer settles yet, then the hosting addresses, which are looked up last (unless **Don't look up hosting addresses** is ticked), and about how many days all of them take at your daily limit. |

If proxycheck.io can't be reached or refuses a lookup, its reason shows in the panel and Fragdex tries again later: after an hour, or when proxycheck.io's day starts again if the daily limit ran out. Labels found so far are kept.

## Linked accounts

Players who share an IP address are [linked](../guide/linked-accounts.md). Tick **VPN, Tor and proxy addresses don't link accounts** to stop a shared VPN, Tor or proxy address from linking them. This changes the linked accounts of some players, so it is off by default. Hosting addresses still link.

## Turn it off

Untick **Turn on VPN detection** and press **Save**. The labels and the **Uses VPN** filter disappear, and the API returns `null` for them, but what was found is kept for when you turn it on again.
