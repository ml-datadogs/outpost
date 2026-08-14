# Fallback tiers — when the fleet is not enough

Outpost's own fleet is tier 1. This doc wires in two backup tiers so a full-fleet
blackout (e.g. a protocol-level RKN wave taking out every node at once) still leaves
a way out.

| Tier | What | Trust | Automation |
|------|------|-------|------------|
| 1 | Outpost fleet (aeza / zomro / hostkey) | ours | full (provision + rotate) |
| 2 | Manual VPS on adopt-tier hosts (**iphoster**) | ours | adopt-only, no API |
| 3 | Public tested configs (**igareck/vpn-configs-for-russia**) | **untrusted exits** | none (client-side subscription) |

---

## Tier 2 — iphoster (manual / adopt-tier)

[iphoster.net](https://iphoster.net) — IPhoster OÜ, Estonian jurisdiction, hosting
since 2005. Registered in `state/registry.yaml` as a **manual provider**: it has
**no provisioning API** (panel/WHMCS only), so `outpost provision` refuses it and
rotation never targets it. It exists for hand-ordered boxes brought in via `adopt`.

Why it's on the list:

- **Payment**: RU-friendly — RU card gateway, QIWI/Payeer-style wallets, and crypto
  (BTC/ETH/LTC). Passes the policy gate.
- **Cheap KVM VPS** (from ~128 ₽/mo), NVMe, locations DE/PL/FR/GB/US/CA.
- **Field-proven**: amnezia/xray stacks are known to run stably on their VPS.

Caveats (from reviews): the panel's location picker is unreliable — orders sometimes
land in a different country. **Verify the exit IP geolocation before adopting**; a
mislocated box may fail the non-RU gate or perform badly.

### Adopting an iphoster box

Order in the panel, note IP + root password, then:

```bash
uv run outpost adopt --provider iphoster --ip <ip> --country DE --password <root-pw>
```

The node lands in the inventory tagged `adopted`, gets the full sing-box + Xray
stack, and is rendered into both subscriptions like any managed node. Rotation
monitors it but will never try to destroy or replace it via API.

### If the box already runs amnezia / xray

An existing self-managed install (e.g. AmneziaVPN's xray container) **conflicts with
the outpost bootstrap**: both want 443, and outpost also claims 8443 + 2053. Two
options:

1. **Fresh VPS for outpost** (recommended): keep the amnezia box untouched as an
   out-of-band spare — its configs live in the Amnezia client, independent of our
   subscription pipeline. If outpost's whole pipeline is down, it still works.
2. **Take the box over**: `adopt` it and let bootstrap replace the stack. The old
   amnezia client configs die; the box becomes a normal outpost node.

Do **not** try to merge the two stacks on one box — port juggling around Reality's
dest/SNI handling breaks in non-obvious ways and is impossible to monitor cleanly.

---

## Tier 3 — public configs (igareck/vpn-configs-for-russia)

[github.com/igareck/vpn-configs-for-russia](https://github.com/igareck/vpn-configs-for-russia)
— a continuously maintained pool of **free public configs, auto-tested from inside
RU every ~2 hours**; dead/slow entries are pruned. Protocols: VLESS (primary),
Shadowsocks, Hysteria2, Trojan, VMess, plus Tor bridges (vanilla / obfs4 / webtunnel).

**This is a last-resort tier.** The exits are operated by unknown third parties:
assume the operator can see SNI/metadata and MITM anything not end-to-end encrypted.
Never touch banking, mail, or anything credentialed through tier 3 — use it to reach
messengers/news and to bootstrap repairs of tiers 1–2.

### Our auto-synced mirror (preferred)

CI ([.github/workflows/fallback.yml](../.github/workflows/fallback.yml)) pulls the
upstream lists every 6 hours (upstream retests every ~2 h), validates and dedupes
them (`outpost fallback`, see `outpost/fallback.py`), and publishes them to the
same token-gated Worker as the main subscriptions:

```text
https://outpost-subs.<you>.workers.dev/<SUB_TOKEN>/fallback          # normal (blacklist) mode
https://outpost-subs.<you>.workers.dev/<SUB_TOKEN>/fallback-white    # whitelist regime
```

Use these instead of the raw GitHub URLs from inside RU: Cloudflare stays reachable
when raw.githubusercontent does not, the sync falls back to a jsDelivr mirror when
GitHub is unreachable from CI, and a failed/empty fetch aborts the publish so KV
always keeps the last good version. To refresh manually: `uv run outpost fallback`
then trigger the workflow, or run it from the Actions tab (workflow_dispatch).

### Upstream URLs (direct, if the Worker itself is down)

Standard ("blacklist") mode, normal internet with blocked resources:

```text
https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_VLESS_RUS_mobile.txt
https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_VLESS_RUS.txt
https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/BLACK_SS+All_RUS.txt
```

Whitelist mode (for heavily filtered mobile networks where only allowlisted
IPs/domains pass — slower, but survives "white internet" shutdowns):

```text
https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/WHITE-CIDR-RU-all.txt
https://raw.githubusercontent.com/igareck/vpn-configs-for-russia/main/WHITE-SNI-RU-all.txt
```

Add the mirror as a **second subscription** in Happ next to the outpost one; keep
auto-update at 1–2 h and sort by real delay, not ping. Surge cannot consume these
(share-link format), which is fine — tier 3 is a mobile/emergency path.

If GitHub raw itself is blocked, the repo maintains mirrors (GitLab, Codeberg,
Gitea, SourceHut, Bitbucket) and proxy paths (GitHack, Yandex-translator wrapping) —
see the repo README for the current list. Tor bridges in `TOR-BRIDGES/` are the
floor below that.
