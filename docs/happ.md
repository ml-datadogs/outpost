# Happ client setup

## Current status (2026-11-20)

The managed inventory renders zero Happ links. GitHub Actions still renders
the subscription and skips publishing to Cloudflare KV. The separate IPRoyal
Netherlands SOCKS5 endpoint passed a Surge policy test and has a direct Happ
setup documented in the [playbook](claude-account-playbook.md).

The managed Happ feed keeps its base64 share-link format. The Worker sets the
subscription title to **Outpost**. Eligible nodes have a country flag in the
link label, which Happ displays as the server icon. Both features use standard
Happ settings. [Flags](https://www.happ.su/main/dev-docs/emoji) ·
[Subscription settings](https://www.happ.su/main/dev-docs/app-management)

## Private Kubernetes feed

The GitOps service uses `https://outpost.dirtyinfra.xyz/<token>/happ`. Its
HTTPS route points to the Cilium Gateway in `k8s-prod`. The Node service runs
the same token check and Happ response as the Worker. Cloudflare DNS resolves
the hostname to the cluster's existing load balancer.

The deployment uses a pinned Node base image and mounts the server files from
the [infrastructure GitOps repository](https://github.com/infra-incubator/k8s/tree/main/cluster/infrastructure/outpost).
When changing the server, update the GitOps copy of `worker/server.mjs`,
`worker/src/index.js`, and `worker/package.json` together. The ConfigMap hash
rolls the pod when the mounted code changes.

Set `HAPP_PROVIDER_ID` in the Kubernetes Deployment to send Happ's optional
`providerid` response header. Add `outpost.dirtyinfra.xyz` to the corresponding
Happ provider account. Happ checks Premium status on its own schedule.
[Provider ID setup](https://docs.happ-proxy.com/getting-started/how-to-start)

The Kubernetes configuration passes its `color-profile.json` as
`HAPP_COLOR_PROFILE`. The provider theme is documented for iOS clients. To reset
the theme on those clients, serve `resetcolors` as the profile value.
[Theme settings](https://www.happ.su/main/dev-docs/app-management#themes-ios-only)

The deployment reads `HAPP_ROUTING_PROFILE` from the GitOps ConfigMap's
`routing-profile.json`. The server sends the UTF-8 JSON as a base64
`happ://routing/onadd/` link in the `routing` response header. Keep the
profile name `Outpost Split` across updates. Refresh Outpost in Happ and
reconnect to apply its rules. For rollback, set the environment variable
to the literal `happ://routing/off`, sync the deployment, then refresh
and reconnect on the phone. [Happ routing](https://www.happ.su/main/dev-docs/routing)

To refresh the feed, set `OUTPOST_HAPP_SOCKS_URL` to the current authenticated
`socks5://user:password@host:port` URL from the working Surge entry. Percent-encode
special characters in the username and password. With the inventory decryption
key available, run `uv run outpost render --happ --out <private-dir>` under a
restrictive umask. Update the `happ` value in the GitOps SopsSecret with the
new file contents and bump the deployment's secret revision. The token only
needs to change when rotating access. Treat both the full import URL and the
rendered file as credentials.

## Historical connectivity results

These results come from an earlier Happ 4.11+ test on a Russian ISP using a
Netherlands VPS.

| Layer | What worked | What often failed |
|-------|------------|------------------|
| **Happ + Reality** | **Yes** — primary approach | — |
| Hy2 / Trojan in Happ | Sometimes on LTE | Home ISP: UDP QUIC blocked, TCP TLS DPI |

**Use VLESS + Reality (Xray on the server).** Do not rely on Hy2/Trojan from Happ on a heavily filtered path.

---

## Server architecture

Two daemons on each node:

```
┌─────────────────────────────────────────────────────────┐
│  VPS (e.g. Hostkey NL)                                    │
│                                                           │
│  sing-box                                                 │
│    UDP 443  → Hysteria2 (+ Salamander obfs)               │
│    TCP 8443 → Trojan (LE or self-signed TLS)              │
│                                                           │
│  Xray-core                                                │
│    TCP 2053 → VLESS + Reality  ← Happ uses this           │
│    TCP 443  → VLESS + Reality  (same keys, backup port)   │
└─────────────────────────────────────────────────────────┘
```

**Why two cores:** Happ runs **Xray-core** for Reality. sing-box’s Reality inbound did not handshake reliably with Happ (`REALITY: processed invalid connection`). Xray as the Reality server fixed it.

Bootstrap deploys both: `outpost/server/bootstrap.py` uploads sing-box + Xray configs; `server/bootstrap.sh` installs certs, sing-box, and Xray.

---

## Reality parameters that work

| Parameter | Value | Notes |
|-----------|-------|-------|
| Protocol | VLESS + Reality | TCP |
| Ports | **2053** (primary), **443** (backup) | Xray listens on both |
| SNI | `www.cloudflare.com` | Must match server `dest` / `serverNames` — see [Choosing a Reality dest](#choosing-a-reality-dest) |
| Fingerprint | **`firefox`** | `chrome` triggered more DPI on test path |
| Flow | **none** | Do **not** use `xtls-rprx-vision` on this path |
| `pbk` / `sid` | From inventory | Must match server keypair + `shortIds` |
| `allowInsecure` / `insecure` / `vcn` | **Never** | Happ 4.11+ rejects these |

Example share link (credentials from your inventory):

```
vless://<uuid>@<ip>:2053?encryption=none&security=reality&type=tcp&sni=www.cloudflare.com&fp=firefox&pbk=<public_key>&sid=<short_id>&spx=%2F#node-reality
```

### Choosing a Reality dest

Reality splices into the real TLS handshake it fetches from `dest`, so **the dest's
certificate chain must fit in Reality's handshake buffer (~2.9 KB)**. Exceed it and
the failure is deeply misleading: client auth *succeeds* (the server logs a valid
`AuthKey` and matching `ClientShortId`), then the handshake dies with
`isHandshakeComplete: false` and the connection is dropped as "invalid". Clients just
show a dead node.

This is exactly what happened on 2026-08-15: `www.microsoft.com` had grown to a
5880-byte chain (8273-byte `Certificate` message) against 2896 bytes of buffer, so
every authenticated client failed while plain `curl` still got a valid Microsoft cert
(unauthenticated traffic is simply proxied through, which makes the node *look* fine).

Measured chains (2026-08-15):

| Host | Chain | Verdict |
|---|---|---|
| `www.cloudflare.com` | ~2493 B | **current default** |
| `speed.cloudflare.com` | ~2520 B | ok |
| `gateway.icloud.com` | ~3198 B | too big |
| `www.apple.com` | ~3231 B | too big |
| `dl.google.com` | ~3513 B | too big |
| `www.microsoft.com` | ~5880 B | **broken** |

Re-measure before changing it — chains grow over time, which is precisely how this
broke:

```bash
openssl s_client -connect <host>:443 -servername <host> -showcerts </dev/null 2>/dev/null \
  | sed -n '/BEGIN CERT/,/END CERT/p' | grep -v CERTIFICATE----- | tr -d '\n' | wc -c
```

To debug a suspected dest problem, set `"show": true` in `realitySettings` plus
`"loglevel": "debug"`, restart xray, and watch `journalctl -u xray` during a client
connection: the `len(s2cSaved)` / `Certificate:` pair shows the mismatch directly.

Port **443** link is identical except `:443` and name suffix `-443`.

Generate fresh links:

```bash
uv run outpost render --happ --out dist-subs
base64 -D -i dist-subs/outpost.happ.txt
```

---

## TLS for Hy2 / Trojan (optional)

Happ 4.11+ removed skip-verify flags. Two options:

### A. Real certificate (recommended if you use Hy2/Trojan)

1. DNS **A** record: `exit.example.com` → VPS IP (REG.RU: DNS only, no proxy).
2. `.env`: `OUTPOST_TLS_DOMAIN=exit.example.com`
3. Re-bootstrap: certbot on the VPS, `node.insecure=false`.
4. Happ links use the domain as host/SNI; **no** `pcs=` / `insecure=`.

### B. Self-signed + cert pin

- Links include `pcs=<hex SHA-256>` of the leaf cert only.
- No `insecure=`, `allowInsecure`, or `vcn=`.

Reality does **not** use this — it pins via `pbk` + `sid`.

---

## Bootstrap / re-deploy

```bash
# .env must include OUTPOST_TLS_DOMAIN if using Let's Encrypt
OUTPOST_TLS_DOMAIN=exit.yourdomain.ru uv run python -c "
from outpost.config import settings
from outpost.store import load_inventory, save_inventory
from outpost.server.bootstrap import bootstrap_node
inv = load_inventory(settings)
node = inv.get('<node_id>')
bootstrap_node(node, settings=settings)
inv.upsert(node)
save_inventory(inv, settings)
"
```

Bootstrap order matters: `tls_domain` is applied **before** rendering sing-box config so Trojan/Hy2 SNI matches the LE cert.

Xray config is written to `/usr/local/etc/xray/config.json` (mode **644** — the service runs as `nobody`).

---

## Happ client checklist

1. **Import only the Reality link** first (delete old profiles).
2. **Routing: Global** (not rule-only).
3. **Turn off Surge TUN** while testing Happ (macOS) — both fight for traffic.
4. If home Wi‑Fi fails, try **mobile LTE** (different DPI).
5. Prefer **2053**; switch to **443** if one port is throttled.

---

## Port layout (defaults)

| Port | Proto | Service | Client |
|------|-------|---------|--------|
| 443 | UDP | Hy2 | Happ / Surge |
| 443 | TCP | Reality | Happ (backup) |
| 8443 | TCP | Trojan | Happ / Surge |
| 2053 | TCP | Reality | Happ (primary) |

Hy2 (UDP) and Reality (TCP) can share **443** — different protocols, no bind conflict.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Ping OK, sites don’t load | DNS not via tunnel — enable remote DNS in Happ; use Global routing |
| `allowInsecure removed` | Old link or `insecure`/`vcn` in URL — re-render, re-import |
| Reality silent fail | Wrong port (2053 closed) or old sing-box-only Reality — re-bootstrap with Xray |
| `failed to read client hello` (server log) | ISP DPI dropping TLS ClientHello — try `fp=firefox`, no flow, other port, or LTE |
| Hy2 timeout, no server logs | UDP/443 blocked — use Reality instead |
| Trojan TLS timeout | TCP DPI on 443 — use Reality instead |

Server-side check:

```bash
ssh root@<ip> 'systemctl is-active xray sing-box; ss -tlnp | grep -E "2053|:443"; journalctl -u xray -n 20 --no-pager'
```

---

## What we learned (Hostkey NL, RU path)

1. **Reality on Xray** + **firefox** + **no Vision flow** + ports **2053/443** = stable in Happ.
2. **Let's Encrypt** on `exit.<domain>` fixes Happ 4.11 TLS for Hy2/Trojan when those paths are reachable.
3. **sing-box Reality ≠ Happ Xray client** — keep Xray for Reality even if sing-box runs everything else.
4. From RU residential ISP, **Reality worked** while Hy2/Trojan did not — protocol choice matters more than cert polish.
